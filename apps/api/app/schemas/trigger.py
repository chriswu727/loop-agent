"""DTOs for triggers (saved task templates)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import settings
from app.domain.authority_token import AuthorityTokenError, normalize_hosts
from app.domain.capability import Capability
from app.schemas.task import LimitsIn


class TriggerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=4, max_length=4_000)
    project_id: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    limits: LimitsIn = Field(default_factory=LimitsIn)
    allowed_tools: list[str] | None = None
    capabilities: list[Capability] | None = None
    allow_egress: bool = False
    egress_hosts: list[str] | None = None
    require_approval: bool = False
    skill: str | None = None
    # Fire automatically every N minutes; omit for manual/webhook-only.
    interval_minutes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_network_authority(self) -> TriggerCreate:
        requested = set(self.capabilities or [])
        if (
            settings.agent_require_egress_hosts
            and (
                self.allow_egress
                or Capability.NET_SHELL in requested
                or Capability.NET_BROWSER in requested
            )
            and not self.egress_hosts
        ):
            raise ValueError("Shell/browser network authority requires egress_hosts")
        if self.egress_hosts:
            try:
                self.egress_hosts = sorted(normalize_hosts(self.egress_hosts))
            except AuthorityTokenError as exc:
                raise ValueError(str(exc)) from exc
        return self


class TriggerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    goal: str
    owner_id: str
    project_id: str
    enabled: bool
    fire_count: int
    secret: str
    max_steps: int
    token_budget: int
    allowed_tools: list[str] | None
    capabilities: list[str] | None
    allow_egress: bool
    egress_hosts: list[str] | None
    require_approval: bool
    skill: str | None
    interval_minutes: int | None
    last_fired_at: datetime | None
    created_at: datetime
    updated_at: datetime
