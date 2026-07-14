from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

from app.domain.authority_revocation import AuthorityRevocationStore
from app.domain.authority_token import (
    EGRESS_PROXY_AUDIENCE,
    AuthorityGrant,
    AuthorityTokenError,
    verify_authority_token,
)
from app.domain.capability import Capability
from app.egress_proxy.audit import AuditStore
from app.egress_proxy.config import EgressProxySettings

ResolvedAddress = tuple[int, str]
Resolver = Callable[[str, int], Awaitable[ResolvedAddress]]


async def resolve_public_destination(host: str, port: int) -> ResolvedAddress:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for family, _type, _proto, _canonname, sockaddr in records:
        address = str(sockaddr[0])
        if ipaddress.ip_address(address).is_global:
            return family, address
    raise AuthorityTokenError(f"Destination {host!r} did not resolve to a public IP")


class EgressProxy:
    def __init__(
        self,
        settings: EgressProxySettings,
        audit: AuditStore,
        *,
        resolver: Resolver = resolve_public_destination,
        revocations: AuthorityRevocationStore | None = None,
    ) -> None:
        self.settings = settings
        self.audit = audit
        self.resolver = resolver
        self.revocations = revocations or AuthorityRevocationStore()
        self._active_writers: dict[str, set[asyncio.StreamWriter]] = {}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        grant: AuthorityGrant | None = None
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            request_line, headers = _parse_request(raw)
            token = _proxy_token(headers)
            keys = self.settings.public_keyring()
            if not token or not keys:
                raise AuthorityTokenError("A signed proxy authority token is required")
            grant = verify_authority_token(token, keys, audience=EGRESS_PROXY_AUDIENCE)
            self._active_writers.setdefault(grant.run_id, set()).add(writer)
            if await self.revocations.is_revoked(grant.run_id):
                raise AuthorityTokenError("Authority run has been revoked")
            if not grant.permits(Capability.NET_SHELL) and not grant.permits(
                Capability.NET_BROWSER
            ):
                raise AuthorityTokenError("Authority token does not grant network access")
            method, target, version = request_line.split(" ", 2)
            host, port, path = _destination(method, target, headers)
            if port not in self.settings.allowed_port_set():
                await self._record(grant, host, port, method, "blocked", "port_not_allowed")
                raise AuthorityTokenError(f"Destination port {port} is not allowed")
            try:
                host_allowed = bool(grant.egress_hosts) and grant.permits_host(host)
            except AuthorityTokenError:
                host_allowed = False
            if not host_allowed:
                await self._record(grant, host, port, method, "blocked", "host_not_allowlisted")
                raise AuthorityTokenError(f"Destination {host!r} is not allowlisted")
            family, address = await self.resolver(host, port)
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(address, port, family=family),
                timeout=self.settings.connect_timeout_seconds,
            )
            assert upstream_writer is not None
            self._active_writers.setdefault(grant.run_id, set()).add(upstream_writer)
            if await self.revocations.is_revoked(grant.run_id):
                raise AuthorityTokenError("Authority run has been revoked")
            await self._record(grant, host, port, method, "allowed", None, address=address)
            if method.upper() == "CONNECT":
                writer.write(f"{version} 200 Connection Established\r\n\r\n".encode())
                await writer.drain()
                relay = _relay_tunnel(reader, writer, upstream_reader, upstream_writer)
            else:
                upstream_writer.write(_forward_headers(method, path, version, headers, host, port))
                await upstream_writer.drain()
                relay = _relay_http(reader, writer, upstream_reader, upstream_writer)
            remaining = max(0.0, (grant.expires_at - datetime.now(UTC)).total_seconds())
            try:
                await asyncio.wait_for(relay, timeout=remaining)
            except TimeoutError:
                await self._record(grant, host, port, method, "blocked", "authority_expired")
        except (AuthorityTokenError, TimeoutError, ValueError) as exc:
            await _respond_error(writer, 403, str(exc))
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await _respond_error(writer, 400, "Malformed proxy request")
        except Exception as exc:
            if grant is not None:
                await self._record(grant, None, None, None, "blocked", type(exc).__name__)
            await _respond_error(writer, 502, "Upstream connection failed")
        finally:
            if grant is not None:
                active = self._active_writers.get(grant.run_id)
                if active is not None:
                    active.discard(writer)
                    if upstream_writer is not None:
                        active.discard(upstream_writer)
                    if not active:
                        self._active_writers.pop(grant.run_id, None)
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def revoke(self, run_id: str) -> None:
        writers = list(self._active_writers.get(run_id, ()))
        for writer in writers:
            writer.close()
        for writer in writers:
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _record(
        self,
        grant: AuthorityGrant,
        host: str | None,
        port: int | None,
        method: str | None,
        decision: str,
        reason: str | None,
        *,
        address: str | None = None,
    ) -> None:
        await self.audit.append(
            grant.run_id,
            {
                "id": str(uuid.uuid4()),
                "at": datetime.now(UTC).isoformat(),
                "kind": "egress",
                "decision": decision,
                "reason": reason,
                "task_id": grant.task_id,
                "owner_id": grant.owner_id,
                "project_id": grant.project_id,
                "run_id": grant.run_id,
                "host": host,
                "port": port,
                "method": method,
                "resolved_ip": address,
            },
        )


def _parse_request(raw: bytes) -> tuple[str, dict[str, str]]:
    text = raw.decode("iso-8859-1")
    lines = text.split("\r\n")
    if not lines or len(lines[0].split(" ")) != 3:
        raise ValueError("Malformed HTTP request line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise ValueError("Malformed HTTP header")
        headers[name.strip().lower()] = value.strip()
    return lines[0], headers


def _proxy_token(headers: dict[str, str]) -> str | None:
    authorization = headers.get("proxy-authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer":
        return value.strip() or None
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    return unquote(password) if separator and username == "loop" else None


def _destination(method: str, target: str, headers: dict[str, str]) -> tuple[str, int, str]:
    if method.upper() == "CONNECT":
        parsed = urlsplit(f"//{target}")
        if not parsed.hostname:
            raise ValueError("CONNECT requires host:port")
        return parsed.hostname, parsed.port or 443, ""
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", ""}:
        raise ValueError("Plain proxy requests must use HTTP; HTTPS uses CONNECT")
    if parsed.hostname:
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        return host, port, path
    host_header = headers.get("host", "")
    host_parsed = urlsplit(f"//{host_header}")
    if not host_parsed.hostname:
        raise ValueError("Proxy request has no destination host")
    return host_parsed.hostname, host_parsed.port or 80, target or "/"


def _forward_headers(
    method: str,
    path: str,
    version: str,
    headers: dict[str, str],
    host: str,
    port: int,
) -> bytes:
    forwarded = {key: value for key, value in headers.items() if key != "proxy-authorization"}
    forwarded["host"] = host if port == 80 else f"{host}:{port}"
    forwarded["connection"] = "close"
    head = [f"{method} {path} {version}", *(f"{key}: {value}" for key, value in forwarded.items())]
    return ("\r\n".join(head) + "\r\n\r\n").encode("iso-8859-1")


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    finally:
        with contextlib.suppress(Exception):
            writer.write_eof()


async def _relay_tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    tasks = {
        asyncio.create_task(_copy(client_reader, upstream_writer)),
        asyncio.create_task(_copy(upstream_reader, client_writer)),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        upstream_writer.close()
        with contextlib.suppress(Exception):
            await upstream_writer.wait_closed()


async def _relay_http(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    upload = asyncio.create_task(_copy(client_reader, upstream_writer))
    try:
        await _copy(upstream_reader, client_writer)
    finally:
        upload.cancel()
        await asyncio.gather(upload, return_exceptions=True)
        upstream_writer.close()
        with contextlib.suppress(Exception):
            await upstream_writer.wait_closed()


async def _respond_error(writer: asyncio.StreamWriter, status: int, detail: str) -> None:
    if writer.is_closing():
        return
    body = (detail[:300] + "\n").encode()
    reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(status, "Error")
    writer.write(
        (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        + body
    )
    with contextlib.suppress(Exception):
        await writer.drain()
