"""Health probes. Liveness is cheap; readiness checks dependencies.

Kubernetes uses these: ``/healthz`` to decide whether to restart a wedged pod,
``/readyz`` to decide whether to route traffic to it.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.v1.deps import CacheDep, SessionDep
from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Is the process alive? No dependency checks — must stay fast and cheap."""
    return {"status": "ok", "service": settings.service_name, "version": settings.version}


@router.get("/readyz", summary="Readiness probe")
async def readyz(session: SessionDep, cache: CacheDep) -> dict[str, object]:
    """Can the process serve traffic? Verifies database and cache connectivity."""
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # degraded if the DB is unreachable
        checks["database"] = "error"

    try:
        await cache.set("readyz:ping", "1", ttl_seconds=5)
        checks["cache"] = "ok"
    except Exception:  # degraded if the cache is unreachable
        checks["cache"] = "error"

    ready = all(v == "ok" for v in checks.values())
    return {"status": "ready" if ready else "not_ready", "checks": checks}
