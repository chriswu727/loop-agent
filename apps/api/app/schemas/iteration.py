"""Pydantic DTO for a loop iteration."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IterationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    number: int
    artifact: str
    score: int
    critique: str
    tokens: int
    created_at: datetime
