"""DTOs for the channel-agnostic chat endpoint."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    chat_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4_000)


class ChatOut(BaseModel):
    reply: str
    task_id: uuid.UUID | None
    status: str
