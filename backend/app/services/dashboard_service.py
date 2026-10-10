"""Read models for the dashboard.

These are the queries the frontend polls, so they are the ones worth caching.
Each is a pure aggregate over PostgreSQL wrapped in ``get_or_set`` against a
tenant-scoped key, which means:

* Postgres stays the source of truth. A cache miss, an unreachable Redis, or a
  disabled cache all produce the same numbers, just more slowly.
* Every key is built by :mod:`app.cache.keys`, whose helpers put the business id
  in the first segment, so one tenant's dashboard can never be served from
  another's cached entry.
* An import, a webhook or a finished analytics job invalidates by prefix, so a
  stale summary cannot outlive the write that changed it.

The loaders are synchronous because SQLAlchemy is. ``get_or_set`` runs them on a
worker thread, so a slow aggregate does not block the event loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.analytics.scoring import RFM_SEGMENT_ORDER, SOURCE_IMPORT, SOURCE_ORDERS
from app.cache import get_or_set, keys as cache_keys
from app.config import settings
from app.core.logging import get_logger
from app.db.models.analytics import AnalyticsRun, CustomerRFM, CustomerSegment
from app.db.models.customer import Customer
from app.db.models.inventory import Inventory
from app.db.models.order import Order
from app.db.models.product import Product

logger = get_logger(__name__)

#: How much history the revenue trend covers by default.
DEFAULT_TREND_DAYS = 30
MAX_TREND_DAYS = 365


def _as_float(value: Any) -> float:
    """Coerce a SQL aggregate to a JSON-safe float.

    ``SUM`` over a ``Numeric`` column returns ``Decimal``, which is not JSON
    serialisable, and ``None`` when no rows matched.
    """
    if value is None:
        return 0.0

    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Loaders (synchronous, tenant-scoped)
# ---------------------------------------------------------------------------


def _load_summary(session: Session, business_id: str) -> dict[str, Any]:
    """Headline counts for the dashboard tiles.

    One query per fact rather than a single joined query on purpose: joining
    customers, orders and inventory in one statement multiplies rows and would
    make every count wrong unless each were wrapped in its own ``DISTINCT``.
    Separate aggregates are both correct and individually indexable.
    """
    customers = session.execute(
        select(func.count()).select_from(Customer).where(Customer.business_id == business_id)
    ).scalar()

    products = session.execute(
        select(func.count()).select_from(Product).where(Product.business_id == business_id)
    ).scalar()

    order_totals = session.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), 0),
            func.avg(Order.total_amount),
        ).where(Order.business_id == business_id)
    ).one()

    order_count, revenue, average_order_value = order_totals

    # Customers who have actually transacted, as distinct from customers on file.
    # The gap between the two is the single most useful number on the page.
    purchasing_customers = session.execute(
        select(func.count(func.distinct(Order.customer_id))).where(
            Order.business_id == business_id, Order.customer_id.is_not(None)
        )
    ).scalar()

    threshold = settings.inventory_low_stock_threshold

    low_stock = session.execute(
        select(func.count())
        .select_from(Inventory)
        .where(
            Inventory.business_id == business_id,
            Inventory.quantity.is_not(None),
            Inventory.quantity <= threshold,
        )
    ).scalar()

    out_of_stock = session.execute(
        select(func.count())
        .select_from(Inventory)
        .where(Inventory.business_id == business_id, Inventory.quantity <= 0)
    ).scalar()

    rfm_rows = session.execute(
        select(func.count()).select_from(CustomerRFM).where(CustomerRFM.business_id == business_id)
    ).scalar()

    segment_rows = session.execute(
        select(func.count())
        .select_from(CustomerSegment)
        .where(CustomerSegment.business_id == business_id)
    ).scalar()

    last_run = session.execute(
        select(AnalyticsRun.job_type, AnalyticsRun.completed_at, AnalyticsRun.status)
        .where(AnalyticsRun.business_id == business_id, AnalyticsRun.status == "completed")
        .order_by(AnalyticsRun.completed_at.desc())
        .limit(1)
    ).first()

    customer_count = _as_int(customers)
    purchasing = _as_int(purchasing_customers)

    return {
        "customers": customer_count,
        "customers_with_orders": purchasing,
        # Reported rather than left to the client to divide, so the dashboard and
        # any export agree on the definition.
        "customers_without_orders": max(customer_count - purchasing, 0),
        "products": _as_int(products),
        "orders": _as_int(order_count),
        "revenue": _as_float(revenue),
        "average_order_value": _as_float(average_order_value),
        "low_stock_items": _as_int(low_stock),
        "out_of_stock_items": _as_int(out_of_stock),
        "low_stock_threshold": threshold,
        "rfm_rows": _as_int(rfm_rows),
        "segment_rows": _as_int(segment_rows),
        "analytics_ready": _as_int(rfm_rows) > 0,
        "last_completed_job": (
            {
                "job_type": last_run[0],
                "completed_at": last_run[1].isoformat() if last_run[1] else None,
            }
            if last_run
            else None
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_revenue_trend(session: Session, business_id: str, days: int) -> dict[str, Any]:
    """Daily revenue for the last ``days`` days.

    Days with no orders are filled with zero. Without that the frontend chart
    would join 3 January straight to 7 January and imply revenue it never earned.
    """
    window_days = max(1, min(days, MAX_TREND_DAYS))

    latest_order_date = session.execute(
        select(func.max(Order.order_date)).where(Order.business_id == business_id)
    ).scalar()

    now = datetime.now(timezone.utc)
    if latest_order_date is not None:
        if latest_order_date.tzinfo is None:
            latest_order_date = latest_order_date.replace(tzinfo=timezone.utc)
        if latest_order_date < (now - timedelta(days=window_days)):
            end = latest_order_date + timedelta(days=1)
        else:
            end = now
    else:
        end = now

    start = end - timedelta(days=window_days - 1)
    start_of_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = end.replace(hour=23, minute=59, second=59, microsecond=999999)

    day = func.date(Order.order_date)

    rows = session.execute(
        select(
            day.label("day"),
            func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            func.count(Order.id).label("orders"),
        )
        .where(
            Order.business_id == business_id,
            Order.order_date.is_not(None),
            Order.order_date >= start_of_day,
            Order.order_date <= end_of_day,
        )
        .group_by(day)
        .order_by(day)
    ).all()

    # SQLite hands back a string from date(); PostgreSQL a date object.
    by_day: dict[str, tuple[float, int]] = {}

    for value, revenue, order_count in rows:
        key = value.isoformat() if hasattr(value, "isoformat") else str(value)
        by_day[key] = (_as_float(revenue), _as_int(order_count))

    points: list[dict[str, Any]] = []

    for offset in range(window_days):
        current = (start_of_day + timedelta(days=offset)).date().isoformat()
        revenue, order_count = by_day.get(current, (0.0, 0))
        points.append({"date": current, "revenue": revenue, "orders": order_count})

    return {
        "days": window_days,
        "start_date": points[0]["date"] if points else None,
        "end_date": points[-1]["date"] if points else None,
        "total_revenue": round(sum(point["revenue"] for point in points), 2),
        "total_orders": sum(point["orders"] for point in points),
        "points": points,
    }


def _load_rfm_distribution(session: Session, business_id: str) -> dict[str, Any]:
    """Segment counts and averages from the stored RFM snapshot.

    Aggregated in SQL rather than by loading every row into Python: a tenant with
    a hundred thousand customers would otherwise pull the whole table into the
    API process on every dashboard load.

    Only rows carrying a segment are counted. An unscored row is real data — it
    just could not be placed in a quintile — and bucketing it as "Unsegmented"
    in a chart of behavioural segments would read as a finding rather than a gap.
    """
    rows = session.execute(
        select(
            CustomerRFM.rfm_segment,
            func.count(),
            func.avg(CustomerRFM.recency),
            func.avg(CustomerRFM.frequency),
            func.avg(CustomerRFM.monetary),
            func.coalesce(func.sum(CustomerRFM.monetary), 0),
        )
        .where(CustomerRFM.business_id == business_id, CustomerRFM.rfm_segment.is_not(None))
        .group_by(CustomerRFM.rfm_segment)
    ).all()

    by_segment = {
        segment: {
            "segment": segment,
            "customers": _as_int(count),
            "avg_recency": _as_float(avg_recency),
            "avg_frequency": _as_float(avg_frequency),
            "avg_monetary": _as_float(avg_monetary),
            "total_monetary": _as_float(total_monetary),
        }
        for segment, count, avg_recency, avg_frequency, avg_monetary, total_monetary in rows
    }

    total = sum(entry["customers"] for entry in by_segment.values())

    # Stable, meaningful order: champions first, lost last, so the chart reads
    # the same way every time regardless of which segments happen to be present.
    ordered = [by_segment[name] for name in RFM_SEGMENT_ORDER if name in by_segment]
    ordered += [by_segment[name] for name in sorted(set(by_segment) - set(RFM_SEGMENT_ORDER))]

    for entry in ordered:
        entry["share"] = round(entry["customers"] / total, 4) if total else 0.0

    source_rows = session.execute(
        select(CustomerRFM.source, func.count())
        .where(CustomerRFM.business_id == business_id)
        .group_by(CustomerRFM.source)
    ).all()

    sources = {(source or SOURCE_ORDERS): _as_int(count) for source, count in source_rows}

    unscored = session.execute(
        select(func.count())
        .select_from(CustomerRFM)
        .where(CustomerRFM.business_id == business_id, CustomerRFM.rfm_segment.is_(None))
    ).scalar()

    return {
        "total_customers_scored": total,
        "unscored_customers": _as_int(unscored),
        "segments": ordered,
        # Provenance is surfaced, not hidden: a dashboard built mostly from
        # uploaded columns deserves to say so.
        "sources": {
            "orders": sources.get(SOURCE_ORDERS, 0),
            "import": sources.get(SOURCE_IMPORT, 0),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_segment_distribution(session: Session, business_id: str) -> dict[str, Any]:
    """Cluster counts from the stored segmentation snapshot."""
    rows = session.execute(
        select(
            CustomerSegment.segment,
            CustomerSegment.cluster,
            func.count(),
            func.avg(CustomerSegment.score),
        )
        .where(CustomerSegment.business_id == business_id)
        .group_by(CustomerSegment.segment, CustomerSegment.cluster)
        .order_by(func.count().desc())
    ).all()

    clusters = [
        {
            "segment": segment,
            "cluster": _as_int(cluster) if cluster is not None else None,
            "customers": _as_int(count),
            "avg_score": _as_float(avg_score),
        }
        for segment, cluster, count, avg_score in rows
    ]

    total = sum(entry["customers"] for entry in clusters)

    for entry in clusters:
        entry["share"] = round(entry["customers"] / total, 4) if total else 0.0

    risk_rows = session.execute(
        select(CustomerSegment.churn_risk, func.count())
        .where(CustomerSegment.business_id == business_id, CustomerSegment.churn_risk.is_not(None))
        .group_by(CustomerSegment.churn_risk)
    ).all()

    model_version = session.execute(
        select(CustomerSegment.model_version)
        .where(
            CustomerSegment.business_id == business_id,
            CustomerSegment.model_version.is_not(None),
        )
        .limit(1)
    ).scalar()

    return {
        "total_customers": total,
        "clusters": clusters,
        "churn_risk": {str(risk): _as_int(count) for risk, count in risk_rows},
        "model_version": model_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_top_customers(session: Session, business_id: str, limit: int) -> list[dict[str, Any]]:
    """Highest-value customers, joined to their name where one is known.

    A left join, not an inner one: a customer created as a shell by the orders
    import has no name yet, and dropping the highest spender from the list
    because their name is blank would be worse than showing a blank name.
    """
    rows = session.execute(
        select(
            CustomerRFM.customer_id,
            Customer.name,
            Customer.email,
            Customer.external_customer_id,
            CustomerRFM.monetary,
            CustomerRFM.frequency,
            CustomerRFM.recency,
            CustomerRFM.rfm_segment,
            CustomerRFM.source,
        )
        .join(Customer, Customer.id == CustomerRFM.customer_id, isouter=True)
        .where(CustomerRFM.business_id == business_id, CustomerRFM.monetary.is_not(None))
        .order_by(CustomerRFM.monetary.desc())
        .limit(max(1, min(limit, 100)))
    ).all()

    return [
        {
            "customer_id": customer_id,
            "name": name,
            "email": email,
            "external_customer_id": external_id,
            "monetary": _as_float(monetary),
            "frequency": _as_int(frequency),
            "recency": _as_float(recency) if recency is not None else None,
            "segment": segment,
            "source": source or SOURCE_ORDERS,
        }
        for (
            customer_id,
            name,
            email,
            external_id,
            monetary,
            frequency,
            recency,
            segment,
            source,
        ) in rows
    ]


def _load_inventory_summary(session: Session, business_id: str) -> dict[str, Any]:
    """Stock health, counted in SQL with a single conditional aggregate."""
    threshold = settings.inventory_low_stock_threshold

    totals = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Inventory.quantity), 0),
            func.coalesce(
                func.sum(case((Inventory.quantity <= 0, 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case(((Inventory.quantity > 0) & (Inventory.quantity <= threshold), 1), else_=0)
                ),
                0,
            ),
        ).where(Inventory.business_id == business_id)
    ).one()

    tracked, units, out_of_stock, low_stock = totals

    return {
        "tracked_products": _as_int(tracked),
        "total_units": _as_int(units),
        "out_of_stock": _as_int(out_of_stock),
        "low_stock": _as_int(low_stock),
        "healthy": max(_as_int(tracked) - _as_int(out_of_stock) - _as_int(low_stock), 0),
        "threshold": threshold,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Cached entry points
# ---------------------------------------------------------------------------


async def get_summary(session: Session, *, business_id: str) -> dict[str, Any]:
    return await get_or_set(
        cache_keys.dashboard_summary(business_id),
        lambda: _load_summary(session, business_id),
        settings.cache_dashboard_ttl_seconds,
    )


async def get_revenue_trend(
    session: Session, *, business_id: str, days: int = DEFAULT_TREND_DAYS
) -> dict[str, Any]:
    window = max(1, min(days, MAX_TREND_DAYS))
    return await get_or_set(
        # The window is part of the key. Without it a 7-day request would be
        # served the cached 30-day answer.
        cache_keys.dashboard_revenue_trend(business_id, window),
        lambda: _load_revenue_trend(session, business_id, window),
        settings.cache_dashboard_ttl_seconds,
    )


async def get_rfm_distribution(session: Session, *, business_id: str) -> dict[str, Any]:
    return await get_or_set(
        cache_keys.rfm_distribution(business_id),
        lambda: _load_rfm_distribution(session, business_id),
        settings.cache_analytics_ttl_seconds,
    )


async def get_segment_distribution(session: Session, *, business_id: str) -> dict[str, Any]:
    return await get_or_set(
        cache_keys.segment_summary(business_id),
        lambda: _load_segment_distribution(session, business_id),
        settings.cache_analytics_ttl_seconds,
    )


async def get_top_customers(
    session: Session, *, business_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    bounded = max(1, min(limit, 100))
    return await get_or_set(
        cache_keys.customer_list(business_id, {"view": "top", "limit": bounded}),
        lambda: _load_top_customers(session, business_id, bounded),
        settings.cache_dashboard_ttl_seconds,
    )


async def get_inventory_summary(session: Session, *, business_id: str) -> dict[str, Any]:
    return await get_or_set(
        cache_keys.inventory_summary(business_id),
        lambda: _load_inventory_summary(session, business_id),
        settings.cache_dashboard_ttl_seconds,
    )
