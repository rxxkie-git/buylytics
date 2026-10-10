"""Segmentation adapter over the existing Phase 1 ML code.

This module does not reimplement clustering. It is a thin translation layer:

    RFM rows  ->  DataFrame shaped the way inference.py expects
              ->  app.services.inference.run_segmentation (KMeans + silhouette)
              ->  per-customer cluster / segment / score / churn risk

Honesty rules that matter here:

* pandas, numpy and scikit-learn are imported inside the functions. If the ML
  stack is missing the job fails with :class:`InsufficientDataError` describing
  the reason — it does not fall back to made-up labels.
* Clustering needs enough customers to be meaningful. Below
  :data:`MIN_CUSTOMERS_FOR_CLUSTERING` the job refuses rather than clustering
  four people into four groups.
* The saved model artefacts in ``backend/models`` are placeholder files that
  fail to deserialise. ``run_segmentation`` already handles that by fitting on
  the tenant's own data, and ``model_meta.usedPretrainedModel`` records which
  path ran. That flag is surfaced all the way to the API so the UI can say where
  a label came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from app.analytics.scoring import RfmRow, customer_scores
from app.config import settings
from app.core.exceptions import InsufficientDataError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Fewer customers than this and clusters carry no information.
MIN_CUSTOMERS_FOR_CLUSTERING = 10

#: Column names ``inference.py`` looks for.
FEATURE_COLUMNS = ("recency", "frequency", "monetary", "avgOrderValue", "totalOrders")


@dataclass
class SegmentAssignment:
    customer_id: str
    segment: str
    cluster: int
    score: float
    churn_risk: str | None = None

    def to_model_fields(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "segment": self.segment,
            "cluster": self.cluster,
            "score": self.score,
            "churn_risk": self.churn_risk,
        }


@dataclass
class SegmentationResult:
    assignments: list[SegmentAssignment] = field(default_factory=list)
    silhouette_score: float = 0.0
    cluster_distribution: list[dict[str, Any]] = field(default_factory=list)
    cluster_profiles: list[dict[str, Any]] = field(default_factory=list)
    segment_mix: list[dict[str, Any]] = field(default_factory=list)
    elbow_method: list[dict[str, float]] = field(default_factory=list)
    model_meta: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        """Run summary for ``AnalyticsRun.result_metadata``.

        Deliberately excludes per-customer rows — those live in
        ``customer_segments``; duplicating them into a JSON column would make the
        run record unbounded.
        """
        return {
            "customers_segmented": len(self.assignments),
            "clusters": len({item.cluster for item in self.assignments}),
            "silhouette_score": self.silhouette_score,
            "used_pretrained_model": bool(self.model_meta.get("usedPretrainedModel")),
            "selected_algorithm": self.model_meta.get("selectedAlgorithm"),
            "model_version": self.model_meta.get("modelVersion"),
            "segment_mix": self.segment_mix,
        }


def _cluster_number(cluster_label: Any) -> int:
    """Turn ``"Cluster 3"`` into ``3``; anything unparseable becomes ``0``."""
    try:
        return int(str(cluster_label).strip().split()[-1])
    except (ValueError, IndexError):
        return 0


def run_segmentation_for_rows(rows: Sequence[RfmRow]) -> SegmentationResult:
    """Cluster customers using the Phase 1 inference pipeline.

    ``rows`` must already be filtered to scorable customers (all three RFM
    dimensions present).
    """
    scorable = [row for row in rows if row.is_scorable]

    if len(scorable) < MIN_CUSTOMERS_FOR_CLUSTERING:
        raise InsufficientDataError(
            f"Segmentation needs at least {MIN_CUSTOMERS_FOR_CLUSTERING} customers with "
            f"complete recency, frequency and monetary values; found {len(scorable)}.",
            details={
                "customers_with_complete_rfm": len(scorable),
                "minimum_required": MIN_CUSTOMERS_FOR_CLUSTERING,
            },
        )

    try:
        import pandas as pd
    except ImportError as exc:
        raise InsufficientDataError(
            "Segmentation requires pandas, which is not installed in this environment.",
            code="ml_stack_unavailable",
            details={"missing_dependency": "pandas"},
        ) from exc

    from app.services.inference import load_inference_artifacts, run_segmentation

    frame = pd.DataFrame(
        [
            {
                "id": row.customer_id,
                "recency": float(row.recency or 0.0),
                "frequency": float(row.frequency),
                "monetary": float(row.monetary),
                "avgOrderValue": float(row.average_order_value),
                "totalOrders": int(row.total_orders),
            }
            for row in scorable
        ]
    )

    artifacts = load_inference_artifacts(settings.models_dir, settings.model_version)
    feature_frame = frame[[column for column in FEATURE_COLUMNS if column in frame.columns]]

    inference_result = run_segmentation(frame, artifacts, feature_frame=feature_frame)
    enriched = inference_result.customers

    scores = customer_scores(scorable)
    assignments: list[SegmentAssignment] = []

    for record in enriched.to_dict(orient="records"):
        customer_id = str(record.get("id"))

        assignments.append(
            SegmentAssignment(
                customer_id=customer_id,
                segment=str(record.get("segment") or "Unclassified"),
                cluster=_cluster_number(record.get("cluster")),
                score=scores.get(customer_id, 0.0),
                churn_risk=str(record["churnRisk"]) if record.get("churnRisk") else None,
            )
        )

    logger.info(
        "Segmentation completed",
        extra={
            "customers": len(assignments),
            "silhouette_score": inference_result.silhouette_score,
            "used_pretrained_model": bool(inference_result.model_meta.get("usedPretrainedModel")),
        },
    )

    return SegmentationResult(
        assignments=assignments,
        silhouette_score=float(inference_result.silhouette_score),
        cluster_distribution=inference_result.cluster_distribution,
        cluster_profiles=inference_result.cluster_profiles,
        segment_mix=inference_result.segment_mix,
        elbow_method=inference_result.elbow_method,
        model_meta=inference_result.model_meta,
    )
