"""Data access for loop iterations."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.iteration import IterationModel
from app.repositories.base import BaseRepository


class IterationRepository(BaseRepository[IterationModel]):
    model = IterationModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_task(self, task_id: uuid.UUID) -> list[IterationModel]:
        """All passes for a task, oldest first — the order the UI replays them."""
        stmt = (
            select(IterationModel)
            .where(IterationModel.task_id == task_id)
            .order_by(IterationModel.number.asc())
        )
        result = await self.session.scalars(stmt)
        return list(result.all())
