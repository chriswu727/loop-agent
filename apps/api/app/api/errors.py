"""Exception handlers: turn exceptions into RFC 9457 problem+json responses.

This is the *only* place that maps errors to HTTP. Services raise domain
exceptions (``app/exceptions.py``); handlers here render them consistently so
every client, in every situation, gets the same predictable error shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.exceptions import AppError
from app.schemas.common import Problem

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)
PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem_response(problem: Problem) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(),
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        problem = Problem(
            title=exc.title,
            status=exc.status_code,
            detail=exc.detail,
            code=exc.code,
            request_id=_request_id(request),
        )
        return _problem_response(problem)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        problem = Problem(
            title="Validation Failed",
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="One or more fields are invalid.",
            code="validation_error",
            request_id=_request_id(request),
        )
        body = problem.model_dump()
        body["errors"] = exc.errors()  # field-level detail for clients
        return JSONResponse(status_code=problem.status, content=body, media_type=PROBLEM_MEDIA_TYPE)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals. Log the real cause, return an opaque 500.
        log.exception("unhandled_exception", path=request.url.path)
        problem = Problem(
            title="Internal Server Error",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred.",
            code="internal_error",
            request_id=_request_id(request),
        )
        return _problem_response(problem)
