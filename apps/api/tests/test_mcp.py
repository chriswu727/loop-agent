"""MCP tool dispatch through the executor (with a fake provider — no subprocess).
The live browser path is exercised by a real run; here we test the wiring."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from app.tools import ToolExecutor, ToolStatus, Workspace
from app.tools.mcp import McpBrowser


class _FakeMcp:
    tool_names: ClassVar[set[str]] = {"browser_navigate"}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        return f"navigated to {args.get('url')}"


async def test_executor_dispatches_mcp_tool(tmp_path: Path) -> None:
    mcp = _FakeMcp()
    ex = ToolExecutor(Workspace(tmp_path / "w"), mcp=mcp)
    res = await ex.execute("browser_navigate", {"url": "https://x.com"})
    assert res.status is ToolStatus.OK
    assert "navigated to https://x.com" in res.observation
    assert mcp.calls == [("browser_navigate", {"url": "https://x.com"})]


async def test_executor_mcp_tool_error_becomes_observation(tmp_path: Path) -> None:
    class _Boom:
        tool_names: ClassVar[set[str]] = {"browser_click"}

        async def call(self, name: str, args: dict) -> str:
            raise RuntimeError("element not found")

    ex = ToolExecutor(Workspace(tmp_path / "w"), mcp=_Boom())
    res = await ex.execute("browser_click", {"ref": "x"})
    assert res.status is ToolStatus.ERROR
    assert "element not found" in res.observation


async def test_unknown_tool_without_mcp_is_error(tmp_path: Path) -> None:
    ex = ToolExecutor(Workspace(tmp_path / "w"))
    res = await ex.execute("browser_navigate", {"url": "x"})
    assert res.status is ToolStatus.ERROR
    assert "Unknown tool" in res.observation


def test_browser_specs_formatting() -> None:
    b = McpBrowser("noop")
    b._tools = [("browser_navigate", "Navigate to a URL"), ("browser_click", "Click an element")]
    specs = b.specs()
    assert "- browser_navigate: Navigate to a URL" in specs
    assert "- browser_click: Click an element" in specs
