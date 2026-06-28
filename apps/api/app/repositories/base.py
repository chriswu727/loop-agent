"""Generic async repository.

Encapsulates the boilerplate CRUD against a SQLAlchemy model so concrete
repositories only add query methods that are actually specific to their entity.
Services depend on repositories, never on the session or the ORM directly.
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """CRUD operations shared by every repository."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id_: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset).order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        return int(await self.session.scalar(stmt) or 0)

    async def create(self, **values: Any) -> ModelT:
        instance = self.model(**values)
        self.session.add(instance)
        await self.session.flush()  # populate PK/defaults without ending the tx
        # Load server-generated columns (e.g. timestamps) inside the async
        # context, so later sync attribute access (Pydantic) never triggers IO.
        await self.session.refresh(instance)
        return instance

    async def update(self, instance: ModelT, **values: Any) -> ModelT:
        for key, value in values.items():
            if value is not None:
                setattr(instance, key, value)
        await self.session.flush()
        # Refresh to pull server-side onupdate values (e.g. updated_at) before
        # the object is serialized outside the async context.
        await self.session.refresh(instance)
        return instance

    async def delete(self, id_: uuid.UUID) -> int:
        result = await self.session.execute(sa_delete(self.model).where(self.model.id == id_))  # type: ignore[attr-defined]
        return int(result.rowcount or 0)
