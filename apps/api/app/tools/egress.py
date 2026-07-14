from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.domain.authority_token import EGRESS_PROXY_AUDIENCE


def authenticated_proxy_url(proxy_url: str, token: str) -> str:
    parsed = urlsplit(proxy_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("Egress proxy URL must be http://host:port")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"loop:{quote(token, safe='')}@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


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
