"""The optional API-token gate: open when unset, enforced when set."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings


async def test_api_is_open_when_no_token(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/tasks")).status_code == 200


async def test_api_requires_token_when_set(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "api_token", "s3cret")
    assert (await client.get("/api/v1/tasks")).status_code == 401
    bad = await client.get("/api/v1/tasks", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401
    ok = await client.get("/api/v1/tasks", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


async def test_health_stays_open_with_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "api_token", "s3cret")
    # Health probes live outside /api/v1, so they must not require the token.
    assert (await client.get("/healthz")).status_code == 200
