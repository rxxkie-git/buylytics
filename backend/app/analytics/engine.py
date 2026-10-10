"""Analytics job execution.

One entry point per job type, each taking a live SQLAlchemy session and an
``AnalyticsRun`` id, doing real work, persisting a snapshot, and returning
metadata for the run record.

These functions are **synchronous** on purpose. They run SQLAlchemy and pandas,
both of which block; the event handler that invokes them pushes them onto a
worker thread so the API's event loop stays responsive. Making them ``async``
would hide that and stall every other request.

Transactions are owned by the caller. The engine flushes but never commits, so a
handler can wrap "run the job" and "record the outcome" in one transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.analytics import rfm as rfm_module
from app.analytics.job_types import (
    JOB_FULL_REFRESH,
    JOB_RFM,
    JOB_SEGMENTATION,
    SUPPORTED_JOB_TYPES,
)
from app.analytics.rfm import RfmResult
from app.analytics.scoring import (
    SOURCE_IMPORT,
    SOURCE_ORDERS,
    RfmRow,
    score_rfm,
    segment_distribution,
)
from app.analytics.segmentation import run_segmentation_for_rows
from app.core.exceptions import InsufficientDataError, ValidationError
from app.core.logging import get_logger
from app.repositories.analytics_repository import AnalyticsRepository

logger = get_logger(__name__)

__all__ = [
    "JOB_FULL_REFRESH",
    "JOB_RFM",
    "JOB_SEGMENTATION",
    "SUPPORTED_JOB_TYPES",
    "run_full_refresh_job",
    "run_job",
    "run_rfm_job",
    "run_segmentation_job",
]


def _load_scored_rfm(session: Session, business_id: str) -> list[RfmRow]:
    """Read the persisted RFM snapshot back as :class:`RfmRow` objects.

    Segmentation runs on whatever RFM the last RFM job wrote, rather than
    recomputing, so the two views can never disagree.
    """
    repository = AnalyticsRepository(session)
    rows: list[RfmRow] = []

    for record in repository.get_rfm(business_id=business_id):
        if record.frequency is None or record.monetary is None:
            continue

        frequency = int(record.frequency)

        if frequency <= 0:
            continue

        rows.append(
            RfmRow(
                customer_id=str(record.customer_id),
                frequency=frequency,
                monetary=float(record.monetary),
                total_orders=int(record.total_orders or frequency),
                average_order_value=float(
                    record.average_order_value
                    if record.average_order_value is not None
                    else (float(record.monetary) / frequency if frequency else 0.0)
                ),
                recency=float(record.recency) if record.recency is not None else None,
                source=record.source or SOURCE_ORDERS,
                r_score=record.r_score,
                f_score=record.f_score,
                m_score=record.m_score,
                rfm_segment=record.rfm_segment,
            )
        )

    return rows


def _load_import_sourced_rows(
    session: Session,
    business_id: str,
    *,
    exclude_customer_ids: set[str],
) -> list[RfmRow]:
    """Passed-through RFM for customers who have no measured orders.

    These values came from genuine ``recency``/``frequency``/``monetary`` columns
    in an uploaded file. They are included so a business that has customer data
    but has not yet imported orders still gets a real snapshot — but a customer
    with measured orders is always taken from the orders, because a transaction is
    evidence and a spreadsheet column is a claim.
    """
    repository = AnalyticsRepository(session)
    rows: list[RfmRow] = []

    for record in repository.get_import_sourced_rfm(
        business_id=business_id, exclude_customer_ids=exclude_customer_ids
    ):
        if record.frequency is None or record.monetary is None:
            continue

        frequency = int(record.frequency)

        if frequency <= 0:
            continue

        monetary = float(record.monetary)

        rows.append(
            RfmRow(
                customer_id=str(record.customer_id),
                frequency=frequency,
                monetary=monetary,
                total_orders=int(record.total_orders or frequency),
                average_order_value=float(
                    record.average_order_value
                    if record.average_order_value is not None
                    else (monetary / frequency if frequency else 0.0)
                ),
                recency=float(record.recency) if record.recency is not None else None,
                source=SOURCE_IMPORT,
            )
        )

    return rows


def run_rfm_job(
    session: Session,
    *,
    business_id: str,
    run_id: str | None = None,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Recompute RFM and replace the tenant's snapshot.

    Orders are the primary source. Customers with no orders but with genuine RFM
    columns from an upload are folded in, and the combined population is scored in
    a single pass — scoring the two sources separately would produce two
    incomparable quintile scales in one table, so a "5" would not mean the same
    thing on two rows of the same dashboard.
    """
    result: RfmResult = rfm_module.compute_rfm_from_orders(
        session, business_id, reference_date=reference_date
    )

    measured_ids = {row.customer_id for row in result.rows}
    passthrough = _load_import_sourced_rows(
        session, business_id, exclude_customer_ids=measured_ids
    )

    combined = list(result.rows) + passthrough

    if not combined:
        raise InsufficientDataError(
            "No orders found for this business, and no uploaded file carried real "
            "recency, frequency and monetary columns, so RFM cannot be computed. "
            "Import an orders file first.",
            details={"customers_with_orders": 0, "customers_from_import": 0},
        )

    # Rescore the union so every quintile comes from one population.
    scored = score_rfm(combined)

    repository = AnalyticsRepository(session)
    written = repository.replace_rfm_snapshot(
        business_id=business_id,
        rows=[row.to_model_fields() for row in scored],
        analytics_run_id=run_id,
    )

    metadata = result.metadata()
    metadata["rows_written"] = written
    metadata["customers_from_orders"] = len(measured_ids)
    metadata["customers_from_import"] = len(passthrough)
    metadata["customers_scored"] = sum(1 for row in scored if row.rfm_segment is not None)
    metadata["segment_distribution"] = segment_distribution(scored)
    metadata["sources_used"] = [
        source
        for source, present in ((SOURCE_ORDERS, bool(measured_ids)), (SOURCE_IMPORT, bool(passthrough)))
        if present
    ]

    logger.info(
        "RFM job finished",
        extra={
            "rows_written": written,
            "scored": metadata["customers_scored"],
            "from_orders": len(measured_ids),
            "from_import": len(passthrough),
        },
    )

    return metadata


def run_segmentation_job(
    session: Session,
    *,
    business_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Cluster customers from the stored RFM snapshot and replace segments."""
    rows = _load_scored_rfm(session, business_id)

    if not rows:
        raise InsufficientDataError(
            "No RFM data is stored for this business. Run the RFM job first.",
            details={"rfm_rows": 0},
        )

    segmentation = run_segmentation_for_rows(rows)

    repository = AnalyticsRepository(session)
    model_version = str(segmentation.model_meta.get("modelVersion") or "")

    written = repository.replace_segment_snapshot(
        business_id=business_id,
        rows=[
            {**assignment.to_model_fields(), "model_version": model_version or None}
            for assignment in segmentation.assignments
        ],
        analytics_run_id=run_id,
    )

    metadata = segmentation.metadata()
    metadata["rows_written"] = written

    logger.info("Segmentation job finished", extra={"rows_written": written})

    return metadata


def run_full_refresh_job(
    session: Session,
    *,
    business_id: str,
    run_id: str | None = None,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """RFM followed by segmentation.

    Segmentation is best-effort: a tenant with real orders but too few customers
    to cluster still gets its RFM refreshed, and the reason segmentation was
    skipped is recorded instead of the job failing outright.
    """
    metadata: dict[str, Any] = {
        "rfm": run_rfm_job(
            session, business_id=business_id, run_id=run_id, reference_date=reference_date
        )
    }

    try:
        metadata["segmentation"] = run_segmentation_job(
            session, business_id=business_id, run_id=run_id
        )
    except InsufficientDataError as exc:
        metadata["segmentation"] = {"skipped": True, "reason": exc.message, "code": exc.code}
        logger.info("Segmentation skipped during full refresh", extra={"reason": exc.message})

    return metadata


_JOB_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    JOB_RFM: run_rfm_job,
    JOB_SEGMENTATION: run_segmentation_job,
    JOB_FULL_REFRESH: run_full_refresh_job,
}


def run_job(
    job_type: str,
    session: Session,
    *,
    business_id: str,
    run_id: str | None = None,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Execute a job by name.

    An unknown ``job_type`` is a client error, not a crash — the API validates
    it too, but the worker cannot trust a message that may have been produced by
    an older deploy.
    """
    handler = _JOB_DISPATCH.get(job_type)

    if handler is None:
        raise ValidationError(
            f"Unknown analytics job type '{job_type}'.",
            details={"supported": list(SUPPORTED_JOB_TYPES)},
        )

    if handler is run_segmentation_job:
        return handler(session, business_id=business_id, run_id=run_id)

    return handler(
        session, business_id=business_id, run_id=run_id, reference_date=reference_date
    )
