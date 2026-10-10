"""Application error hierarchy.

Kept free of any FastAPI import so that services, the analytics engine, the
cache and the event workers can raise these without pulling the web framework
in. :mod:`app.core.errors` re-exports everything here and adds the HTTP
handlers.

Every error carries a stable machine-readable ``code`` so the frontend can
branch on behaviour without string-matching prose.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import request_id_var

HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_409_CONFLICT = 409
HTTP_422_UNPROCESSABLE_ENTITY = 422
HTTP_429_TOO_MANY_REQUESTS = 429
HTTP_500_INTERNAL_SERVER_ERROR = 500
HTTP_503_SERVICE_UNAVAILABLE = 503


class AppError(Exception):
    """Base class for expected, client-facing application errors."""

    status_code: int = HTTP_400_BAD_REQUEST
    code: str = "app_error"
    message: str = "Request could not be completed"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.code = code or self.__class__.code
        self.status_code = status_code or self.__class__.status_code
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}

        if self.details:
            error["details"] = self.details

        request_id = request_id_var.get()
        if request_id:
            error["request_id"] = request_id

        return {"error": error}


class ValidationError(AppError):
    status_code = HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "Request payload failed validation"


class NotFoundError(AppError):
    status_code = HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = HTTP_409_CONFLICT
    code = "conflict"
    message = "Resource conflicts with existing state"


class AuthenticationError(AppError):
    status_code = HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication required"


class AuthorizationError(AppError):
    """Raised when an authenticated caller touches another tenant's data."""

    status_code = HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "Not permitted for this business"


class TenantIsolationError(AuthorizationError):
    code = "tenant_isolation_violation"
    message = "Resource does not belong to the authenticated business"


class WebhookVerificationError(AppError):
    status_code = HTTP_401_UNAUTHORIZED
    code = "webhook_verification_failed"
    message = "Webhook signature verification failed"


class RateLimitError(AppError):
    status_code = HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests"


class DependencyUnavailableError(AppError):
    """A backing dependency (Kafka, Redis, database) is unreachable."""

    status_code = HTTP_503_SERVICE_UNAVAILABLE
    code = "dependency_unavailable"
    message = "A required dependency is currently unavailable"


class ConfigurationError(AppError):
    status_code = HTTP_500_INTERNAL_SERVER_ERROR
    code = "configuration_error"
    message = "Server is not configured correctly"


class JobExecutionError(AppError):
    status_code = HTTP_500_INTERNAL_SERVER_ERROR
    code = "job_execution_failed"
    message = "Analytics job failed"


class InsufficientDataError(AppError):
    """Not enough real data to produce an honest analytics result.

    Raised instead of returning fabricated or placeholder numbers.
    """

    status_code = HTTP_422_UNPROCESSABLE_ENTITY
    code = "insufficient_data"
    message = "Not enough data to compute this analysis"


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
]
