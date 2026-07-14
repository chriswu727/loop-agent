"""Data access for tasks."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.task import TaskModel
from app.repositories.base import BaseRepository


class TaskRepository(BaseRepository[TaskModel]):
    model = TaskModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_roots(
        self, *, limit: int, offset: int, owner_id: str | None = None
    ) -> list[TaskModel]:
        """Top-level tasks only — spawned sub-agents (parent_id set) are excluded
        so they don't pollute the task list; they show under their parent."""
        conditions: list[ColumnElement[bool]] = [TaskModel.parent_id.is_(None)]
        if owner_id is not None:
            conditions.append(TaskModel.owner_id == owner_id)
        stmt = (
            select(TaskModel)
            .where(*conditions)
            .order_by(TaskModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.scalars(stmt)).all())

    async def get_by_idempotency_key(self, key: str, *, owner_id: str) -> TaskModel | None:
        result: TaskModel | None = await self.session.scalar(
            select(TaskModel).where(
                TaskModel.idempotency_key == key, TaskModel.owner_id == owner_id
            )
        )
        return result

    async def claim_pending(self, task_id: uuid.UUID) -> TaskModel | None:
        claimed = await self.session.scalar(
            update(TaskModel)
            .where(TaskModel.id == task_id, TaskModel.status == "pending")
            .values(status="running")
            .returning(TaskModel.id)
        )
        if claimed is None:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get(task_id)

    async def count_roots(self, *, owner_id: str | None = None) -> int:
        conditions: list[ColumnElement[bool]] = [TaskModel.parent_id.is_(None)]
        if owner_id is not None:
            conditions.append(TaskModel.owner_id == owner_id)
        stmt = select(func.count()).select_from(TaskModel).where(*conditions)
        return (await self.session.scalar(stmt)) or 0

    async def list_for_owner(self, owner_id: str, *, limit: int, offset: int) -> list[TaskModel]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.owner_id == owner_id)
            .order_by(TaskModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.scalars(stmt)).all())

    async def count_for_owner(self, owner_id: str) -> int:
        stmt = select(func.count()).select_from(TaskModel).where(TaskModel.owner_id == owner_id)
        return (await self.session.scalar(stmt)) or 0

    async def list_children(self, parent_id: uuid.UUID) -> list[TaskModel]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.parent_id == parent_id)
            .order_by(TaskModel.created_at.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def recent_for_chat(
        self,
        chat_id: str,
        *,
        exclude_id: uuid.UUID,
        owner_id: str,
        limit: int = 5,
    ) -> list[TaskModel]:
        """Earlier turns of the same conversation (most recent first) — used to
        give a chat/session multi-turn context."""
        stmt = (
            select(TaskModel)
            .where(TaskModel.chat_id == chat_id)
            .where(TaskModel.owner_id == owner_id)
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
