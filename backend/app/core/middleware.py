"""HTTP middleware: request correlation IDs and access logging.

The middleware is deliberately thin. It assigns/propagates a request ID, binds
it into the logging context so every downstream log line carries it, and emits
one structured access line per request.

It never logs the ``Authorization`` header, cookies, query strings or request
bodies — any of those can carry a JWT or customer data.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, logging_context, new_request_id

logger = get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"

#: Paths that should not produce an access log line on every hit. Container
#: orchestrators poll these once a second and would otherwise drown the logs.
_QUIET_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/favicon.ico"})

#: A request ID from a client is only trusted if it looks like an ID. Prevents
#: log injection via newline/control characters in a header.
_MAX_INBOUND_REQUEST_ID_LENGTH = 128


def _sanitize_inbound_request_id(raw_value: str | None) -> str | None:
    if not raw_value:
        return None

    candidate = raw_value.strip()

    if not candidate or len(candidate) > _MAX_INBOUND_REQUEST_ID_LENGTH:
        return None

    if not all(char.isalnum() or char in {"-", "_", ":", "."} for char in candidate):
        return None

    return candidate


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to the logging context and the response headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _sanitize_inbound_request_id(
            request.headers.get(REQUEST_ID_HEADER)
        ) or new_request_id()

        request.state.request_id = request_id
        started_at = time.perf_counter()

        with logging_context(request_id=request_id):
            try:
                response = await call_next(request)
            except Exception:
                # The exception handlers produce the client response; this line
                # exists so the access log records the failed request too.
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                logger.exception(
                    "Request failed",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "duration_ms": duration_ms,
                    },
                )
                raise

            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id

            if request.url.path not in _QUIET_PATHS:
                logger.info(
                    "Request completed",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )

            return response
