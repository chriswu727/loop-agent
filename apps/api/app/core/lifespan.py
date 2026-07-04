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
from app.core.llm.client import aclose_llm_client
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

    # Fail any task this node left RUNNING by a previous crash/restart. Only in
    # inline mode — in worker mode a separate process owns runs, so the API
    # restarting must not fail tasks a live worker is still executing.
    if settings.execution_mode == "inline":
        from app.db.session import get_sessionmaker
        from app.services.runner import reconcile_interrupted_tasks

        async with get_sessionmaker()() as session:
            await reconcile_interrupted_tasks(session)

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

    # Surface a misconfiguration at boot, not only when the first task fails.
    from app.core.llm.registry import configured_providers

    if not configured_providers(settings.llm_default_provider):
        log.warning(
            "startup.no_llm_provider",
            hint="Tasks will fail. Set DEEPSEEK_API_KEY (or another), or DEMO_MODE=1 "
            "to try it without a key, or OLLAMA_BASE_URL for a local model.",
        )

    # Container isolation is the hard safety boundary; if it's wanted but Docker or
    # the image is missing, shell commands run on the host (reduced isolation) —
    # tell the operator their posture at boot, not just per-task.
    if settings.agent_sandbox != "inline":
        from app.tools.sandbox import docker_available, image_present

        if not (docker_available() and image_present(settings.agent_sandbox_image)):
            log.warning(
                "startup.sandbox_unavailable",
                wanted=settings.agent_sandbox,
                hint="Docker or the sandbox image is unavailable — shell commands run "
                "INLINE (reduced isolation, best-effort policy). Start Docker and build "
                "the image (`make sandbox-image`) for the container jail.",
            )

    log.info("startup.complete")
    try:
        yield
    finally:
        log.info("shutdown.begin")
        if scheduler_stop is not None and scheduler_task is not None:
            # Cancel (not just signal): a tick mid-agent-run would otherwise make
            # shutdown wait for the whole run. reconcile fixes any left RUNNING.
            scheduler_stop.set()
            scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler_task
        if telegram_stop is not None and telegram_task is not None:
            telegram_stop.set()
            telegram_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telegram_task
        await aclose_llm_client()
        await cache.close()
        await dispose_engine()
        log.info("shutdown.complete")
