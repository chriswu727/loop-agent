"""A durable product-delivery lineage across successive verified tasks."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProductSessionModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_sessions"

    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
