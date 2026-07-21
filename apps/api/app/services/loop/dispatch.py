"""Route a parsed decision without executing side effects."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class DispatchKind(enum.StrEnum):
    INVALID = "invalid"
    BLOCKED = "blocked"
    APPROVAL = "approval"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class DispatchRoute:
    kind: DispatchKind
    observation: str | None = None
    approval_reason: str | None = None


class ActionDispatchPolicy:
    def route(
        self,
        tool: str | None,
        *,
        valid_tools: set[str] | frozenset[str],
        guard_block: str | None,
        repeated_write_count: int,
        approval_reason: str | None,
        last_write_path: str | None,
    ) -> DispatchRoute:
        if tool is None:
            return DispatchRoute(
                DispatchKind.INVALID,
                "Could not parse a valid action. Respond with one JSON object using a valid "
                f"tool: {sorted(valid_tools)}.",
            )
        if guard_block:
            return DispatchRoute(DispatchKind.BLOCKED, guard_block)
        if tool in {"write_file", "edit_file"} and repeated_write_count >= 3:
            return DispatchRoute(
                DispatchKind.BLOCKED,
                f"Blocked: you have written '{last_write_path}' {repeated_write_count} times "
                "without running it. Writing it again is not allowed — run it with "
                "run_command, call finish with checks, or take a different action.",
            )
        if approval_reason is not None:
            return DispatchRoute(DispatchKind.APPROVAL, approval_reason=approval_reason)
        return DispatchRoute(DispatchKind.EXECUTE)
