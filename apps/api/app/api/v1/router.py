"""Aggregates business resource routers under ``/api/v1``.

Register new resource routers here. Versioning lives in the path so you can ship
``/api/v2`` alongside v1 without breaking existing clients. (Health probes are
mounted at the application root, not here — see ``app/main.py``.)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.auth import require_api_token
from app.api.v1.routes import memory, skills, tasks, triggers

# One gate for the whole surface: with API_TOKEN set, every route needs it.
api_router = APIRouter(dependencies=[Depends(require_api_token)])
api_router.include_router(tasks.router)
api_router.include_router(memory.router)
api_router.include_router(skills.router)
api_router.include_router(triggers.router)
