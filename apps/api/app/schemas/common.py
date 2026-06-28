"""Shared API schemas: pagination envelope and the RFC 9457 problem document."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """A consistent pagination envelope for list endpoints."""

    items: list[T]
    total: int = Field(description="Total number of matching records")
    limit: int
    offset: int


class Problem(BaseModel):
    """RFC 9457 (problem+json) error body. The single error shape clients see."""

    type: str = Field(default="about:blank", description="URI identifying the problem type")
    title: str
    status: int
    detail: str | None = None
    code: str = Field(description="Stable machine-readable error code")
    request_id: str | None = None
