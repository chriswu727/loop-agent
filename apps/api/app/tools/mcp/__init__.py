"""MCP client integration — lets the agent use tools from external MCP servers
(today: a headless browser via @playwright/mcp), bound by the same envelope."""

from app.tools.mcp.browser import McpBrowser

__all__ = ["McpBrowser"]
