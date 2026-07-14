from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain.authority_token import (
    EGRESS_PROXY_AUDIENCE,
    PROVIDER_GATEWAY_AUDIENCE,
    AuthorityGrant,
    AuthorityTokenError,
    validate_authority_public_key,
    verify_authority_token,
)
from app.provider_gateway.config import ProviderGatewaySettings
from app.provider_gateway.runtime import ProviderGatewayRuntime

log = logging.getLogger("loop.provider_gateway")


class InvokeRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


def create_app(settings: ProviderGatewaySettings | None = None) -> FastAPI:
    config = settings or ProviderGatewaySettings()
    runtime = ProviderGatewayRuntime(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        key = config.public_key_pem()
        if config.require_authority_key and not key:
            raise RuntimeError("Provider gateway requires an Ed25519 authority verification key")
        if key:
            validate_authority_public_key(key)
        yield
        await runtime.close_all()

    application = FastAPI(title="Loop Provider Gateway", version="1", lifespan=lifespan)
    application.state.runtime = runtime
    application.state.settings = config

    def grant(request: Request) -> AuthorityGrant:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        key = config.public_key_pem()
        if scheme.lower() != "bearer" or not token or not key:
            raise HTTPException(status_code=401, detail="A signed authority token is required")
        try:
            return verify_authority_token(token, key, audience=PROVIDER_GATEWAY_AUDIENCE)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def verified_egress_token(authority: AuthorityGrant, token: str | None) -> str | None:
        if not token:
            return None
        key = config.public_key_pem()
        if not key:
            raise AuthorityTokenError("Authority verification key is unavailable")
        proxy_grant = verify_authority_token(token, key, audience=EGRESS_PROXY_AUDIENCE)
        same_identity = (
            proxy_grant.task_id,
            proxy_grant.owner_id,
            proxy_grant.project_id,
            proxy_grant.run_id,
        ) == (
            authority.task_id,
            authority.owner_id,
            authority.project_id,
            authority.run_id,
        )
        if (
            not same_identity
            or proxy_grant.capabilities != authority.capabilities
            or proxy_grant.egress_hosts != authority.egress_hosts
        ):
            raise AuthorityTokenError("Provider and egress authority grants do not match")
        return token

    @application.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "provider-gateway",
            "authority_key_configured": bool(config.public_key_pem()),
            "providers": runtime.configured_providers(),
        }

    @application.get("/v1/tools")
    async def tools(
        authority: AuthorityGrant = Depends(grant),
        x_loop_egress_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            egress_token = verified_egress_token(authority, x_loop_egress_token)
            inventory = await runtime.tools(authority, egress_token=egress_token)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"tools": inventory}

    @application.post("/v1/tools/{tool}")
    async def invoke(
        tool: str,
        payload: InvokeRequest,
        authority: AuthorityGrant = Depends(grant),
    ) -> Any:
        try:
            result, audit = await runtime.invoke(authority, tool, payload.args)
        except AuthorityTokenError as exc:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": str(exc),
                    "audit": runtime.audit_event(
                        authority, tool, payload.args, decision="blocked", reason=str(exc)
                    ),
                },
            )
        except Exception as exc:
            log.error("Provider tool call failed: %s", type(exc).__name__)
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "Provider call failed",
                    "audit": runtime.audit_event(
                        authority,
                        tool,
                        payload.args,
                        decision="unavailable",
                        reason=type(exc).__name__,
                    ),
                },
            )
        return {"result": result, "audit": audit}

    @application.delete("/v1/session")
    async def close(authority: AuthorityGrant = Depends(grant)) -> dict[str, bool]:
        await runtime.close(authority.run_id)
        return {"closed": True}

    return application


app = create_app()
