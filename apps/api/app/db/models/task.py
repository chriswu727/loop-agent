"""ORM model for a published task and its agent run state.

``rubric`` is stored as JSON (success criteria) so it stays portable across
Postgres and SQLite. Everything else is scalar — no vendor-specific column
types — so the model runs on a laptop and in the cluster alike.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.task import TaskStatus


class TaskModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, default="local", index=True)
    project_id: Mapped[str] = mapped_column(
        String(100), nullable=False, default="default", index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.PENDING.value, index=True
    )
    rubric: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    authority_schema: Mapped[str] = mapped_column(
        String(40), nullable=False, default="loop.capabilities/v1"
    )
    requested_capabilities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    resolved_capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Capability envelope: which executor tools this task may use. NULL = all.
    allowed_tools: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Network egress is default-deny; True lets the task reach the network.
    allow_egress: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # If egress is allowed, an optional allowlist of destination hosts. Empty/None =
    # any host; a non-empty list restricts egress to just those hosts (best-effort
    # at the policy layer; container mode is all-or-nothing).
    egress_hosts: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # When True, non-allowlisted commands pause for the user to approve.
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Legacy provider toggles retained for wire compatibility.
    use_browser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Provider capabilities remain independent from shell network authority.
    use_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Calendar is resolved into read/write capabilities at execution time.
    use_calendar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    use_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional signed skill this task runs under (by name).
    skill: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Sub-agent delegation: a spawned task points at its parent and tracks depth.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True, index=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Chat origin: the channel/chat this task came from, so replies route back.
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # The action awaiting approval while paused: {"tool": ..., "args": {...}}.
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Limits — the hard guardrails for this task.
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)

    # Live run state, updated after each step.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # execution|judgment
    receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_schema: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # How shell commands were isolated: "container", "kubernetes", or "inline".
    sandbox: Mapped[str | None] = mapped_column(String(20), nullable=True)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workspace_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
