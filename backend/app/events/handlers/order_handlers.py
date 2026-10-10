"""Order event handlers.

Orders are the input to RFM, so an order write invalidates more than the order
list: recency, frequency and monetary all move, which means the customer views,
the analytics views and the dashboard are all stale.

A bulk orders import additionally queues an analytics refresh, because RFM stored
against the previous transaction set is now wrong rather than merely old.
"""

from __future__ import annotations

from app.analytics.job_types import JOB_FULL_REFRESH
from app.cache import keys as cache_keys
from app.config import settings
from app.core.logging import get_logger
from app.events.consumer import handles
from app.events.handlers._shared import drop_caches, payload_int, payload_str, tenant_of
from app.events.schemas import Event, EventType

logger = get_logger(__name__)


@handles(EventType.ORDER_CREATED)
@handles(EventType.ORDER_UPDATED)
async def invalidate_on_order_write(event: Event) -> None:
    business_id = tenant_of(event)

    await drop_caches(
        event,
        cache_keys.order_write_prefixes(business_id),
        reason="order_write",
    )


@handles(EventType.ORDERS_IMPORTED)
async def refresh_analytics_after_orders_import(event: Event) -> None:
    """Invalidate order-derived caches and queue an RFM refresh.

    The refresh is queued rather than run here: this handler may be executing on
    the API event loop under the inline transport, and it should return in
    milliseconds. :func:`app.services.analytics_job_service.queue_job` writes a
    ``queued`` row and publishes, so the work lands on the worker path like any
    other job.
    """
    business_id = tenant_of(event)
    rows_imported = payload_int(event, "rows_imported")

    await drop_caches(
        event,
        cache_keys.order_write_prefixes(business_id),
        reason="orders_imported",
    )

    if not settings.analytics_auto_refresh_on_import:
        logger.info(
            "Automatic analytics refresh is disabled; RFM will stay stale until a "
            "job is requested",
            extra={"business_id": business_id},
        )
        return

    if rows_imported <= 0:
        logger.info(
            "Orders import produced no rows; skipping analytics refresh",
            extra={"business_id": business_id},
        )
        return

    from app.services.analytics_job_service import queue_job

    outcome = await queue_job(
        business_id=business_id,
        job_type=JOB_FULL_REFRESH,
        requested_by="event:orders.imported",
        dataset_import_id=payload_str(event, "import_id"),
    )

    logger.info(
        "Analytics refresh queued after orders import",
        extra={
            "business_id": business_id,
            "job_id": outcome.get("job_id"),
            "rows_imported": rows_imported,
            "dispatched": outcome.get("dispatched"),
        },
    )
