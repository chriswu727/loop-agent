"""The channel-agnostic /chat endpoint returns the reply for a run turn."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.db.models.task import TaskModel
from app.domain.task import StopReason, TaskStatus


async def test_chat_endpoint_replies(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    done = TaskModel(
        id=uuid4(),
        status=TaskStatus.COMPLETED.value,
        stop_reason=StopReason.GOAL_ACHIEVED.value,
        summary="did the thing",
    )

    async def fake_run(chat_id: str, message: str) -> TaskModel:
        assert chat_id == "s1" and message == "do a thing"
        return done

    # run_chat_turn hits the real DB + LLM; stub it so we test the route + reply shape.
    monkeypatch.setattr("app.api.v1.routes.chat.run_chat_turn", fake_run)

    resp = await client.post("/api/v1/chat", json={"chat_id": "s1", "message": "do a thing"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Done. did the thing"
    assert body["status"] == "completed"
    assert body["task_id"] == str(done.id)


async def test_chat_requires_message(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/chat", json={"chat_id": "s1", "message": ""})
    assert resp.status_code == 422
