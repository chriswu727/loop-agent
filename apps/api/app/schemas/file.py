"""DTOs for browsing a task's workspace output."""

from __future__ import annotations

from pydantic import BaseModel


class FileEntry(BaseModel):
    path: str
    size: int


class FileContent(BaseModel):
    path: str
    content: str
    size: int
    truncated: bool
