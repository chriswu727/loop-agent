"""Application lifespan: deterministic startup and graceful shutdown.

On startup we configure logging, initialise tracing, and open the database and
cache pools. On shutdown (SIGTERM during a rolling deploy) we close them so no
connections leak and in-flight work can drain. The yielded ``state`` dict is
attached to ``app.state`` and reachable from request handlers.
"""

from __future__ import annotations

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

    log.info("startup.complete")
    try:
        yield
    finally:
        log.info("shutdown.begin")
        await cache.close()
        await dispose_engine()
        log.info("shutdown.complete")
