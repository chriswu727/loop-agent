"""Reusable FastAPI dependencies for v1.

Dependencies are how we inject the request-scoped session, build services, do
auth, and rate-limit — all without handlers knowing how any of it is wired.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, params
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import Cache
from app.core.security import decode_access_token
from app.db.session import get_session
from app.exceptions import RateLimitedError, UnauthorizedError
from app.repositories.step import StepRepository
from app.repositories.task import TaskRepository
from app.repositories.trigger import TriggerRepository
from app.services.task import TaskService
from app.services.trigger import TriggerService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_cache(request: Request) -> Cache:
    cache: Cache = request.app.state.cache
    return cache


CacheDep = Annotated[Cache, Depends(get_cache)]


def get_task_service(session: SessionDep, request: Request) -> TaskService:
    subject = str(getattr(request.state, "subject", "local"))
    return TaskService(TaskRepository(session), StepRepository(session), subject=subject)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


def get_trigger_service(session: SessionDep, request: Request) -> TriggerService:
    subject = str(getattr(request.state, "subject", "local"))
    return TriggerService(
        TriggerRepository(session),
        TaskService(TaskRepository(session), StepRepository(session), subject=subject),
    )


TriggerServiceDep = Annotated[TriggerService, Depends(get_trigger_service)]


# --- Bearer subject dependency ----------------------------------------------
_bearer = HTTPBearer(auto_error=False)


async def get_current_subject(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Verify a bearer token and return its subject. Raises 401 if invalid.

    Apply with ``Depends(get_current_subject)`` to protect a route. Returns the
    verified ``sub`` claim.
    """
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as exc:  # normalise all JWT errors to 401
        raise UnauthorizedError("Invalid or expired token") from exc
    return str(payload.get("sub", ""))


CurrentSubject = Annotated[str, Depends(get_current_subject)]


# --- Simple Redis-backed rate limiter --------------------------------------
def rate_limit(*, limit: int = 60, window_seconds: int = 60) -> params.Depends:
    """Dependency factory: at most ``limit`` requests per ``window`` per client.

    Returns a ready Depends, so use it directly:
    ``dependencies=[rate_limit(limit=10, window_seconds=60)]``.
    """

    async def _dependency(request: Request, cache: CacheDep) -> None:
        client = request.client.host if request.client else "anonymous"
        key = f"ratelimit:{request.url.path}:{client}"
        count = await cache.incr(key, ttl_seconds=window_seconds)
        if count > limit:
            raise RateLimitedError(f"Rate limit exceeded: {limit}/{window_seconds}s")

    dep: params.Depends = Depends(_dependency)
    return dep
