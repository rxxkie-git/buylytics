"""Mapping inbound webhook deliveries onto internal domain events.

Standard library only, so the routing rules are unit-testable.

A webhook is an *external* contract and an internal event is not. This module is
the translation layer between them, which keeps two things true: an external
sender cannot inject an arbitrary internal event type, and the internal event
catalogue can be renamed without breaking anyone's integration.

Only ``generic`` is implemented. That is deliberate — a "shopify" or
"woocommerce" mapping written from memory, against payloads never actually seen,
would be a guess dressed up as a feature. The registry below is the extension
point: add a source, map its event names onto internal ones, and the rest of the
pipeline (verification, idempotency, persistence, publishing) works unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.events.schemas import EventType
from app.events.topics import CUSTOMER_EVENTS, INVENTORY_EVENTS, ORDER_EVENTS

#: The only source with a mapping today.
SOURCE_GENERIC = "generic"

SUPPORTED_SOURCES: tuple[str, ...] = (SOURCE_GENERIC,)


@dataclass(frozen=True)
class EventRoute:
    """Where a translated webhook goes."""

    event_type: str
    topic: str


#: Inbound event name -> internal event and topic.
#:
#: Import-style bulk events are intentionally absent: a webhook delivers one
#: business fact, and a caller that wants a bulk load should use the import API
#: where the file can be validated and a row count reported.
_GENERIC_ROUTES: dict[str, EventRoute] = {
    "customer.created": EventRoute(EventType.CUSTOMER_CREATED, CUSTOMER_EVENTS),
    "customer.updated": EventRoute(EventType.CUSTOMER_UPDATED, CUSTOMER_EVENTS),
    "order.created": EventRoute(EventType.ORDER_CREATED, ORDER_EVENTS),
    "order.updated": EventRoute(EventType.ORDER_UPDATED, ORDER_EVENTS),
    "inventory.updated": EventRoute(EventType.INVENTORY_UPDATED, INVENTORY_EVENTS),
}

_ROUTES_BY_SOURCE: dict[str, dict[str, EventRoute]] = {
    SOURCE_GENERIC: _GENERIC_ROUTES,
}


def normalise_source(source: str | None) -> str:
    return (source or "").strip().lower()


def is_supported_source(source: str | None) -> bool:
    return normalise_source(source) in _ROUTES_BY_SOURCE


def supported_event_types(source: str | None = SOURCE_GENERIC) -> list[str]:
    return sorted(_ROUTES_BY_SOURCE.get(normalise_source(source), {}))


def resolve_route(source: str | None, event_type: str | None) -> EventRoute | None:
    """Look up the internal route for an inbound event, or ``None``."""
    routes = _ROUTES_BY_SOURCE.get(normalise_source(source))

    if not routes:
        return None

    return routes.get((event_type or "").strip().lower())


def extract_event_type(payload: Mapping[str, Any], header_value: str | None = None) -> str | None:
    """Find the event name in a delivery.

    The header wins when present — it is inside the signed material by way of the
    body only if the sender put it there, whereas ``webhook_event_type`` in the
    body always is. Both are checked so senders that can only set one still work.
    """
    if header_value and str(header_value).strip():
        return str(header_value).strip().lower()

    for key in ("event_type", "eventType", "type", "topic", "event"):
        value = payload.get(key)
        if value and isinstance(value, str) and value.strip():
            return value.strip().lower()

    return None


def extract_external_id(payload: Mapping[str, Any], header_value: str | None = None) -> str | None:
    """Find the sender's own id for this delivery.

    That id is what makes replay detection possible, so several conventional
    field names are checked before giving up.
    """
    if header_value and str(header_value).strip():
        return str(header_value).strip()

    for key in ("event_id", "eventId", "id", "delivery_id", "deliveryId"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the domain part of a delivery.

    A sender may wrap the record under ``data`` / ``payload`` / ``object``, or
    send it at the top level. Envelope metadata is stripped either way so
    downstream handlers see a consistent shape.
    """
    for key in ("data", "payload", "object", "record"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return dict(nested)

    envelope_keys = {
        "event_type",
        "eventType",
        "type",
        "topic",
        "event",
        "event_id",
        "eventId",
        "delivery_id",
        "deliveryId",
        "business_id",
        "businessId",
        "timestamp",
        "signature",
    }

    return {key: value for key, value in payload.items() if key not in envelope_keys}
