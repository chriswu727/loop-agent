from __future__ import annotations

import base64
import contextlib
from collections.abc import Callable
from typing import Any

import httpx

from app.domain.authority_token import (
    AUTHORITY_CONTROL_AUDIENCE,
    EGRESS_PROXY_AUDIENCE,
    PROVIDER_GATEWAY_AUDIENCE,
)
from app.domain.capability import Capability
from app.tools.vision import _mime_for
from app.tools.workspace import Workspace


class ProviderGatewayClient:
    capability = Capability.NET_BROWSER

    def __init__(
        self,
        base_url: str,
        workspace: Workspace,
        token_factory: Callable[[str], str],
        *,
        timeout_seconds: int = 65,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace
        self.token_factory = token_factory
        self.client = httpx.AsyncClient(timeout=timeout_seconds)
        self.tool_names: set[str] = set()
        self.tools: list[dict[str, str]] = []
        self._audit: list[dict[str, Any]] = []

    async def start(self) -> None:
        try:
            response = await self.client.get(
                f"{self.base_url}/v1/tools",
                headers={
                    "Authorization": f"Bearer {self.token_factory(PROVIDER_GATEWAY_AUDIENCE)}",
                    "X-Loop-Egress-Token": self.token_factory(EGRESS_PROXY_AUDIENCE),
                },
            )
            response.raise_for_status()
            body = response.json()
            raw_tools = body.get("tools", [])
            self.tools = [item for item in raw_tools if isinstance(item, dict)]
            self.tool_names = {
                str(item["name"]) for item in self.tools if isinstance(item.get("name"), str)
            }
        except Exception:
            await self.client.aclose()
            raise

    async def call(self, name: str, args: dict[str, Any]) -> str:
        payload = dict(args)
        if name == "see_image":
            payload = self._image_payload(args)
        response = await self.client.post(
            f"{self.base_url}/v1/tools/{name}",
            headers={
                "Authorization": f"Bearer {self.token_factory(PROVIDER_GATEWAY_AUDIENCE)}",
                "X-Loop-Egress-Token": self.token_factory(EGRESS_PROXY_AUDIENCE),
            },
            json={"args": payload},
        )
        body = response.json()
        audit = body.get("audit")
        if isinstance(audit, dict):
            self._audit.append(audit)
        response.raise_for_status()
        return str(body.get("result", ""))[:5000]

    async def stop(self) -> None:
        try:
            with contextlib.suppress(Exception):
                await self.client.delete(
                    f"{self.base_url}/v1/session",
                    headers={
                        "Authorization": f"Bearer {self.token_factory(PROVIDER_GATEWAY_AUDIENCE)}"
                    },
                )
        finally:
            await self.client.aclose()

    async def revoke(self) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/v1/revocations",
            headers={"Authorization": f"Bearer {self.token_factory(AUTHORITY_CONTROL_AUDIENCE)}"},
        )
        body = response.json()
        response.raise_for_status()
        audit = body.get("audit")
        return audit if isinstance(audit, dict) else {}

    def specs(self, capability_prefix: str) -> str:
        return "\n".join(
            f"- {item['name']}: {item.get('description', '')}"
            for item in self.tools
            if str(item.get("capability", "")).startswith(capability_prefix)
        )

    def drain_audit(self) -> list[dict[str, Any]]:
        events = self._audit
        self._audit = []
        return events

    def _image_payload(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        mime = _mime_for(path)
        if not path or mime is None:
            raise ValueError("see_image needs a png/jpg/gif/webp workspace path")
        target = self.workspace.resolve(path)
        data = target.read_bytes()
        if len(data) > 8_000_000:
            raise ValueError("Image is too large (over 8MB)")
        return {
            "image_base64": base64.b64encode(data).decode(),
            "mime": mime,
            "prompt": str(args.get("prompt", "")),
        }
