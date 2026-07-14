"""A saved task template that can be fired to publish a task.

Firing a trigger (by an external event hitting its webhook) publishes and starts
a task from the stored template — so the agent can act when something happens,
not only when a person sits down to publish a task.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TriggerModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "triggers"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, default="local", index=True)
    project_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default="default", index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fire_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Webhook secret: firing over HTTP must present this token. Generated on create.
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    # Heartbeat: fire automatically every N minutes (NULL = manual/webhook only).
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The task configuration each fire produces — mirrors a published task.
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_tools: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    capabilities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    allow_egress: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    egress_hosts: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skill: Mapped[str | None] = mapped_column(String(100), nullable=True)
