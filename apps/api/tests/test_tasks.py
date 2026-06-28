"""HTTP-surface tests for the task API. The background loop trigger is stubbed
in conftest, so these exercise transport, validation, and limit clamping only.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_publish_creates_pending_task_with_clamped_limits(client: AsyncClient) -> None:
    # Absurd limits must be clamped to the configured hard caps.
    resp = await client.post(
        "/api/v1/tasks",
        json={
            "goal": "draft a short product blurb",
            "limits": {"max_iterations": 9999, "token_budget": 99_000_000, "target_score": 100},
        },
    )
    assert resp.status_code == 201
    task = resp.json()
    assert task["status"] == "pending"
    assert task["goal"] == "draft a short product blurb"
    assert task["limits"]["max_iterations"] == 15  # clamped to the cap
    assert task["limits"]["token_budget"] == 200_000  # clamped to the cap
    assert task["limits"]["target_score"] == 100
    assert task["best_score"] == 0


async def test_score_above_100_is_rejected(client: AsyncClient) -> None:
    # A score is 0-100 by definition, so an out-of-range target is invalid input.
    resp = await client.post(
        "/api/v1/tasks",
        json={"goal": "anything at all", "limits": {"target_score": 999}},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_defaults_applied_when_limits_omitted(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/tasks", json={"goal": "write a haiku about loops"})
    assert resp.status_code == 201
    limits = resp.json()["limits"]
    assert limits["max_iterations"] == 6
    assert limits["token_budget"] == 60_000
    assert limits["target_score"] == 90


async def test_goal_too_short_is_validation_error(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/tasks", json={"goal": "hi"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_list_get_and_iterations(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "summarise a paragraph"})).json()
    task_id = created["id"]

    page = (await client.get("/api/v1/tasks")).json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == task_id

    got = await client.get(f"/api/v1/tasks/{task_id}")
    assert got.status_code == 200

    its = await client.get(f"/api/v1/tasks/{task_id}/iterations")
    assert its.status_code == 200
    assert its.json() == []  # nothing run yet (trigger stubbed)


async def test_cancel_pending_task(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/tasks", json={"goal": "outline a blog post"})).json()
    task_id = created["id"]

    cancelled = await client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Cancelling again is a conflict — it's already terminal.
    again = await client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"


async def test_limits_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tasks/limits")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_iterations_cap"] == 15
    assert body["target_score_default"] == 90


async def test_unknown_task_is_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
