from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.domain.authority_token import (
    AUTHORITY_CONTROL_AUDIENCE,
    EGRESS_PROXY_AUDIENCE,
    PROVIDER_GATEWAY_AUDIENCE,
    AuthorityGrant,
    AuthorityTokenError,
    issue_authority_token,
    public_key_pem,
)
from app.domain.capability import Capability
from app.provider_gateway.config import ProviderGatewaySettings
from app.provider_gateway.main import create_app
from app.provider_gateway.providers import (
    _AuthenticatedProxyRelay,
    _browser_subprocess_env,
    _write_browser_proxy_config,
)
from app.provider_gateway.runtime import ProviderGatewayRuntime
from app.tools import CapabilityEnvelope, ToolExecutor, ToolStatus, Workspace
from app.tools.provider_gateway import ProviderGatewayClient


def _keys() -> tuple[str, str]:
    private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    return private, public_key_pem(private)


def _token(
    private: str,
    capabilities: list[Capability],
    *,
    audience: str = PROVIDER_GATEWAY_AUDIENCE,
    run_id: str = "task-1:1",
    egress_hosts: list[str] | None = None,
) -> str:
    return issue_authority_token(
        private,
        audience=audience,
        task_id="task-1",
        owner_id="owner-1",
        project_id="project-1",
        run_id=run_id,
        capabilities=capabilities,
        egress_hosts=egress_hosts or [],
        ttl_seconds=120,
    )


async def test_gateway_exposes_and_invokes_only_token_granted_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, public = _keys()
    settings = ProviderGatewaySettings(
        authority_public_key=public,
        browser_enabled=False,
        smtp_host="smtp.example.com",
        smtp_user="me@example.com",
        smtp_password="secret",
    )
    app = create_app(settings)
    runtime: ProviderGatewayRuntime = app.state.runtime

    async def fake_email(name: str, args: dict[str, Any]) -> str:
        assert name == "read_inbox"
        assert args == {"limit": 2}
        return "- safe inbox data"

    monkeypatch.setattr(runtime.email, "call", fake_email)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {_token(private, [Capability.EMAIL_READ])}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        inventory = await client.get("/v1/tools", headers=headers)
        assert inventory.status_code == 200
        assert [tool["name"] for tool in inventory.json()["tools"]] == ["read_inbox"]

        invoked = await client.post(
            "/v1/tools/read_inbox", headers=headers, json={"args": {"limit": 2}}
        )
        assert invoked.status_code == 200
        assert invoked.json()["result"] == "- safe inbox data"
        assert (
            invoked.json()["audit"]
            | {
                "kind": "provider",
                "tool": "read_inbox",
                "task_id": "task-1",
                "owner_id": "owner-1",
                "project_id": "project-1",
                "run_id": "task-1:1",
                "decision": "allowed",
            }
            == invoked.json()["audit"]
        )

        denied = await client.post(
            "/v1/tools/send_email",
            headers=headers,
            json={"args": {"to": "victim@example.com"}},
        )
        assert denied.status_code == 403
        assert denied.json()["audit"]["decision"] == "blocked"


async def test_gateway_rejects_unsigned_requests() -> None:
    app = create_app(ProviderGatewaySettings(browser_enabled=False))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        response = await client.get("/v1/tools")
    assert response.status_code == 401


async def test_gateway_revokes_run_with_signed_control_token(tmp_path) -> None:
    private, public = _keys()
    app = create_app(
        ProviderGatewaySettings(
            authority_public_key=public,
            browser_enabled=False,
            smtp_host="smtp.example.com",
            smtp_user="me@example.com",
            smtp_password="secret",
            revocation_database_path=str(tmp_path / "revocations.sqlite3"),
        )
    )
    provider_token = _token(private, [Capability.EMAIL_READ])
    control_token = _token(
        private,
        [Capability.EMAIL_READ],
        audience=AUTHORITY_CONTROL_AUDIENCE,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        before = await client.get(
            "/v1/tools", headers={"Authorization": f"Bearer {provider_token}"}
        )
        revoked = await client.post(
            "/v1/revocations",
            headers={"Authorization": f"Bearer {control_token}"},
        )
        after = await client.get("/v1/tools", headers={"Authorization": f"Bearer {provider_token}"})
        wrong_audience = await client.post(
            "/v1/revocations",
            headers={"Authorization": f"Bearer {provider_token}"},
        )

    assert before.status_code == 200
    assert revoked.status_code == 200
    assert revoked.json()["audit"]["decision"] == "revoked"
    assert after.status_code == 403
    assert after.json()["detail"] == "Authority run has been revoked"
    assert wrong_audience.status_code == 403
    assert app.state.revocations.durable


async def test_gateway_rejects_egress_grant_from_another_run() -> None:
    private, public = _keys()
    app = create_app(
        ProviderGatewaySettings(
            authority_public_key=public,
            browser_enabled=True,
            egress_proxy_url="http://egress-proxy:8080",
        )
    )
    capability = [Capability.NET_BROWSER]
    headers = {
        "Authorization": (
            f"Bearer {_token(private, capability, egress_hosts=['docs.example.com'])}"
        ),
        "X-Loop-Egress-Token": _token(
            private,
            capability,
            audience=EGRESS_PROXY_AUDIENCE,
            run_id="task-2:1",
            egress_hosts=["example.com"],
        ),
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        response = await client.get("/v1/tools", headers=headers)
    assert response.status_code == 403
    assert "do not match" in response.json()["detail"]


async def test_browser_invocation_requires_matching_fresh_egress_grant() -> None:
    private, public = _keys()
    app = create_app(ProviderGatewaySettings(authority_public_key=public, browser_enabled=False))
    runtime: ProviderGatewayRuntime = app.state.runtime
    rotated: list[str] = []

    class FakeBrowser:
        tools: ClassVar[list[dict[str, str]]] = [
            {
                "name": "browser_navigate",
                "description": "Navigate",
                "capability": "net.browser",
            }
        ]

        async def update_egress_token(self, token: str) -> None:
            rotated.append(token)

        async def call(self, _name: str, _args: dict[str, Any]) -> str:
            return "navigated"

        async def stop(self) -> None:
            return None

    runtime._browsers["task-1:1"] = FakeBrowser()  # type: ignore[assignment]
    provider_token = _token(private, [Capability.NET_BROWSER], egress_hosts=["docs.example.com"])
    matching_egress = _token(
        private,
        [Capability.NET_BROWSER],
        audience=EGRESS_PROXY_AUDIENCE,
        egress_hosts=["docs.example.com"],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        missing = await client.post(
            "/v1/tools/browser_navigate",
            headers={"Authorization": f"Bearer {provider_token}"},
            json={"args": {"url": "https://docs.example.com"}},
        )
        mismatch = await client.post(
            "/v1/tools/browser_navigate",
            headers={
                "Authorization": f"Bearer {provider_token}",
                "X-Loop-Egress-Token": _token(
                    private,
                    [Capability.NET_BROWSER],
                    audience=EGRESS_PROXY_AUDIENCE,
                    run_id="task-2:1",
                    egress_hosts=["docs.example.com"],
                ),
            },
            json={"args": {"url": "https://docs.example.com"}},
        )
        allowed = await client.post(
            "/v1/tools/browser_navigate",
            headers={
                "Authorization": f"Bearer {provider_token}",
                "X-Loop-Egress-Token": matching_egress,
            },
            json={"args": {"url": "https://docs.example.com"}},
        )

    assert missing.status_code == 403
    assert mismatch.status_code == 403
    assert allowed.status_code == 200
    assert rotated == [matching_egress]


async def test_gateway_does_not_return_provider_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private, public = _keys()
    settings = ProviderGatewaySettings(
        authority_public_key=public,
        browser_enabled=False,
        smtp_host="smtp.example.com",
        smtp_user="me@example.com",
        smtp_password="secret",
    )
    app = create_app(settings)
    runtime: ProviderGatewayRuntime = app.state.runtime

    async def failed_email(_name: str, _args: dict[str, Any]) -> str:
        raise RuntimeError("provider-secret-should-not-leak")

    monkeypatch.setattr(runtime.email, "call", failed_email)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {_token(private, [Capability.EMAIL_READ])}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        response = await client.post(
            "/v1/tools/read_inbox", headers=headers, json={"args": {"limit": 1}}
        )
    assert response.status_code == 502
    assert response.json()["detail"] == "Provider call failed"
    assert response.json()["audit"]["decision"] == "unavailable"
    assert response.json()["audit"]["reason"] == "RuntimeError"
    assert "provider-secret" not in response.text


async def test_gateway_client_keeps_denied_call_audit(tmp_path) -> None:
    private, public = _keys()
    app = create_app(
        ProviderGatewaySettings(
            authority_public_key=public,
            browser_enabled=False,
            smtp_host="smtp.example.com",
            smtp_user="me@example.com",
            smtp_password="secret",
        )
    )

    def token_factory(audience: str) -> str:
        return issue_authority_token(
            private,
            audience=audience,
            task_id="task-1",
            owner_id="owner-1",
            project_id="project-1",
            run_id="task-1:1",
            capabilities=[Capability.EMAIL_READ],
            egress_hosts=[],
            ttl_seconds=120,
        )

    gateway = ProviderGatewayClient(
        "http://gateway", Workspace(tmp_path / "workspace"), token_factory
    )
    await gateway.client.aclose()
    gateway.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    )
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.call("send_email", {"to": "victim@example.com"})
    assert gateway.drain_audit()[0]["decision"] == "blocked"
    await gateway.client.aclose()


def test_browser_subprocess_receives_no_provider_credentials_or_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setenv("CALDAV_PASSWORD", "calendar-secret")
    monkeypatch.setenv("PROVIDER_GATEWAY_GEMINI_API_KEY", "vision-secret")

    env = _browser_subprocess_env()

    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "/ms-playwright"
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "SMTP_PASSWORD" not in env
    assert "CALDAV_PASSWORD" not in env
    assert "PROVIDER_GATEWAY_GEMINI_API_KEY" not in env


def test_browser_proxy_config_contains_only_private_loopback_relay() -> None:
    path = _write_browser_proxy_config("http://127.0.0.1:49152")
    try:
        assert path.stat().st_mode & 0o777 == 0o600
        config = json.loads(path.read_text())
        assert config["browser"]["launchOptions"]["proxy"] == {"server": "http://127.0.0.1:49152"}
        assert config["browser"]["contextOptions"]["proxy"] == {"server": "http://127.0.0.1:49152"}
        assert "short-token" not in path.read_text()
    finally:
        path.unlink(missing_ok=True)


async def test_browser_proxy_relay_injects_and_rotates_egress_authority() -> None:
    authorizations: list[str] = []

    async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = (await reader.readuntil(b"\r\n\r\n")).decode("iso-8859-1")
        authorizations.extend(
            line.partition(":")[2].strip()
            for line in request.split("\r\n")
            if line.lower().startswith("proxy-authorization:")
        )
        writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])
    relay = _AuthenticatedProxyRelay(f"http://127.0.0.1:{upstream_port}", "token-one")
    await relay.start()

    async def request() -> None:
        parsed = urlsplit(relay.proxy_url)
        reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
        writer.write(
            b"GET http://docs.example.com/ HTTP/1.1\r\n"
            b"Host: docs.example.com\r\n"
            b"Proxy-Authorization: Basic attacker-controlled\r\n\r\n"
        )
        await writer.drain()
        assert b"204 No Content" in await asyncio.wait_for(reader.read(), timeout=5)
        writer.close()
        await writer.wait_closed()

    try:
        await request()
        await relay.update_token("token-two")
        await request()
    finally:
        await relay.close()
        upstream.close()
        await upstream.wait_closed()

    assert [base64.b64decode(value.partition(" ")[2]).decode() for value in authorizations] == [
        "loop:token-one",
        "loop:token-two",
    ]


async def test_browser_proxy_relay_drops_old_connections_when_authority_rotates() -> None:
    async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])
    relay = _AuthenticatedProxyRelay(f"http://127.0.0.1:{upstream_port}", "token-one")
    await relay.start()
    parsed = urlsplit(relay.proxy_url)
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    writer.write(b"CONNECT docs.example.com:443 HTTP/1.1\r\nHost: docs.example.com:443\r\n\r\n")
    await writer.drain()
    assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")

    try:
        await relay.update_token("token-two")
        assert await asyncio.wait_for(reader.read(), timeout=1) == b""
    finally:
        writer.close()
        await writer.wait_closed()
        await relay.close()
        upstream.close()
        await upstream.wait_closed()


async def test_gateway_browser_tool_dispatches_through_executor(tmp_path) -> None:
    class FakeGateway:
        capability = ProviderGatewayClient.capability
        tool_names: ClassVar[set[str]] = {"browser_navigate"}

        async def call(self, name: str, args: dict[str, Any]) -> str:
            assert name == "browser_navigate"
            assert args == {"url": "https://docs.example.com"}
            return "navigated"

    executor = ToolExecutor(
        Workspace(tmp_path / "workspace"),
        provider_gateway=FakeGateway(),
        envelope=CapabilityEnvelope.from_capabilities(
            [Capability.NET_BROWSER], egress_hosts=["docs.example.com"]
        ),
    )

    result = await executor.execute("browser_navigate", {"url": "https://docs.example.com"})

    assert result.status is ToolStatus.OK
    assert result.observation == "navigated"


async def test_browser_gateway_enforces_host_before_provider_call() -> None:
    settings = ProviderGatewaySettings(browser_enabled=False)
    runtime = ProviderGatewayRuntime(settings)
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeBrowser:
        tools: ClassVar[list[dict[str, str]]] = [
            {
                "name": "browser_navigate",
                "description": "Navigate",
                "capability": "net.browser",
            }
        ]

        async def update_egress_token(self, token: str) -> None:
            assert token == "fresh-egress-token"

        async def call(self, name: str, args: dict[str, Any]) -> str:
            calls.append((name, args))
            return "ok"

        async def stop(self) -> None:
            return None

    runtime._browsers["task-1:1"] = FakeBrowser()  # type: ignore[assignment]
    grant = AuthorityGrant(
        token_id="token",
        task_id="task-1",
        owner_id="owner-1",
        project_id="project-1",
        run_id="task-1:1",
        capabilities=frozenset({Capability.NET_BROWSER}),
        egress_hosts=frozenset({"example.com"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    with pytest.raises(AuthorityTokenError, match="not allowlisted"):
        await runtime.invoke(
            grant,
            "browser_navigate",
            {"url": "https://example.com.evil.test/"},
            egress_token="fresh-egress-token",
        )
    result, audit = await runtime.invoke(
        grant,
        "browser_navigate",
        {"url": "https://docs.example.com/"},
        egress_token="fresh-egress-token",
    )
    assert result == "ok"
    assert audit["target"] == "docs.example.com"
    assert calls == [("browser_navigate", {"url": "https://docs.example.com/"})]
