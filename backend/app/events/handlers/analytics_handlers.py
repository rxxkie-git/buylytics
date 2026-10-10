"""Analytics job handlers.

This is where the asynchronous job lifecycle is actually driven:

    analytics.job.requested  ->  claim the queued row, run the engine,
                                 write completed / failed
    analytics.job.completed  ->  invalidate the tenant's analytics caches
    analytics.job.failed     ->  log; the failure reason is already on the row

The handler is thin on purpose. All the work lives in
:mod:`app.services.analytics_job_service`, so the same lifecycle can be driven
from a test, a CLI backfill or a future scheduler without going through Kafka.

Idempotency has two independent layers. The ``processed_events`` ledger stops a
redelivered message from reaching the handler at all; if that table is
unavailable the handler still refuses to re-run a job whose row is already in a
terminal state. Either layer alone is sufficient.
"""

from __future__ import annotations

from datetime import datetime

from app.cache import keys as cache_keys
from app.core.logging import get_logger
from app.events.consumer import handles
from app.events.handlers._shared import drop_caches, payload_str, tenant_of
from app.events.schemas import Event, EventType

logger = get_logger(__name__)


def _parse_reference_date(raw: str | None) -> datetime | None:
    """Parse an optional RFM reference date from the payload.

    An unparseable value is ignored rather than failing the job: the engine's
    default — the tenant's most recent order date — is a good answer, and the
    chosen date is recorded in the run metadata either way.
    """
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Ignoring unparseable reference_date on analytics job event")
        return None


@handles(EventType.ANALYTICS_JOB_REQUESTED)
async def run_requested_job(event: Event) -> None:
    """Execute a queued analytics job.

    ``run_id`` is required. Without it there is no row to transition, and
    inventing one would produce a result nobody asked for — so the handler fails,
    which dead-letters the message for inspection.
    """
    business_id = tenant_of(event)
    run_id = payload_str(event, "run_id")

    if not run_id:
        raise ValueError("analytics.job.requested payload is missing 'run_id'")

    job_type = payload_str(event, "job_type") or ""
    reference_date = _parse_reference_date(payload_str(event, "reference_date"))

    from app.services.analytics_job_service import run_job_from_event

    outcome = await run_job_from_event(
        business_id=business_id,
        run_id=run_id,
        job_type=job_type,
        reference_date=reference_date,
    )

    logger.info(
        "Analytics job event handled",
        extra={
            "business_id": business_id,
            "job_id": run_id,
            "job_type": job_type or "from_row",
            "status": outcome.get("status"),
        },
    )


@handles(EventType.ANALYTICS_JOB_COMPLETED)
async def invalidate_after_job(event: Event) -> None:
    """Drop analytics caches once a job has written new results.

    ``run_job_from_event`` already invalidates before publishing this event. The
    duplication is intentional and cheap: it means a job completed by any other
    path — a CLI backfill, a replayed message — still refreshes the read side.
    """
    business_id = tenant_of(event)

    prefixes = cache_keys.analytics_write_prefixes(business_id)
    prefixes.append(cache_keys.namespace_prefix(business_id, cache_keys.JOBS_NS))

    await drop_caches(event, prefixes, reason="analytics_job_completed")


@handles(EventType.ANALYTICS_JOB_FAILED)
async def log_failed_job(event: Event) -> None:
    """Record a failed job and refresh the job list.

    The analytics caches are left alone: the previous results are still the most
    accurate available, and clearing them would replace real (if older) numbers
    with an empty dashboard.
    """
    business_id = tenant_of(event)

    logger.error(
        "Analytics job failed",
        extra={
            "business_id": business_id,
            "job_id": payload_str(event, "run_id"),
            "job_type": payload_str(event, "job_type"),
            "error_code": payload_str(event, "error_code"),
        },
    )

    await drop_caches(
        event,
        [cache_keys.namespace_prefix(business_id, cache_keys.JOBS_NS)],
        reason="analytics_job_failed",
    )
