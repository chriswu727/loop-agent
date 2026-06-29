"""Triggers: save a task template and fire it to publish a task."""

from __future__ import annotations

from httpx import AsyncClient


async def test_create_list_fire_delete(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/triggers",
        json={
            "name": "nightly summary",
            "goal": "Write today's summary into summary.md",
            "limits": {"max_steps": 9999},
            "allow_egress": False,
            "require_approval": True,
        },
    )
    assert created.status_code == 201
    trigger = created.json()
    assert trigger["name"] == "nightly summary"
    assert trigger["max_steps"] == 40  # clamped to the cap
    assert trigger["require_approval"] is True
    assert trigger["secret"]  # a webhook secret was generated
    tid = trigger["id"]
    sec = trigger["secret"]

    listed = (await client.get("/api/v1/triggers")).json()
    assert any(t["id"] == tid for t in listed)

    # Fire it with the secret -> a task is published (the agent run is stubbed).
    fired = await client.post(
        f"/api/v1/triggers/{tid}/fire", headers={"X-Trigger-Secret": sec}
    )
    assert fired.status_code == 200
    task = fired.json()
    assert task["status"] == "pending"
    assert task["goal"] == "Write today's summary into summary.md"
    assert task["require_approval"] is True  # template config carried onto the task

    page = (await client.get("/api/v1/tasks")).json()
    assert any(t["id"] == task["id"] for t in page["items"])

    # fire_count bumped.
    again = (await client.get("/api/v1/triggers")).json()
    assert next(t for t in again if t["id"] == tid)["fire_count"] == 1

    assert (await client.delete(f"/api/v1/triggers/{tid}")).status_code == 204
    assert (await client.get("/api/v1/triggers")).json() == []


async def test_fire_requires_the_secret(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/triggers", json={"name": "hook", "goal": "do the webhook thing"}
    )
    trigger = created.json()
    tid, sec = trigger["id"], trigger["secret"]

    # No secret -> 403; wrong secret -> 403.
    assert (await client.post(f"/api/v1/triggers/{tid}/fire")).status_code == 403
    bad = await client.post(f"/api/v1/triggers/{tid}/fire", headers={"X-Trigger-Secret": "nope"})
    assert bad.status_code == 403

    # Correct secret via query param (for webhook senders that only send a URL).
    ok = await client.post(f"/api/v1/triggers/{tid}/fire?secret={sec}")
    assert ok.status_code == 200
    assert ok.json()["status"] == "pending"


async def test_fire_unknown_trigger_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/triggers/00000000-0000-0000-0000-000000000000/fire",
        headers={"X-Trigger-Secret": "whatever"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
