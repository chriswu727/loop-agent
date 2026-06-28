"""Pure domain entities for the agent loop. No framework, no ORM, no I/O.

A ``Task`` is what a user publishes: a goal plus the limits that bound how hard
the agent is allowed to work on it. A ``TaskStatus`` tracks its lifecycle.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime


class TaskStatus(enum.StrEnum):
    PENDING = "pending"  # published, not yet picked up
    RUNNING = "running"  # the loop is iterating
    COMPLETED = "completed"  # stopped on a success/stop condition
    CANCELLED = "cancelled"  # the user pulled the plug
    FAILED = "failed"  # the loop errored out


class StopReason(enum.StrEnum):
    TARGET_REACHED = "target_reached"
    MAX_ITERATIONS = "max_iterations"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PLATEAU = "plateau"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(slots=True)
class Limits:
    """The hard boundary the loop must respect for a single task."""

    max_iterations: int
    token_budget: int
    target_score: int


@dataclass(slots=True)
class Task:
    id: uuid.UUID
    goal: str
    status: TaskStatus
    limits: Limits
    rubric: list[str] = field(default_factory=list)
    best_score: int = 0
    best_artifact: str | None = None
    iterations_used: int = 0
    tokens_used: int = 0
    stop_reason: StopReason | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
