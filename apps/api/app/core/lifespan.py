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

    # Fail any task left RUNNING by a crash/restart. In inline mode the API is the
    # only executor, so on restart every RUNNING task is stranded (stale_seconds=0).
    # In worker mode separate processes own runs, so only fail genuinely-stuck tasks
    # (no update within the stale window) — never one a live worker is executing.
    from app.db.session import get_sessionmaker
    from app.services.runner import reconcile_interrupted_tasks

    _stale = 0 if settings.execution_mode == "inline" else settings.worker_stale_task_seconds
    async with get_sessionmaker()() as session:
        await reconcile_interrupted_tasks(session, stale_seconds=_stale)

    # Same crash-recovery hygiene for the filesystem: drop any verify-<uuid> workspace
    # copies a mid-verification crash left behind (none are in flight at startup).
    from pathlib import Path

    from app.services.verification import sweep_orphaned_verify_dirs

    swept = sweep_orphaned_verify_dirs(Path(settings.agent_workspaces_root))
    if swept:
        log.info("startup.swept_verify_dirs", count=swept)

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

    if (
        settings.agent_sandbox not in {"off", "inline"}
        and settings.agent_sandbox_backend in {"auto", "docker"}
        and settings.execution_mode == "inline"
    ):
        from app.tools.sandbox import docker_available, image_present

        if not (docker_available() and image_present(settings.agent_sandbox_image)):
            log.warning(
                "startup.sandbox_unavailable",
                wanted=settings.agent_sandbox,
                hint=(
                    "Docker or the sandbox image is unavailable. Required mode will refuse "
                    "tasks; preferred mode will label the inline fallback."
                ),
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
