"""Application lifespan: deterministic startup and graceful shutdown.

On startup we configure logging, initialise tracing, and open the database and
cache pools. On shutdown (SIGTERM during a rolling deploy) we close them so no
connections leak and in-flight work can drain. The yielded ``state`` dict is
attached to ``app.state`` and reachable from request handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.cache.redis import create_cache
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, init_engine
from app.observability.tracing import setup_tracing

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("startup.begin", environment=settings.environment, version=settings.version)

    setup_tracing(app)
    init_engine()
    # On the zero-infra SQLite path there are no migrations to run, so create the
    # schema on startup. Postgres deployments use Alembic migrations instead.
    if settings.is_sqlite:
        from app.db.base import Base
        from app.db.session import get_engine

        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    cache = create_cache()
    app.state.cache = cache

    # Start the trigger heartbeat (fires due interval triggers). Inline-mode only
    # so we don't run two schedulers when a separate worker is deployed.
    scheduler_stop: asyncio.Event | None = None
    scheduler_task: asyncio.Task[None] | None = None
    if settings.scheduler_enabled and settings.execution_mode == "inline":
        from app.services.scheduler import run_scheduler

        scheduler_stop = asyncio.Event()
        scheduler_task = asyncio.create_task(run_scheduler(scheduler_stop))

    # Telegram chat inlet — polls for messages and runs them as tasks (inline mode
    # so the loop runs in-process).
    telegram_stop: asyncio.Event | None = None
    telegram_task: asyncio.Task[None] | None = None
    if settings.telegram_bot_token and settings.execution_mode == "inline":
        from app.services.telegram import run_telegram_bot

        telegram_stop = asyncio.Event()
        telegram_task = asyncio.create_task(run_telegram_bot(telegram_stop))

    log.info("startup.complete")
    try:
        yield
    finally:
        log.info("shutdown.begin")
        if scheduler_stop is not None and scheduler_task is not None:
            scheduler_stop.set()
            await scheduler_task
        if telegram_stop is not None and telegram_task is not None:
            telegram_stop.set()
            telegram_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_task
        await cache.close()
        await dispose_engine()
        log.info("shutdown.complete")
