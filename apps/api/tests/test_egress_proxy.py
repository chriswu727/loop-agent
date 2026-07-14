from __future__ import annotations

import asyncio
import base64
import socket

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.domain.authority_revocation import AuthorityRevocationStore
from app.domain.authority_token import (
    AUTHORITY_CONTROL_AUDIENCE,
    EGRESS_PROXY_AUDIENCE,
    AuthorityTokenError,
    issue_authority_token,
    public_key_pem,
)
from app.domain.capability import Capability
from app.egress_proxy.admin import create_admin_app
from app.egress_proxy.audit import AuditStore
from app.egress_proxy.config import EgressProxySettings
from app.egress_proxy.proxy import EgressProxy, resolve_public_destination


def _keys() -> tuple[str, str]:
    private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    return private, public_key_pem(private)


def _token(
    private: str,
    hosts: list[str],
    *,
    ttl_seconds: int = 120,
    audience: str = EGRESS_PROXY_AUDIENCE,
) -> str:
    return issue_authority_token(
        private,
        audience=audience,
        task_id="task-1",
        owner_id="owner-1",
        project_id="project-1",
        run_id="task-1:1",
        capabilities=[Capability.NET_SHELL],
        egress_hosts=hosts,
        ttl_seconds=ttl_seconds,
    )


async def _request(proxy_port: int, token: str, target: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    basic = base64.b64encode(f"loop:{token}".encode()).decode()
    writer.write(
        (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {target.split('/', 3)[2]}\r\n"
            f"Proxy-Authorization: Basic {basic}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    await writer.wait_closed()
    return response


async def _connect(
    proxy_port: int, token: str, target: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    basic = base64.b64encode(f"loop:{token}".encode()).decode()
    writer.write(
        (
            f"CONNECT {target} HTTP/1.1\r\n"
            f"Host: {target}\r\n"
            f"Proxy-Authorization: Basic {basic}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    assert b"200 Connection Established" in response
    return reader, writer


async def test_proxy_routes_allowlisted_host_and_records_pinned_ip() -> None:
    private, public = _keys()

    async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b"through-proxy"
        writer.write(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])

    async def resolver(host: str, port: int) -> tuple[int, str]:
        assert (host, port) == ("allowed.example", upstream_port)
        return socket.AF_INET, "127.0.0.1"

    audit = AuditStore()
    proxy = EgressProxy(
        EgressProxySettings(authority_public_key=public, allowed_ports=str(upstream_port)),
        audit,
        resolver=resolver,
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    try:
        response = await _request(
            proxy_port,
            _token(private, ["allowed.example"]),
            f"http://allowed.example:{upstream_port}/resource",
        )
        assert b"200 OK" in response
        assert response.endswith(b"through-proxy")
        events = await audit.list("task-1:1")
        assert len(events) == 1
        assert (
            events[0]
            | {
                "kind": "egress",
                "decision": "allowed",
                "host": "allowed.example",
                "port": upstream_port,
                "resolved_ip": "127.0.0.1",
            }
            == events[0]
        )
    finally:
        server.close()
        upstream.close()
        await server.wait_closed()
        await upstream.wait_closed()


async def test_proxy_blocks_host_outside_token_allowlist_before_resolving() -> None:
    private, public = _keys()
    resolved = False

    async def resolver(host: str, port: int) -> tuple[int, str]:
        nonlocal resolved
        resolved = True
        return socket.AF_INET, "127.0.0.1"

    audit = AuditStore()
    proxy = EgressProxy(
        EgressProxySettings(authority_public_key=public),
        audit,
        resolver=resolver,
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    try:
        response = await _request(
            proxy_port,
            _token(private, ["allowed.example"]),
            "http://evil.example/steal",
        )
        assert b"403 Forbidden" in response
        assert not resolved
        events = await audit.list("task-1:1")
        assert events[0]["decision"] == "blocked"
        assert events[0]["reason"] == "host_not_allowlisted"
    finally:
        server.close()
        await server.wait_closed()


async def test_proxy_connect_tunnel_is_destination_bound() -> None:
    private, public = _keys()

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(await reader.readexactly(4))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])

    async def resolver(host: str, port: int) -> tuple[int, str]:
        assert (host, port) == ("browser.example", upstream_port)
        return socket.AF_INET, "127.0.0.1"

    audit = AuditStore()
    proxy = EgressProxy(
        EgressProxySettings(authority_public_key=public, allowed_ports=str(upstream_port)),
        audit,
        resolver=resolver,
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    try:
        reader, writer = await _connect(
            proxy_port,
            _token(private, ["browser.example"]),
            f"browser.example:{upstream_port}",
        )
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), timeout=5) == b"ping"
        writer.close()
        await writer.wait_closed()
        events = await audit.list("task-1:1")
        assert events[0]["method"] == "CONNECT"
        assert events[0]["decision"] == "allowed"
    finally:
        server.close()
        upstream.close()
        await server.wait_closed()
        await upstream.wait_closed()


async def test_proxy_closes_existing_tunnel_when_authority_expires() -> None:
    private, public = _keys()

    async def hold_open(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])

    async def resolver(_host: str, _port: int) -> tuple[int, str]:
        return socket.AF_INET, "127.0.0.1"

    audit = AuditStore()
    proxy = EgressProxy(
        EgressProxySettings(authority_public_key=public, allowed_ports=str(upstream_port)),
        audit,
        resolver=resolver,
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    try:
        reader, writer = await _connect(
            proxy_port,
            _token(private, ["browser.example"], ttl_seconds=2),
            f"browser.example:{upstream_port}",
        )
        assert await asyncio.wait_for(reader.read(), timeout=3) == b""
        events = await audit.list("task-1:1")
        for _ in range(10):
            if len(events) == 2:
                break
            await asyncio.sleep(0)
            events = await audit.list("task-1:1")
        assert [event["decision"] for event in events] == ["allowed", "blocked"]
        assert events[-1]["reason"] == "authority_expired"
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        upstream.close()
        await server.wait_closed()
        await upstream.wait_closed()


async def test_signed_revocation_closes_tunnel_and_blocks_run(tmp_path) -> None:
    private, public = _keys()

    async def hold_open(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])

    async def resolver(_host: str, _port: int) -> tuple[int, str]:
        return socket.AF_INET, "127.0.0.1"

    settings = EgressProxySettings(
        authority_public_key=public,
        allowed_ports=str(upstream_port),
    )
    audit = AuditStore()
    revocations = AuthorityRevocationStore(tmp_path / "revocations.sqlite3")
    proxy = EgressProxy(settings, audit, resolver=resolver, revocations=revocations)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    token = _token(private, ["browser.example"])
    control = _token(
        private,
        ["browser.example"],
        audience=AUTHORITY_CONTROL_AUDIENCE,
    )
    admin = create_admin_app(settings, audit, revocations, proxy.revoke)
    try:
        reader, writer = await _connect(
            proxy_port,
            token,
            f"browser.example:{upstream_port}",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin), base_url="http://admin"
        ) as client:
            revoked = await client.post(
                "/v1/revocations",
                headers={"Authorization": f"Bearer {control}"},
            )
        assert revoked.status_code == 200
        assert revoked.json()["audit"]["decision"] == "revoked"
        assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        blocked = await _request(
            proxy_port,
            token,
            f"http://browser.example:{upstream_port}/resource",
        )
        assert b"403 Forbidden" in blocked
        assert b"revoked" in blocked
        assert await revocations.is_revoked("task-1:1")
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        upstream.close()
        await server.wait_closed()
        await upstream.wait_closed()


async def test_proxy_blocks_unapproved_port_before_resolving() -> None:
    private, public = _keys()
    resolved = False

    async def resolver(_host: str, _port: int) -> tuple[int, str]:
        nonlocal resolved
        resolved = True
        return socket.AF_INET, "127.0.0.1"

    audit = AuditStore()
    proxy = EgressProxy(
        EgressProxySettings(authority_public_key=public, allowed_ports="443"),
        audit,
        resolver=resolver,
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    proxy_port = int(server.sockets[0].getsockname()[1])
    try:
        response = await _request(
            proxy_port,
            _token(private, ["allowed.example"]),
            "http://allowed.example:8443/resource",
        )
        assert b"403 Forbidden" in response
        assert not resolved
        events = await audit.list("task-1:1")
        assert events[0]["reason"] == "port_not_allowed"
    finally:
        server.close()
        await server.wait_closed()


async def test_audit_store_discards_oldest_events_at_its_bound() -> None:
    audit = AuditStore(max_events_per_run=2)
    await audit.append("run", {"id": "one"})
    await audit.append("run", {"id": "two"})
    await audit.append("run", {"id": "three"})
    assert await audit.list("run") == [{"id": "two"}, {"id": "three"}]


async def test_audit_store_survives_restart_and_enforces_durable_bounds(tmp_path) -> None:
    path = tmp_path / "audit" / "events.sqlite3"
    first = AuditStore(path, max_events_per_run=2, max_events_total=3)
    await first.append("run-one", {"id": "one"})
    await first.append("run-one", {"id": "two"})
    await first.append("run-one", {"id": "three"})
    await first.append("run-two", {"id": "four"})

    restarted = AuditStore(path, max_events_per_run=2, max_events_total=3)

    assert restarted.durable
    assert await restarted.list("run-one") == [{"id": "two"}, {"id": "three"}]
    assert await restarted.list("run-two") == [{"id": "four"}]


async def test_default_resolver_rejects_private_destinations() -> None:
    with pytest.raises(AuthorityTokenError, match="public IP"):
        await resolve_public_destination("localhost", 80)
