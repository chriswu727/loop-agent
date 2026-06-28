"""List the installed skills and whether each one verifies."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.services.skills import SkillStore

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillRead(BaseModel):
    name: str
    description: str
    verified: bool
    reason: str
    allowed_tools: list[str] | None
    allow_egress: bool


@router.get("", response_model=list[SkillRead], summary="List installed skills")
async def list_skills() -> list[SkillRead]:
    store = SkillStore(Path(settings.agent_skills_root), settings.agent_skill_trust_public_key)
    return [
        SkillRead(
            name=s.manifest.name,
            description=s.manifest.description,
            verified=s.verified,
            reason=s.reason,
            allowed_tools=s.manifest.allowed_tools,
            allow_egress=s.manifest.allow_egress,
        )
        for s in store.list_skills()
    ]
