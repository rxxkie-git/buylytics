"""RFM measurement from the orders tables.

Aggregates real transactions into the values that :mod:`app.analytics.scoring`
turns into scores and labels. Two honest sources of RFM exist and no third:

``orders``
    Computed here. Recency is days between the reference date and the customer's
    most recent dated order; frequency is the order count; monetary is the summed
    order value (recovered from ``order_items`` when order headers carry no
    total).
``import``
    Passed through from genuine ``recency`` / ``frequency`` / ``monetary``
    columns in an uploaded file — see
    :func:`app.analytics.scoring.rfm_from_import_record`.

A customer with no orders and no real columns is **omitted**, not given
placeholder values.

Every query in this module filters on ``business_id``. There is no function here
that accepts a pre-joined dataset which could span tenants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.scoring import (
    SOURCE_IMPORT,
    SOURCE_ORDERS,
    RfmRow,
    has_real_rfm_columns,
    rfm_from_import_record,
    rfm_segment_label,
    score_rfm,
    segment_distribution,
)
from app.core.logging import get_logger
from app.db.models.customer import Customer
from app.db.models.order import Order, OrderItem
from app.repositories.bulk import bind_safe_chunk_size, chunked

logger = get_logger(__name__)

__all__ = [
    "EXCLUDED_ORDER_STATUSES",
    "SOURCE_IMPORT",
    "SOURCE_ORDERS",
    "RfmResult",
    "RfmRow",
    "compute_rfm_from_orders",
    "customer_lookup",
    "has_real_rfm_columns",
    "resolve_reference_date",
    "rfm_from_import_record",
    "rfm_segment_label",
    "score_rfm",
    "segment_distribution",
]

#: Order statuses excluded from RFM. A cancelled or refunded order is not
#: revenue and should not make a customer look active.
EXCLUDED_ORDER_STATUSES = frozenset(
    {"cancelled", "canceled", "refunded", "returned", "voided", "void", "failed", "draft"}
)


@dataclass
class RfmResult:
    rows: list[RfmRow] = field(default_factory=list)
    reference_date: datetime | None = None
    customers_with_orders: int = 0
    customers_scored: int = 0
    customers_without_dates: int = 0
    excluded_orders: int = 0
    monetary_from_items: int = 0

    def metadata(self) -> dict[str, Any]:
        """Run summary. Safe to persist in ``AnalyticsRun.result_metadata``.

        Contains counts and configuration only — no customer identifiers, no
        credentials.
        """
        return {
            "reference_date": self.reference_date.isoformat() if self.reference_date else None,
            "customers_with_orders": self.customers_with_orders,
            "customers_scored": self.customers_scored,
            "customers_without_order_dates": self.customers_without_dates,
            "orders_excluded_by_status": self.excluded_orders,
            "monetary_recovered_from_order_items": self.monetary_from_items,
            "source": SOURCE_ORDERS,
            "excluded_statuses": sorted(EXCLUDED_ORDER_STATUSES),
        }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        # SQLite (used by the test suite) returns naive datetimes even for
        # timezone-aware columns.
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _revenue_orders_only():
    """SQL predicate excluding non-revenue order statuses, case-insensitively."""
    return func.lower(func.coalesce(Order.status, "")).notin_(EXCLUDED_ORDER_STATUSES)


def resolve_reference_date(
    session: Session,
    business_id: str,
    *,
    override: datetime | None = None,
) -> datetime | None:
    """Pick the date recency is measured from.

    Defaults to the business's most recent order date rather than "now". A
    historical dataset — a 2010-2011 retail export, say — would otherwise show
    every customer as thousands of days lapsed: true in wall-clock terms, useless
    for comparing customers with each other. The chosen date is always recorded
    in the run metadata so a reader knows what the numbers mean.
    """
    if override is not None:
        return _as_utc(override)

    stmt = select(func.max(Order.order_date)).where(
        Order.business_id == str(business_id),
        Order.order_date.is_not(None),
        _revenue_orders_only(),
    )

    return _as_utc(session.execute(stmt).scalar_one_or_none())


def compute_rfm_from_orders(
    session: Session,
    business_id: str,
    *,
    reference_date: datetime | None = None,
) -> RfmResult:
    """Aggregate real RFM values for every customer of one business."""
    business = str(business_id)
    result = RfmResult()

    result.excluded_orders = int(
        session.execute(
            select(func.count())
            .select_from(Order)
            .where(Order.business_id == business, ~_revenue_orders_only())
        ).scalar()
        or 0
    )

    reference = resolve_reference_date(session, business, override=reference_date)
    result.reference_date = reference

    order_stmt = (
        select(
            Order.customer_id.label("customer_id"),
            func.count(Order.id).label("order_count"),
            func.sum(Order.total_amount).label("order_total"),
            func.max(Order.order_date).label("last_order_at"),
        )
        .where(Order.business_id == business, _revenue_orders_only())
        .group_by(Order.customer_id)
    )

    aggregates: dict[str, dict[str, Any]] = {}

    for row in session.execute(order_stmt):
        if not row.customer_id:
            continue

        aggregates[str(row.customer_id)] = {
            "order_count": int(row.order_count or 0),
            "order_total": float(row.order_total) if row.order_total is not None else None,
            "last_order_at": _as_utc(row.last_order_at),
        }

    result.customers_with_orders = len(aggregates)

    if not aggregates:
        logger.info("No orders found; RFM produced no rows")
        return result

    _recover_monetary_from_items(session, business, aggregates, result)

    rows: list[RfmRow] = []

    for customer_id, aggregate in aggregates.items():
        order_count = aggregate["order_count"]

        if order_count <= 0:
            continue

        monetary = float(aggregate["order_total"] or 0.0)
        last_order_at = aggregate["last_order_at"]

        if last_order_at is None or reference is None:
            recency: float | None = None
            result.customers_without_dates += 1
        else:
            elapsed_days = (reference - last_order_at).total_seconds() / 86400.0
            # Same-day orders are zero days lapsed, never negative.
            recency = round(max(0.0, elapsed_days), 2)

        rows.append(
            RfmRow(
                customer_id=customer_id,
                frequency=order_count,
                monetary=round(monetary, 2),
                total_orders=order_count,
                average_order_value=round(monetary / order_count, 2),
                recency=recency,
                last_order_at=last_order_at,
                source=SOURCE_ORDERS,
            )
        )

    rows.sort(key=lambda item: item.monetary, reverse=True)

    result.rows = score_rfm(rows)
    result.customers_scored = sum(1 for row in result.rows if row.rfm_segment is not None)

    logger.info(
        "RFM computed from orders",
        extra={
            "customers": len(result.rows),
            "scored": result.customers_scored,
            "reference_date": reference.isoformat() if reference else None,
        },
    )

    return result


def _recover_monetary_from_items(
    session: Session,
    business: str,
    aggregates: dict[str, dict[str, Any]],
    result: RfmResult,
) -> None:
    """Fill in monetary values from ``order_items`` when order totals are empty.

    Some imports populate line items but leave the order header total null.
    Summing the items is a measurement, not an estimate, so it is safe to use.

    Chunked, because the width of this ``IN`` is set by the data rather than by a
    page size: it is one parameter per customer whose orders carry no total. On
    ``dataset/df_Orders.csv`` — which has no amount column at all — that is *every*
    customer, 88,907 of them. PostgreSQL's wire protocol allows 65,535 bind
    parameters per statement, so the single-statement version raised before
    computing anything, and only on a real-sized tenant. SQLite accepts hundreds of
    thousands, which is why a smaller test never saw it.
    """
    missing = [
        customer_id
        for customer_id, aggregate in aggregates.items()
        if not aggregate["order_total"]
    ]

    if not missing:
        return

    for chunk in chunked(missing, bind_safe_chunk_size()):
        stmt = (
            select(
                Order.customer_id.label("customer_id"),
                func.sum(OrderItem.total_amount).label("items_total"),
            )
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.business_id == business,
                Order.customer_id.in_(list(chunk)),
                _revenue_orders_only(),
            )
            .group_by(Order.customer_id)
        )

        # Grouping by customer inside each chunk is safe without a merge step: a
        # customer appears in exactly one chunk, so no partial sum can be split
        # across two statements and overwrite the other half.
        for row in session.execute(stmt):
            customer_id = str(row.customer_id)

            if row.items_total is None or customer_id not in aggregates:
                continue

            aggregates[customer_id]["order_total"] = float(row.items_total)
            result.monetary_from_items += 1


def customer_lookup(
    session: Session,
    business_id: str,
    customer_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Display fields for a set of customers, scoped to the tenant.

    Used to attach names to analytics rows without the analytics queries having
    to join customers themselves.

    Chunked for the same reason as :func:`_recover_monetary_from_items`: the caller
    decides the width, and a caller labelling a whole tenant's RFM snapshot passes
    one id per customer.
    """
    if not customer_ids:
        return {}

    lookup: dict[str, dict[str, Any]] = {}

    for chunk in chunked([str(value) for value in customer_ids], bind_safe_chunk_size()):
        stmt = select(
            Customer.id,
            Customer.external_customer_id,
            Customer.name,
            Customer.email,
            Customer.city,
        ).where(
            Customer.business_id == str(business_id),
            Customer.id.in_(list(chunk)),
        )

        for row in session.execute(stmt):
            lookup[str(row.id)] = {
                "external_customer_id": row.external_customer_id,
                "name": row.name,
                "email": row.email,
                "city": row.city,
            }

    return lookup
