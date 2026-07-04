"""The capability envelope: a task's declared, enforced authority.

An envelope says exactly what a task (and, later, a signed skill) is allowed to
do. It is enforced at the single tool choke point (``ToolExecutor.execute``), so
the agent's prose can ask for anything but only what the envelope grants will
run. Today it scopes the tool set; the same object is where workspace-subpath
and network-egress scoping will live (enforced in a later phase). The default
envelope grants the full tool set, so existing behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

# The tools the executor itself dispatches (finish/ask_user are loop control flow,
# not executor tools, so they are never gated here).
EXECUTOR_TOOLS = frozenset({"write_file", "edit_file", "read_file", "run_command"})


@dataclass(slots=True, frozen=True)
class CapabilityEnvelope:
    allowed_tools: frozenset[str]
    egress_allowed: bool = False
    # If egress is allowed, an optional allowlist of destination hosts. Empty = any
    # host; non-empty restricts egress to just these (best-effort at the policy layer).
    egress_hosts: frozenset[str] = frozenset()

    @classmethod
    def full(cls) -> CapabilityEnvelope:
        """The default: every executor tool is permitted."""
        return cls(allowed_tools=EXECUTOR_TOOLS)

    @classmethod
    def from_tools(
        cls,
        tools: list[str] | None,
        *,
        egress_allowed: bool = False,
        egress_hosts: list[str] | None = None,
    ) -> CapabilityEnvelope:
        """Build from a user/skill-supplied tool list. ``None`` means full tool
        access; an empty or invalid list is narrowed to whatever valid tools were
        named. Network egress is default-deny unless explicitly granted."""
        allowed = (
            EXECUTOR_TOOLS if tools is None else frozenset(t for t in tools if t in EXECUTOR_TOOLS)
        )
        return cls(
            allowed_tools=allowed,
            egress_allowed=egress_allowed,
            egress_hosts=frozenset(h.strip().lower() for h in (egress_hosts or []) if h.strip()),
        )

    def permits(self, tool: str) -> bool:
        # Only executor tools are gated; control-flow tools are always allowed.
        return tool not in EXECUTOR_TOOLS or tool in self.allowed_tools

    def egress_host_allowed(self, host: str) -> bool:
        """Whether egress to ``host`` is permitted. An empty allowlist means any
        host (once egress itself is granted); otherwise the host — or a subdomain
        of a listed host — must be present (``api.github.com`` matches ``github.com``)."""
        if not self.egress_hosts:
            return True
        h = host.strip().lower().rstrip(".")
        return any(h == a or h.endswith("." + a) for a in self.egress_hosts)

    def restricted_executor_tools(self) -> list[str] | None:
        """Sorted allowed executor tools if this envelope is narrower than full,
        else None — used to tell the planner what it may use."""
        if self.allowed_tools == EXECUTOR_TOOLS:
            return None
        return sorted(self.allowed_tools)
