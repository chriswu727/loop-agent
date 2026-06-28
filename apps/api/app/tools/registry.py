"""Dispatch a parsed tool call to the right tool, applying the safety policy.

The agent emits ``{"tool": ..., "args": {...}}``; this turns that into a
``ToolResult``. Every failure path (bad args, sandbox violation, blocked command)
becomes a normal observation so the loop keeps going and the agent can adapt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.tools.base import ToolError, ToolResult, ToolStatus
from app.tools.envelope import CapabilityEnvelope
from app.tools.policy import Verdict, evaluate_command
from app.tools.shell import run_command
from app.tools.workspace import Workspace

# Hook points around every tool call — the seam for approval gates, egress
# enforcement, and skill instrumentation. A before-hook returning a ToolResult
# short-circuits the call (e.g. "denied by approval"); returning None proceeds.
BeforeHook = Callable[[str, dict[str, Any]], Awaitable[ToolResult | None]]
AfterHook = Callable[[str, dict[str, Any], ToolResult], Awaitable[None]]

# Tools the agent may call. ``finish`` is handled by the loop itself, not here,
# but it's documented so the agent knows it exists.
TOOL_SPECS = """\
- write_file: create or overwrite a file. args: {"path": "relative/path", "content": "..."}
- edit_file: replace an exact, unique snippet. args: {"path": "...", "old": "...", "new": "..."}
- read_file: read a file you created. args: {"path": "relative/path"}
- run_command: run a shell command in the workspace. args: {"command": "..."}
- ask_user: pause to ask the user when you need input. args: {"question": "..."}
- remember: save a durable note for future tasks. args: {"note": "...", "topic": "optional"}
- finish: you are done. args: {"summary": "...", "checks": [ ... ]}. Provide "checks" \
the verifier re-runs to PROVE the work: \
{"kind":"command","command":"...","expect_exit":0,"expect_stdout":"..."}, \
{"kind":"file_exists","path":"..."}, or {"kind":"file_contains","path":"...","text":"..."}. \
Always include checks when the goal involves files or runnable code.\
"""

# ``finish``, ``ask_user`` and ``remember`` are handled by the loop, not the executor.
VALID_TOOLS = {
    "write_file", "edit_file", "read_file", "run_command", "ask_user", "remember", "finish",
}


class ToolExecutor:
    def __init__(
        self,
        workspace: Workspace,
        *,
        approval_mode: str = "auto",
        command_timeout: int = 60,
        output_limit: int = 4000,
        envelope: CapabilityEnvelope | None = None,
        before_tool: BeforeHook | None = None,
        after_tool: AfterHook | None = None,
    ) -> None:
        self.workspace = workspace
        self.approval_mode = approval_mode
        self.command_timeout = command_timeout
        self.output_limit = output_limit
        # The single point where the task's declared authority is enforced.
        self.envelope = envelope or CapabilityEnvelope.full()
        self.before_tool = before_tool
        self.after_tool = after_tool

    async def execute(self, tool: str, args: dict[str, Any]) -> ToolResult:
        # Capability gate: a tool the envelope doesn't grant never runs.
        if not self.envelope.permits(tool):
            return ToolResult(
                f"Tool '{tool}' is not permitted by this task's capability envelope.",
                ToolStatus.BLOCKED,
            )
        # Before-hook may veto/short-circuit (e.g. an approval gate). None = proceed.
        if self.before_tool is not None:
            veto = await self.before_tool(tool, args)
            if veto is not None:
                return veto
        result = await self._dispatch(tool, args)
        if self.after_tool is not None:
            await self.after_tool(tool, args, result)
        return result

    async def _dispatch(self, tool: str, args: dict[str, Any]) -> ToolResult:
        try:
            if tool == "write_file":
                written = self.workspace.write(str(args["path"]), str(args.get("content", "")))
                return ToolResult(written)
            if tool == "edit_file":
                edited = self.workspace.edit(
                    str(args["path"]), str(args["old"]), str(args["new"])
                )
                return ToolResult(edited)
            if tool == "read_file":
                return ToolResult(self.workspace.read(str(args["path"])))
            if tool == "run_command":
                return await self._run(str(args["command"]))
            return ToolResult(
                f"Unknown tool {tool!r}. Valid tools: {sorted(VALID_TOOLS)}", ToolStatus.ERROR
            )
        except KeyError as exc:
            return ToolResult(
                f"Missing required argument {exc} for tool {tool!r}", ToolStatus.ERROR
            )
        except ToolError as exc:
            status = ToolStatus.BLOCKED if exc.blocked else ToolStatus.ERROR
            return ToolResult(str(exc), status)

    async def _run(self, command: str) -> ToolResult:
        # Dangerous commands are always hard-blocked here. NEEDS_APPROVAL gating
        # is handled by the loop (which can pause for a human), not the executor.
        verdict, reason = evaluate_command(command)
        if verdict is Verdict.DENY:
            return ToolResult(f"Blocked by safety policy ({reason}). Try a safer approach.",
                              ToolStatus.BLOCKED)
        return await run_command(
            command,
            self.workspace.root,
            timeout_seconds=self.command_timeout,
            output_limit=self.output_limit,
        )
