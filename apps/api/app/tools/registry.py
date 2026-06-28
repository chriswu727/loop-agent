"""Dispatch a parsed tool call to the right tool, applying the safety policy.

The agent emits ``{"tool": ..., "args": {...}}``; this turns that into a
``ToolResult``. Every failure path (bad args, sandbox violation, blocked command)
becomes a normal observation so the loop keeps going and the agent can adapt.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolError, ToolResult, ToolStatus
from app.tools.policy import Verdict, evaluate_command
from app.tools.shell import run_command
from app.tools.workspace import Workspace

# Tools the agent may call. ``finish`` is handled by the loop itself, not here,
# but it's documented so the agent knows it exists.
TOOL_SPECS = """\
- write_file: create or overwrite a file. args: {"path": "relative/path", "content": "..."}
- read_file: read a file you created. args: {"path": "relative/path"}
- run_command: run a shell command in the workspace. args: {"command": "..."}
- finish: you are done. args: {"summary": "what you produced and where"}\
"""

VALID_TOOLS = {"write_file", "read_file", "run_command", "finish"}


class ToolExecutor:
    def __init__(
        self,
        workspace: Workspace,
        *,
        approval_mode: str = "auto",
        command_timeout: int = 60,
        output_limit: int = 4000,
    ) -> None:
        self.workspace = workspace
        self.approval_mode = approval_mode
        self.command_timeout = command_timeout
        self.output_limit = output_limit

    async def execute(self, tool: str, args: dict[str, Any]) -> ToolResult:
        try:
            if tool == "write_file":
                written = self.workspace.write(str(args["path"]), str(args.get("content", "")))
                return ToolResult(written)
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
        verdict, reason = evaluate_command(command)
        if verdict is Verdict.DENY:
            return ToolResult(f"Blocked by safety policy ({reason}). Try a safer approach.",
                              ToolStatus.BLOCKED)
        if verdict is Verdict.NEEDS_APPROVAL and self.approval_mode == "manual":
            return ToolResult(
                f"Command needs approval ({reason}) and approval mode is manual. "
                "Use an allowlisted command instead.",
                ToolStatus.BLOCKED,
            )
        return await run_command(
            command,
            self.workspace.root,
            timeout_seconds=self.command_timeout,
            output_limit=self.output_limit,
        )
