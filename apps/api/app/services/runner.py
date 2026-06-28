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

import uuid

from app.core.config import settings
from app.core.llm import get_llm_client
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.repositories.iteration import IterationRepository
from app.repositories.task import TaskRepository
from app.services.agent_loop import AgentLoopService
from app.workers.queue import enqueue

log = get_logger("runner")

RUN_TASK_JOB = "run_task"


async def execute_task(task_id: uuid.UUID) -> None:
    """Run the full loop for one task in a freshly-owned session."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tasks = TaskRepository(session)
        iterations = IterationRepository(session)
        service = AgentLoopService(tasks, iterations, get_llm_client())
        await service.run(task_id)


async def trigger_task(task_id: uuid.UUID) -> None:
    """Kick off a task according to the configured execution mode."""
    if settings.execution_mode == "worker":
        await enqueue(RUN_TASK_JOB, {"task_id": str(task_id)})
        log.info("runner.enqueued", task_id=str(task_id))
    else:
        await execute_task(task_id)
