"""Application exception hierarchy.

Services raise these *domain-meaningful* errors; the transport layer
(``app/api/errors.py``) is the only place that knows how to turn them into HTTP
responses. This keeps services free of HTTP concerns and gives clients a stable,
typed error contract (RFC 9457 problem+json).
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected application errors.

    Attributes map onto an RFC 9457 problem document.
    """

    status_code: int = 500
    title: str = "Internal Server Error"
    code: str = "internal_error"

    def __init__(self, detail: str | None = None, *, extra: dict[str, Any] | None = None) -> None:
        self.detail = detail or self.title
        self.extra = extra or {}
        super().__init__(self.detail)


class NotFoundError(AppError):
    status_code = 404
    title = "Resource Not Found"
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    title = "Conflict"
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    title = "Validation Failed"
    code = "validation_error"


class UnauthorizedError(AppError):
    status_code = 401
    title = "Unauthorized"
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    title = "Forbidden"
    code = "forbidden"


class RateLimitedError(AppError):
    status_code = 429
    title = "Too Many Requests"
    code = "rate_limited"
