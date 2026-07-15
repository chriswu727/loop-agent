from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from app.domain.authority_revocation import AuthorityRevocationStore
from app.domain.authority_token import (
    AUTHORITY_CONTROL_AUDIENCE,
    EGRESS_PROXY_AUDIENCE,
    AuthorityGrant,
    AuthorityTokenError,
    verify_authority_token,
)
from app.egress_proxy.audit import AuditStore
from app.egress_proxy.config import EgressProxySettings

log = logging.getLogger("loop.egress_proxy.admin")


def create_admin_app(
    settings: EgressProxySettings,
    audit: AuditStore,
    revocations: AuthorityRevocationStore,
    revoke_connections: Callable[[str], Awaitable[None]],
) -> FastAPI:
    app = FastAPI(title="Loop Egress Proxy Admin", version="1")

    async def grant(request: Request) -> AuthorityGrant:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        keys = settings.public_keyring()
        if scheme.lower() != "bearer" or not token or not keys:
            raise HTTPException(status_code=401, detail="A signed authority token is required")
        try:
            authority = verify_authority_token(token, keys, audience=EGRESS_PROXY_AUDIENCE)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if await revocations.is_revoked(authority.run_id):
            raise HTTPException(status_code=403, detail="Authority run has been revoked")
        return authority

    def control_grant(request: Request) -> AuthorityGrant:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        keys = settings.public_keyring()
        if scheme.lower() != "bearer" or not token or not keys:
            raise HTTPException(status_code=401, detail="A signed control token is required")
        try:
            return verify_authority_token(token, keys, audience=AUTHORITY_CONTROL_AUDIENCE)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "egress-proxy",
            "authority_key_configured": bool(settings.public_keyring()),
            "authority_key_count": len(settings.public_keyring()),
            "audit_durable": audit.durable,
            "audit_shared": audit.shared,
            "audit_backend": audit.backend,
            "revocations_durable": revocations.durable,
            "revocations_shared": revocations.shared,
            "revocation_backend": revocations.backend,
        }

    @app.get("/readyz")
    async def ready() -> dict[str, Any]:
        try:
            await audit.ready()
            await revocations.ready()
        except Exception as exc:
            log.warning("Egress proxy enforcement state is unavailable")
            raise HTTPException(
                status_code=503,
                detail="Shared enforcement state is unavailable",
            ) from exc
        return {
            "status": "ready",
            "audit_backend": audit.backend,
            "revocation_backend": revocations.backend,
        }

    @app.post("/v1/revocations")
    async def revoke(authority: AuthorityGrant = Depends(control_grant)) -> dict[str, Any]:
        await revocations.revoke(authority.run_id, authority.expires_at)
        await revoke_connections(authority.run_id)
        event = {
            "id": str(uuid.uuid4()),
            "at": datetime.now(UTC).isoformat(),
            "kind": "authority",
            "decision": "revoked",
            "task_id": authority.task_id,
            "owner_id": authority.owner_id,
            "project_id": authority.project_id,
            "run_id": authority.run_id,
            "service": "egress-proxy",
        }
        await audit.append(authority.run_id, event)
        return {"revoked": True, "audit": event}

    @app.get("/v1/audit")
    async def events(authority: AuthorityGrant = Depends(grant)) -> dict[str, Any]:
        return {"events": await audit.list(authority.run_id)}

    return app
