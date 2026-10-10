"""Structured logging for the platform.

Design notes
------------
* Stdlib only. No dependency on FastAPI, pydantic or a logging library, so the
  formatter and the redaction rules are unit-testable in any environment.
* Correlation IDs live in :mod:`contextvars`, so they propagate through async
  call stacks (request handler -> service -> repository) and into Kafka
  consumers without being threaded through every function signature.
* Secrets are never logged. :func:`redact` scrubs known-sensitive keys, and the
  formatter runs it over every ``extra`` payload as a second line of defence.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Correlation context
# ---------------------------------------------------------------------------

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
business_id_var: ContextVar[str | None] = ContextVar("business_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
event_id_var: ContextVar[str | None] = ContextVar("event_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

_CONTEXT_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": request_id_var,
    "business_id": business_id_var,
    "user_id": user_id_var,
    "event_id": event_id_var,
    "job_id": job_id_var,
}


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_context() -> dict[str, str]:
    """Snapshot of the correlation IDs that are currently set."""
    snapshot: dict[str, str] = {}

    for name, holder in _CONTEXT_VARS.items():
        value = holder.get()
        if value:
            snapshot[name] = value

    return snapshot


@contextmanager
def logging_context(**values: str | None) -> Iterator[None]:
    """Bind correlation IDs for the duration of a block.

    Unknown keys are ignored rather than raising, so callers can pass a loose
    dict without defensive filtering.
    """
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []

    for name, value in values.items():
        holder = _CONTEXT_VARS.get(name)
        if holder is None or value is None:
            continue
        tokens.append((holder, holder.set(value)))

    try:
        yield
    finally:
        for holder, token in reversed(tokens):
            holder.reset(token)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

#: Substrings that mark a mapping key as sensitive. Matched case-insensitively
#: against the key, so ``CLERK_SECRET_KEY``, ``db_password`` and
#: ``authorization`` are all caught.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "signature",
    "session_id",
    "cookie",
    "jwt",
    "bearer",
    "dsn",
    "database_url",
    "connection_string",
)

REDACTED = "***redacted***"

_MAX_REDACT_DEPTH = 6

#: Matches ``scheme://user:password@host`` so a connection string that slips
#: into a free-text message still loses its password.
_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/@\s]+):(?P<secret>[^@/\s]+)@")

#: Matches ``Bearer <token>`` in free text.
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/=]{8,}")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_text(value: str) -> str:
    """Remove credentials embedded in a free-text string."""
    scrubbed = _URL_CREDENTIALS_RE.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@",
        value,
    )
    return _BEARER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", scrubbed)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively replace sensitive values with :data:`REDACTED`.

    Keys are matched by name; string values are additionally scrubbed for
    embedded credentials. Depth is bounded so a cyclic or pathological
    structure cannot hang the logger.
    """
    if _depth >= _MAX_REDACT_DEPTH:
        return "***truncated***"

    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                result[key] = REDACTED
            else:
                result[key] = redact(item, _depth + 1)
        return result

    if isinstance(value, (list, tuple, set)):
        return [redact(item, _depth + 1) for item in value]

    if isinstance(value, str):
        return scrub_text(value)

    return value


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

#: Attributes present on every ``LogRecord``; anything else was passed as an
#: ``extra`` and belongs in the structured payload.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_")
    }


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON with correlation IDs attached."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib name
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_text(record.getMessage()),
        }

        payload.update(current_context())

        extras = _record_extras(record)
        if extras:
            payload.update(redact(extras))

        if record.exc_info:
            payload["exception"] = scrub_text(self.formatException(record.exc_info))

        if record.stack_info:
            payload["stack"] = scrub_text(self.formatStack(record.stack_info))

        try:
            return json.dumps(payload, default=str)
        except (TypeError, ValueError):
            # Never let a logging call take down a request.
            return json.dumps(
                {
                    "timestamp": payload["timestamp"],
                    "level": payload["level"],
                    "logger": payload["logger"],
                    "message": payload["message"],
                    "log_serialization_error": True,
                },
                default=str,
            )


class ConsoleLogFormatter(logging.Formatter):
    """Human-readable formatter for local development.

    Applies the same redaction rules as the JSON formatter.
    """

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s")

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib name
        base = super().format(record)
        context = current_context()

        parts = [scrub_text(base)]

        if context:
            parts.append(" ".join(f"{key}={value}" for key, value in sorted(context.items())))

        extras = _record_extras(record)
        if extras:
            safe_extras = redact(extras)
            parts.append(
                " ".join(f"{key}={safe_extras[key]!r}" for key in sorted(safe_extras))
            )

        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_configured = False


def configure_logging(level: str = "INFO", log_format: str = "json", *, force: bool = False) -> None:
    """Install the root logging handler. Idempotent unless ``force`` is set."""
    global _configured

    if _configured and not force:
        return

    resolved_level = getattr(logging, str(level).strip().upper(), logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    if str(log_format).strip().lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(ConsoleLogFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)

    root.addHandler(handler)
    root.setLevel(resolved_level)

    # uvicorn installs its own colourised handlers; route them through ours so
    # every line in production is structured and redacted.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(noisy)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # SQLAlchemy echoes bound parameters at INFO; keep it at WARNING so customer
    # data and connection details never land in the log stream.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
