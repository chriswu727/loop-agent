"""ORM model for a published task and its agent run state.

``rubric`` is stored as JSON (success criteria) so it stays portable across
Postgres and SQLite. Everything else is scalar — no vendor-specific column
types — so the model runs on a laptop and in the cluster alike.
"""

from __future__ import annotations

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.task import TaskStatus


class TaskModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.PENDING.value, index=True
    )
    rubric: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Limits — the hard guardrails for this task.
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)

    # Live run state, updated after each step.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workspace_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
