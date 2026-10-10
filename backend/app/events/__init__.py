"""Event bus package.

Layout
------
``topics``
    Central topic catalogue with optional environment prefixing.
``schemas``
    The :class:`Event` envelope and the event-type catalogue.
``producer``
    Publishing, with Kafka / inline / null transports.
``consumer``
    Handler registry, dispatch, idempotency, dead-lettering, Kafka loop.
``handlers``
    Domain reactions to events.
``worker``
    Standalone worker entrypoint.

Only the envelope and topic names are re-exported here. ``producer`` and
``consumer`` are imported from their own modules on purpose — keeping them out
of package import removes any chance of a partially-initialised package during
the deferred imports those two modules use to reference each other.
"""

from __future__ import annotations

from app.events.schemas import (
    ALL_EVENT_TYPES,
    SCHEMA_VERSION,
    Event,
    EventType,
    InvalidEventError,
    make_event,
)
from app.events.topics import (
    ALL_TOPICS,
    ANALYTICS_JOBS,
    CUSTOMER_EVENTS,
    DEAD_LETTER,
    INVENTORY_EVENTS,
    ORDER_EVENTS,
    RAW_IMPORTS,
    WORKER_TOPICS,
)

__all__ = [
    "ALL_EVENT_TYPES",
    "ALL_TOPICS",
    "ANALYTICS_JOBS",
    "CUSTOMER_EVENTS",
    "DEAD_LETTER",
    "Event",
    "EventType",
    "INVENTORY_EVENTS",
    "InvalidEventError",
    "ORDER_EVENTS",
    "RAW_IMPORTS",
    "SCHEMA_VERSION",
    "WORKER_TOPICS",
    "make_event",
]
