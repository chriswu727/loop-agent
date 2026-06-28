"""Pydantic DTOs for tasks — the API contract.

Input is intentionally small (a goal plus optional limit overrides); the read
model exposes the full live loop state so the UI can render progress without a
second call.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class LimitsIn(BaseModel):
    """Optional per-task overrides. Omitted fields fall back to configured
    defaults; provided values are clamped to the hard caps in the service."""

    max_iterations: int | None = Field(default=None, ge=1)
    token_budget: int | None = Field(default=None, ge=1_000)
    target_score: int | None = Field(default=None, ge=1, le=100)


class TaskCreate(BaseModel):
    goal: str = Field(min_length=4, max_length=4_000)
    limits: LimitsIn = Field(default_factory=LimitsIn)


class LimitsRead(BaseModel):
    max_iterations: int
    token_budget: int
    target_score: int


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal: str
    status: str
    rubric: list[str]
    limits: LimitsRead
    best_score: int
    best_artifact: str | None
    iterations_used: int
    tokens_used: int
    stop_reason: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: object) -> TaskRead:
        """Build from the ORM model, folding the flat limit columns into a
        nested object so the wire shape reads cleanly."""
        m = model
        return cls(
            id=m.id,  # type: ignore[attr-defined]
            goal=m.goal,  # type: ignore[attr-defined]
            status=m.status,  # type: ignore[attr-defined]
            rubric=m.rubric or [],  # type: ignore[attr-defined]
            limits=LimitsRead(
                max_iterations=m.max_iterations,  # type: ignore[attr-defined]
                token_budget=m.token_budget,  # type: ignore[attr-defined]
                target_score=m.target_score,  # type: ignore[attr-defined]
            ),
            best_score=m.best_score,  # type: ignore[attr-defined]
            best_artifact=m.best_artifact,  # type: ignore[attr-defined]
            iterations_used=m.iterations_used,  # type: ignore[attr-defined]
            tokens_used=m.tokens_used,  # type: ignore[attr-defined]
            stop_reason=m.stop_reason,  # type: ignore[attr-defined]
            error=m.error,  # type: ignore[attr-defined]
            created_at=m.created_at,  # type: ignore[attr-defined]
            updated_at=m.updated_at,  # type: ignore[attr-defined]
        )


class LimitDefaults(BaseModel):
    """Exposed to the UI so the publish form can render sane controls."""

    max_iterations_default: int = settings.loop_max_iterations_default
    max_iterations_cap: int = settings.loop_max_iterations_cap
    token_budget_default: int = settings.loop_token_budget_default
    token_budget_cap: int = settings.loop_token_budget_cap
    target_score_default: int = settings.loop_target_score_default
