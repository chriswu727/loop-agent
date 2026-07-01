"""Shared chat-turn handling — the integration seam for any channel.

A message either answers a task that's awaiting input (resuming it) or starts a
new task in that conversation (chat_id), then runs to completion or the next
pause. The Telegram poller and the HTTP /chat endpoint both call ``run_chat_turn``,
so a new channel (Discord, Slack, a webhook) is just another caller.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import TaskModel
from app.db.session import get_sessionmaker
from app.domain.task import StopReason, TaskStatus
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate
from app.services.runner import execute_task
from app.services.task import TaskService


def reply_for(task: TaskModel) -> str:
    """Turn a task's terminal/paused state into a chat reply."""
    if task.status == TaskStatus.AWAITING_INPUT.value:
        return task.pending_question or "I need a bit more information to continue."
    if task.status == TaskStatus.COMPLETED.value:
        if task.stop_reason == StopReason.GOAL_ACHIEVED.value:
            return f"Done. {task.summary or ''}".strip()
        return f"Stopped ({task.stop_reason}). {task.summary or ''}".strip()
    if task.status == TaskStatus.FAILED.value:
        return f"Failed: {task.error or 'unknown error'}"
    return f"Status: {task.status}"


async def find_awaiting(session: AsyncSession, chat_id: str) -> TaskModel | None:
    result = await session.execute(
        select(TaskModel)
        .where(TaskModel.chat_id == chat_id)
        .where(TaskModel.status == TaskStatus.AWAITING_INPUT.value)
        .order_by(TaskModel.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def run_chat_turn(chat_id: str, message: str) -> TaskModel | None:
    """Answer an open question or start a new task for this conversation, run it
    to completion/pause, and return the resulting task (with reply_for state)."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        awaiting = await find_awaiting(session, chat_id)
        service = TaskService(TaskRepository(session), StepRepository(session))
        if awaiting is not None:
            await service.respond(awaiting.id, message)  # resume the paused task
            task_id = awaiting.id
        else:
            task = await service.publish(TaskCreate(goal=message.strip(), chat_id=chat_id))
            task_id = task.id

    await execute_task(task_id)  # runs to completion or the next pause (own session)

    async with sessionmaker() as session:
        return await TaskRepository(session).get(task_id)
