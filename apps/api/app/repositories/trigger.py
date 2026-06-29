"""Data access for triggers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trigger import TriggerModel
from app.repositories.base import BaseRepository


class TriggerRepository(BaseRepository[TriggerModel]):
    model = TriggerModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
