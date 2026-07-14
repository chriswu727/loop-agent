"""Background scheduler: periodically fire due interval triggers.

Runs as a single asyncio task in the API process (started by the app lifespan).
Each tick opens its own session, finds triggers whose interval has elapsed, and
fires them. A failing tick is logged and the loop continues — the scheduler must
never take the app down.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.repositories.trigger import TriggerRepository
from app.services.task import TaskService
from app.services.trigger import TriggerService

log = get_logger("scheduler")


async def _tick_once() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        service = TriggerService(
            TriggerRepository(session),
            TaskService(TaskRepository(session), StepRepository(session)),
            subject=None,
        )
        fired = await service.tick()
        if fired:
            log.info("scheduler.tick", fired=fired)


async def run_scheduler(stop: asyncio.Event) -> None:
    log.info("scheduler.started", interval_seconds=settings.scheduler_tick_seconds)
    try:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_tick_seconds)
            if stop.is_set():
                break
            try:
                await _tick_once()
            except Exception:  # never let a bad tick kill the scheduler
                log.exception("scheduler.tick_failed")
    finally:
        log.info("scheduler.stopped")
