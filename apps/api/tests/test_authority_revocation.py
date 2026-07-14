from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
