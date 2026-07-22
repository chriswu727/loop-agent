from __future__ import annotations

import pytest

from app.cache.redis import InMemoryCache


@pytest.mark.asyncio
async def test_in_memory_increment_keeps_the_original_window_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr("app.cache.redis.time.monotonic", lambda: now[0])
    cache = InMemoryCache()

    assert await cache.incr("key", ttl_seconds=10) == 1
    now[0] = 109.0
    assert await cache.incr("key", ttl_seconds=10) == 2
    now[0] = 111.0
    assert await cache.incr("key", ttl_seconds=10) == 1
