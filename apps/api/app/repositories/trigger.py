"""Data access for triggers."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trigger import TriggerModel
from app.repositories.base import BaseRepository


class TriggerRepository(BaseRepository[TriggerModel]):
    model = TriggerModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_owner(self, owner_id: str) -> list[TriggerModel]:
        stmt = (
            select(TriggerModel)
            .where(TriggerModel.owner_id == owner_id)
            .order_by(TriggerModel.created_at.desc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def claim_fire(
        self,
        trigger_id: uuid.UUID,
        *,
        expected_last_fired_at: datetime | None,
        now: datetime,
    ) -> TriggerModel | None:
        timestamp_match = (
            TriggerModel.last_fired_at.is_(None)
            if expected_last_fired_at is None
            else TriggerModel.last_fired_at == expected_last_fired_at
        )
        claimed = await self.session.scalar(
            update(TriggerModel)
            .where(
                TriggerModel.id == trigger_id,
                TriggerModel.enabled.is_(True),
                timestamp_match,
            )
            .values(
                last_fired_at=now,
                fire_count=TriggerModel.fire_count + 1,
            )
            .returning(TriggerModel.id)
        )
        if claimed is None:
            return None
        await self.session.flush()
        return await self.get(trigger_id)
