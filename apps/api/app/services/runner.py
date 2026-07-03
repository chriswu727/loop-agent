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
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import get_llm_client
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.domain.task import StopReason, TaskStatus
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


async def reconcile_interrupted_tasks(session: AsyncSession) -> int:
    """Fail any task left RUNNING by a crash/restart. Inline runs are in-process
    background tasks, so a restart strands them in RUNNING forever otherwise —
    breaking the 'leave it running unattended' promise. Paused (awaiting_input)
    tasks are untouched; they resume correctly when answered."""
    result = await session.execute(
        text(
            "UPDATE tasks SET status=:failed, stop_reason=:reason, error=:msg WHERE status=:running"
        ),
        {
            "failed": TaskStatus.FAILED.value,
            "reason": StopReason.ERROR.value,
            "msg": "Interrupted by a server restart.",
            "running": TaskStatus.RUNNING.value,
        },
    )
    await session.commit()
    count = result.rowcount or 0
    if count:
        log.warning("runner.reconciled_interrupted", count=count)
    return count


async def execute_task(task_id: uuid.UUID) -> None:
    """Run the full loop for one task in a freshly-owned session. Bounded by a
    concurrency gate so the session (and its DB connection) is only held once a
    slot is free — excess runs queue instead of exhausting the pool."""
    async with _run_gate():
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            tasks = TaskRepository(session)
            steps = StepRepository(session)
            service = AgentReactService(tasks, steps, get_llm_client())
            await service.run(task_id)


async def trigger_task(task_id: uuid.UUID) -> None:
    """Kick off a task according to the configured execution mode."""
    if settings.execution_mode == "worker":
        await enqueue(RUN_TASK_JOB, {"task_id": str(task_id)})
        log.info("runner.enqueued", task_id=str(task_id))
    else:
        await execute_task(task_id)
