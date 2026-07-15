from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain.authority_revocation import AuthorityRevocationStore
from app.domain.authority_token import (
    AUTHORITY_CONTROL_AUDIENCE,
    EGRESS_PROXY_AUDIENCE,
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
    revocations = AuthorityRevocationStore(
        config.revocation_database_path,
        redis_url=config.resolved_state_redis_url(),
        namespace=config.resolved_state_namespace(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        keys = config.public_keyring()
        if config.require_authority_key and not keys:
            raise RuntimeError("Provider gateway requires an Ed25519 authority verification key")
        if config.require_durable_revocations and not revocations.durable:
            raise RuntimeError("Provider gateway requires durable authority revocations")
        if config.require_shared_state and not revocations.shared:
            raise RuntimeError("Provider gateway requires shared enforcement state")
        for key in keys.values():
            validate_authority_public_key(key)
        await revocations.ready()
        await revocations.subscribe(runtime.close)
        try:
            yield
        finally:
            await runtime.close_all()
            await revocations.close()

    application = FastAPI(title="Loop Provider Gateway", version="1", lifespan=lifespan)
    application.state.runtime = runtime
    application.state.revocations = revocations
    application.state.settings = config

    async def grant(request: Request) -> AuthorityGrant:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        keys = config.public_keyring()
        if scheme.lower() != "bearer" or not token or not keys:
            raise HTTPException(status_code=401, detail="A signed authority token is required")
        try:
            authority = verify_authority_token(token, keys, audience=config.authority_audience)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if await revocations.is_revoked(authority.run_id):
            raise HTTPException(status_code=403, detail="Authority run has been revoked")
        return authority

    def control_grant(request: Request) -> AuthorityGrant:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        keys = config.public_keyring()
        if scheme.lower() != "bearer" or not token or not keys:
            raise HTTPException(status_code=401, detail="A signed control token is required")
        try:
            return verify_authority_token(token, keys, audience=AUTHORITY_CONTROL_AUDIENCE)
        except AuthorityTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def verified_egress_token(authority: AuthorityGrant, token: str | None) -> str | None:
        if not token:
            return None
        keys = config.public_keyring()
        if not keys:
            raise AuthorityTokenError("Authority verification key is unavailable")
        proxy_grant = verify_authority_token(token, keys, audience=EGRESS_PROXY_AUDIENCE)
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
            "service": config.service_name,
            "authority_key_configured": bool(config.public_keyring()),
            "authority_key_count": len(config.public_keyring()),
            "revocations_durable": revocations.durable,
            "revocations_shared": revocations.shared,
            "revocation_backend": revocations.backend,
            "providers": runtime.configured_providers(),
        }

    @application.get("/readyz")
    async def ready() -> dict[str, Any]:
        try:
            await revocations.ready()
        except Exception as exc:
            log.warning("Provider gateway enforcement state is unavailable")
            raise HTTPException(
                status_code=503,
                detail="Shared enforcement state is unavailable",
            ) from exc
        return {"status": "ready", "revocation_backend": revocations.backend}

    @application.post("/v1/revocations")
    async def revoke(authority: AuthorityGrant = Depends(control_grant)) -> dict[str, Any]:
        await revocations.revoke(authority.run_id, authority.expires_at)
        await runtime.close(authority.run_id)
        return {
            "revoked": True,
            "audit": {
                "id": str(uuid.uuid4()),
                "at": datetime.now(UTC).isoformat(),
                "kind": "authority",
                "decision": "revoked",
                "task_id": authority.task_id,
                "owner_id": authority.owner_id,
                "project_id": authority.project_id,
                "run_id": authority.run_id,
                "service": config.service_name,
            },
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
        x_loop_egress_token: str | None = Header(default=None),
    ) -> Any:
        try:
            egress_token = verified_egress_token(authority, x_loop_egress_token)
            result, audit = await runtime.invoke(
                authority, tool, payload.args, egress_token=egress_token
            )
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
