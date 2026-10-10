"""Event envelope and event-type catalogue.

The envelope is a plain :mod:`dataclasses` structure rather than a pydantic
model. Two reasons:

1. Events are produced from background workers and tests that have no reason to
   pull in the web framework's validation stack.
2. Schema round-tripping stays unit-testable in a bare Python environment.

``schema_version`` is present from day one so a future consumer can handle old
messages still sitting in a partition after a deploy.

Every envelope carries ``business_id``. Tenant scoping is a property of the
message itself, not of the topic — a single topic carries all tenants and
consumers must filter on the envelope field.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class EventType:
    """String constants for event types. Grouped by domain."""

    # raw.imports
    IMPORT_RECEIVED = "import.received"
    IMPORT_COMPLETED = "import.completed"
    IMPORT_FAILED = "import.failed"

    # customers.events
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMERS_IMPORTED = "customers.imported"

    # orders.events
    ORDER_CREATED = "order.created"
    ORDER_UPDATED = "order.updated"
    ORDERS_IMPORTED = "orders.imported"

    # inventory.events
    INVENTORY_UPDATED = "inventory.updated"
    INVENTORY_LOW_STOCK = "inventory.low_stock"
    INVENTORY_IMPORTED = "inventory.imported"

    # analytics.jobs
    ANALYTICS_JOB_REQUESTED = "analytics.job.requested"
    ANALYTICS_JOB_COMPLETED = "analytics.job.completed"
    ANALYTICS_JOB_FAILED = "analytics.job.failed"


ALL_EVENT_TYPES: frozenset[str] = frozenset(
    value
    for key, value in vars(EventType).items()
    if not key.startswith("_") and isinstance(value, str)
)


class InvalidEventError(ValueError):
    """Raised when a message cannot be parsed into a valid :class:`Event`."""


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class Event:
    """An immutable domain event.

    Attributes
    ----------
    event_id:
        Unique per event. Consumers use it for idempotency.
    event_type:
        One of :class:`EventType`.
    business_id:
        Owning tenant. Required — there are no cross-tenant events.
    timestamp:
        ISO-8601 UTC string for when the event occurred.
    payload:
        Domain data. Must be JSON-serialisable.
    schema_version:
        Envelope version, for forward compatibility.
    correlation_id:
        Request ID that triggered the event, for end-to-end tracing.
    source:
        Which component emitted it (``api``, ``webhook``, ``worker``).
    """

    event_type: str
    business_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_utc_now_iso)
    schema_version: int = SCHEMA_VERSION
    correlation_id: str | None = None
    source: str = "api"

    def __post_init__(self) -> None:
        if not self.event_type or not str(self.event_type).strip():
            raise InvalidEventError("event_type is required")

        if not self.business_id or not str(self.business_id).strip():
            raise InvalidEventError("business_id is required on every event")

        if not isinstance(self.payload, dict):
            raise InvalidEventError("payload must be a dict")

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "business_id": self.business_id,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, separators=(",", ":"))

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    # -- deserialisation ----------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        if not isinstance(data, dict):
            raise InvalidEventError("event must be a JSON object")

        missing = [key for key in ("event_type", "business_id") if not data.get(key)]
        if missing:
            raise InvalidEventError(f"event missing required field(s): {', '.join(missing)}")

        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            raise InvalidEventError("payload must be a JSON object")

        raw_version = data.get("schema_version", SCHEMA_VERSION)
        try:
            schema_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise InvalidEventError("schema_version must be an integer") from exc

        return cls(
            event_type=str(data["event_type"]),
            business_id=str(data["business_id"]),
            payload=payload,
            event_id=str(data.get("event_id") or uuid.uuid4()),
            timestamp=str(data.get("timestamp") or _utc_now_iso()),
            schema_version=schema_version,
            correlation_id=(str(data["correlation_id"]) if data.get("correlation_id") else None),
            source=str(data.get("source") or "api"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Event":
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidEventError("event bytes are not valid UTF-8") from exc

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidEventError(f"event is not valid JSON: {exc}") from exc

        return cls.from_dict(parsed)

    # -- helpers ------------------------------------------------------------

    @property
    def partition_key(self) -> str:
        """Kafka message key.

        Keying by ``business_id`` guarantees per-tenant ordering and keeps one
        tenant's events on one partition, which is what the analytics
        consumers assume.
        """
        return self.business_id

    def log_fields(self) -> dict[str, Any]:
        """Safe fields for structured logging — never includes the payload."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "business_id": self.business_id,
            "schema_version": self.schema_version,
            "source": self.source,
        }


def make_event(
    event_type: str,
    business_id: str,
    payload: dict[str, Any] | None = None,
    *,
    correlation_id: str | None = None,
    source: str = "api",
) -> Event:
    """Convenience constructor that fills the correlation ID from log context."""
    if correlation_id is None:
        try:
            from app.core.logging import request_id_var

            correlation_id = request_id_var.get()
        except Exception:  # pragma: no cover - logging must never break producing
            correlation_id = None

    return Event(
        event_type=event_type,
        business_id=business_id,
        payload=payload or {},
        correlation_id=correlation_id,
        source=source,
    )
