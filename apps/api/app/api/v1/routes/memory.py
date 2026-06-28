"""Read the agent's cross-task memory."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.services.memory import MemoryStore

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryRead(BaseModel):
    content: str


@router.get("", response_model=MemoryRead, summary="What the agent remembers across tasks")
async def get_memory() -> MemoryRead:
    store = MemoryStore(Path(settings.agent_memory_root))
    return MemoryRead(content=store.snapshot(limit=20_000))
