"""Pure domain enums for the autonomous agent — the task lifecycle and the reason
a run stopped. No framework, no ORM, no I/O.

The task's data lives on the ORM model (``db/models/task.py``) and its DTOs
(``schemas/task.py``); these enums are the shared vocabulary both speak in.
"""

from __future__ import annotations

import enum


class TaskStatus(enum.StrEnum):
    PENDING = "pending"  # published or resumable, not currently being worked
    RUNNING = "running"  # the agent is working
    AWAITING_INPUT = "awaiting_input"  # paused on an ask_user question
    COMPLETED = "completed"  # verified goal achievement
    STOPPED = "stopped"  # bounded stop without a verified result
    CANCELLED = "cancelled"  # the user pulled the plug
    FAILED = "failed"  # the agent errored out


class StopReason(enum.StrEnum):
    GOAL_ACHIEVED = "goal_achieved"  # agent finished and the verifier accepted it
    MAX_STEPS = "max_steps"  # used its allotted steps
    BUDGET_EXHAUSTED = "budget_exhausted"  # spent its token budget
    STUCK = "stuck"  # repeated failures / no progress
    CANCELLED = "cancelled"
    ERROR = "error"
