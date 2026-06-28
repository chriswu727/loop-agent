"""Before-tool guards composed onto the executor.

Guards are ``BeforeHook``s: they inspect a tool call and may return a ToolResult
to block it. The egress guard enforces default-deny networking — a task can only
reach the network if its capability envelope grants egress.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolResult, ToolStatus
from app.tools.envelope import CapabilityEnvelope
from app.tools.policy import network_command_reason
from app.tools.registry import BeforeHook


def make_egress_guard(envelope: CapabilityEnvelope) -> BeforeHook:
    async def guard(tool: str, args: dict[str, Any]) -> ToolResult | None:
        if tool == "run_command" and not envelope.egress_allowed:
            reason = network_command_reason(str(args.get("command", "")))
            if reason is not None:
                return ToolResult(
                    f"Network access is not permitted by this task ({reason}). "
                    "Egress is default-deny; re-publish the task with network access "
                    "enabled if it genuinely needs to reach the internet.",
                    ToolStatus.BLOCKED,
                )
        return None

    return guard
