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


async def test_run_chat_turn_routes_new_vs_resume(engine: object) -> None:
    """The shared chat seam: a fresh message publishes a task tagged with the
    conversation; a message while a task awaits input resumes that same task."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.repositories.step import StepRepository
    from app.repositories.task import TaskRepository
    from app.schemas.task import TaskCreate
    from app.services import chat as chat_svc
    from app.services.task import TaskService

    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def fake_execute(task_id: object) -> None:
        async with sm() as s:
            t = await TaskRepository(s).get(task_id)  # type: ignore[arg-type]
            t.status = "completed"
            t.stop_reason = "goal_achieved"
            t.summary = "done"
            await s.commit()

    # New conversation -> a task is published with the chat_id and then run.
    fresh = await chat_svc.run_chat_turn(
        "conv-1", "write a hello script", sessionmaker=sm, execute=fake_execute
    )
    assert fresh is not None and fresh.chat_id == "conv-1" and fresh.status == "completed"

    # A task awaiting input in another conversation...
    async with sm() as s:
        svc = TaskService(TaskRepository(s), StepRepository(s))
        awaiting = await svc.publish(TaskCreate(goal="do the thing here", chat_id="conv-2"))
        awaiting.status = "awaiting_input"
        awaiting.pending_question = "what colour?"
        await s.commit()
        awaiting_id = awaiting.id

    # ...a follow-up resumes THAT task (respond), it is not a new publish.
    resumed = await chat_svc.run_chat_turn("conv-2", "blue", sessionmaker=sm, execute=fake_execute)
    assert resumed is not None and resumed.id == awaiting_id
    assert resumed.pending_question is None  # the question was answered


async def test_chat_is_rate_limited(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each /chat call runs a whole agent loop, so it's rate-limited like publish."""

    async def fake_run(chat_id: str, message: str) -> None:
        return None  # cheap: skip the real DB + LLM

    monkeypatch.setattr("app.api.v1.routes.chat.run_chat_turn", fake_run)

    statuses = []
    for _ in range(22):
        resp = await client.post("/api/v1/chat", json={"chat_id": "x", "message": "hello there"})
        statuses.append(resp.status_code)

    assert statuses[:20] == [200] * 20  # first 20 allowed in the window
    assert 429 in statuses[20:]  # then rate-limited
