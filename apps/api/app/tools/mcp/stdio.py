"""Bounded, namespaced stdio MCP providers for local agent runs."""

from __future__ import annotations

import asyncio
import json
import shlex
from contextlib import AsyncExitStack
from typing import Any

from app.core.logging import get_logger
from app.domain.capability import Capability

log = get_logger("mcp")


class McpStdioProvider:
    def __init__(
        self,
        name: str,
        command: str,
        capability: Capability,
        allowed_tools: frozenset[str],
        *,
        output_cap: int = 6_000,
        call_timeout: int = 60,
    ) -> None:
        self.name = name
        self.capability = capability
        self._argv = shlex.split(command)
        if not self._argv:
            raise ValueError(f"{name} MCP command is empty")
        self._allowed_tools = allowed_tools
        self._output_cap = output_cap
        self._call_timeout = call_timeout
        self._stack = AsyncExitStack()
        self._session: Any = None
        self._public_to_remote: dict[str, str] = {}
        self._specs: list[str] = []
        self.tool_names: set[str] = set()

    async def start(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._argv[0], args=self._argv[1:])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        for tool in listed.tools:
            if tool.name not in self._allowed_tools:
                continue
            public_name = f"{self.name}_{tool.name}"
            self._public_to_remote[public_name] = tool.name
            self._specs.append(self._format_spec(public_name, tool))
        self.tool_names = set(self._public_to_remote)
        if not self.tool_names:
            raise RuntimeError(f"{self.name} MCP exposed none of the allowed tools")
        log.info("mcp.provider_started", provider=self.name, tools=len(self.tool_names))

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if self._session is None:
            raise RuntimeError(f"{self.name} MCP is not available")
        remote_name = self._public_to_remote.get(name)
        if remote_name is None:
            raise ValueError(f"Unknown {self.name} MCP tool: {name}")
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(remote_name, args), timeout=self._call_timeout
            )
        except TimeoutError as exc:
            raise RuntimeError(f"{name} timed out after {self._call_timeout}s") from exc
        text = (
            "".join(getattr(content, "text", "") for content in getattr(result, "content", []))
            or "(no text output)"
        )
        if getattr(result, "isError", False):
            raise RuntimeError(f"MCP tool error: {text}")
        if len(text) > self._output_cap:
            return text[: self._output_cap] + " …[truncated]"
        return text

    async def stop(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:
            log.warning("mcp.provider_stop_failed", provider=self.name)

    def specs(self) -> str:
        return "\n".join(self._specs)

    @staticmethod
    def _format_spec(public_name: str, tool: Any) -> str:
        description = " ".join((tool.description or "").strip().split())
        schema = getattr(tool, "inputSchema", {}) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        arguments: dict[str, str] = {}
        if isinstance(properties, dict):
            for key, definition in list(properties.items())[:8]:
                value_type = "value"
                if isinstance(definition, dict):
                    declared = definition.get("type")
                    if isinstance(declared, str):
                        value_type = declared
                    elif isinstance(declared, list):
                        value_type = "|".join(str(item) for item in declared)
                arguments[str(key)] = value_type + ("*" if key in required else "")
        args = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        summary = description.split(". ", 1)[0] if description else "MCP tool"
        if len(summary) > 140:
            summary = summary[:137].rsplit(" ", 1)[0] + "…"
        punctuation = "" if summary.endswith((".", "!", "?", "…")) else "."
        return f"- {public_name}: {summary}{punctuation} args: {args}"


class McpPool:
    def __init__(self, providers: list[McpStdioProvider]) -> None:
        self.providers = providers
        self.tool_names: set[str] = set()
        self._by_tool: dict[str, McpStdioProvider] = {}

    async def start(self) -> None:
        started: list[McpStdioProvider] = []
        try:
            for provider in self.providers:
                started.append(provider)
                await provider.start()
                overlap = self.tool_names & provider.tool_names
                if overlap:
                    raise RuntimeError(f"Duplicate MCP tool names: {sorted(overlap)}")
                self.tool_names |= provider.tool_names
                self._by_tool.update(dict.fromkeys(provider.tool_names, provider))
        except asyncio.CancelledError:
            for provider in reversed(started):
                await provider.stop()
            raise
        except Exception:
            for provider in reversed(started):
                await provider.stop()
            raise

    async def call(self, name: str, args: dict[str, Any]) -> str:
        provider = self._by_tool.get(name)
        if provider is None:
            raise ValueError(f"Unknown MCP tool: {name}")
        return await provider.call(name, args)

    def capability_for(self, name: str) -> Capability | None:
        provider = self._by_tool.get(name)
        return provider.capability if provider is not None else None

    def specs(self) -> str:
        return "\n".join(provider.specs() for provider in self.providers if provider.specs())

    async def stop(self) -> None:
        for provider in reversed(self.providers):
            await provider.stop()
