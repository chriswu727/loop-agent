#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.domain.authority_revocation import AuthorityRevocationStore
from app.domain.authority_token import EGRESS_PROXY_AUDIENCE, issue_authority_token, public_key_pem
from app.domain.capability import Capability
from app.egress_proxy.admin import create_admin_app
from app.egress_proxy.audit import AuditStore
from app.egress_proxy.config import EgressProxySettings
from app.egress_proxy.proxy import EgressProxy
from app.provider_gateway.config import ProviderGatewaySettings
from app.provider_gateway.main import create_app

RUN_ID = "acceptance-run"
UPSTREAM_HOST = "acceptance.example"
SCRIPT_PATH = str(Path(__file__).resolve())


def _keypair() -> tuple[str, str]:
    private = (
        Ed25519PrivateKey.generate()
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    return private, public_key_pem(private)


def _token(private_key: str) -> str:
    return issue_authority_token(
        private_key,
        audience=EGRESS_PROXY_AUDIENCE,
        task_id="acceptance-task",
        owner_id="acceptance-owner",
        project_id="acceptance-project",
        run_id=RUN_ID,
        capabilities=[Capability.NET_SHELL],
        egress_hosts=[UPSTREAM_HOST],
        ttl_seconds=900,
    )


async def _connect(
    proxy_port: int,
    token: str,
    upstream_port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    basic = base64.b64encode(f"loop:{token}".encode()).decode()
    target = f"{UPSTREAM_HOST}:{upstream_port}"
    writer.write(
        (
            f"CONNECT {target} HTTP/1.1\r\n"
            f"Host: {target}\r\n"
            f"Proxy-Authorization: Basic {basic}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    if b"200 Connection Established" not in response:
        raise RuntimeError(f"Proxy rejected acceptance tunnel: {response!r}")
    return reader, writer


async def _request(proxy_port: int, token: str, upstream_port: int) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    basic = base64.b64encode(f"loop:{token}".encode()).decode()
    target = f"http://{UPSTREAM_HOST}:{upstream_port}/proof"
    writer.write(
        (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {UPSTREAM_HOST}:{upstream_port}\r\n"
            f"Proxy-Authorization: Basic {basic}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    await writer.wait_closed()
    return response


async def _hold_open(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.read()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _proxy_child(args: argparse.Namespace) -> None:
    settings = EgressProxySettings(
        authority_public_key=args.public_key,
        allowed_ports=str(args.upstream_port),
    )
    audit = AuditStore(redis_url=args.redis_url, namespace=args.namespace)
    revocations = AuthorityRevocationStore(
        redis_url=args.redis_url,
        namespace=f"{args.namespace}:authority",
    )

    async def resolver(host: str, port: int) -> tuple[int, str]:
        if (host, port) != (UPSTREAM_HOST, args.upstream_port):
            raise RuntimeError(f"Unexpected acceptance destination {host}:{port}")
        return socket.AF_INET, "127.0.0.1"

    proxy = EgressProxy(settings, audit, resolver=resolver, revocations=revocations)
    await audit.ready()
    await revocations.ready()
    await revocations.subscribe(proxy.revoke)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    print(json.dumps({"proxy_port": port}), flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await revocations.close()
        await audit.close()


async def _read_child_ready(process: asyncio.subprocess.Process) -> int:
    if process.stdout is None:
        raise RuntimeError("Acceptance proxy child has no stdout")
    line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    if not line:
        stderr = await process.stderr.read() if process.stderr is not None else b""
        raise RuntimeError(
            f"Acceptance proxy child exited before ready: {stderr.decode(errors='replace')}"
        )
    payload = json.loads(line)
    return int(payload["proxy_port"])


async def _stop_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _delete_acceptance_state(redis_url: str, namespace: str) -> None:
    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.delete(
            f"{namespace}:audit",
            f"{namespace}:authority:revocations",
        )
    finally:
        await client.aclose()


async def _exercise(redis_url: str, namespace: str) -> None:
    await _delete_acceptance_state(redis_url, namespace)
    private_key, public_key = _keypair()
    token = _token(private_key)
    upstream = await asyncio.start_server(_hold_open, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        SCRIPT_PATH,
        "_proxy-child",
        "--redis-url",
        redis_url,
        "--namespace",
        namespace,
        "--public-key",
        public_key,
        "--upstream-port",
        str(upstream_port),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    revocations = AuthorityRevocationStore(
        redis_url=redis_url,
        namespace=f"{namespace}:authority",
    )
    audit = AuditStore(redis_url=redis_url, namespace=namespace)
    writer: asyncio.StreamWriter | None = None
    try:
        proxy_port = await _read_child_ready(child)
        reader, writer = await _connect(proxy_port, token, upstream_port)
        await revocations.revoke(RUN_ID, datetime.now(UTC) + timedelta(minutes=15))
        if await asyncio.wait_for(reader.read(), timeout=3) != b"":
            raise RuntimeError("Cross-process revocation did not close the active tunnel")
        blocked = await _request(proxy_port, token, upstream_port)
        if b"403 Forbidden" not in blocked or b"revoked" not in blocked:
            raise RuntimeError(f"Revoked run was not rejected: {blocked!r}")
        events = await audit.list(RUN_ID)
        if not any(
            event.get("decision") == "allowed" and event.get("method") == "CONNECT"
            for event in events
        ):
            raise RuntimeError("Shared audit did not receive the child proxy CONNECT event")
        print(
            json.dumps(
                {
                    "status": "exercised",
                    "run_id": RUN_ID,
                    "cross_process_disconnect": True,
                    "revoked_request_blocked": True,
                    "audit_events": len(events),
                },
                sort_keys=True,
            )
        )
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        await _stop_child(child)
        upstream.close()
        await upstream.wait_closed()
        await revocations.close()
        await audit.close()


async def _verify_recovery(redis_url: str, namespace: str) -> None:
    revocations = AuthorityRevocationStore(
        redis_url=redis_url,
        namespace=f"{namespace}:authority",
    )
    audit = AuditStore(redis_url=redis_url, namespace=namespace)
    try:
        await revocations.ready()
        await audit.ready()
        if not await revocations.is_revoked(RUN_ID):
            raise RuntimeError("Run revocation did not survive the Redis restart")
        events = await audit.list(RUN_ID)
        if not any(
            event.get("decision") == "allowed" and event.get("method") == "CONNECT"
            for event in events
        ):
            raise RuntimeError("Proxy audit did not survive the Redis restart")
        print(
            json.dumps(
                {
                    "status": "recovered",
                    "run_id": RUN_ID,
                    "revocation_persisted": True,
                    "audit_persisted": True,
                },
                sort_keys=True,
            )
        )
    finally:
        await revocations.close()
        await audit.close()
    await _delete_acceptance_state(redis_url, namespace)


async def _verify_unavailable(redis_url: str, namespace: str) -> None:
    provider = create_app(
        ProviderGatewaySettings(
            browser_enabled=False,
            state_redis_url=redis_url,
            state_namespace=f"{namespace}:provider",
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=provider),
        base_url="http://provider",
    ) as client:
        provider_ready = await client.get("/readyz")
    await provider.state.revocations.close()

    audit = AuditStore(redis_url=redis_url, namespace=namespace)
    revocations = AuthorityRevocationStore(
        redis_url=redis_url,
        namespace=f"{namespace}:authority",
    )

    async def revoke_connections(_run_id: str) -> None:
        return None

    admin = create_admin_app(
        EgressProxySettings(),
        audit,
        revocations,
        revoke_connections,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin),
            base_url="http://egress-admin",
        ) as client:
            proxy_ready = await client.get("/readyz")
    finally:
        await revocations.close()
        await audit.close()
    if provider_ready.status_code != 503 or proxy_ready.status_code != 503:
        raise RuntimeError(
            "Enforcement readiness did not fail closed while Redis was unavailable: "
            f"provider={provider_ready.status_code}, proxy={proxy_ready.status_code}"
        )
    print(
        json.dumps(
            {
                "status": "unavailable",
                "provider_readiness": provider_ready.status_code,
                "proxy_readiness": proxy_ready.status_code,
            },
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Exercise Loop's shared enforcement state")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("exercise", "verify-recovery", "verify-unavailable"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--redis-url", required=True)
        subparser.add_argument("--namespace", required=True)
    child = subparsers.add_parser("_proxy-child")
    child.add_argument("--redis-url", required=True)
    child.add_argument("--namespace", required=True)
    child.add_argument("--public-key", required=True)
    child.add_argument("--upstream-port", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "_proxy-child":
        asyncio.run(_proxy_child(args))
    elif args.command == "exercise":
        asyncio.run(_exercise(args.redis_url, args.namespace))
    elif args.command == "verify-recovery":
        asyncio.run(_verify_recovery(args.redis_url, args.namespace))
    else:
        asyncio.run(_verify_unavailable(args.redis_url, args.namespace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
