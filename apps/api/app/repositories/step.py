"""Data access for agent steps."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.step import StepModel
from app.repositories.base import BaseRepository


class StepRepository(BaseRepository[StepModel]):
    model = StepModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_task(self, task_id: uuid.UUID) -> list[StepModel]:
        """All steps for a task, oldest first — the order the UI replays them."""
        stmt = (
            select(StepModel).where(StepModel.task_id == task_id).order_by(StepModel.number.asc())
        )
        result = await self.session.scalars(stmt)
        return list(result.all())
