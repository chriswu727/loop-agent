"""ORM model for a published task and its agent run state.

``rubric`` is stored as JSON (success criteria) so it stays portable across
Postgres and SQLite. Everything else is scalar — no vendor-specific column
types — so the model runs on a laptop and in the cluster alike.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text, Uuid
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
    # Capability envelope: which executor tools this task may use. NULL = all.
    allowed_tools: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Network egress is default-deny; True lets the task reach the network.
    allow_egress: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When True, non-allowlisted commands pause for the user to approve.
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When True, a headless browser (MCP) is available to the agent; implies egress.
    use_browser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When True, email tools (send/read) are available; implies egress.
    use_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When True, calendar tools (list/create) are available; implies egress.
    use_calendar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional signed skill this task runs under (by name).
    skill: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Sub-agent delegation: a spawned task points at its parent and tracks depth.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True, index=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Chat origin: the channel/chat this task came from, so replies route back.
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # The action awaiting approval while paused: {"tool": ..., "args": {...}}.
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Limits — the hard guardrails for this task.
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)

    # Live run state, updated after each step.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # execution|judgment
    receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # How the run's shell commands were isolated: "container" or "inline".
    sandbox: Mapped[str | None] = mapped_column(String(20), nullable=True)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workspace_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
