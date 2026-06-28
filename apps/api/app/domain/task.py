"""Pure domain entities for the autonomous agent. No framework, no ORM, no I/O.

A ``Task`` is what a user publishes: a goal plus the limits that bound how hard
the agent is allowed to work. The agent then drives itself — planning, calling
tools, observing results — until the goal is achieved or a limit stops it.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime


class TaskStatus(enum.StrEnum):
    PENDING = "pending"  # published or resumable, not currently being worked
    RUNNING = "running"  # the agent is working
    AWAITING_INPUT = "awaiting_input"  # paused on an ask_user question
    COMPLETED = "completed"  # stopped on a stop condition (see StopReason)
    CANCELLED = "cancelled"  # the user pulled the plug
    FAILED = "failed"  # the agent errored out


class StopReason(enum.StrEnum):
    GOAL_ACHIEVED = "goal_achieved"  # agent finished and the verifier accepted it
    MAX_STEPS = "max_steps"  # used its allotted steps
    BUDGET_EXHAUSTED = "budget_exhausted"  # spent its token budget
    STUCK = "stuck"  # repeated failures / no progress
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(slots=True)
class Limits:
    """The hard boundary the agent must respect for a single task."""

    max_steps: int
    token_budget: int


@dataclass(slots=True)
class Task:
    id: uuid.UUID
    goal: str
    status: TaskStatus
    limits: Limits
    rubric: list[str] = field(default_factory=list)
    pending_question: str | None = None  # set while paused on ask_user
    summary: str | None = None  # the agent's final account of what it did
    verification_score: int = 0  # the verifier's grade of the finished work (0-100)
    verified_by: str | None = None  # "execution" (checks re-ran) | "judgment"
    receipt_hash: str | None = None  # content address of the task's Receipt
    steps_used: int = 0
    tokens_used: int = 0
    workspace_path: str | None = None
    stop_reason: StopReason | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
