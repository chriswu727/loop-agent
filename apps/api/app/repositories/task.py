"""Data access for tasks."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.task import TaskModel
from app.repositories.base import BaseRepository


class TaskRepository(BaseRepository[TaskModel]):
    model = TaskModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

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
