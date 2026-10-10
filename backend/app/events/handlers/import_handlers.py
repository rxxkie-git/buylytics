"""Import event handlers.

An import is the entry point for every dataset in the platform, so these handlers
sit on the seam between "raw file accepted" and "analytics are current".

The payload contract, produced by the import routes:

``import_id``      the ``dataset_imports`` row id
``dataset_type``   ``customers`` | ``orders`` | ``products`` | ``inventory``
``rows_total``     rows found in the file
``rows_imported``  rows written
``rows_failed``    rows rejected by validation

Filenames and row contents are not logged. A filename can carry a customer name,
and a rejected row certainly does.
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

DATASET_CUSTOMERS = "customers"
DATASET_ORDERS = "orders"
DATASET_PRODUCTS = "products"
DATASET_INVENTORY = "inventory"

#: Which cache prefixes a completed import invalidates. Unknown dataset types
#: fall back to the union, since guessing narrow would serve stale data.
_PREFIX_BUILDERS = {
    DATASET_CUSTOMERS: cache_keys.customer_write_prefixes,
    DATASET_ORDERS: cache_keys.order_write_prefixes,
    DATASET_PRODUCTS: cache_keys.product_write_prefixes,
    DATASET_INVENTORY: cache_keys.inventory_write_prefixes,
}

#: Dataset types whose contents change RFM, and therefore warrant a refresh.
_ANALYTICS_RELEVANT = frozenset({DATASET_CUSTOMERS, DATASET_ORDERS})


def _prefixes_for(business_id: str, dataset_type: str | None) -> list[str]:
    builder = _PREFIX_BUILDERS.get((dataset_type or "").strip().lower())

    if builder is not None:
        return builder(business_id)

    # Unrecognised dataset: clear everything the tenant has cached. Correct, and
    # a full rebuild costs one round of cache misses.
    return [cache_keys.tenant_prefix(business_id)]


@handles(EventType.IMPORT_RECEIVED)
async def log_import_received(event: Event) -> None:
    """Record that a file was accepted.

    No cache work: nothing has been written to the domain tables yet, so no read
    is stale. This handler is the audit trail and the hook point for a future
    move to fully asynchronous parsing.
    """
    logger.info(
        "Import received",
        extra={
            "business_id": tenant_of(event),
            "import_id": payload_str(event, "import_id"),
            "dataset_type": payload_str(event, "dataset_type"),
            "rows_total": payload_int(event, "rows_total"),
        },
    )


@handles(EventType.IMPORT_COMPLETED)
async def refresh_after_import(event: Event) -> None:
    """Invalidate the affected caches and, when relevant, queue an analytics run.

    Note the ordering: caches are dropped first, so a client polling immediately
    after the import reads fresh domain data even while the analytics job is
    still queued. The job then updates RFM and segments, and its own completion
    handler invalidates again.
    """
    business_id = tenant_of(event)
    dataset_type = (payload_str(event, "dataset_type") or "").lower()
    rows_imported = payload_int(event, "rows_imported")

    logger.info(
        "Import completed",
        extra={
            "business_id": business_id,
            "import_id": payload_str(event, "import_id"),
            "dataset_type": dataset_type or "unknown",
            "rows_imported": rows_imported,
            "rows_failed": payload_int(event, "rows_failed"),
        },
    )

    await drop_caches(
        event,
        _prefixes_for(business_id, dataset_type),
        reason=f"import_completed:{dataset_type or 'unknown'}",
    )

    if dataset_type not in _ANALYTICS_RELEVANT:
        return

    if not settings.analytics_auto_refresh_on_import:
        logger.info(
            "Automatic analytics refresh is disabled; request a job to update RFM",
            extra={"business_id": business_id},
        )
        return

    if rows_imported <= 0:
        logger.info(
            "Import wrote no rows; skipping analytics refresh",
            extra={"business_id": business_id, "dataset_type": dataset_type},
        )
        return

    from app.services.analytics_job_service import queue_job

    outcome = await queue_job(
        business_id=business_id,
        job_type=JOB_FULL_REFRESH,
        requested_by=f"event:{event.event_type}",
        dataset_import_id=payload_str(event, "import_id"),
    )

    logger.info(
        "Analytics refresh queued after import",
        extra={
            "business_id": business_id,
            "job_id": outcome.get("job_id"),
            "dataset_type": dataset_type,
            "dispatched": outcome.get("dispatched"),
        },
    )


@handles(EventType.IMPORT_FAILED)
async def log_import_failure(event: Event) -> None:
    """Record a failed import.

    Nothing is invalidated: a failed import wrote nothing, so cached reads are
    still accurate. ``error`` is a short reason string produced by the import
    service, never a raw exception with connection details in it.
    """
    logger.error(
        "Import failed",
        extra={
            "business_id": tenant_of(event),
            "import_id": payload_str(event, "import_id"),
            "dataset_type": payload_str(event, "dataset_type"),
            "error": payload_str(event, "error"),
        },
    )
