"""Data access for tasks."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import TaskModel
from app.repositories.base import BaseRepository


class TaskRepository(BaseRepository[TaskModel]):
    model = TaskModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_roots(self, *, limit: int, offset: int) -> list[TaskModel]:
        """Top-level tasks only — spawned sub-agents (parent_id set) are excluded
        so they don't pollute the task list; they show under their parent."""
        stmt = (
            select(TaskModel)
            .where(TaskModel.parent_id.is_(None))
            .order_by(TaskModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.scalars(stmt)).all())

    async def count_roots(self) -> int:
        stmt = select(func.count()).select_from(TaskModel).where(TaskModel.parent_id.is_(None))
        return (await self.session.scalar(stmt)) or 0

    async def list_children(self, parent_id: uuid.UUID) -> list[TaskModel]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.parent_id == parent_id)
            .order_by(TaskModel.created_at.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def recent_for_chat(
        self, chat_id: str, *, exclude_id: uuid.UUID, limit: int = 5
    ) -> list[TaskModel]:
        """Earlier turns of the same conversation (most recent first) — used to
        give a chat/session multi-turn context."""
        stmt = (
            select(TaskModel)
            .where(TaskModel.chat_id == chat_id)
            .where(TaskModel.id != exclude_id)
            .order_by(TaskModel.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def update_state(self, task_id: uuid.UUID, **values: object) -> TaskModel | None:
        """Patch live loop columns (status, best_score, tokens_used, …).

        Unlike the base ``update``, this writes values straight from the id so
        the worker can persist progress without holding the ORM instance, and it
        intentionally allows falsy values (e.g. resetting a counter to 0).
        """
        task = await self.get(task_id)
        if task is None:
            return None
        for key, value in values.items():
            setattr(task, key, value)
        await self.session.flush()
        await self.session.refresh(task)
        return task
