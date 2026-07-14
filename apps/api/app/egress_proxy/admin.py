from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from app.domain.authority_token import (
    EGRESS_PROXY_AUDIENCE,
    AuthorityGrant,
    AuthorityTokenError,
    verify_authority_token,
)
from app.egress_proxy.audit import AuditStore
from app.egress_proxy.config import EgressProxySettings


def create_admin_app(settings: EgressProxySettings, audit: AuditStore) -> FastAPI:
    app = FastAPI(title="Loop Egress Proxy Admin", version="1")

    def grant(request: Request) -> AuthorityGrant:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        key = settings.public_key_pem()
        if scheme.lower() != "bearer" or not token or not key:
            raise HTTPException(status_code=401, detail="A signed authority token is required")
        try:
            return verify_authority_token(token, key, audience=EGRESS_PROXY_AUDIENCE)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "egress-proxy",
            "authority_key_configured": bool(settings.public_key_pem()),
            "audit_durable": audit.durable,
        }

    @app.get("/v1/audit")
    async def events(authority: AuthorityGrant = Depends(grant)) -> dict[str, Any]:
        return {"events": await audit.list(authority.run_id)}

    return app
