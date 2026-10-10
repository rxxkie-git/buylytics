"""Shared helpers for event handlers.

Kept private to the package. The point is that a handler body should read as one
sentence of intent — "orders changed, drop the order-derived caches" — with the
defensive detail factored out here.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.cache import invalidate_prefixes
from app.core.logging import get_logger
from app.events.schemas import Event

logger = get_logger(__name__)


def tenant_of(event: Event) -> str:
    """Return the event's tenant.

    ``Event.__post_init__`` already rejects a missing ``business_id``, so this is
    a narrowing helper rather than a validation step. It exists so that no handler
    is ever tempted to reach into ``event.payload`` for a tenant id.
    """
    return str(event.business_id)


async def drop_caches(event: Event, prefixes: Iterable[str], *, reason: str) -> int:
    """Invalidate cache prefixes for the event's tenant.

    A cache that is unreachable returns zero and logs; it never fails the
    handler, because the cache is not the source of truth. Worst case a reader
    sees a stale value until the TTL expires.
    """
    removed = await invalidate_prefixes(prefixes)

    logger.info(
        "Cache invalidated after event",
        extra={
            "reason": reason,
            "keys_removed": removed,
            "business_id": tenant_of(event),
            "event_type": event.event_type,
        },
    )

    return removed


def payload_str(event: Event, key: str) -> str | None:
    """Read a string field from the payload, treating blanks as absent."""
    value = event.payload.get(key)

    if value is None:
        return None

    text = str(value).strip()
    return text or None


def payload_int(event: Event, key: str, default: int = 0) -> int:
    value = event.payload.get(key)

    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def summary_fields(event: Event, *keys: str) -> dict[str, Any]:
    """Pick a few payload fields for a log line.

    Explicit allow-listing, because payloads carry customer data and a handler
    log must not become an accidental export of it.
    """
    return {key: event.payload.get(key) for key in keys if key in event.payload}
