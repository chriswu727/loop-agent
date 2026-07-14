"""Authenticate the /api/v1 surface with a service token or a scoped user JWT."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings
from app.core.security import decode_access_token


async def require_api_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    expected = settings.api_token
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if expected and provided and hmac.compare_digest(provided, expected):
        request.state.subject = "service:api-token"
        return
    if provided:
        try:
            payload = decode_access_token(provided)
            subject = str(payload.get("sub", "")).strip()
            if subject:
                request.state.subject = subject
                return
        except Exception:
            pass
    if not expected and not settings.auth_required:
        request.state.subject = "local"
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
