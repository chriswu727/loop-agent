"""Request-ID + access-log middleware.

Assigns (or honours an inbound) ``X-Request-ID``, binds it to the logging
context so every log line for the request is correlated, times the request, and
emits one structured access log per request. Returns the id to the client so it
can be quoted in bug reports.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_request_id, clear_request_context, get_logger

REQUEST_ID_HEADER = "X-Request-ID"
log = get_logger("http.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        bind_request_id(request_id)
        request.state.request_id = request_id

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            log.exception(
                "request.failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            log.info(
                "request.handled",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            clear_request_context()
