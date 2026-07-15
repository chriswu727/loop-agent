from __future__ import annotations

import asyncio
import base64
import contextlib
import email
import imaplib
import json
import os
import shlex
import smtplib
import socket
import ssl
import tempfile
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.provider_gateway.config import ProviderGatewaySettings


class EmailProvider:
    def __init__(self, settings: ProviderGatewaySettings) -> None:
        self.settings = settings

    async def call(self, name: str, args: dict[str, Any], egress_token: str | None) -> str:
        if not egress_token:
            raise RuntimeError("Email provider requires fresh egress authority")
        if name == "send_email":
            host = self.settings.smtp_host or ""
            port = self.settings.smtp_port
            operation = self._send
        elif name == "read_inbox":
            host = self.settings.imap_host or self.settings.smtp_host or ""
            port = self.settings.imap_port
            operation = self._read
        else:
            raise ValueError(f"Unknown email tool {name!r}")
        relay = _AuthenticatedTunnelRelay(
            self.settings.proxy_required(), egress_token, target_host=host, target_port=port
        )
        await relay.start()
        try:
            return await asyncio.to_thread(operation, args, relay.endpoint)
        finally:
            await relay.close()

    def _send(self, args: dict[str, Any], relay: tuple[str, int]) -> str:
        to = str(args.get("to", "")).strip()
        if not to:
            return "send_email needs a 'to' address."
        msg = EmailMessage()
        msg["From"] = self.settings.email_from or self.settings.smtp_user or ""
        msg["To"] = to
        msg["Subject"] = str(args.get("subject", "")).strip()
        msg.set_content(str(args.get("body", "")))
        with _SMTPThroughRelay(
            self.settings.smtp_host or "",
            self.settings.smtp_port,
            relay,
            timeout=self.settings.upstream_timeout_seconds,
        ) as server:
            if self.settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            server.login(self.settings.smtp_user or "", self.settings.smtp_password or "")
            server.send_message(msg)
        return f"Email sent to {to} (subject: {msg['Subject']!r})."

    def _read(self, args: dict[str, Any], relay: tuple[str, int]) -> str:
        limit = max(1, min(int(args.get("limit", 5) or 5), 20))
        host = self.settings.imap_host or self.settings.smtp_host or ""
        with _IMAP4SSLThroughRelay(
            host,
            self.settings.imap_port,
            relay,
            timeout=self.settings.upstream_timeout_seconds,
        ) as box:
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

    async def call(self, name: str, args: dict[str, Any], egress_token: str | None) -> str:
        if not egress_token:
            raise RuntimeError("Calendar provider requires fresh egress authority")
        relay = _AuthenticatedProxyRelay(self.settings.proxy_required(), egress_token)
        await relay.start()
        try:
            if name == "list_events":
                return await asyncio.to_thread(self._list, args, relay.proxy_url)
            if name == "create_event":
                return await asyncio.to_thread(self._create, args, relay.proxy_url)
            raise ValueError(f"Unknown calendar tool {name!r}")
        finally:
            await relay.close()

    def _calendar(self, proxy_url: str) -> Any:
        import caldav

        client = caldav.DAVClient(  # type: ignore[operator]
            url=self.settings.caldav_url or "",
            proxy=proxy_url,
            username=self.settings.caldav_user or "",
            password=self.settings.caldav_password or "",
            enable_rfc6764=False,
        )
        calendars = client.principal().calendars()
        if not calendars:
            return None
        if self.settings.caldav_calendar:
            for calendar in calendars:
                if calendar.name == self.settings.caldav_calendar:
                    return calendar
        return calendars[0]

    def _list(self, args: dict[str, Any], proxy_url: str) -> str:
        days = max(1, min(int(args.get("days", 7) or 7), 60))
        calendar = self._calendar(proxy_url)
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

    def _create(self, args: dict[str, Any], proxy_url: str) -> str:
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
        calendar = self._calendar(proxy_url)
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

    async def call(self, args: dict[str, Any], egress_token: str | None) -> str:
        if not egress_token:
            raise RuntimeError("Vision provider requires fresh egress authority")
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
        relay = _AuthenticatedProxyRelay(self.settings.proxy_required(), egress_token)
        await relay.start()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.upstream_timeout_seconds, proxy=relay.proxy_url
            ) as client:
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
        finally:
            await relay.close()
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "(the vision model returned no description)"
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts) or "(no description)"


class _SMTPThroughRelay(smtplib.SMTP):
    def __init__(
        self,
        target_host: str,
        target_port: int,
        relay: tuple[str, int],
        *,
        timeout: float,
    ) -> None:
        self._relay = relay
        super().__init__(target_host, target_port, timeout=timeout)

    def _get_socket(self, _host: str, _port: int, timeout: float) -> socket.socket:
        return socket.create_connection(self._relay, timeout)


class _IMAP4SSLThroughRelay(imaplib.IMAP4_SSL):
    def __init__(
        self,
        target_host: str,
        target_port: int,
        relay: tuple[str, int],
        *,
        timeout: float,
    ) -> None:
        self._relay = relay
        super().__init__(
            target_host,
            target_port,
            ssl_context=ssl.create_default_context(),
            timeout=timeout,
        )

    def _create_socket(self, timeout: float) -> socket.socket:
        raw = socket.create_connection(self._relay, timeout)
        return cast(socket.socket, self.ssl_context.wrap_socket(raw, server_hostname=self.host))


class BrowserProvider:
    def __init__(self, command: str, *, proxy_url: str | None, egress_token: str | None) -> None:
        self._argv = shlex.split(command)
        self._proxy_url = proxy_url
        self._egress_token = egress_token
        self._stack = AsyncExitStack()
        self._session: Any = None
        self._config_path: Path | None = None
        self._relay: _AuthenticatedProxyRelay | None = None
        self.tools: list[dict[str, str]] = []

    async def start(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if not self._proxy_url or not self._egress_token:
            raise RuntimeError("Browser requires an authenticated egress proxy")
        if not self._argv:
            raise RuntimeError("Browser command is empty")
        if any(
            arg == "--config"
            or arg.startswith("--config=")
            or arg == "--proxy-server"
            or arg.startswith("--proxy-server=")
            for arg in self._argv[1:]
        ):
            raise RuntimeError("Browser command must not override the enforced proxy config")
        try:
            self._relay = _AuthenticatedProxyRelay(self._proxy_url, self._egress_token)
            await self._relay.start()
            self._config_path = _write_browser_proxy_config(self._relay.proxy_url)
            params = StdioServerParameters(
                command=self._argv[0],
                args=[*self._argv[1:], "--config", str(self._config_path)],
                env=_browser_subprocess_env(),
            )
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

    async def update_egress_token(self, token: str) -> None:
        if self._relay is None:
            raise RuntimeError("Browser proxy relay is unavailable")
        await self._relay.update_token(token)

    async def stop(self) -> None:
        try:
            await self._stack.aclose()
        finally:
            if self._config_path is not None:
                self._config_path.unlink(missing_ok=True)
                self._config_path = None
            if self._relay is not None:
                await self._relay.close()
                self._relay = None


class _AuthenticatedProxyRelay:
    def __init__(self, upstream_url: str, token: str) -> None:
        self._upstream_host, self._upstream_port = _proxy_endpoint(upstream_url)
        self._token = token
        self._server: asyncio.AbstractServer | None = None
        self._listen_port: int | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def proxy_url(self) -> str:
        if self._server is None or self._listen_port is None:
            raise RuntimeError("Browser proxy relay is unavailable")
        return f"http://127.0.0.1:{self._listen_port}"

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            sockets = getattr(self._server, "sockets", None)
            if not sockets:
                await self.close()
                raise RuntimeError("Browser proxy relay did not bind a socket")
            self._listen_port = int(sockets[0].getsockname()[1])

    async def update_token(self, token: str) -> None:
        if not token:
            raise ValueError("Browser egress token is required")
        if token == self._token:
            return
        self._token = token
        await self._close_connections()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._listen_port = None
        await self._close_connections()
        self._writers.clear()

    async def _close_connections(self) -> None:
        writers = list(self._writers)
        for writer in writers:
            writer.close()
        for writer in writers:
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        self._writers.add(writer)
        try:
            request_head = await reader.readuntil(b"\r\n\r\n")
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self._upstream_host, self._upstream_port
            )
            self._writers.add(upstream_writer)
            upstream_writer.write(_inject_proxy_authorization(request_head, self._token))
            await upstream_writer.drain()
            await _relay_streams(reader, writer, upstream_reader, upstream_writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            for active in (writer, upstream_writer):
                if active is None:
                    continue
                self._writers.discard(active)
                active.close()
                with contextlib.suppress(Exception):
                    await active.wait_closed()


class _AuthenticatedTunnelRelay:
    def __init__(
        self,
        upstream_url: str,
        token: str,
        *,
        target_host: str,
        target_port: int,
    ) -> None:
        if not target_host or any(character.isspace() for character in target_host):
            raise ValueError("Provider target host is invalid")
        if not 1 <= target_port <= 65535:
            raise ValueError("Provider target port is invalid")
        self._upstream_host, self._upstream_port = _proxy_endpoint(upstream_url)
        self._token = token
        self._target_host = target_host
        self._target_port = target_port
        self._server: asyncio.AbstractServer | None = None
        self._listen_port: int | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def endpoint(self) -> tuple[str, int]:
        if self._server is None or self._listen_port is None:
            raise RuntimeError("Provider tunnel relay is unavailable")
        return "127.0.0.1", self._listen_port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = getattr(self._server, "sockets", None)
        if not sockets:
            await self.close()
            raise RuntimeError("Provider tunnel relay did not bind a socket")
        self._listen_port = int(sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._listen_port = None
        writers = list(self._writers)
        for writer in writers:
            writer.close()
        for writer in writers:
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._writers.clear()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        self._writers.add(writer)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self._upstream_host, self._upstream_port
            )
            self._writers.add(upstream_writer)
            basic = base64.b64encode(f"loop:{self._token}".encode()).decode()
            target = f"{self._target_host}:{self._target_port}"
            upstream_writer.write(
                (
                    f"CONNECT {target} HTTP/1.1\r\n"
                    f"Host: {target}\r\n"
                    f"Proxy-Authorization: Basic {basic}\r\n\r\n"
                ).encode()
            )
            await upstream_writer.drain()
            response = await upstream_reader.readuntil(b"\r\n\r\n")
            status = response.split(b"\r\n", 1)[0].split(b" ", 2)
            if len(status) < 2 or status[1] != b"200":
                raise ConnectionError("Egress proxy rejected provider tunnel")
            await _relay_streams(reader, writer, upstream_reader, upstream_writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            for active in (writer, upstream_writer):
                if active is None:
                    continue
                self._writers.discard(active)
                active.close()
                with contextlib.suppress(Exception):
                    await active.wait_closed()


def _proxy_endpoint(proxy_url: str) -> tuple[str, int]:
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
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise ValueError("Provider gateway egress proxy URL has an invalid port") from exc
    return parsed.hostname, port


def _browser_proxy(proxy_url: str) -> dict[str, str]:
    parsed = urlsplit(proxy_url)
    hostname, _ = _proxy_endpoint(proxy_url)
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = f":{parsed.port}" if parsed.port else ""
    return {"server": urlunsplit((parsed.scheme, f"{host}{port}", "", "", ""))}


def _write_browser_proxy_config(proxy_url: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="loop-browser-", suffix=".json")
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(
                {
                    "browser": {
                        "launchOptions": {"proxy": _browser_proxy(proxy_url)},
                        "contextOptions": {"proxy": _browser_proxy(proxy_url)},
                    }
                },
                handle,
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _inject_proxy_authorization(request_head: bytes, token: str) -> bytes:
    lines = request_head.removesuffix(b"\r\n\r\n").split(b"\r\n")
    if not lines or b" " not in lines[0]:
        raise ConnectionError("Invalid browser proxy request")
    filtered = [
        line
        for line in lines[1:]
        if line.partition(b":")[0].strip().lower() != b"proxy-authorization"
    ]
    encoded = base64.b64encode(f"loop:{token}".encode())
    return b"\r\n".join([lines[0], *filtered, b"Proxy-Authorization: Basic " + encoded, b"", b""])


async def _relay_streams(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
        while data := await source.read(64 * 1024):
            destination.write(data)
            await destination.drain()

    tasks = {
        asyncio.create_task(copy(client_reader, upstream_writer)),
        asyncio.create_task(copy(upstream_reader, client_writer)),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, *pending, return_exceptions=True)


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
