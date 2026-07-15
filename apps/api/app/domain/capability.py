"""Versioned, typed authority granted to an agent task."""

from __future__ import annotations

import enum
from collections.abc import Iterable

CAPABILITY_SCHEMA_VERSION = "loop.capabilities/v1"


class Capability(enum.StrEnum):
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    EXEC = "exec"
    NET_SHELL = "net.shell"
    NET_BROWSER = "net.browser"
    EMAIL_READ = "email.read"
    EMAIL_SEND = "email.send"
    CALENDAR_READ = "calendar.read"
    CALENDAR_WRITE = "calendar.write"
    VISION = "vision"
    RESEARCH_READ = "research.read"
    QA_BROWSER = "qa.browser"
    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"
    TASK_SPAWN = "task.spawn"


CONTROL_TOOLS = frozenset({"finish", "ask_user"})

TOOL_CAPABILITIES: dict[str, frozenset[Capability]] = {
    "read_file": frozenset({Capability.FS_READ}),
    "write_file": frozenset({Capability.FS_WRITE}),
    "edit_file": frozenset({Capability.FS_READ, Capability.FS_WRITE}),
    "run_command": frozenset({Capability.EXEC}),
    "remember": frozenset({Capability.MEMORY_WRITE}),
    "spawn": frozenset({Capability.TASK_SPAWN}),
    "read_inbox": frozenset({Capability.EMAIL_READ}),
    "send_email": frozenset({Capability.EMAIL_SEND}),
    "list_events": frozenset({Capability.CALENDAR_READ}),
    "create_event": frozenset({Capability.CALENDAR_WRITE}),
    "see_image": frozenset({Capability.VISION, Capability.FS_READ}),
}

EXECUTOR_TOOL_CAPABILITIES = {
    "read_file": Capability.FS_READ,
    "write_file": Capability.FS_WRITE,
    "edit_file": Capability.FS_WRITE,
    "run_command": Capability.EXEC,
}

LEGACY_DEFAULT_CAPABILITIES = frozenset(
    {
        Capability.FS_READ,
        Capability.FS_WRITE,
        Capability.EXEC,
        Capability.MEMORY_READ,
        Capability.MEMORY_WRITE,
        Capability.TASK_SPAWN,
    }
)


def parse_capabilities(values: Iterable[str | Capability]) -> frozenset[Capability]:
    parsed: set[Capability] = set()
    for value in values:
        parsed.add(value if isinstance(value, Capability) else Capability(value))
    return frozenset(parsed)


def legacy_capabilities(
    allowed_tools: list[str] | None,
    *,
    allow_egress: bool,
    use_browser: bool,
    use_email: bool,
    use_calendar: bool,
    use_vision: bool = False,
) -> frozenset[Capability]:
    if allowed_tools is None:
        capabilities = set(LEGACY_DEFAULT_CAPABILITIES)
    else:
        capabilities = {
            capability
            for tool in allowed_tools
            for capability in TOOL_CAPABILITIES.get(tool, frozenset())
        }
        capabilities.update({Capability.MEMORY_READ, Capability.MEMORY_WRITE})
    if allow_egress:
        capabilities.add(Capability.NET_SHELL)
    if use_browser:
        capabilities.add(Capability.NET_BROWSER)
    if use_email:
        capabilities.update({Capability.EMAIL_READ, Capability.EMAIL_SEND})
    if use_calendar:
        capabilities.update({Capability.CALENDAR_READ, Capability.CALENDAR_WRITE})
    if use_vision:
        capabilities.add(Capability.VISION)
        capabilities.add(Capability.FS_READ)
    return frozenset(capabilities)


def sorted_capabilities(capabilities: Iterable[Capability]) -> list[str]:
    return sorted(capability.value for capability in capabilities)
