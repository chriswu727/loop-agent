"""A headless-browser tool provider backed by an MCP server (@playwright/mcp).

Loop spawns the MCP server as a subprocess, discovers its tools, and exposes them
to the agent as ordinary tools — so "go to this page and extract X" becomes real
browser actions. The session lives for one agent run and is torn down after.

Browsing is network egress, so it is only started for tasks that opt in
(``use_browser``), which also implies egress is allowed.
"""

from __future__ import annotations

import asyncio
import shlex
from contextlib import AsyncExitStack
from typing import Any

from app.core.logging import get_logger

log = get_logger("mcp")

_OUTPUT_CAP = 5000  # chars of a tool result fed back to the agent (snapshots are big)
_CALL_TIMEOUT = 60  # seconds before a single browser action is abandoned


class McpBrowser:
    def __init__(self, command: str) -> None:
        self._argv = shlex.split(command)
        self._stack = AsyncExitStack()
        self._session: Any = None
        self.tool_names: set[str] = set()
        self._tools: list[tuple[str, str]] = []

    async def start(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._argv[0], args=self._argv[1:])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self._tools = [
            (t.name, (t.description or "").strip().splitlines()[0] if t.description else "")
            for t in listed.tools
        ]
        self.tool_names = {name for name, _ in self._tools}
        log.info("mcp.browser_started", tools=len(self.tool_names))

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if self._session is None:
            return "Browser session is not available."
        # Bound the call so a hung navigation can't hang the task (and leak its DB
        # session) forever — every other tool primitive is time-bounded too.
        try:
            res = await asyncio.wait_for(self._session.call_tool(name, args), timeout=_CALL_TIMEOUT)
        except TimeoutError:
            return f"{name} timed out after {_CALL_TIMEOUT}s."
        text = "".join(getattr(c, "text", "") for c in getattr(res, "content", []))
        if not text:
            return "(no text output)"
        return text if len(text) <= _OUTPUT_CAP else text[:_OUTPUT_CAP] + " …[truncated]"

    async def stop(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:  # teardown of a crashed subprocess must never fail the task
            log.warning("mcp.browser_stop_failed")

    def specs(self) -> str:
        """A compact tool list for the planner prompt."""
        return "\n".join(f"- {name}: {desc[:90]}" for name, desc in self._tools)
