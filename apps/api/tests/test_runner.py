"""The inline run gate bounds concurrency so runs can't exhaust the DB pool."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import app.services.runner as runner
from app.core.config import settings


async def test_execute_task_bounds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_max_concurrent_runs", 2)
    runner._run_gates.clear()  # fresh gate at the new limit

    active = 0
    peak = 0

    class _CountingService:
        def __init__(self, *args: object) -> None:
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

    await asyncio.gather(*(runner.execute_task(uuid4()) for _ in range(6)))

    assert peak == 2  # reached the cap, never exceeded it


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
