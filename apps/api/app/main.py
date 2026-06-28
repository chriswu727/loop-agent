"""Application factory.

Assembles the FastAPI app: lifespan (startup/shutdown), middleware stack,
exception handlers, health probes, metrics, and the versioned API router.
Keeping construction in a factory makes the app trivially importable by tests
and by alternative entrypoints (e.g. the worker) without side effects at import.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.api.v1.routes import health
from app.core.config import settings
from app.core.lifespan import lifespan
from app.middleware.request_id import RequestContextMiddleware
from app.observability.metrics import MetricsMiddleware, metrics_endpoint


def create_app() -> FastAPI:
    app = FastAPI(
        title="Scalable Starter API",
        version=settings.version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # --- Middleware (outermost first) ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # --- Error handling ---
    register_exception_handlers(app)

    # --- Operational endpoints (root, unversioned) ---
    app.include_router(health.router)
    if settings.prometheus_enabled:
        app.add_route("/metrics", lambda _request: metrics_endpoint(), include_in_schema=False)

    # --- Business API (versioned) ---
    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
