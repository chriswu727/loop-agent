"""DTOs for triggers (saved task templates)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.task import LimitsIn


class TriggerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=4, max_length=4_000)
    limits: LimitsIn = Field(default_factory=LimitsIn)
    allowed_tools: list[str] | None = None
    allow_egress: bool = False
    require_approval: bool = False
    skill: str | None = None


class TriggerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    goal: str
    enabled: bool
    fire_count: int
    max_steps: int
    token_budget: int
    allowed_tools: list[str] | None
    allow_egress: bool
    require_approval: bool
    skill: str | None
    created_at: datetime
    updated_at: datetime
