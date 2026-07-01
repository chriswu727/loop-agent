"""Telegram inlet: the client wire calls, the reply formatting, and routing a
message to an open (awaiting) task. The full message->task->reply round trip
reuses the already-tested publish/respond/execute path and needs a bot token to
run live."""

from __future__ import annotations

import json

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import TaskModel
from app.domain.task import StopReason, TaskStatus
from app.repositories.task import TaskRepository
from app.services.telegram import TelegramClient, _find_awaiting, reply_for


async def test_client_get_updates_and_send() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "getUpdates" in request.url.path:
            seen["offset"] = request.url.params.get("offset")
            return httpx.Response(
                200,
                json={"result": [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "hi"}}]},
            )
        seen["sent"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = TelegramClient("tok", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    updates = await client.get_updates(0, poll_seconds=1)
    assert updates[0]["message"]["text"] == "hi"
    assert seen["offset"] == "0"
    await client.send_message("42", "hello there")
    assert seen["sent"] == {"chat_id": "42", "text": "hello there"}


def test_reply_for_each_state() -> None:
    awaiting = TaskModel(status=TaskStatus.AWAITING_INPUT.value, pending_question="Approve? yes/no")
    assert reply_for(awaiting) == "Approve? yes/no"

    done = TaskModel(
        status=TaskStatus.COMPLETED.value,
        stop_reason=StopReason.GOAL_ACHIEVED.value,
        summary="wrote the report",
    )
    assert reply_for(done) == "Done. wrote the report"

    stopped = TaskModel(
        status=TaskStatus.COMPLETED.value, stop_reason=StopReason.MAX_STEPS.value, summary="partial"
    )
    assert "Stopped (max_steps)" in reply_for(stopped)

    failed = TaskModel(status=TaskStatus.FAILED.value, error="boom")
    assert reply_for(failed) == "Failed: boom"


async def test_find_awaiting_for_chat(session: AsyncSession) -> None:
    repo = TaskRepository(session)
    common = dict(  # noqa: C408
        rubric=[],
        max_steps=5,
        token_budget=1000,
        summary=None,
        verification_score=0,
        steps_used=0,
        tokens_used=0,
        workspace_path=None,
    )
    await repo.create(
        goal="open one",
        status=TaskStatus.AWAITING_INPUT.value,
        chat_id="42",
        pending_question="q?",
        **common,
    )
    await repo.create(
        goal="finished one", status=TaskStatus.COMPLETED.value, chat_id="42", **common
    )
    await session.commit()

    found = await _find_awaiting(session, "42")
    assert found is not None and found.status == TaskStatus.AWAITING_INPUT.value
    assert await _find_awaiting(session, "999") is None  # different chat
