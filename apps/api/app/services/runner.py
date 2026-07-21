"""Where a task run is actually triggered and executed.

Two execution modes share one code path (:func:`execute_task`):

* ``inline``  — the API process runs the loop in a background task. Zero extra
  infrastructure, ideal for a laptop or a small single-node deployment.
* ``worker``  — the API enqueues the id on Redis and a separate worker process
  runs the loop, so loops scale independently of request traffic.

Both triggers fire *after* the publishing request has committed, so the worker
or background session always finds the row.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import get_llm_client, get_verifier_client
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.domain.task import StopReason, TaskStatus
from app.observability.metrics import TASK_RUN_DURATION, TASK_RUNS
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.services.agent_react import AgentReactService
from app.workers.queue import enqueue

log = get_logger("runner")

RUN_TASK_JOB = "run_task"

# One gate per event loop (tests spin up fresh loops), so concurrent runs — each
# holding a DB session for its whole duration — can't exhaust the connection pool.
_run_gates: dict[int, asyncio.Semaphore] = {}


def _run_gate() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    gate = _run_gates.get(loop_id)
    if gate is None:
        gate = asyncio.Semaphore(settings.agent_max_concurrent_runs)
        _run_gates[loop_id] = gate
    return gate


async def _heartbeat_task(task_id: uuid.UUID, stop: asyncio.Event) -> None:
    from sqlalchemy import func, update

    from app.db.models.task import TaskModel

    interval = max(10, settings.worker_visibility_timeout_seconds // 3)
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        async with get_sessionmaker()() as session:
            await session.execute(
                update(TaskModel)
                .where(TaskModel.id == task_id, TaskModel.status == TaskStatus.RUNNING.value)
                .values(updated_at=func.now())
            )
            await session.commit()


async def _task_cancelled(task_id: uuid.UUID) -> bool:
    from sqlalchemy import select

    from app.db.models.task import TaskModel

    async with get_sessionmaker()() as session:
        status = await session.scalar(select(TaskModel.status).where(TaskModel.id == task_id))
    return status == TaskStatus.CANCELLED.value


async def reconcile_interrupted_tasks(
    session: AsyncSession, *, stale_seconds: int = 0, requeue: bool = False
) -> int:
    """Fail tasks left RUNNING by a crash/restart, so they don't sit stranded RUNNING
    forever (breaking the 'leave it running unattended' promise). Paused
    (awaiting_input) tasks are untouched; they resume when answered.

    With ``stale_seconds > 0`` only tasks whose last update is older than that window
    are failed — a live run keeps bumping ``updated_at`` every step, so this is safe
    to run while OTHER workers are actively processing (worker mode). With 0 (inline
    restart, where the API is the only executor) every RUNNING task is stranded."""
    from sqlalchemy import exists, func, text, update
    from sqlalchemy.orm import aliased

    from app.db.models.task import TaskModel

    conditions = [TaskModel.status == TaskStatus.RUNNING.value]
    if stale_seconds > 0:
        # A parent whose sub-agent is still going is ALIVE even though its own
        # updated_at is frozen (the spawn runs the child synchronously, as one parent
        # step). Never fail a task that has a non-terminal child — the child's own
        # staleness reconciles it first, then the parent on a later pass.
        child = aliased(TaskModel)
        conditions.append(
            ~exists().where(
                child.parent_id == TaskModel.id,
                child.status.in_(
                    (
                        TaskStatus.RUNNING.value,
                        TaskStatus.PENDING.value,
                        TaskStatus.AWAITING_INPUT.value,
                    )
                ),
            )
        )
        # Compute the cutoff with the DB's own clock so it matches how updated_at was
        # stored (naive-UTC on SQLite, aware-UTC on Postgres) — mixing a Python-side
        # tz with the column's storage raises "can't compare naive and aware". secs is
        # a validated int, so the interpolation is safe.
        secs = int(stale_seconds)
        bind = session.get_bind()
        cutoff = (
            func.datetime("now", f"-{secs} seconds")
            if bind.dialect.name == "sqlite"
            else func.now() - text(f"interval '{secs} seconds'")
        )
        conditions.append(TaskModel.updated_at < cutoff)
    values: dict[str, object] = (
        {
            "status": TaskStatus.PENDING.value,
            "error": "Interrupted — recovered by the durable worker queue.",
            "attempt": TaskModel.attempt + 1,
        }
        if requeue
        else {
            "status": TaskStatus.FAILED.value,
            "stop_reason": StopReason.ERROR.value,
            "error": "Interrupted — the runner crashed or restarted mid-run.",
        }
    )
    result = await session.execute(
        update(TaskModel)
        .where(*conditions)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    count = result.rowcount or 0  # type: ignore[attr-defined]
    if count:
        log.warning("runner.reconciled_interrupted", count=count, stale_seconds=stale_seconds)
    return count


async def execute_task(task_id: uuid.UUID) -> None:
    """Run the full loop for one task in a freshly-owned session. Bounded by a
    concurrency gate so the session (and its DB connection) is only held once a
    slot is free — excess runs queue instead of exhausting the pool."""
    async with _run_gate():
        started = time.perf_counter()
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            tasks = TaskRepository(session)
            steps = StepRepository(session)
            service = AgentReactService(
                tasks,
                steps,
                get_llm_client(),
                verifier_llm=get_verifier_client(),
                cancellation_probe=_task_cancelled,
            )
            stop = asyncio.Event()
            heartbeat = asyncio.create_task(_heartbeat_task(task_id, stop))
            try:
                await service.run(task_id)
            finally:
                stop.set()
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                task = None
                with contextlib.suppress(Exception):
                    task = await tasks.get(task_id)
                if task is not None:
                    TASK_RUNS.labels(
                        status=task.status,
                        stop_reason=task.stop_reason or "none",
                    ).inc()
                TASK_RUN_DURATION.observe(time.perf_counter() - started)


async def trigger_task(task_id: uuid.UUID) -> None:
    """Kick off a task according to the configured execution mode."""
    if settings.execution_mode == "worker":
        await enqueue(RUN_TASK_JOB, {"task_id": str(task_id)})
        log.info("runner.enqueued", task_id=str(task_id))
    else:
        await execute_task(task_id)
