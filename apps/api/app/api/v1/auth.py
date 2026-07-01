"""Optional bearer-token gate for the whole /api/v1 surface.

The agent can run shell commands, so an exposed, unauthenticated API is remote
code execution. When ``API_TOKEN`` is set every /api/v1 route requires
``Authorization: Bearer <token>``; when it is unset the API is open (only safe on
a trusted/loopback network, which is also why the server binds 127.0.0.1 by
default). Health probes are mounted outside /api/v1 and stay open.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    expected = settings.api_token
    if not expected:
        return  # auth disabled — open API (loopback-only by default)
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
