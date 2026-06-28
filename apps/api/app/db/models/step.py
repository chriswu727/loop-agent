"""ORM model for a single agent step, owned by a task."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StepModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "steps"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    thought: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    observation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ok")
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
