"""Async engine + session lifecycle.

A single engine per process, a session per request. The :func:`get_session`
dependency yields a session and commits on success / rolls back on error, so
handlers never manage transactions by hand.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> None:
    """Create the global engine + session factory. Idempotent."""
    global _engine, _sessionmaker
    if _engine is not None:
        return
    if settings.is_sqlite:
        # SQLite has no connection pool to size; keep the args it accepts.
        _engine = create_async_engine(settings.sqlalchemy_dsn, echo=settings.db_echo)
    else:
        _engine = create_async_engine(
            settings.sqlalchemy_dsn,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,  # detect dropped connections before using them
        )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Session factory for code outside the request cycle (worker, background
    loop), which must open and own its own session rather than receive one."""
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a transactional session scoped to one request."""
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
