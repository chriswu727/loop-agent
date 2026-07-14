from __future__ import annotations

import asyncio
import base64
import email
import imaplib
import json
import os
import shlex
import smtplib
import ssl
import tempfile
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.provider_gateway.config import ProviderGatewaySettings


class EmailProvider:
    def __init__(self, settings: ProviderGatewaySettings) -> None:
        self.settings = settings

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "send_email":
            return await asyncio.to_thread(self._send, args)
        if name == "read_inbox":
            return await asyncio.to_thread(self._read, args)
        raise ValueError(f"Unknown email tool {name!r}")

    def _send(self, args: dict[str, Any]) -> str:
        to = str(args.get("to", "")).strip()
        if not to:
            return "send_email needs a 'to' address."
        msg = EmailMessage()
        msg["From"] = self.settings.email_from or self.settings.smtp_user or ""
        msg["To"] = to
        msg["Subject"] = str(args.get("subject", "")).strip()
        msg.set_content(str(args.get("body", "")))
        with smtplib.SMTP(
            self.settings.smtp_host or "",
            self.settings.smtp_port,
            timeout=self.settings.upstream_timeout_seconds,
        ) as server:
            if self.settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            server.login(self.settings.smtp_user or "", self.settings.smtp_password or "")
            server.send_message(msg)
        return f"Email sent to {to} (subject: {msg['Subject']!r})."

    def _read(self, args: dict[str, Any]) -> str:
        limit = max(1, min(int(args.get("limit", 5) or 5), 20))
        host = self.settings.imap_host or self.settings.smtp_host or ""
        with imaplib.IMAP4_SSL(host) as box:
            box.login(self.settings.smtp_user or "", self.settings.smtp_password or "")
            box.select("INBOX")
            _typ, data = box.search(None, "ALL")
            ids = data[0].split()[-limit:]
            items: list[str] = []
            for msg_id in reversed(ids):
                _t, raw = box.fetch(msg_id, "(RFC822)")
                if not raw or not isinstance(raw[0], tuple):
                    continue
                parsed = email.message_from_bytes(raw[0][1])
                items.append(
                    f"- From: {parsed.get('From', '?')}\n"
                    f"  Subject: {parsed.get('Subject', '(none)')}\n"
                    f"  Date: {parsed.get('Date', '?')}\n"
                    f"  Preview: {_body_preview(parsed)}"
                )
        return "\n".join(items) or "(inbox empty)"


class CalendarProvider:
    def __init__(self, settings: ProviderGatewaySettings) -> None:
        self.settings = settings

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "list_events":
            return await asyncio.to_thread(self._list, args)
        if name == "create_event":
            return await asyncio.to_thread(self._create, args)
        raise ValueError(f"Unknown calendar tool {name!r}")

    def _calendar(self) -> Any:
        import caldav

        client = caldav.DAVClient(  # type: ignore[operator]
            url=self.settings.caldav_url or "",
            username=self.settings.caldav_user or "",
            password=self.settings.caldav_password or "",
        )
        calendars = client.principal().calendars()
        if not calendars:
            return None
        if self.settings.caldav_calendar:
            for calendar in calendars:
                if calendar.name == self.settings.caldav_calendar:
                    return calendar
        return calendars[0]

    def _list(self, args: dict[str, Any]) -> str:
        days = max(1, min(int(args.get("days", 7) or 7), 60))
        calendar = self._calendar()
        if calendar is None:
            return "No calendar found for these credentials."
        start = datetime.now(UTC)
        events = calendar.search(
            start=start, end=start + timedelta(days=days), event=True, expand=True
        )
        lines: list[str] = []
        for event in events[:25]:
            component = event.icalendar_component
            summary = component.get("summary", "(no title)")
            dtstart = component.get("dtstart")
            when = dtstart.dt.isoformat() if dtstart else "?"
            lines.append(f"- {when}: {summary}")
        return "\n".join(lines) or f"(no events in the next {days} days)"

    def _create(self, args: dict[str, Any]) -> str:
        from icalendar import Calendar, Event

        summary = str(args.get("summary", "")).strip()
        if not summary:
            return "create_event needs a 'summary'."
        try:
            start = datetime.fromisoformat(str(args["start"]))
            end = (
                datetime.fromisoformat(str(args["end"]))
                if args.get("end")
                else start + timedelta(hours=1)
            )
        except (KeyError, ValueError):
            return "create_event needs ISO 'start' and optional 'end'."
        calendar = self._calendar()
        if calendar is None:
            return "No calendar found for these credentials."
        obj = Calendar()  # type: ignore[no-untyped-call]
        event = Event()  # type: ignore[no-untyped-call]
        event.add("summary", summary)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("uid", f"{uuid.uuid4().hex}@loop")
        if args.get("description"):
            event.add("description", str(args["description"]))
        obj.add_component(event)
        calendar.save_event(obj.to_ical().decode())
        return f"Event created: {summary!r} at {start.isoformat()}."


class VisionProvider:
    def __init__(self, settings: ProviderGatewaySettings) -> None:
        self.settings = settings

    async def call(self, args: dict[str, Any]) -> str:
        encoded = args.get("image_base64")
        mime = str(args.get("mime", ""))
        if not isinstance(encoded, str) or mime not in {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
        }:
            return "see_image received an invalid image payload."
        try:
            image = base64.b64decode(encoded, validate=True)
        except ValueError:
            return "see_image received invalid base64 data."
        if len(image) > 8_000_000:
            return "Image is too large (over 8MB)."
        prompt = str(args.get("prompt", "")).strip() or "Describe this image in detail."
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent"
        )
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            response = await client.post(
                url,
                params={"key": self.settings.gemini_api_key or ""},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": prompt},
                                {"inline_data": {"mime_type": mime, "data": encoded}},
                            ],
                        }
                    ],
                    "generationConfig": {"maxOutputTokens": 800, "temperature": 0.2},
                },
            )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "(the vision model returned no description)"
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts) or "(no description)"


class BrowserProvider:
    def __init__(self, command: str, *, proxy_url: str | None, egress_token: str | None) -> None:
        self._argv = shlex.split(command)
        self._proxy_url = proxy_url
        self._egress_token = egress_token
        self._stack = AsyncExitStack()
        self._session: Any = None
        self._config_path: Path | None = None
        self.tools: list[dict[str, str]] = []

    async def start(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if not self._proxy_url or not self._egress_token:
            raise RuntimeError("Browser requires an authenticated egress proxy")
        if any(
            arg == "--config"
            or arg.startswith("--config=")
            or arg == "--proxy-server"
            or arg.startswith("--proxy-server=")
            for arg in self._argv[1:]
        ):
            raise RuntimeError("Browser command must not override the enforced proxy config")
        self._config_path = _write_browser_proxy_config(self._proxy_url, self._egress_token)
        params = StdioServerParameters(
            command=self._argv[0],
            args=[*self._argv[1:], "--config", str(self._config_path)],
            env=_browser_subprocess_env(),
        )
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            listed = await self._session.list_tools()
            self.tools = [
                {
                    "name": tool.name,
                    "description": (tool.description or "").strip().splitlines()[0][:180],
                    "capability": "net.browser",
                }
                for tool in listed.tools
            ]
        except Exception:
            await self.stop()
            raise

    async def call(self, name: str, args: dict[str, Any]) -> str:
        if self._session is None:
            raise RuntimeError("Browser session is unavailable")
        result = await asyncio.wait_for(self._session.call_tool(name, args), timeout=60)
        text = "".join(getattr(item, "text", "") for item in getattr(result, "content", []))
        return (text or "(no text output)")[:5000]

    async def stop(self) -> None:
        try:
            await self._stack.aclose()
        finally:
            if self._config_path is not None:
                self._config_path.unlink(missing_ok=True)
                self._config_path = None


def _browser_proxy(proxy_url: str, token: str) -> dict[str, str]:
    parsed = urlsplit(proxy_url)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Provider gateway egress proxy URL must be http://host:port")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return {
        "server": urlunsplit((parsed.scheme, f"{host}{port}", "", "", "")),
        "username": "loop",
        "password": token,
    }


def _write_browser_proxy_config(proxy_url: str, token: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="loop-browser-", suffix=".json")
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(
                {
                    "browser": {
                        "launchOptions": {"proxy": _browser_proxy(proxy_url, token)},
                        "contextOptions": {"proxy": _browser_proxy(proxy_url, token)},
                    }
                },
                handle,
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _browser_subprocess_env() -> dict[str, str]:
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "NODE_PATH",
        "PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "TMPDIR",
    }
    return {name: value for name in allowed if (value := os.environ.get(name))}


def _body_preview(parsed: Any, limit: int = 300) -> str:
    try:
        payload: Any = b""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    break
        else:
            payload = parsed.get_payload(decode=True) or b""
        text = payload if isinstance(payload, bytes) else b""
        body = text.decode("utf-8", errors="replace").strip().replace("\n", " ")
        return body[:limit] + ("…" if len(body) > limit else "")
    except Exception:
        return "(could not read body)"
