"""MCP tool dispatch through the executor (with a fake provider — no subprocess).
The live browser path is exercised by a real run; here we test the wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.domain.capability import Capability
from app.tools import CapabilityEnvelope, ToolExecutor, ToolStatus, Workspace
from app.tools.mcp import McpBrowser, McpPool, McpStdioProvider


class _FakeMcp:
    tool_names: ClassVar[set[str]] = {"browser_navigate"}
    capability = Capability.NET_BROWSER

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        return f"navigated to {args.get('url')}"


async def test_pool_cancellation_stops_partially_started_provider() -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingProvider:
        tool_names: ClassVar[set[str]] = set()

        async def start(self) -> None:
            started.set()
            await asyncio.Event().wait()

        async def stop(self) -> None:
            stopped.set()

    pool = McpPool([BlockingProvider()])  # type: ignore[list-item]
    running = asyncio.create_task(pool.start())
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert stopped.is_set()


async def test_executor_dispatches_mcp_tool(tmp_path: Path) -> None:
    mcp = _FakeMcp()
    ex = ToolExecutor(
        Workspace(tmp_path / "w"),
        mcp=mcp,
        envelope=CapabilityEnvelope.from_capabilities(["net.browser"]),
    )
    res = await ex.execute("browser_navigate", {"url": "https://x.com"})
    assert res.status is ToolStatus.OK
    assert "navigated to https://x.com" in res.observation
    assert mcp.calls == [("browser_navigate", {"url": "https://x.com"})]


async def test_executor_mcp_tool_error_becomes_observation(tmp_path: Path) -> None:
    class _Boom:
        tool_names: ClassVar[set[str]] = {"browser_click"}
        capability = Capability.NET_BROWSER

        async def call(self, name: str, args: dict) -> str:
            raise RuntimeError("element not found")

    ex = ToolExecutor(
        Workspace(tmp_path / "w"),
        mcp=_Boom(),
        envelope=CapabilityEnvelope.from_capabilities(["net.browser"]),
    )
    res = await ex.execute("browser_click", {"ref": "x"})
    assert res.status is ToolStatus.ERROR
    assert "element not found" in res.observation


async def test_unknown_tool_without_provider_is_default_denied(tmp_path: Path) -> None:
    ex = ToolExecutor(
        Workspace(tmp_path / "w"),
        envelope=CapabilityEnvelope.from_capabilities(["net.browser"]),
    )
    res = await ex.execute("browser_navigate", {"url": "x"})
    assert res.status is ToolStatus.BLOCKED
    assert "capability envelope" in res.observation


async def test_browser_tool_is_default_denied(tmp_path: Path) -> None:
    ex = ToolExecutor(Workspace(tmp_path / "w"), mcp=_FakeMcp())

    res = await ex.execute("browser_navigate", {"url": "https://x.com"})

    assert res.status is ToolStatus.BLOCKED
    assert "capability envelope" in res.observation


def test_browser_specs_formatting() -> None:
    b = McpBrowser("noop")
    b._tools = [("browser_navigate", "Navigate to a URL"), ("browser_click", "Click an element")]
    specs = b.specs()
    assert "- browser_navigate: Navigate to a URL" in specs
    assert "- browser_click: Click an element" in specs


def test_namespaced_mcp_spec_keeps_required_argument_shape() -> None:
    tool = SimpleNamespace(
        description="Search the web quickly.\nMore details.",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    )

    spec = McpStdioProvider._format_spec("sibyl_quick_search", tool)

    assert spec == (
        '- sibyl_quick_search: Search the web quickly. args: {"query":"string*","limit":"integer"}'
    )


async def test_auxiliary_mcp_uses_per_tool_capability(tmp_path: Path) -> None:
    class _ResearchProvider:
        tool_names: ClassVar[set[str]] = {"sibyl_quick_search"}
        capability = Capability.RESEARCH_READ

        async def call(self, _name: str, args: dict) -> str:
            return f"sources for {args['query']}"

    pool = McpPool([])
    provider = _ResearchProvider()
    pool.tool_names = set(provider.tool_names)
    pool._by_tool["sibyl_quick_search"] = provider  # type: ignore[assignment]
    executor = ToolExecutor(
        Workspace(tmp_path / "w"),
        auxiliary_mcp=pool,
        envelope=CapabilityEnvelope.from_capabilities(["research.read"]),
    )

    result = await executor.execute("sibyl_quick_search", {"query": "Loop Agent"})

    assert result.status is ToolStatus.OK
    assert result.observation == "sources for Loop Agent"
