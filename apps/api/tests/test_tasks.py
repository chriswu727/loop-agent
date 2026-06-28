"""HTTP-surface tests for the task API. The background agent trigger is stubbed
in conftest, so these exercise transport, validation, and limit clamping only.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_publish_creates_pending_task_with_clamped_limits(client: AsyncClient) -> None:
    # Absurd limits must be clamped to the configured hard caps.
    resp = await client.post(
        "/api/v1/tasks",
        json={
            "goal": "build a small script",
            "limits": {"max_steps": 9999, "token_budget": 99_000_000},
        },
    )
    assert resp.status_code == 201
    task = resp.json()
    assert task["status"] == "pending"
    assert task["goal"] == "build a small script"
    assert task["limits"]["max_steps"] == 40  # clamped to the cap
    assert task["limits"]["token_budget"] == 200_000  # clamped to the cap
    assert task["verification_score"] == 0


async def test_defaults_applied_when_limits_omitted(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/tasks", json={"goal": "write a haiku file"})
    assert resp.status_code == 201
    limits = resp.json()["limits"]
    assert limits["max_steps"] == 12
    assert limits["token_budget"] == 60_000


async def test_goal_too_short_is_validation_error(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/tasks", json={"goal": "hi"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_list_get_and_steps(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "summarise a paragraph"})).json()
    task_id = created["id"]

    page = (await client.get("/api/v1/tasks")).json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == task_id

    got = await client.get(f"/api/v1/tasks/{task_id}")
    assert got.status_code == 200

    steps = await client.get(f"/api/v1/tasks/{task_id}/steps")
    assert steps.status_code == 200
    assert steps.json() == []  # nothing run yet (trigger stubbed)


async def test_cancel_pending_task(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "outline a blog post"})).json()
    task_id = created["id"]

    cancelled = await client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    again = await client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"


async def test_limits_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tasks/limits")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_steps_cap"] == 40
    assert body["max_steps_default"] == 12


async def test_unknown_task_is_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


async def test_files_empty_before_any_run(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "make a file"})).json()
    resp = await client.get(f"/api/v1/tasks/{created['id']}/files")
    assert resp.status_code == 200
    assert resp.json() == []  # no workspace yet (trigger stubbed)


async def test_respond_conflicts_when_not_awaiting_input(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "do a thing"})).json()
    resp = await client.post(f"/api/v1/tasks/{created['id']}/respond", json={"answer": "hi"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


async def test_pending_question_is_exposed(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "another thing"})).json()
    assert created["pending_question"] is None  # field is part of the contract
