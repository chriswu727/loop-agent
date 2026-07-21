from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.domain.authority_token import AuthorityGrant, AuthorityTokenError
from app.domain.capability import TOOL_CAPABILITIES, Capability
from app.provider_gateway.config import ProviderGatewaySettings
from app.provider_gateway.providers import (
    BrowserProvider,
    CalendarProvider,
    EmailProvider,
    VisionProvider,
)

STATIC_TOOLS: dict[str, tuple[str, str]] = {
    "read_inbox": ("email.read", "Read recent inbox messages."),
    "send_email": ("email.send", "Send an email after Loop approval."),
    "list_events": ("calendar.read", "List upcoming calendar events."),
    "create_event": ("calendar.write", "Create a calendar event after Loop approval."),
    "see_image": ("vision", "Describe or answer a question about an image."),
}


class ProviderGatewayRuntime:
    def __init__(self, settings: ProviderGatewaySettings) -> None:
        self.settings = settings
        self.email = EmailProvider(settings)
        self.calendar = CalendarProvider(settings)
        self.vision = VisionProvider(settings)
        self._browsers: dict[str, BrowserProvider] = {}
        self._browser_lock = asyncio.Lock()

    def configured_providers(self) -> list[str]:
        providers: list[str] = []
        if self.settings.email_configured:
            providers.append("email")
        if self.settings.calendar_configured:
            providers.append("calendar")
        if self.settings.gemini_api_key:
            providers.append("vision")
        if self.settings.browser_enabled:
            providers.append("browser")
        return providers

    async def tools(
        self, grant: AuthorityGrant, *, egress_token: str | None = None
    ) -> list[dict[str, str]]:
        tools: list[dict[str, str]] = []
        if self.settings.email_configured:
            tools.extend(self._static_tools(grant, {"read_inbox", "send_email"}))
        if self.settings.calendar_configured:
            tools.extend(self._static_tools(grant, {"list_events", "create_event"}))
        if self.settings.gemini_api_key:
            tools.extend(self._static_tools(grant, {"see_image"}))
        if grant.permits(Capability.NET_BROWSER) and self.settings.browser_enabled:
            if not grant.egress_hosts:
                raise AuthorityTokenError("Browser authority requires an explicit host allowlist")
            browser = await self._browser(grant, egress_token)
            tools.extend(browser.tools)
        return tools

    async def invoke(
        self,
        grant: AuthorityGrant,
        tool: str,
        args: dict[str, Any],
        *,
        egress_token: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        required = TOOL_CAPABILITIES.get(tool)
        browser = self._browsers.get(grant.run_id)
        browser_tools = {item["name"] for item in browser.tools} if browser is not None else set()
        if required is None and tool not in browser_tools:
            raise AuthorityTokenError(f"Unknown provider tool {tool!r}")
        if required is not None and not required <= grant.capabilities:
            raise AuthorityTokenError(f"Authority token does not grant {tool!r}")
        if required is None and not grant.permits(Capability.NET_BROWSER):
            raise AuthorityTokenError("Authority token does not grant browser access")
        if tool in {"send_email", "create_event"}:
            try:
                uuid.UUID(str(args["operation_id"]))
            except (KeyError, ValueError) as exc:
                raise AuthorityTokenError(
                    f"{tool} requires a Loop operation_id for duplicate protection"
                ) from exc

        if browser is not None and tool in browser_tools:
            if not egress_token:
                raise AuthorityTokenError("Browser requires fresh egress authority")
            self._enforce_browser_target(grant, args)
            await browser.update_egress_token(egress_token)
            result = await browser.call(tool, args)
        elif tool in {"read_inbox", "send_email"}:
            if not self.settings.email_configured:
                raise RuntimeError("Email provider is not configured")
            result = await self.email.call(
                tool, args, self._require_provider_egress(grant, tool, egress_token)
            )
        elif tool in {"list_events", "create_event"}:
            if not self.settings.calendar_configured:
                raise RuntimeError("Calendar provider is not configured")
            result = await self.calendar.call(
                tool, args, self._require_provider_egress(grant, tool, egress_token)
            )
        elif tool == "see_image":
            if not self.settings.gemini_api_key:
                raise RuntimeError("Vision provider is not configured")
            result = await self.vision.call(
                args, self._require_provider_egress(grant, tool, egress_token)
            )
        else:
            raise AuthorityTokenError(f"Unknown provider tool {tool!r}")
        return result, self.audit_event(grant, tool, args, decision="allowed")

    def audit_event(
        self,
        grant: AuthorityGrant,
        tool: str,
        args: dict[str, Any],
        *,
        decision: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        target = self._target(tool, args)
        return {
            "id": str(uuid.uuid4()),
            "at": datetime.now(UTC).isoformat(),
            "kind": "provider",
            "decision": decision,
            "reason": reason,
            "task_id": grant.task_id,
            "owner_id": grant.owner_id,
            "project_id": grant.project_id,
            "run_id": grant.run_id,
            "tool": tool,
            "target": target,
        }

    async def close(self, run_id: str) -> None:
        browser = self._browsers.pop(run_id, None)
        if browser is not None:
            await browser.stop()

    async def close_all(self) -> None:
        browsers = list(self._browsers.values())
        self._browsers.clear()
        for browser in browsers:
            await browser.stop()

    def _static_tools(self, grant: AuthorityGrant, names: set[str]) -> list[dict[str, str]]:
        tools: list[dict[str, str]] = []
        for name in sorted(names):
            capability, description = STATIC_TOOLS[name]
            if Capability(capability) in grant.capabilities:
                tools.append({"name": name, "description": description, "capability": capability})
        return tools

    async def _browser(self, grant: AuthorityGrant, egress_token: str | None) -> BrowserProvider:
        proxy_url = self.settings.resolved_egress_proxy_url()
        if not proxy_url or not egress_token:
            raise AuthorityTokenError("Browser requires the destination-enforcing egress proxy")
        existing = self._browsers.get(grant.run_id)
        if existing is not None:
            await existing.update_egress_token(egress_token)
            return existing
        async with self._browser_lock:
            existing = self._browsers.get(grant.run_id)
            if existing is not None:
                await existing.update_egress_token(egress_token)
                return existing
            browser = BrowserProvider(
                self.settings.browser_command,
                proxy_url=proxy_url,
                egress_token=egress_token,
            )
            await browser.start()
            self._browsers[grant.run_id] = browser
            return browser

    def _enforce_browser_target(self, grant: AuthorityGrant, args: dict[str, Any]) -> None:
        raw = args.get("url")
        if not isinstance(raw, str):
            return
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AuthorityTokenError("Browser URL must be an absolute HTTP(S) URL")
        if not grant.permits_host(parsed.hostname):
            raise AuthorityTokenError(f"Browser target {parsed.hostname!r} is not allowlisted")

    def _require_provider_egress(
        self, grant: AuthorityGrant, tool: str, egress_token: str | None
    ) -> str:
        if not egress_token:
            raise AuthorityTokenError("Provider requires fresh egress authority")
        if tool == "read_inbox":
            host = self.settings.imap_host or self.settings.smtp_host
        elif tool == "send_email":
            host = self.settings.smtp_host
        elif tool in {"list_events", "create_event"}:
            host = urlsplit(self.settings.caldav_url or "").hostname
        else:
            host = "generativelanguage.googleapis.com"
        if not host or not grant.permits_host(host):
            raise AuthorityTokenError(f"Provider upstream {host or '(missing)'} is not allowlisted")
        return egress_token

    def _target(self, tool: str, args: dict[str, Any]) -> str | None:
        if tool == "send_email":
            return str(args.get("to", "")).rsplit("@", 1)[-1] or None
        if tool == "read_inbox":
            return self.settings.imap_host or self.settings.smtp_host
        if tool in {"list_events", "create_event"}:
            return urlsplit(self.settings.caldav_url or "").hostname
        if tool == "see_image":
            return "generativelanguage.googleapis.com"
        raw = args.get("url")
        return urlsplit(raw).hostname if isinstance(raw, str) else None
