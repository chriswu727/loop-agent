"""Pydantic DTOs for tasks — the API contract.

Input is small (a goal plus optional limit overrides); the read model exposes
the full live run state so the UI can render progress without a second call.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class LimitsIn(BaseModel):
    """Optional per-task overrides. Omitted fields fall back to configured
    defaults; provided values are clamped to the hard caps in the service."""

    max_steps: int | None = Field(default=None, ge=1)
    token_budget: int | None = Field(default=None, ge=1_000)


class TaskCreate(BaseModel):
    goal: str = Field(min_length=4, max_length=4_000)
    limits: LimitsIn = Field(default_factory=LimitsIn)


class LimitsRead(BaseModel):
    max_steps: int
    token_budget: int


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal: str
    status: str
    rubric: list[str]
    limits: LimitsRead
    summary: str | None
    verification_score: int
    steps_used: int
    tokens_used: int
    workspace_path: str | None
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
                max_steps=m.max_steps,  # type: ignore[attr-defined]
                token_budget=m.token_budget,  # type: ignore[attr-defined]
            ),
            summary=m.summary,  # type: ignore[attr-defined]
            verification_score=m.verification_score,  # type: ignore[attr-defined]
            steps_used=m.steps_used,  # type: ignore[attr-defined]
            tokens_used=m.tokens_used,  # type: ignore[attr-defined]
            workspace_path=m.workspace_path,  # type: ignore[attr-defined]
            stop_reason=m.stop_reason,  # type: ignore[attr-defined]
            error=m.error,  # type: ignore[attr-defined]
            created_at=m.created_at,  # type: ignore[attr-defined]
            updated_at=m.updated_at,  # type: ignore[attr-defined]
        )


class LimitDefaults(BaseModel):
    """Exposed to the UI so the publish form can render sane controls."""

    max_steps_default: int = settings.agent_max_steps_default
    max_steps_cap: int = settings.agent_max_steps_cap
    token_budget_default: int = settings.loop_token_budget_default
    token_budget_cap: int = settings.loop_token_budget_cap
