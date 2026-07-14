"""Prometheus metrics (the RED method: Rate, Errors, Duration).

Exposes counters and a latency histogram, plus an ASGI middleware that records
every request and a ``/metrics`` endpoint factory. Labels use the *route
template* (e.g. ``/api/v1/items/{item_id}``) rather than the raw path to avoid
unbounded cardinality.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)
TASK_RUNS = Counter(
    "loop_task_runs_total",
    "Task runs by terminal outcome",
    ["status", "stop_reason"],
)
TASK_RUN_DURATION = Histogram(
    "loop_task_run_duration_seconds",
    "End-to-end task execution duration",
)
TOOL_DENIALS = Counter(
    "loop_tool_denials_total",
    "Tool calls denied by the capability envelope",
    ["tool"],
)
QUEUE_JOBS = Counter(
    "loop_queue_jobs_total",
    "Durable queue jobs by outcome",
    ["outcome"],
)
RECEIPT_REPLAYS = Counter(
    "loop_receipt_replays_total",
    "Receipt replay attempts by outcome",
    ["outcome"],
)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        path = _route_template(request)
        REQUEST_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        return response


def metrics_endpoint() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
