"""Test fixtures: an in-memory SQLite database and in-memory cache, so the full
HTTP stack can be exercised with zero external services (no Postgres, no Redis).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models as _models  # noqa: F401  (register models on Base.metadata)
from app.cache.redis import InMemoryCache
from app.db.base import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory connection for the test
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def override_get_session() -> AsyncIterator[object]:
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session
    app.state.cache = InMemoryCache()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[object]:
    """A bare session for exercising services/repositories directly."""
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessionmaker() as s:
        yield s


@pytest.fixture(autouse=True)
def _no_real_background_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing schedules a background loop that would hit a real LLM. Replace
    the trigger with a no-op so HTTP tests never touch the network."""

    async def _noop(task_id: object) -> None:
        return None

    monkeypatch.setattr("app.api.v1.routes.tasks.trigger_task", _noop, raising=True)
