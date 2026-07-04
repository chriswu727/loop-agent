"""Before-tool guards composed onto the executor.

Guards are ``BeforeHook``s: they inspect a tool call and may return a ToolResult
to block it. The egress guard enforces default-deny networking — a task can only
reach the network if its capability envelope grants egress.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.tools.base import ToolResult, ToolStatus
from app.tools.envelope import CapabilityEnvelope
from app.tools.policy import (
    code_network_reason,
    destination_hosts,
    network_command_reason,
    script_paths_in,
)
from app.tools.registry import BeforeHook

if TYPE_CHECKING:
    from app.tools.workspace import Workspace


def make_egress_guard(
    envelope: CapabilityEnvelope, workspace: Workspace | None = None
) -> BeforeHook:
    async def guard(tool: str, args: dict[str, Any]) -> ToolResult | None:
        if tool != "run_command":
            return None
        command = str(args.get("command", ""))

        # The command may look innocent (`python fetch.py`) while the script it runs
        # reaches the network, so scan referenced scripts' contents too. Gather the
        # command + those contents once, for both the reaches-network check and the
        # per-host allowlist check.
        reason = network_command_reason(command)
        texts = [command]
        if workspace is not None:
            # Honor a leading `cd <dir>` so we scan the file that actually runs
            # (`cd sub && python fetch.py` runs sub/fetch.py, not ./fetch.py).
            cd = re.match(r"\s*cd\s+([^\s;&|]+)\s*(?:&&|;)", command)
            prefix = f"{cd.group(1).strip('/')}/" if cd else ""
            for path in script_paths_in(command):
                candidate = path if path.startswith("/") else prefix + path
                try:
                    content = workspace.read(candidate, limit=200_000)
                except Exception:
                    continue
                texts.append(content)
                if reason is None and (
                    network_command_reason(content) or code_network_reason(content)
                ):
                    reason = f"script {path} reaches the network"

        if not envelope.egress_allowed:
            if reason is not None:
                return ToolResult(
                    f"Network access is not permitted by this task ({reason}). "
                    "Egress is default-deny; re-publish the task with network access "
                    "enabled if it genuinely needs to reach the internet.",
                    ToolStatus.BLOCKED,
                )
            return None

        # Egress is granted; if it's restricted to an allowlist, block any named
        # destination host that isn't on it (best-effort — container mode is
        # all-or-nothing; a host we can't see statically still passes).
        if envelope.egress_hosts and reason is not None:
            targets = destination_hosts("\n".join(texts))
            disallowed = sorted(h for h in targets if not envelope.egress_host_allowed(h))
            if disallowed:
                return ToolResult(
                    f"Egress to {', '.join(disallowed)} is not on this task's allowlist "
                    f"({', '.join(sorted(envelope.egress_hosts))}). Only the declared hosts "
                    "are reachable.",
                    ToolStatus.BLOCKED,
                )
        return None

    return guard
