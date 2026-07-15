from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis

from app.domain.authority_revocation import AuthorityRevocationStore


async def test_authority_revocation_is_run_scoped_and_expires() -> None:
    store = AuthorityRevocationStore()
    await store.revoke("run-one", datetime.now(UTC) + timedelta(minutes=1))

    assert await store.is_revoked("run-one")
    assert not await store.is_revoked("run-two")

    await store.revoke("expired", datetime.now(UTC) - timedelta(seconds=1))
    assert not await store.is_revoked("expired")


async def test_authority_revocation_survives_restart(tmp_path) -> None:
    path = tmp_path / "authority.sqlite3"
    first = AuthorityRevocationStore(path)
    await first.revoke("run-one", datetime.now(UTC) + timedelta(minutes=1))

    restarted = AuthorityRevocationStore(path)

    assert restarted.durable
    assert await restarted.is_revoked("run-one")


async def test_redis_revocation_is_shared_and_notifies_other_instances() -> None:
    server = fakeredis.FakeServer()
    first_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    second_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    first = AuthorityRevocationStore(redis_client=first_client, namespace="test:authority")
    second = AuthorityRevocationStore(redis_client=second_client, namespace="test:authority")
    notified = asyncio.Event()
    revoked_runs: list[str] = []

    async def on_revoke(run_id: str) -> None:
        revoked_runs.append(run_id)
        notified.set()

    try:
        await second.subscribe(on_revoke)
        await first.revoke("run-one", datetime.now(UTC) + timedelta(minutes=1))

        await asyncio.wait_for(notified.wait(), timeout=1)
        assert first.shared and second.shared
        assert first.backend == second.backend == "redis"
        assert await second.is_revoked("run-one")
        assert revoked_runs == ["run-one"]
    finally:
        await first.close()
        await second.close()
        await first_client.aclose()
        await second_client.aclose()


async def test_redis_subscriber_catches_revocation_published_before_it_started() -> None:
    server = fakeredis.FakeServer()
    first_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    second_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    first = AuthorityRevocationStore(redis_client=first_client, namespace="test:catch-up")
    second = AuthorityRevocationStore(redis_client=second_client, namespace="test:catch-up")
    notified = asyncio.Event()

    async def on_revoke(run_id: str) -> None:
        assert run_id == "run-before-subscribe"
        notified.set()

    try:
        await first.revoke(
            "run-before-subscribe",
            datetime.now(UTC) + timedelta(minutes=1),
        )
        await second.subscribe(on_revoke)

        await asyncio.wait_for(notified.wait(), timeout=2)
    finally:
        await first.close()
        await second.close()
        await first_client.aclose()
        await second_client.aclose()
