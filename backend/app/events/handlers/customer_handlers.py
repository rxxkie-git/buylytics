"""Customer event handlers.

Customer writes invalidate the customer reads *and* everything derived from
them — RFM, segments and the dashboard all restate customer data.
"""

from __future__ import annotations

from app.cache import keys as cache_keys
from app.core.logging import get_logger
from app.events.consumer import handles
from app.events.handlers._shared import drop_caches, payload_int, tenant_of
from app.events.schemas import Event, EventType

logger = get_logger(__name__)


@handles(EventType.CUSTOMER_CREATED)
@handles(EventType.CUSTOMER_UPDATED)
async def invalidate_on_customer_write(event: Event) -> None:
    business_id = tenant_of(event)

    await drop_caches(
        event,
        cache_keys.customer_write_prefixes(business_id),
        reason="customer_write",
    )


@handles(EventType.CUSTOMERS_IMPORTED)
async def invalidate_on_customers_imported(event: Event) -> None:
    """A bulk customer load invalidates the same prefixes as a single write.

    Prefix invalidation means the cost is the same for one customer or fifty
    thousand — there is no per-row key to chase.
    """
    business_id = tenant_of(event)

    logger.info(
        "Customers imported",
        extra={
            "business_id": business_id,
            "rows_imported": payload_int(event, "rows_imported"),
            "rows_failed": payload_int(event, "rows_failed"),
        },
    )

    await drop_caches(
        event,
        cache_keys.customer_write_prefixes(business_id),
        reason="customers_imported",
    )
