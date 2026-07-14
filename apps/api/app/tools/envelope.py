"""Resolved task authority enforced at the tool-dispatch choke point."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.capability import (
    CONTROL_TOOLS,
    EXECUTOR_TOOL_CAPABILITIES,
    TOOL_CAPABILITIES,
    Capability,
    legacy_capabilities,
    parse_capabilities,
    sorted_capabilities,
)

EXECUTOR_TOOLS = frozenset(EXECUTOR_TOOL_CAPABILITIES)


@dataclass(slots=True, frozen=True)
class CapabilityEnvelope:
    capabilities: frozenset[Capability]
    egress_hosts: frozenset[str] = frozenset()
    legacy_allowed_tools: frozenset[str] | None = None

    @classmethod
    def full(cls) -> CapabilityEnvelope:
        return cls(capabilities=frozenset(Capability))

    @classmethod
    def from_capabilities(
        cls,
        capabilities: list[str | Capability] | frozenset[Capability],
        *,
        egress_hosts: list[str] | None = None,
    ) -> CapabilityEnvelope:
        return cls(
            capabilities=parse_capabilities(capabilities),
            egress_hosts=frozenset(
                host.strip().lower().rstrip(".") for host in (egress_hosts or []) if host.strip()
            ),
        )

    @classmethod
    def from_tools(
        cls,
        tools: list[str] | None,
        *,
        egress_allowed: bool = False,
        egress_hosts: list[str] | None = None,
    ) -> CapabilityEnvelope:
        envelope = cls.from_capabilities(
            legacy_capabilities(
                tools,
                allow_egress=egress_allowed,
                use_browser=False,
                use_email=False,
                use_calendar=False,
            ),
            egress_hosts=egress_hosts,
        )
        return cls(
            capabilities=envelope.capabilities,
            egress_hosts=envelope.egress_hosts,
            legacy_allowed_tools=(
                None
                if tools is None
                else frozenset(tool for tool in tools if tool in EXECUTOR_TOOLS)
            ),
        )

    @property
    def allowed_tools(self) -> frozenset[str]:
        return frozenset(tool for tool in EXECUTOR_TOOLS if self.permits(tool))

    @property
    def egress_allowed(self) -> bool:
        return Capability.NET_SHELL in self.capabilities

    def permits(
        self,
        tool: str,
        *,
        provider_capability: Capability | None = None,
    ) -> bool:
        if tool in CONTROL_TOOLS:
            return True
        if (
            tool in EXECUTOR_TOOLS
            and self.legacy_allowed_tools is not None
            and tool not in self.legacy_allowed_tools
        ):
            return False
        required = TOOL_CAPABILITIES.get(tool)
        if required is None and provider_capability is not None:
            required = frozenset({provider_capability})
        if required is None:
            return False
        return required <= self.capabilities

    def permits_capability(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def egress_host_allowed(self, host: str) -> bool:
        if not self.egress_allowed:
            return False
        if not self.egress_hosts:
            return True
        normalized = host.strip().lower().rstrip(".")
        return any(
            normalized == allowed or normalized.endswith("." + allowed)
            for allowed in self.egress_hosts
        )

    def restricted_executor_tools(self) -> list[str] | None:
        allowed = sorted(self.allowed_tools)
        return None if set(allowed) == EXECUTOR_TOOLS else allowed

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "loop.capabilities/v1",
            "capabilities": sorted_capabilities(self.capabilities),
            "egress_hosts": sorted(self.egress_hosts),
        }
