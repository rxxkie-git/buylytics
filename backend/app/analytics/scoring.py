"""RFM domain logic: measurements, scoring and labelling.

Deliberately free of SQLAlchemy, pandas and scikit-learn. Everything here is
plain Python over plain values, which means the scoring rules can be tested
without a database or the ML stack — and it keeps the "what do these numbers
mean" decisions in one readable place.

:mod:`app.analytics.rfm` supplies the measurements by querying orders;
:mod:`app.analytics.segmentation` consumes them.

The one rule that governs this whole module: **nothing is invented.** A missing
dimension stays ``None`` and its score stays ``None``. Phase 1's
``app.services.preprocess.build_customer_frame`` generated synthetic recency for
demo purposes; nothing here calls it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

#: Number of quantile buckets. Five is the RFM convention.
SCORE_BUCKETS = 5

#: Provenance markers stored on ``customer_rfm.source``.
SOURCE_ORDERS = "orders"
SOURCE_IMPORT = "import"


@dataclass
class RfmRow:
    """One customer's RFM measurements.

    ``recency`` is ``None`` when the customer has orders but none carry a date.
    That is recorded as unknown rather than guessed, which is why the field is
    optional all the way through to the database column.
    """

    customer_id: str
    frequency: int
    monetary: float
    total_orders: int
    average_order_value: float
    recency: float | None = None
    last_order_at: datetime | None = None
    source: str = SOURCE_ORDERS
    r_score: int | None = None
    f_score: int | None = None
    m_score: int | None = None
    rfm_segment: str | None = None

    @property
    def is_scorable(self) -> bool:
        """Whether all three dimensions are present, as scoring requires."""
        return self.recency is not None and self.frequency > 0 and self.monetary is not None

    def to_model_fields(self) -> dict[str, Any]:
        """Shape this row for :class:`app.db.models.analytics.CustomerRFM`."""
        return {
            "customer_id": self.customer_id,
            "recency": self.recency,
            "frequency": float(self.frequency),
            "monetary": float(self.monetary),
            "total_orders": int(self.total_orders),
            "average_order_value": float(self.average_order_value),
            "r_score": self.r_score,
            "f_score": self.f_score,
            "m_score": self.m_score,
            "rfm_segment": self.rfm_segment,
            "source": self.source,
        }

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "recency": self.recency,
            "frequency": self.frequency,
            "monetary": round(float(self.monetary), 2),
            "total_orders": self.total_orders,
            "average_order_value": round(float(self.average_order_value), 2),
            "r_score": self.r_score,
            "f_score": self.f_score,
            "m_score": self.m_score,
            "rfm_segment": self.rfm_segment,
            "source": self.source,
            "last_order_at": self.last_order_at.isoformat() if self.last_order_at else None,
        }


# ---------------------------------------------------------------------------
# Quintile scoring
# ---------------------------------------------------------------------------


def quantile_thresholds(values: Sequence[float], buckets: int = SCORE_BUCKETS) -> list[float]:
    """Cut points splitting ``values`` into ``buckets`` roughly equal groups.

    Midpoints between neighbouring order statistics are used rather than exact
    order statistics, so a value equal to a boundary lands in the higher bucket
    predictably instead of depending on floating-point equality.
    """
    ordered = sorted(float(value) for value in values)
    count = len(ordered)

    if count == 0 or buckets < 2:
        return []

    thresholds: list[float] = []

    for index in range(1, buckets):
        position = index * count / buckets
        floor_position = int(math.floor(position))
        lower = ordered[max(0, min(count - 1, floor_position - 1))]
        upper = ordered[max(0, min(count - 1, floor_position))]
        thresholds.append((lower + upper) / 2.0)

    return thresholds


def bucket_of(value: float, thresholds: Sequence[float]) -> int:
    """Map ``value`` to ``1..len(thresholds)+1``, ascending."""
    score = 1

    for threshold in thresholds:
        if value > threshold:
            score += 1

    return min(score, SCORE_BUCKETS)


def score_rfm(rows: Iterable[RfmRow]) -> list[RfmRow]:
    """Attach quintile scores and a segment label in place, returning the rows.

    Scores are relative to this business's own customer base — a "5" means top
    quintile for this tenant, not an absolute threshold. Customers missing any
    dimension keep ``None`` scores and no label rather than being bucketed on
    incomplete data.

    With very few customers the scores are still assigned but carry little
    meaning, so callers should report how many customers were scored alongside
    the results.
    """
    row_list = list(rows)
    scorable = [row for row in row_list if row.is_scorable]

    if not scorable:
        return row_list

    recency_cuts = quantile_thresholds([float(row.recency) for row in scorable])
    frequency_cuts = quantile_thresholds([float(row.frequency) for row in scorable])
    monetary_cuts = quantile_thresholds([float(row.monetary) for row in scorable])

    for row in scorable:
        # Recency is inverted: fewer days since the last order is better.
        row.r_score = SCORE_BUCKETS + 1 - bucket_of(float(row.recency), recency_cuts)
        row.f_score = bucket_of(float(row.frequency), frequency_cuts)
        row.m_score = bucket_of(float(row.monetary), monetary_cuts)
        row.rfm_segment = rfm_segment_label(row.r_score, row.f_score, row.m_score)

    return row_list


def rfm_segment_label(r_score: int, f_score: int, m_score: int) -> str:
    """Map R/F/M scores to a standard RFM segment name.

    Rules are ordered from most to least valuable; the first match wins. This is
    the conventional RFM grid, compressed to nine names so a dashboard legend
    stays readable.
    """
    if r_score >= 4 and f_score >= 4 and m_score >= 4:
        return "Champions"

    if f_score >= 4 and m_score >= 4:
        # Buys often and spends well — the only question is whether they are
        # still active.
        return "Cannot Lose Them" if r_score <= 2 else "Loyal Customers"

    if r_score >= 4 and f_score <= 1:
        return "New Customers"

    if r_score >= 4:
        return "Potential Loyalist"

    if r_score == 3 and f_score >= 3:
        return "Needs Attention"

    if r_score == 3:
        return "About To Sleep"

    if f_score >= 3 or m_score >= 4:
        return "At Risk"

    return "Hibernating"


#: Display order for dashboards, so bars do not reshuffle between refreshes.
RFM_SEGMENT_ORDER = (
    "Champions",
    "Loyal Customers",
    "Potential Loyalist",
    "New Customers",
    "Needs Attention",
    "About To Sleep",
    "Cannot Lose Them",
    "At Risk",
    "Hibernating",
)


def segment_distribution(rows: Iterable[RfmRow]) -> list[dict[str, Any]]:
    """Count customers per RFM segment, in a stable display order."""
    counts: dict[str, int] = {}

    for row in rows:
        if row.rfm_segment:
            counts[row.rfm_segment] = counts.get(row.rfm_segment, 0) + 1

    total = sum(counts.values())
    ordered = [name for name in RFM_SEGMENT_ORDER if name in counts]
    ordered += sorted(name for name in counts if name not in RFM_SEGMENT_ORDER)

    return [
        {
            "segment": name,
            "customers": counts[name],
            "share": round(counts[name] / total, 4) if total else 0.0,
        }
        for name in ordered
    ]


def normalise(values: Sequence[float]) -> list[float]:
    """Min-max scale to 0..1; a flat series becomes all 0.5."""
    if not values:
        return []

    numeric = [float(value) for value in values]
    low = min(numeric)
    high = max(numeric)

    if high == low:
        return [0.5] * len(numeric)

    span = high - low
    return [(value - low) / span for value in numeric]


def customer_scores(rows: Sequence[RfmRow]) -> dict[str, float]:
    """Per-customer value score on 0..100.

    Uses the same weighting ``inference._assign_labels`` applies to cluster
    averages (monetary 0.50, frequency 0.35, inverted recency 0.15), evaluated
    per customer instead of per cluster so two customers in one cluster can still
    be ranked. Identical weights mean the score can never contradict the label.
    """
    if not rows:
        return {}

    monetary = normalise([float(row.monetary) for row in rows])
    frequency = normalise([float(row.frequency) for row in rows])
    recency = normalise([float(row.recency or 0.0) for row in rows])

    return {
        row.customer_id: round(
            (monetary[index] * 0.50 + frequency[index] * 0.35 + (1 - recency[index]) * 0.15) * 100,
            2,
        )
        for index, row in enumerate(rows)
    }


# ---------------------------------------------------------------------------
# Passthrough of genuine RFM columns from an upload
# ---------------------------------------------------------------------------

#: Column spellings accepted as real RFM values in an uploaded file.
RECENCY_ALIASES = (
    "recency",
    "days_since_last_order",
    "dayssincelastorder",
    "last_order_days",
    "days_since_purchase",
)
FREQUENCY_ALIASES = (
    "frequency",
    "order_count",
    "ordercount",
    "total_orders",
    "totalorders",
    "purchases",
    "purchase_count",
)
MONETARY_ALIASES = (
    "monetary",
    "total_spend",
    "totalspend",
    "revenue",
    "total_amount",
    "lifetime_value",
    "lifetimevalue",
)


def _normalise_header(name: Any) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def _first_numeric(record: dict[str, Any], aliases: Sequence[str]) -> float | None:
    """Find the first alias present in ``record`` holding a usable number.

    Header matching is case- and separator-insensitive, because real uploads
    spell things ``Total Spend``, ``total-spend`` and ``TOTALSPEND``.
    """
    normalised = {_normalise_header(key): value for key, value in record.items()}

    for alias in aliases:
        if alias not in normalised:
            continue

        value = normalised[alias]

        if value is None or value == "":
            continue

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue

        if math.isnan(numeric) or math.isinf(numeric):
            continue

        return numeric

    return None


def has_real_rfm_columns(record: dict[str, Any]) -> bool:
    """Whether a parsed upload row carries all three genuine RFM values."""
    return (
        _first_numeric(record, RECENCY_ALIASES) is not None
        and _first_numeric(record, FREQUENCY_ALIASES) is not None
        and _first_numeric(record, MONETARY_ALIASES) is not None
    )


def rfm_from_import_record(customer_id: str, record: dict[str, Any]) -> RfmRow | None:
    """Build an RFM row from real columns in an uploaded file.

    Returns ``None`` when any of the three values is absent, negative or
    nonsensical. Partial data is never completed with a guess.
    """
    recency = _first_numeric(record, RECENCY_ALIASES)
    frequency = _first_numeric(record, FREQUENCY_ALIASES)
    monetary = _first_numeric(record, MONETARY_ALIASES)

    if recency is None or frequency is None or monetary is None:
        return None

    if frequency < 1 or recency < 0 or monetary < 0:
        return None

    order_count = int(frequency)

    return RfmRow(
        customer_id=str(customer_id),
        frequency=order_count,
        monetary=round(float(monetary), 2),
        total_orders=order_count,
        average_order_value=round(float(monetary) / order_count, 2),
        recency=round(float(recency), 2),
        source=SOURCE_IMPORT,
    )
