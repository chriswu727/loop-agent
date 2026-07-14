"""Read the agent's cross-task memory."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.core.config import settings
from app.services.memory import MemoryStore, scoped_memory_root

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryRead(BaseModel):
    content: str


@router.get("", response_model=MemoryRead, summary="What the agent remembers across tasks")
async def get_memory(
    request: Request,
    project_id: str = Query(default="default", min_length=1, max_length=100),
) -> MemoryRead:
    subject = str(getattr(request.state, "subject", "local"))
    store = MemoryStore(scoped_memory_root(Path(settings.agent_memory_root), subject, project_id))
    return MemoryRead(content=store.snapshot(limit=20_000))
