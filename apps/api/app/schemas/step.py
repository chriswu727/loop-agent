"""Pydantic DTO for an agent step."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    number: int
    thought: str
    tool: str
    tool_args: dict[str, Any]
    observation: str
    status: str
    tokens: int
    prev_hash: str | None
    hash: str
    created_at: datetime


class LedgerStatus(BaseModel):
    """Result of re-verifying a task's tamper-evident step chain."""

    verified: bool
    head: str
    length: int
    broken_at: int | None  # the first step number whose hash didn't match
