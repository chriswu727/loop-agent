"""Tool contract shared by every tool the agent can call.

A tool takes already-parsed arguments and returns an *observation* — the text
the agent sees next turn — plus a status. Tools never raise into the loop; a
failure is a normal observation the agent can react to, exactly like a human
seeing a command fail and trying again.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ToolStatus(enum.StrEnum):
    OK = "ok"
    ERROR = "error"  # the tool ran but failed (e.g. command exited non-zero)
    BLOCKED = "blocked"  # refused by the safety policy / sandbox


@dataclass(slots=True)
class ToolResult:
    observation: str
    status: ToolStatus = ToolStatus.OK


class ToolError(Exception):
    """Raised inside a tool for a sandbox/argument violation; the registry turns
    it into a BLOCKED/ERROR observation rather than crashing the loop."""

    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked
