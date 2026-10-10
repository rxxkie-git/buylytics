from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .ml_algorithm import apply_ml_algorithm
from .ml_algorithm import select_best_unsupervised_model
from .product_analysis import _normalize_name, _to_serializable_records

LOGGER = logging.getLogger(__name__)

CLUSTER_COLORS = ["#0F9D8A", "#2563EB", "#F97316", "#A855F7"]
SEGMENT_COLORS = {
    "High Value Customers": "#0F9D8A",
    "Loyal Customers": "#2563EB",
    "At Risk Customers": "#F97316",
    "Low Value Customers": "#A855F7",
}
SEGMENT_SHORT_LABELS = {
    "High Value Customers": "High Value",
    "Loyal Customers": "Loyal",
    "At Risk Customers": "At Risk",
    "Low Value Customers": "Low Value",
}
DEFAULT_FEATURE_COLUMNS = ["recency", "frequency", "monetary", "avgOrderValue", "totalOrders"]

CUSTOMER_CLUSTER_FEATURE_OPTIONS = {
    "default": ["recency", "frequency", "monetary"],
    "recency_frequency": ["recency", "frequency"],
    "recency_monetary": ["recency", "monetary"],
    "frequency_monetary": ["frequency", "monetary"],
    "recency": ["recency"],
    "frequency": ["frequency"],
    "monetary": ["monetary"],
}


@dataclass
class InferenceArtifacts:
    model: Any | None
    preprocessor: Any | None
    feature_columns: list[str]
    model_version: str


@dataclass
class InferenceResult:
    customers: pd.DataFrame
    cluster_distribution: list[dict[str, Any]]
    cluster_profiles: list[dict[str, Any]]
    segment_mix: list[dict[str, Any]]
    silhouette_score: float
    elbow_method: list[dict[str, float]]
    model_meta: dict[str, Any]


def load_inference_artifacts(models_dir: Path, model_version: str) -> InferenceArtifacts:
    feature_columns_path = models_dir / "feature_columns.json"
    model_path = models_dir / "model.joblib"
    preprocessor_path = models_dir / "preprocessor.joblib"

    feature_columns = DEFAULT_FEATURE_COLUMNS.copy()

    if feature_columns_path.exists():
        try:
            content = json.loads(feature_columns_path.read_text(encoding="utf-8"))
            if isinstance(content, list) and content:
                feature_columns = [str(column) for column in content]
        except Exception as exc:  # pragma: no cover - defensive parsing
            LOGGER.warning("Unable to parse feature columns from %s: %s", feature_columns_path, exc)

    model = _safe_joblib_load(model_path)
    preprocessor = _safe_joblib_load(preprocessor_path)

    return InferenceArtifacts(
        model=model,
        preprocessor=preprocessor,
        feature_columns=feature_columns,
        model_version=model_version,
    )


def run_segmentation(
    customers: pd.DataFrame,
    artifacts: InferenceArtifacts,
    feature_frame: pd.DataFrame | None = None,
) -> InferenceResult:
    working = customers.copy()
    feature_frame_for_model = _prepare_feature_frame(working, artifacts.feature_columns)
    feature_matrix = feature_frame_for_model.to_numpy(dtype=float)
    scaled_matrix = _scale_matrix(feature_matrix)
    engineered_feature_frame = _prepare_engineered_feature_frame(feature_frame)

    raw_clusters: np.ndarray | None = None
    used_pretrained_model = False
    selected_algorithm = "fallback"
    candidate_scores: list[dict[str, Any]] = []
    best_silhouette_score = 0.0

    if artifacts.model is not None:
        prediction_frames = [frame for frame in [engineered_feature_frame, feature_frame_for_model] if frame is not None]
        prediction_error: Exception | None = None

        for prediction_frame in prediction_frames:
            try:
                raw_clusters = _predict_with_artifacts(prediction_frame, artifacts)
                used_pretrained_model = True
                break
            except Exception as exc:  # pragma: no cover - model-specific edge cases
                prediction_error = exc

        if raw_clusters is None and prediction_error is not None:
            LOGGER.warning(
                "Model prediction failed, falling back to heuristic segmentation: %s",
                prediction_error,
            )
            used_pretrained_model = False

    if raw_clusters is None:
        selection = select_best_unsupervised_model(
            engineered_feature_frame if engineered_feature_frame is not None else feature_frame_for_model
        )
        raw_clusters = selection.labels
        selected_algorithm = selection.selected_algorithm
        candidate_scores = selection.candidate_scores
        best_silhouette_score = selection.best_silhouette_score
    else:
        selected_algorithm = "saved-artifact"

    enriched_customers = _assign_labels(working, raw_clusters)

    cluster_distribution = _build_cluster_distribution(enriched_customers)
    cluster_profiles = _build_cluster_profiles(enriched_customers)
    segment_mix = _build_segment_mix(enriched_customers)

    silhouette = _compute_silhouette_score(scaled_matrix, enriched_customers["cluster"])
    elbow_method = _compute_elbow_curve(scaled_matrix)

    model_meta = {
        "usedPretrainedModel": used_pretrained_model,
        "modelVersion": artifacts.model_version,
        "featureColumns": artifacts.feature_columns,
        "selectedAlgorithm": selected_algorithm,
        "candidateScores": candidate_scores,
        "bestSilhouetteScore": best_silhouette_score,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    return InferenceResult(
        customers=enriched_customers,
        cluster_distribution=cluster_distribution,
        cluster_profiles=cluster_profiles,
        segment_mix=segment_mix,
        silhouette_score=silhouette,
        elbow_method=elbow_method,
        model_meta=model_meta,
    )


def _safe_joblib_load(path: Path) -> Any | None:
    if not path.exists() or path.stat().st_size == 0:
        return None

    try:
        return joblib.load(path)
    except Exception as exc:  # pragma: no cover - model-specific edge cases
        LOGGER.warning("Unable to load model artifact %s: %s", path, exc)
        return None


def _prepare_feature_frame(customers: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(index=customers.index)

    for column in feature_columns:
        if column in customers.columns:
            frame[column] = pd.to_numeric(customers[column], errors="coerce")
        else:
            frame[column] = 0.0

    return frame.fillna(frame.median(numeric_only=True)).fillna(0.0)


def _get_customer_cluster_features(cluster_by: str) -> list[str]:
    normalized = _normalize_name(cluster_by or "default")
    return CUSTOMER_CLUSTER_FEATURE_OPTIONS.get(normalized, CUSTOMER_CLUSTER_FEATURE_OPTIONS["default"])


def rerun_customer_clustering(
    customers: pd.DataFrame,
    cluster_by: str = "default",
    cluster_count: int = 4,
) -> dict[str, Any]:
    cluster_columns = _get_customer_cluster_features(cluster_by)
    available_columns = [col for col in cluster_columns if col in customers.columns]

    if not available_columns or customers.empty:
        return {
            "available": False,
            "message": "Insufficient customer attributes available for cluster reanalysis.",
            "totalClusters": 0,
            "optimalK": 0,
            "silhouetteScore": 0.0,
            "clusterDistribution": [],
            "scatterData": [],
            "elbowMethod": [],
            "clusterProfiles": [],
            "modelMeta": {
                "selectedAlgorithm": "kmeans",
                "bestSilhouetteScore": 0.0,
                "candidateScores": [],
            },
            "clusterBy": cluster_by,
            "featureColumns": available_columns,
        }

    feature_frame = customers[available_columns].copy()
    feature_frame = feature_frame.apply(pd.to_numeric, errors="coerce").fillna(feature_frame.median(numeric_only=True)).fillna(0.0)
    matrix = feature_frame.to_numpy(dtype=float)
    scaled_matrix = _scale_matrix(matrix)

    n_clusters = min(max(2, int(cluster_count)), len(customers))
    try:
        # Imported here rather than at module scope so this module still loads
        # when scikit-learn is absent. Without this import the call below raised
        # NameError and was silently swallowed by the except clause, collapsing
        # every customer into a single cluster.
        from sklearn.cluster import KMeans

        model = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = model.fit_predict(scaled_matrix)
    except Exception as exc:
        LOGGER.warning("KMeans reanalysis unavailable, using a single cluster: %s", exc)
        labels = np.zeros(len(customers), dtype=int)
        n_clusters = 1

    clustered = customers.copy()
    clustered["cluster_id"] = labels
    clustered["cluster"] = clustered["cluster_id"].apply(lambda value: f"Cluster {int(value) + 1}")
    clustered["segment"] = clustered["cluster"]
    clustered["segmentShortLabel"] = clustered["cluster"]

    cluster_profiles = (
        clustered.groupby("cluster", as_index=False)
        .agg(
            customers=("cluster", "count"),
            avgRecency=("recency", "mean"),
            avgFrequency=("frequency", "mean"),
            avgMonetary=("monetary", "mean"),
        )
        .sort_values("cluster")
        .reset_index(drop=True)
    )

    cluster_distribution = []
    for index, row in cluster_profiles.iterrows():
        cluster_distribution.append(
            {
                "cluster": row["cluster"],
                "label": row["cluster"],
                "customers": int(row["customers"]),
                "fill": CLUSTER_COLORS[index % len(CLUSTER_COLORS)],
            }
        )

    sample_frame = clustered if len(clustered) <= 2000 else clustered.sample(n=2000, random_state=42)
    ids = sample_frame["id"].tolist() if "id" in sample_frame.columns else [None] * len(sample_frame)
    clusters = sample_frame["cluster"].tolist()
    cluster_ids = sample_frame["cluster_id"].tolist()
    recencies = sample_frame.get("recency", pd.Series(0.0, index=sample_frame.index)).astype(float).tolist()
    frequencies = sample_frame.get("frequency", pd.Series(0.0, index=sample_frame.index)).astype(float).tolist()
    monetaries = sample_frame.get("monetary", pd.Series(0.0, index=sample_frame.index)).astype(float).tolist()

    scatter_data: list[dict[str, Any]] = [
        {
            "customerId": cid,
            "cluster": cl,
            "recency": r,
            "frequency": f,
            "monetary": m,
            "fill": CLUSTER_COLORS[int(c_id) % len(CLUSTER_COLORS)],
        }
        for cid, cl, c_id, r, f, m in zip(ids, clusters, cluster_ids, recencies, frequencies, monetaries)
    ]

    silhouette = _compute_silhouette_score(scaled_matrix, clustered["cluster"])
    elbow_method = _compute_elbow_curve(scaled_matrix)

    candidate_scores = [
        {
            "algorithm": "kmeans",
            "silhouetteScore": round(float(silhouette), 4),
            "clusterCount": int(n_clusters),
            "noisePoints": 0,
        }
    ]

    model_meta = {
        "selectedAlgorithm": "kmeans",
        "bestSilhouetteScore": round(float(silhouette), 4),
        "candidateScores": candidate_scores,
        "featureColumns": available_columns,
        "clusterBy": cluster_by,
    }

    return {
        "available": True,
        "totalClusters": int(clustered["cluster"].nunique()),
        "optimalK": int(n_clusters),
        "silhouetteScore": float(silhouette),
        "clusterDistribution": cluster_distribution,
        "scatterData": scatter_data,
        "elbowMethod": elbow_method,
        "clusterProfiles": _to_serializable_records(cluster_profiles),
        "modelMeta": model_meta,
        "clusterBy": cluster_by,
        "featureColumns": available_columns,
    }


def _predict_with_artifacts(feature_frame: pd.DataFrame, artifacts: InferenceArtifacts) -> np.ndarray:
    predictions_array = apply_ml_algorithm(
        feature_frame=feature_frame,
        model=artifacts.model,
        preprocessor=artifacts.preprocessor,
    )

    if len(predictions_array) != len(feature_frame):
        raise ValueError("Prediction count does not match input rows.")

    return predictions_array


def _prepare_engineered_feature_frame(feature_frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if feature_frame is None or feature_frame.empty:
        return None

    prepared = feature_frame.copy()

    for column in prepared.columns:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    prepared = prepared.fillna(prepared.median(numeric_only=True)).fillna(0.0)
    return prepared


def _scale_matrix(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix

    means = matrix.mean(axis=0)
    stds = matrix.std(axis=0)
    stds[stds == 0] = 1

    return (matrix - means) / stds


def _heuristic_clusters(feature_frame: pd.DataFrame, scaled_matrix: np.ndarray) -> np.ndarray:
    row_count = len(feature_frame)

    if row_count == 0:
        return np.array([])

    def column_or_zero(column_name: str) -> np.ndarray:
        if column_name not in feature_frame.columns:
            return np.zeros(row_count)
        column_index = feature_frame.columns.get_loc(column_name)
        return scaled_matrix[:, column_index]

    monetary = column_or_zero("monetary")
    frequency = column_or_zero("frequency")
    recency = column_or_zero("recency")

    score = monetary * 0.55 + frequency * 0.35 - recency * 0.25
    thresholds = np.quantile(score, [0.25, 0.5, 0.75])

    return np.digitize(score, thresholds, right=True)


def _assign_labels(customers: pd.DataFrame, raw_clusters: np.ndarray) -> pd.DataFrame:
    working = customers.copy()
    working["_rawCluster"] = pd.Series(raw_clusters, index=working.index).astype(str)

    profiles = (
        working.groupby("_rawCluster", as_index=False)
        .agg(
            avgRecency=("recency", "mean"),
            avgFrequency=("frequency", "mean"),
            avgMonetary=("monetary", "mean"),
        )
        .sort_values("_rawCluster")
        .reset_index(drop=True)
    )

    if profiles.empty:
        working["cluster"] = "Cluster 1"
        working["segment"] = "Low Value Customers"
        working["segmentShortLabel"] = "Low Value"
        return working.drop(columns=["_rawCluster"])

    score = (
        _normalize_series(profiles["avgMonetary"]) * 0.5
        + _normalize_series(profiles["avgFrequency"]) * 0.35
        + (1 - _normalize_series(profiles["avgRecency"])) * 0.15
    )

    profiles["score"] = score
    ordered_clusters = profiles.sort_values("score", ascending=False)["_rawCluster"].tolist()

    cluster_name_map = {
        cluster_key: f"Cluster {index + 1}"
        for index, cluster_key in enumerate(ordered_clusters)
    }
    segment_map = _assign_segment_labels(ordered_clusters, profiles)

    working["cluster"] = working["_rawCluster"].map(cluster_name_map)
    working["segment"] = working["_rawCluster"].map(segment_map).fillna("Low Value Customers")
    working["segmentShortLabel"] = (
        working["segment"].map(SEGMENT_SHORT_LABELS).fillna("Low Value")
    )
    working["churnRisk"] = working["recency"].apply(_to_churn_risk)

    return working.drop(columns=["_rawCluster"])


def _normalize_series(series: pd.Series) -> pd.Series:
    min_value = float(series.min())
    max_value = float(series.max())

    if max_value == min_value:
        return pd.Series([0.5] * len(series), index=series.index)

    return (series - min_value) / (max_value - min_value)


def _assign_segment_labels(
    ordered_clusters: list[str],
    profiles: pd.DataFrame,
) -> dict[str, str]:
    labels: dict[str, str] = {}

    if not ordered_clusters:
        return labels

    labels[ordered_clusters[0]] = "High Value Customers"

    if len(ordered_clusters) > 1:
        labels[ordered_clusters[1]] = "Loyal Customers"

    remaining_clusters = [
        cluster for cluster in ordered_clusters if cluster not in {ordered_clusters[0], ordered_clusters[1] if len(ordered_clusters) > 1 else None}
    ]

    if remaining_clusters:
        profile_map = profiles.set_index("_rawCluster").to_dict(orient="index")
        at_risk_cluster = max(
            remaining_clusters,
            key=lambda cluster_key: float(profile_map[cluster_key]["avgRecency"]),
        )
        labels[at_risk_cluster] = "At Risk Customers"

        for cluster_key in remaining_clusters:
            if cluster_key != at_risk_cluster:
                labels[cluster_key] = "Low Value Customers"

    for cluster_key in ordered_clusters:
        labels.setdefault(cluster_key, "Low Value Customers")

    return labels


def _build_cluster_distribution(customers: pd.DataFrame) -> list[dict[str, Any]]:
    grouped = (
        customers.groupby(["cluster", "segmentShortLabel"], as_index=False)
        .agg(customers=("id", "count"))
        .sort_values("cluster")
        .reset_index(drop=True)
    )

    distribution: list[dict[str, Any]] = []

    for _, row in grouped.iterrows():
        cluster_index = _cluster_index(row["cluster"])
        distribution.append(
            {
                "cluster": row["cluster"],
                "label": row["segmentShortLabel"],
                "customers": int(row["customers"]),
                "fill": CLUSTER_COLORS[cluster_index % len(CLUSTER_COLORS)],
            }
        )

    return distribution


def _build_cluster_profiles(customers: pd.DataFrame) -> list[dict[str, Any]]:
    grouped = (
        customers.groupby(["cluster", "segmentShortLabel"], as_index=False)
        .agg(
            avgRecency=("recency", "mean"),
            avgFrequency=("frequency", "mean"),
            avgMonetary=("monetary", "mean"),
        )
        .sort_values("cluster")
        .reset_index(drop=True)
    )

    profiles: list[dict[str, Any]] = []

    for _, row in grouped.iterrows():
        cluster_index = _cluster_index(row["cluster"])
        profiles.append(
            {
                "cluster": row["cluster"],
                "label": row["segmentShortLabel"],
                "fill": CLUSTER_COLORS[cluster_index % len(CLUSTER_COLORS)],
                "avgRecency": round(float(row["avgRecency"]), 1),
                "avgFrequency": round(float(row["avgFrequency"]), 1),
                "avgMonetary": int(round(float(row["avgMonetary"]))),
            }
        )

    return profiles


def _build_segment_mix(customers: pd.DataFrame) -> list[dict[str, Any]]:
    grouped = (
        customers.groupby(["segment", "segmentShortLabel"], as_index=False)
        .agg(value=("id", "count"))
        .sort_values("value", ascending=False)
        .reset_index(drop=True)
    )

    segment_mix: list[dict[str, Any]] = []

    for _, row in grouped.iterrows():
        segment_mix.append(
            {
                "name": row["segmentShortLabel"],
                "value": int(row["value"]),
                "fill": SEGMENT_COLORS.get(row["segment"], "#64748B"),
                "segment": row["segment"],
            }
        )

    return segment_mix


def _compute_silhouette_score(matrix: np.ndarray, clusters: pd.Series) -> float:
    try:
        from sklearn.metrics import silhouette_score
    except Exception:
        return 0.0

    if len(matrix) < 3:
        return 0.0

    labels, _ = pd.factorize(clusters)

    if len(np.unique(labels)) < 2:
        return 0.0

    try:
        sample_size = min(5000, len(matrix)) if len(matrix) > 5000 else None
        score = float(silhouette_score(matrix, labels, sample_size=sample_size, random_state=42))
    except Exception:
        return 0.0

    score = max(0.0, min(1.0, score))
    return round(score, 2)


def _compute_elbow_curve(matrix: np.ndarray) -> list[dict[str, float]]:
    try:
        from sklearn.cluster import KMeans
    except Exception:
        return _fallback_elbow_curve(matrix)

    if len(matrix) < 3:
        return _fallback_elbow_curve(matrix)

    # Subsample large matrices for fast elbow computation without memory spike
    if len(matrix) > 10000:
        rng = np.random.default_rng(42)
        sample_indices = rng.choice(len(matrix), size=10000, replace=False)
        sub_matrix = matrix[sample_indices]
    else:
        sub_matrix = matrix

    max_k = min(6, len(sub_matrix) - 1)
    points: list[dict[str, float]] = []

    for k in range(2, max_k + 1):
        try:
            model = KMeans(n_clusters=k, random_state=42, n_init=10)
            model.fit(sub_matrix)
            scale_factor = len(matrix) / len(sub_matrix)
            points.append({"k": k, "inertia": round(float(model.inertia_ * scale_factor), 2)})
        except Exception:
            return _fallback_elbow_curve(matrix)

    return points or _fallback_elbow_curve(matrix)


def _fallback_elbow_curve(matrix: np.ndarray) -> list[dict[str, float]]:
    variance = float(np.var(matrix)) if matrix.size else 1.0
    base = max(300.0, variance * max(len(matrix), 1) * 100)

    return [
        {"k": 2, "inertia": round(base, 2)},
        {"k": 3, "inertia": round(base * 0.68, 2)},
        {"k": 4, "inertia": round(base * 0.46, 2)},
        {"k": 5, "inertia": round(base * 0.38, 2)},
        {"k": 6, "inertia": round(base * 0.33, 2)},
    ]


def _cluster_index(cluster_name: str) -> int:
    try:
        return max(int(str(cluster_name).split()[-1]) - 1, 0)
    except Exception:
        return 0


def _to_churn_risk(recency: int) -> str:
    if int(recency) > 60:
        return "High"
    if int(recency) > 30:
        return "Medium"
    return "Low"
