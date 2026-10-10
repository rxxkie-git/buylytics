"""Inventory event handlers.

Inventory affects the stock views and the dashboard, but not RFM — a stock level
says nothing about what a customer bought. The invalidation set is deliberately
narrower than the order one.

``inventory.low_stock`` is an alert, not a write: nothing is invalidated, the
event exists so that a notifier or a future webhook fan-out has something to
subscribe to.
"""

from __future__ import annotations

from app.cache import keys as cache_keys
from app.core.logging import get_logger
from app.events.consumer import handles
from app.events.handlers._shared import drop_caches, payload_int, payload_str, tenant_of
from app.events.schemas import Event, EventType

logger = get_logger(__name__)


@handles(EventType.INVENTORY_UPDATED)
@handles(EventType.INVENTORY_IMPORTED)
async def invalidate_on_inventory_write(event: Event) -> None:
    business_id = tenant_of(event)

    await drop_caches(
        event,
        # Products and inventory are separate namespaces but a stock change moves
        # both the inventory views and any product listing that shows stock.
        cache_keys.inventory_write_prefixes(business_id)
        + [cache_keys.namespace_prefix(business_id, cache_keys.PRODUCTS_NS)],
        reason=f"inventory_write:{event.event_type}",
    )


@handles(EventType.INVENTORY_LOW_STOCK)
async def record_low_stock(event: Event) -> None:
    """Log a low-stock alert.

    Only the product identifier and the counts are logged — never the whole
    payload, which may carry supplier or cost fields.
    """
    logger.warning(
        "Low stock detected",
        extra={
            "business_id": tenant_of(event),
            "product_id": payload_str(event, "product_id"),
            "sku": payload_str(event, "sku"),
            "quantity_available": payload_int(event, "quantity_available"),
            "threshold": payload_int(event, "threshold"),
        },
    )
