"""Data access for durable product-delivery lineages."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.product_session import ProductSessionModel
from app.repositories.base import BaseRepository


class ProductSessionRepository(BaseRepository[ProductSessionModel]):
    model = ProductSessionModel

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_for_update(self, session_id: uuid.UUID) -> ProductSessionModel | None:
        result: ProductSessionModel | None = await self.session.scalar(
            select(ProductSessionModel)
            .where(ProductSessionModel.id == session_id)
            .with_for_update()
        )
        return result
