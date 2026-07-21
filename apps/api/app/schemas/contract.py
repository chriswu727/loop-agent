"""Typed, content-addressed acceptance contract for repository tasks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.capability import Capability


class ContractCheck(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["command", "file_exists", "file_contains"]
    command: str | None = Field(default=None, max_length=1_000)
    path: str | None = Field(default=None, max_length=500)
    text: str | None = Field(default=None, max_length=2_000)
    expect_exit: int = 0
    expect_stdout: str | None = Field(default=None, max_length=2_000)
    criterion_ids: list[str] = Field(default_factory=list, max_length=12)
    source: Literal["contract", "system"] = "contract"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip()
        parts = path.split("/")
        if "\\" in path or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("contract paths must be workspace-relative POSIX paths")
        return path

    @model_validator(mode="after")
    def validate_target(self) -> ContractCheck:
        if self.kind == "command" and not (self.command or "").strip():
            raise ValueError("command checks require command")
        if self.kind in {"file_exists", "file_contains"} and not self.path:
            raise ValueError("file checks require path")
        if self.kind == "file_contains" and not self.text:
            raise ValueError("file_contains checks require text")
        return self


class RepositoryDiscovery(BaseModel):
    manifests: list[str] = Field(default_factory=list, max_length=100)
    scripts: dict[str, str] = Field(default_factory=dict)
    test_files: list[str] = Field(default_factory=list, max_length=100)
    build_outputs: list[str] = Field(default_factory=list, max_length=50)
    quality_checks: list[ContractCheck] = Field(default_factory=list, max_length=16)
    files_scanned: int = Field(default=0, ge=0)
    truncated: bool = False


class ContractCritique(BaseModel):
    accepted: bool
    issues: list[str] = Field(default_factory=list, max_length=12)
    question: str | None = Field(default=None, max_length=1_000)
    provider: str = Field(default="unknown", max_length=80)
    model: str = Field(default="unknown", max_length=160)


class ContractModelIdentity(BaseModel):
    provider: str = Field(default="unknown", max_length=80)
    model: str = Field(default="unknown", max_length=160)


class ContractProposal(BaseModel):
    criteria: list[str] = Field(min_length=1, max_length=12)
    checks: list[ContractCheck] = Field(default_factory=list, max_length=72)
    artifacts: list[str] = Field(default_factory=list, max_length=48)
    risk: Literal["low", "medium", "high"]
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    confidence: int = Field(ge=0, le=100)
    authority_requests: list[Capability] = Field(default_factory=list, max_length=15)

    @field_validator("criteria", "assumptions")
    @classmethod
    def normalize_text_list(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts(cls, value: list[str]) -> list[str]:
        artifacts = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        for artifact in artifacts:
            parts = artifact.split("/")
            if "\\" in artifact or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("artifacts must be workspace-relative POSIX paths")
        return artifacts

    @model_validator(mode="after")
    def validate_criteria(self) -> ContractProposal:
        if not self.criteria:
            raise ValueError("at least one non-empty criterion is required")
        return self


class ContractDraft(ContractProposal):
    checks: list[ContractCheck] = Field(default_factory=list, max_length=96)
    artifacts: list[str] = Field(default_factory=list, max_length=64)
    schema_version: Literal["loop.contract-draft/v1"] = "loop.contract-draft/v1"
    compiler: ContractModelIdentity
    discovery: RepositoryDiscovery
    clarifications: list[str] = Field(default_factory=list, max_length=12)
    critique: ContractCritique
