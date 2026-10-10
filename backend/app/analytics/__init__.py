"""Analytics engine.

Modules
-------
``scoring``
    Pure RFM domain logic: quintile scoring, segment labelling and passthrough of
    genuine RFM columns from an upload. No SQLAlchemy, no pandas — the rules are
    testable on their own.
``rfm``
    Measurement: aggregates real recency / frequency / monetary from the orders
    tables, every query scoped by ``business_id``.
``segmentation``
    Thin adapter onto the existing Phase 1 clustering in
    :mod:`app.services.inference`. Raises rather than inventing labels when the
    data or the ML stack is not there.
``engine``
    Job execution: compute, persist a snapshot, return run metadata.

Only ``scoring`` is re-exported here. ``rfm``, ``segmentation`` and ``engine``
are imported from their own modules on purpose: this keeps the domain rules
loadable — and unit-testable — without SQLAlchemy or the ML stack present.

Nothing in this package fabricates a number. A customer with no order history is
left out of the results; a job with too little data fails with
``insufficient_data`` and an explanation.
"""

from __future__ import annotations

from app.analytics.scoring import (
    RFM_SEGMENT_ORDER,
    SCORE_BUCKETS,
    SOURCE_IMPORT,
    SOURCE_ORDERS,
    RfmRow,
    bucket_of,
    customer_scores,
    has_real_rfm_columns,
    normalise,
    quantile_thresholds,
    rfm_from_import_record,
    rfm_segment_label,
    score_rfm,
    segment_distribution,
)

__all__ = [
    "RFM_SEGMENT_ORDER",
    "RfmRow",
    "SCORE_BUCKETS",
    "SOURCE_IMPORT",
    "SOURCE_ORDERS",
    "bucket_of",
    "customer_scores",
    "has_real_rfm_columns",
    "normalise",
    "quantile_thresholds",
    "rfm_from_import_record",
    "rfm_segment_label",
    "score_rfm",
    "segment_distribution",
]
