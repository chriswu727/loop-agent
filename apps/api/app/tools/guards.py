"""Before-tool guards composed onto the executor.

Guards are ``BeforeHook``s: they inspect a tool call and may return a ToolResult
to block it. The egress guard enforces default-deny networking — a task can only
reach the network if its capability envelope grants egress.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.tools.base import ToolResult, ToolStatus
from app.tools.envelope import CapabilityEnvelope
from app.tools.policy import code_network_reason, network_command_reason, script_paths_in
from app.tools.registry import BeforeHook

if TYPE_CHECKING:
    from app.tools.workspace import Workspace


def make_egress_guard(
    envelope: CapabilityEnvelope, workspace: Workspace | None = None
) -> BeforeHook:
    async def guard(tool: str, args: dict[str, Any]) -> ToolResult | None:
        if tool == "run_command" and not envelope.egress_allowed:
            command = str(args.get("command", ""))
            reason = network_command_reason(command)
            # The command may look innocent (`python fetch.py`) while the script it
            # runs reaches the network. Scan referenced scripts' contents too, so a
            # file can't be used to slip past default-deny egress on the inline path.
            if reason is None and workspace is not None:
                for path in script_paths_in(command):
                    try:
                        content = workspace.read(path, limit=200_000)
                    except Exception:
                        continue
                    if code_network_reason(content) is not None:
                        reason = f"script {path} reaches the network"
                        break
            if reason is not None:
                return ToolResult(
                    f"Network access is not permitted by this task ({reason}). "
                    "Egress is default-deny; re-publish the task with network access "
                    "enabled if it genuinely needs to reach the internet.",
                    ToolStatus.BLOCKED,
                )
        return None

    return guard
