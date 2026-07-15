"""Pydantic DTOs for tasks — the API contract.

Input is small (a goal plus optional limit overrides); the read model exposes
the full live run state so the UI can render progress without a second call.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.domain.authority_token import AuthorityTokenError, normalize_hosts
from app.domain.capability import CAPABILITY_SCHEMA_VERSION, Capability
from app.schemas.file import FileEntry
from app.schemas.step import LedgerStatus, StepRead


class LimitsIn(BaseModel):
    """Optional per-task overrides. Omitted fields fall back to configured
    defaults; provided values are clamped to the hard caps in the service."""

    max_steps: int | None = Field(default=None, ge=1)
    token_budget: int | None = Field(default=None, ge=1_000)


class TaskCreate(BaseModel):
    goal: str = Field(min_length=4, max_length=4_000)
    success_criteria: list[str] | None = None
    verification_commands: list[str] = Field(default_factory=list)
    verification_mode: str | None = Field(default=None, pattern=r"^(strict|judgment)$")
    project_id: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    # Optional path relative to LOOP_LOCAL_PROJECTS_ROOT. The source repository
    # must be clean; execution happens in a detached, source-unlinked clone.
    project_path: str | None = Field(default=None, min_length=1, max_length=500)
    limits: LimitsIn = Field(default_factory=LimitsIn)
    # When false, the task is created as a draft (PENDING, not started) so files
    # can be uploaded into its workspace before the agent runs. Start with /start.
    autostart: bool = True
    # Legacy tool input retained for API compatibility. New callers should send
    # the typed capabilities list below.
    allowed_tools: list[str] | None = None
    capabilities: list[Capability] | None = None
    # Network egress is default-deny; set true only if the task needs the network.
    allow_egress: bool = False
    # Required destination allowlist for shell/browser networking. Provider-specific
    # endpoints (SMTP, CalDAV, vision) remain separately capability-bound.
    egress_hosts: list[str] | None = None
    # When true, non-allowlisted commands pause for the user to approve before running.
    require_approval: bool = False
    # Legacy provider toggles map to independent typed capabilities.
    use_browser: bool = False
    # Email does not grant shell network authority.
    use_email: bool = False
    # Calendar does not grant shell network authority.
    use_calendar: bool = False
    use_vision: bool = False
    # Groups tasks into one conversation/session (prior turns become context).
    chat_id: str | None = None
    # Run under this signed skill (by name). Refused if it doesn't verify.
    skill: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("success_criteria")
    @classmethod
    def validate_success_criteria(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        criteria = [item.strip() for item in value if item.strip()]
        if not 1 <= len(criteria) <= 12:
            raise ValueError("success_criteria must contain between 1 and 12 items")
        if any(len(item) > 500 for item in criteria):
            raise ValueError("each success criterion must be at most 500 characters")
        return criteria

    @field_validator("verification_commands")
    @classmethod
    def validate_verification_commands(cls, value: list[str]) -> list[str]:
        commands = [item.strip() for item in value if item.strip()]
        if len(commands) > 8:
            raise ValueError("verification_commands may contain at most 8 commands")
        if any(len(item) > 1_000 for item in commands):
            raise ValueError("each verification command must be at most 1000 characters")
        return commands

    @model_validator(mode="after")
    def validate_network_authority(self) -> TaskCreate:
        requested = set(self.capabilities or [])
        shell_network = self.allow_egress or Capability.NET_SHELL in requested
        browser_network = self.use_browser or Capability.NET_BROWSER in requested
        if (
            settings.agent_require_egress_hosts
            and (shell_network or browser_network)
            and not self.egress_hosts
        ):
            raise ValueError("Shell/browser network authority requires egress_hosts")
        if self.egress_hosts:
            try:
                self.egress_hosts = sorted(normalize_hosts(self.egress_hosts))
            except AuthorityTokenError as exc:
                raise ValueError(str(exc)) from exc
        if self.project_path and self.verification_mode != "judgment" and not self.success_criteria:
            raise ValueError("strict local-project tasks require user-confirmed success_criteria")
        return self


class RespondIn(BaseModel):
    """The user's answer to an ask_user question, which resumes the run."""

    answer: str = Field(min_length=1, max_length=4_000)


class LimitsRead(BaseModel):
    max_steps: int
    token_budget: int


class ProjectChangeSetRead(BaseModel):
    project_path: str
    base_commit: str
    base_branch: str | None
    state: str
    applied_patch_sha256: str | None


class ChangeSetFileRead(BaseModel):
    path: str
    status: str
    additions: int | None
    deletions: int | None
    previous_path: str | None = None


class ChangeSetRead(ProjectChangeSetRead):
    patch_sha256: str
    files: list[ChangeSetFileRead]
    diff: str
    diff_truncated: bool
    can_apply: bool
    can_discard: bool
    can_undo: bool
    blocked_reason: str | None


class AuthorityEnforcementRead(BaseModel):
    provider_gateway: bool
    browser_gateway: bool
    email_gateway: bool
    calendar_gateway: bool
    vision_gateway: bool
    egress_proxy: bool


class AuthorityRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = Field(
        default=CAPABILITY_SCHEMA_VERSION, alias="schema", serialization_alias="schema"
    )
    requested: list[str] | None
    resolved: list[str]
    egress_hosts: list[str]
    sandbox: str | None
    audit: list[dict[str, object]]
    enforcement: AuthorityEnforcementRead


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal: str
    owner_id: str
    project_id: str
    status: str
    rubric: list[str]
    criteria_source: str
    verification_mode: str
    required_checks: list[dict[str, object]]
    baseline_checks: list[dict[str, object]]
    pending_question: str | None
    allowed_tools: list[str] | None
    authority: AuthorityRead
    allow_egress: bool
    egress_hosts: list[str] | None
    require_approval: bool
    use_browser: bool
    use_email: bool
    use_calendar: bool
    use_vision: bool
    skill: str | None
    parent_id: uuid.UUID | None
    depth: int
    idempotency_key: str | None
    attempt: int
    limits: LimitsRead
    summary: str | None
    verification_score: int
    verified_by: str | None
    receipt_hash: str | None
    sandbox: str | None
    steps_used: int
    tokens_used: int
    change_set: ProjectChangeSetRead | None
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
            owner_id=m.owner_id,  # type: ignore[attr-defined]
            project_id=m.project_id,  # type: ignore[attr-defined]
            status=m.status,  # type: ignore[attr-defined]
            rubric=m.rubric or [],  # type: ignore[attr-defined]
            criteria_source=m.criteria_source,  # type: ignore[attr-defined]
            verification_mode=m.verification_mode,  # type: ignore[attr-defined]
            required_checks=m.required_checks or [],  # type: ignore[attr-defined]
            baseline_checks=m.baseline_checks or [],  # type: ignore[attr-defined]
            pending_question=m.pending_question,  # type: ignore[attr-defined]
            allowed_tools=m.allowed_tools,  # type: ignore[attr-defined]
            authority=AuthorityRead(
                schema=m.authority_schema,  # type: ignore[attr-defined]
                requested=m.requested_capabilities,  # type: ignore[attr-defined]
                resolved=m.resolved_capabilities or [],  # type: ignore[attr-defined]
                egress_hosts=m.egress_hosts or [],  # type: ignore[attr-defined]
                sandbox=m.sandbox,  # type: ignore[attr-defined]
                audit=m.authority_audit or [],  # type: ignore[attr-defined]
                enforcement=AuthorityEnforcementRead(
                    provider_gateway=bool(
                        settings.agent_provider_gateway_url
                        or settings.agent_email_gateway_url
                        or settings.agent_calendar_gateway_url
                        or settings.agent_vision_gateway_url
                    ),
                    browser_gateway=bool(settings.agent_browser_gateway_url),
                    email_gateway=bool(settings.agent_email_gateway_url),
                    calendar_gateway=bool(settings.agent_calendar_gateway_url),
                    vision_gateway=bool(settings.agent_vision_gateway_url),
                    egress_proxy=bool(settings.agent_egress_proxy_url),
                ),
            ),
            allow_egress=m.allow_egress,  # type: ignore[attr-defined]
            egress_hosts=m.egress_hosts,  # type: ignore[attr-defined]
            require_approval=m.require_approval,  # type: ignore[attr-defined]
            use_browser=m.use_browser,  # type: ignore[attr-defined]
            use_email=m.use_email,  # type: ignore[attr-defined]
            use_calendar=m.use_calendar,  # type: ignore[attr-defined]
            use_vision=m.use_vision,  # type: ignore[attr-defined]
            skill=m.skill,  # type: ignore[attr-defined]
            parent_id=m.parent_id,  # type: ignore[attr-defined]
            depth=m.depth,  # type: ignore[attr-defined]
            idempotency_key=m.idempotency_key,  # type: ignore[attr-defined]
            attempt=m.attempt,  # type: ignore[attr-defined]
            limits=LimitsRead(
                max_steps=m.max_steps,  # type: ignore[attr-defined]
                token_budget=m.token_budget,  # type: ignore[attr-defined]
            ),
            summary=m.summary,  # type: ignore[attr-defined]
            verification_score=m.verification_score,  # type: ignore[attr-defined]
            verified_by=m.verified_by,  # type: ignore[attr-defined]
            receipt_hash=m.receipt_hash,  # type: ignore[attr-defined]
            sandbox=m.sandbox,  # type: ignore[attr-defined]
            steps_used=m.steps_used,  # type: ignore[attr-defined]
            tokens_used=m.tokens_used,  # type: ignore[attr-defined]
            change_set=(
                ProjectChangeSetRead(
                    project_path=m.project_relative_path,  # type: ignore[attr-defined]
                    base_commit=m.project_base_commit,  # type: ignore[attr-defined]
                    base_branch=m.project_base_branch,  # type: ignore[attr-defined]
                    state=m.change_state,  # type: ignore[attr-defined]
                    applied_patch_sha256=m.applied_patch_sha256,  # type: ignore[attr-defined]
                )
                if m.project_source_path  # type: ignore[attr-defined]
                else None
            ),
            stop_reason=m.stop_reason,  # type: ignore[attr-defined]
            error=m.error,  # type: ignore[attr-defined]
            created_at=m.created_at,  # type: ignore[attr-defined]
            updated_at=m.updated_at,  # type: ignore[attr-defined]
        )


class TaskSnapshot(BaseModel):
    """One streamed update: everything the live task view renders."""

    task: TaskRead
    steps: list[StepRead]
    files: list[FileEntry]
    ledger: LedgerStatus


class LimitDefaults(BaseModel):
    """Exposed to the UI so the publish form can render sane controls."""

    max_steps_default: int = settings.agent_max_steps_default
    max_steps_cap: int = settings.agent_max_steps_cap
    token_budget_default: int = settings.loop_token_budget_default
    token_budget_cap: int = settings.loop_token_budget_cap
    local_projects_enabled: bool = bool(settings.loop_local_projects_root)
