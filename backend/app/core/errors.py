"""FastAPI exception handlers for the application error hierarchy.

The error classes themselves live in :mod:`app.core.exceptions`, which imports
no web framework, so workers and the analytics engine can raise them without
depending on FastAPI. They are re-exported here for backwards compatibility —
``from app.core.errors import NotFoundError`` keeps working.

Internal failure detail is logged, never returned: an unexpected exception
produces a generic 500 body so stack traces, SQL and connection strings cannot
leak to a client. The one thing that body does name is the exception *class*,
which is source code rather than data — see :func:`unhandled_exception_handler`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    DependencyUnavailableError,
    InsufficientDataError,
    JobExecutionError,
    NotFoundError,
    RateLimitError,
    TenantIsolationError,
    ValidationError,
    WebhookVerificationError,
)
from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

__all__ = [
    "AppError",
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "ConflictError",
    "DependencyUnavailableError",
    "InsufficientDataError",
    "JobExecutionError",
    "NotFoundError",
    "RateLimitError",
    "TenantIsolationError",
    "ValidationError",
    "WebhookVerificationError",
    "register_exception_handlers",
]


def _generic_payload(code: str, message: str) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}

    request_id = request_id_var.get()
    if request_id:
        error["request_id"] = request_id

    return {"error": error}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    log = logger.warning if exc.status_code < 500 else logger.error
    log(
        "Handled application error",
        extra={
            "error_code": exc.code,
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method,
        },
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Normalise plain ``HTTPException`` into the same envelope.

    Phase 1 routes raise ``HTTPException`` directly; this keeps their responses
    shaped like the Phase 2 ones without having to rewrite them all.
    """
    code_by_status = {
        status.HTTP_400_BAD_REQUEST: "bad_request",
        status.HTTP_401_UNAUTHORIZED: "unauthenticated",
        status.HTTP_403_FORBIDDEN: "forbidden",
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_409_CONFLICT: "conflict",
        status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
        status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
        status.HTTP_503_SERVICE_UNAVAILABLE: "dependency_unavailable",
    }
    code = code_by_status.get(exc.status_code, "http_error")
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"

    payload = _generic_payload(code, detail)
    headers = getattr(exc, "headers", None)

    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return field-level validation problems without echoing submitted values.

    ``exc.errors()`` includes an ``input`` key holding the offending value,
    which may be a credential on an auth-ish endpoint. Only the location and
    the reason are returned.
    """
    fields = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
            "reason": error.get("msg", "invalid"),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]

    logger.warning(
        "Request validation failed",
        extra={
            "path": request.url.path,
            "method": request.method,
            "invalid_field_count": len(fields),
        },
    )

    payload = _generic_payload("validation_error", "Request payload failed validation")
    payload["error"]["details"] = {"fields": fields}

    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort. Log with traceback, return a 500 that can be acted on.

    The body stays free of internal detail — no traceback, no SQL, no connection
    string — but it does name the exception *class* and echo the request id.

    That distinction is the whole point. ``str(exc)`` is data: a driver error
    embeds the statement and its bound parameters, which for this application
    means customer rows. ``type(exc).__name__`` is source code, written by a
    developer and identical for every tenant, so it carries no data at all. It is
    the same reasoning ``_classify_connection_error`` already uses.

    Why it matters here: a customers import failed in the UI with nothing but
    "An unexpected error occurred", which is true and useless. It gave no way to
    tell a serialization failure from a dead database from a bad row, so the only
    route to the cause was reading container logs. ``"type": "ValueError"`` plus a
    request id to grep for turns that into one lookup, and the operator still has
    to open the logs to see the traceback.
    """
    logger.exception(
        "Unhandled exception",
        extra={
            "path": request.url.path,
            "method": request.method,
            "exception_type": type(exc).__name__,
        },
    )

    payload = _generic_payload("internal_error", "An unexpected error occurred")
    payload["error"]["type"] = type(exc).__name__

    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
