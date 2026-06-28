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
    created_at: datetime
