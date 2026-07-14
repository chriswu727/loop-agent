from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.domain.authority_token import AUTHORITY_CONTROL_AUDIENCE, EGRESS_PROXY_AUDIENCE

ProxyResolver = Callable[[str, int], Awaitable[str]]


def _proxy_parts(proxy_url: str) -> tuple[str, str, int]:
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
        raise ValueError("Egress proxy URL must be http://host:port")
    return parsed.scheme, parsed.hostname, parsed.port or 80


def authenticated_proxy_url(proxy_url: str, token: str) -> str:
    scheme, hostname, port_number = _proxy_parts(proxy_url)
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = f":{port_number}"
    netloc = f"loop:{quote(token, safe='')}@{host}{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


async def resolve_proxy_endpoint(
    proxy_url: str,
    *,
    resolver: ProxyResolver | None = None,
) -> str:
    scheme, hostname, port = _proxy_parts(proxy_url)
    try:
        address = ipaddress.ip_address(hostname).compressed
    except ValueError:
        if resolver is None:
            loop = asyncio.get_running_loop()
            records = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            if not records:
                raise OSError(f"Egress proxy {hostname!r} did not resolve") from None
            address = ipaddress.ip_address(str(records[0][4][0])).compressed
        else:
            address = ipaddress.ip_address(str(await resolver(hostname, port))).compressed
    host = f"[{address}]" if ":" in address else address
    return urlunsplit((scheme, f"{host}:{port}", "", "", ""))


class EgressAuditClient:
    def __init__(self, base_url: str, token_factory: Callable[[str], str]) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_factory = token_factory
        self._seen: set[str] = set()

    async def fetch_new(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.base_url}/v1/audit",
                headers={"Authorization": f"Bearer {self.token_factory(EGRESS_PROXY_AUDIENCE)}"},
            )
        response.raise_for_status()
        raw_events = response.json().get("events", [])
        events: list[dict[str, Any]] = []
        for event in raw_events if isinstance(raw_events, list) else []:
            if not isinstance(event, dict):
                continue
            event_id = event.get("id")
            if not isinstance(event_id, str) or event_id in self._seen:
                continue
            self._seen.add(event_id)
            events.append(event)
        return events

    async def revoke(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self.base_url}/v1/revocations",
                headers={
                    "Authorization": f"Bearer {self.token_factory(AUTHORITY_CONTROL_AUDIENCE)}"
                },
            )
        body = response.json()
        response.raise_for_status()
        audit = body.get("audit")
        return audit if isinstance(audit, dict) else {}
