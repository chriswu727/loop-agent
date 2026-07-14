from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_access_token


@pytest.fixture
def authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(settings, "secret_key", "test-session-secret-that-is-at-least-32-bytes")


def bearer(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject)}"}


async def test_jwt_subjects_cannot_read_each_others_tasks(
    client: AsyncClient, authenticated: None
) -> None:
    alice = bearer("github:1")
    bob = bearer("github:2")
    created = await client.post("/api/v1/tasks", json={"goal": "Alice private task"}, headers=alice)
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["owner_id"] == "github:1"

    assert (await client.get(f"/api/v1/tasks/{task_id}", headers=bob)).status_code == 404
    assert (await client.get("/api/v1/tasks", headers=bob)).json()["total"] == 0
    assert (await client.get("/api/v1/tasks", headers=alice)).json()["total"] == 1


async def test_idempotency_is_stable_per_owner(client: AsyncClient, authenticated: None) -> None:
    payload = {"goal": "Publish exactly once", "idempotency_key": "publish-key-0001"}
    alice_first = await client.post("/api/v1/tasks", json=payload, headers=bearer("github:1"))
    alice_second = await client.post("/api/v1/tasks", json=payload, headers=bearer("github:1"))
    bob = await client.post("/api/v1/tasks", json=payload, headers=bearer("github:2"))

    assert alice_first.status_code == alice_second.status_code == bob.status_code == 201
    assert alice_first.json()["id"] == alice_second.json()["id"]
    assert bob.json()["id"] != alice_first.json()["id"]


async def test_triggers_are_scoped_to_the_jwt_subject(
    client: AsyncClient, authenticated: None
) -> None:
    alice = bearer("github:1")
    bob = bearer("github:2")
    created = await client.post(
        "/api/v1/triggers",
        json={"name": "alice-hook", "goal": "Run Alice automation"},
        headers=alice,
    )
    assert created.status_code == 201
    assert created.json()["owner_id"] == "github:1"
    assert len((await client.get("/api/v1/triggers", headers=alice)).json()) == 1
    assert (await client.get("/api/v1/triggers", headers=bob)).json() == []


async def test_trigger_webhook_uses_its_secret_without_a_bearer_token(
    client: AsyncClient, authenticated: None
) -> None:
    created = await client.post(
        "/api/v1/triggers",
        json={"name": "external-hook", "goal": "Run external automation"},
        headers=bearer("github:1"),
    )
    trigger = created.json()

    denied = await client.post(f"/hooks/triggers/{trigger['id']}")
    assert denied.status_code == 403

    fired = await client.post(
        f"/hooks/triggers/{trigger['id']}",
        headers={"X-Trigger-Secret": trigger["secret"]},
    )
    assert fired.status_code == 200
    assert fired.json()["owner_id"] == "github:1"

    owner_tasks = await client.get("/api/v1/tasks", headers=bearer("github:1"))
    other_tasks = await client.get("/api/v1/tasks", headers=bearer("github:2"))
    assert owner_tasks.json()["total"] == 1
    assert other_tasks.json()["total"] == 0


async def test_auth_rejects_missing_and_wrong_audience_tokens(
    client: AsyncClient, authenticated: None
) -> None:
    assert (await client.get("/api/v1/tasks")).status_code == 401
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "github:1",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.jwt_issuer,
            "aud": "some-other-api",
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
