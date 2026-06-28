"""Cache abstraction backed by Redis.

Services depend on the :class:`Cache` *protocol*, never on Redis directly, so
tests can substitute :class:`InMemoryCache` and you can swap the backend without
touching business code. The same Redis connection also serves as the broker for
the background-worker queue (see ``app/workers/queue.py``).
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

import redis.asyncio as aioredis

from app.core.config import settings


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def incr(self, key: str, *, ttl_seconds: int | None = None) -> int: ...
    async def close(self) -> None: ...


class RedisCache:
    """Production cache backed by Redis."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    @property
    def client(self) -> aioredis.Redis:
        return self._client

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def incr(self, key: str, *, ttl_seconds: int | None = None) -> int:
        value = await self._client.incr(key)
        if value == 1 and ttl_seconds:
            await self._client.expire(key, ttl_seconds)
        return int(value)

    async def close(self) -> None:
        await self._client.aclose()


class InMemoryCache:
    """Dependency-free cache for tests and local fallback."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    def _expired(self, key: str) -> bool:
        item = self._store.get(key)
        if item is None:
            return True
        _, expires_at = item
        return expires_at is not None and expires_at < time.monotonic()

    async def get(self, key: str) -> str | None:
        if self._expired(key):
            self._store.pop(key, None)
            return None
        return self._store[key][0]

    async def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        expires = time.monotonic() + ttl_seconds if ttl_seconds else None
        self._store[key] = (value, expires)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def incr(self, key: str, *, ttl_seconds: int | None = None) -> int:
        current = 0 if self._expired(key) else int(self._store[key][0])
        current += 1
        await self.set(key, str(current), ttl_seconds=ttl_seconds)
        return current

    async def close(self) -> None:
        self._store.clear()


def create_cache() -> Cache:
    """Factory used at startup. Redis by default; in-memory on the zero-infra
    path so the app runs with no Redis to install."""
    use_memory = settings.cache_backend == "memory" or (
        settings.cache_backend == "auto" and settings.is_sqlite
    )
    if use_memory:
        return InMemoryCache()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    return RedisCache(client)
