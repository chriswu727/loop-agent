"""The inline run gate bounds concurrency so runs can't exhaust the DB pool."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import app.services.runner as runner
from app.core.config import settings


async def test_execute_task_bounds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_max_concurrent_runs", 4)
    runner._run_gates.clear()  # fresh gate at the new limit

    active = 0
    peak = 0

    class _CountingService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run(self, _task_id: object) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1

    class _NullSession:
        async def __aenter__(self) -> _NullSession:
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

    monkeypatch.setattr(runner, "AgentReactService", _CountingService)
    monkeypatch.setattr(runner, "get_sessionmaker", lambda: _NullSession)
    monkeypatch.setattr(runner, "get_llm_client", lambda: None)

    await asyncio.gather(*(runner.execute_task(uuid4()) for _ in range(20)))

    assert peak == 4  # reached the cap, never exceeded it


async def test_duplicate_delivery_claims_one_task_exactly_once(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.models as _models  # noqa: F401
    from app.db.base import Base
    from app.repositories.task import TaskRepository

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessions() as session:
        task = await TaskRepository(session).create(
            goal="claim once",
            status="pending",
            rubric=[],
            max_steps=4,
            token_budget=10_000,
            summary=None,
            verification_score=0,
            steps_used=0,
            tokens_used=0,
            workspace_path=None,
        )
        await session.commit()

    async def claim() -> bool:
        async with sessions() as session:
            return await TaskRepository(session).claim_pending(task.id) is not None

    try:
        claims = await asyncio.gather(*(claim() for _ in range(20)))
        assert sum(claims) == 1
    finally:
        await engine.dispose()


async def test_claim_recovers_working_state_but_never_human_wait_state(session) -> None:
    from app.domain.loop import LoopState
    from app.repositories.task import TaskRepository

    repo = TaskRepository(session)
    common = {
        "status": "pending",
        "rubric": [],
        "max_steps": 4,
        "token_budget": 10_000,
        "summary": None,
        "verification_score": 0,
        "steps_used": 0,
        "tokens_used": 0,
        "workspace_path": None,
    }
    interrupted = await repo.create(
        goal="recover me",
        loop_state=LoopState.ACTING.value,
        transition_sequence=3,
        **common,
    )
    waiting = await repo.create(
        goal="wait for me",
        loop_state=LoopState.AWAITING_INPUT.value,
        **common,
    )
    await session.commit()

    claimed = await repo.claim_pending(interrupted.id)
    assert claimed is not None
    assert claimed.loop_state == LoopState.PREPARING.value
    assert claimed.transition_reason == "worker_recovered_interrupted_task"
    assert claimed.transition_sequence == 4
    assert await repo.claim_pending(waiting.id) is None


async def test_worker_run_task_handler_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's run_task handler runs the loop for the payload's task id."""
    import app.workers.worker as worker

    called = []

    async def fake_execute(task_id: object) -> None:
        called.append(task_id)

    monkeypatch.setattr("app.services.runner.execute_task", fake_execute)
    tid = uuid4()
    await worker.HANDLERS["run_task"]({"task_id": str(tid)})
    assert called == [tid]


async def test_trigger_task_enqueues_in_worker_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """In worker mode, publishing enqueues the id instead of running inline."""
    monkeypatch.setattr(settings, "execution_mode", "worker")
    enqueued = []

    async def fake_enqueue(job: str, payload: dict) -> None:
        enqueued.append((job, payload))

    monkeypatch.setattr(runner, "enqueue", fake_enqueue)
    tid = uuid4()
    await runner.trigger_task(tid)
    assert enqueued == [(runner.RUN_TASK_JOB, {"task_id": str(tid)})]


async def test_reconcile_only_fails_stale_running_tasks(session) -> None:
    # Staleness-bounded reconcile: a task actively running (recent updated_at) is
    # left alone; one stranded by a crash (old updated_at) is failed. This is what
    # makes reconcile safe to run while sibling workers are live.
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.db.models.task import TaskModel
    from app.repositories.task import TaskRepository
    from app.services.runner import reconcile_interrupted_tasks

    repo = TaskRepository(session)
    common: dict[str, object] = {
        "rubric": [],
        "max_steps": 8,
        "token_budget": 1000,
        "summary": None,
        "verification_score": 0,
        "steps_used": 0,
        "tokens_used": 0,
        "workspace_path": None,
    }
    fresh = await repo.create(goal="fresh", status="running", **common)
    stale = await repo.create(goal="stale", status="running", **common)
    await session.commit()
    old = datetime.now(UTC) - timedelta(seconds=3600)
    await session.execute(update(TaskModel).where(TaskModel.id == stale.id).values(updated_at=old))
    await session.commit()

    failed = await reconcile_interrupted_tasks(session, stale_seconds=900)
    assert failed == 1  # only the genuinely-stranded one

    await session.refresh(fresh)
    await session.refresh(stale)
    assert fresh.status == "running"  # a recent/live run is untouched
    assert stale.status == "failed"

    # stale_seconds=0 (inline restart) fails every RUNNING task.
    assert await reconcile_interrupted_tasks(session, stale_seconds=0) == 1
    await session.refresh(fresh)
    assert fresh.status == "failed"


async def test_reconcile_spares_a_parent_with_a_live_child(session) -> None:
    # A parent's updated_at freezes while its sub-agent runs (spawn is synchronous),
    # so staleness alone would wrongly fail a live parent. It's spared while a child
    # is non-terminal, then failed once the child finishes.
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.db.models.task import TaskModel
    from app.repositories.task import TaskRepository
    from app.services.runner import reconcile_interrupted_tasks

    repo = TaskRepository(session)
    common = {
        "rubric": [],
        "max_steps": 8,
        "token_budget": 1000,
        "summary": None,
        "verification_score": 0,
        "steps_used": 0,
        "tokens_used": 0,
        "workspace_path": None,
    }
    parent = await repo.create(goal="parent", status="running", **common)
    child = await repo.create(
        goal="child", status="running", parent_id=parent.id, depth=1, **common
    )
    await session.commit()
    old = datetime.now(UTC) - timedelta(seconds=3600)
    await session.execute(update(TaskModel).where(TaskModel.id == parent.id).values(updated_at=old))
    await session.commit()

    assert await reconcile_interrupted_tasks(session, stale_seconds=900) == 0  # child is live
    await session.refresh(parent)
    assert parent.status == "running"

    child.status = "completed"
    await session.commit()
    assert await reconcile_interrupted_tasks(session, stale_seconds=900) == 1  # now orphaned
    await session.refresh(parent)
    assert parent.status == "failed"
