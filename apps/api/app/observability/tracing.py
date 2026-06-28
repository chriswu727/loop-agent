"""OpenTelemetry tracing setup.

Auto-instruments FastAPI, SQLAlchemy, and Redis and exports spans over OTLP.
Entirely opt-in: if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, tracing is a no-op
so local dev and tests have zero overhead and no noisy connection errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)


def setup_tracing(app: FastAPI) -> None:
    if not settings.tracing_enabled:
        log.info("tracing.disabled")
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {"service.name": settings.service_name, "service.version": settings.version}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    log.info("tracing.enabled", endpoint=settings.otel_exporter_otlp_endpoint)
