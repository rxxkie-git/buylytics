from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, DBSCAN, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import MinMaxScaler, Normalizer, RobustScaler, StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline


@dataclass
class UnsupervisedSelectionResult:
    labels: np.ndarray
    selected_algorithm: str
    candidate_scores: list[dict[str, Any]]
    best_silhouette_score: float


def select_best_unsupervised_model(feature_frame: pd.DataFrame) -> UnsupervisedSelectionResult:
    """Fit unsupervised candidates and return the best silhouette-scoring result.

    The current candidate set is K-Means, Agglomerative Clustering, and DBSCAN.
    Persist the returned metadata alongside the dataset so the selected approach
    can be audited later.
    """

    matrix = _prepare_matrix(feature_frame)

    if matrix.size == 0 or len(matrix) < 2:
        labels = np.zeros(len(feature_frame), dtype=int)
        return UnsupervisedSelectionResult(
            labels=labels,
            selected_algorithm="fallback",
            candidate_scores=[],
            best_silhouette_score=0.0,
        )

    candidates: list[tuple[str, np.ndarray, Any]] = []

    kmeans_labels = _fit_kmeans(matrix)
    if kmeans_labels is not None:
        candidates.append(("kmeans", kmeans_labels, None))

    if len(matrix) <= 5000:
        agg_labels = _fit_agglomerative(matrix)
        if agg_labels is not None:
            candidates.append(("agglomerative", agg_labels, None))

        dbscan_labels = _fit_dbscan(matrix)
        if dbscan_labels is not None:
            candidates.append(("dbscan", dbscan_labels, None))

    scored_candidates: list[dict[str, Any]] = []
    best_labels: np.ndarray | None = None
    best_algorithm = "fallback"
    best_score = float("-inf")

    for algorithm_name, labels, _ in candidates:
        score = _safe_silhouette_score(matrix, labels)
        scored_candidates.append(
            {
                "algorithm": algorithm_name,
                "silhouetteScore": round(score, 4),
                "clusterCount": int(len(set(labels)) - (1 if -1 in set(labels) else 0)),
                "noisePoints": int((labels == -1).sum()) if np.any(labels == -1) else 0,
            }
        )

        if score > best_score:
            best_score = score
            best_labels = labels
            best_algorithm = algorithm_name

    if best_labels is None:
        best_labels = np.zeros(len(feature_frame), dtype=int)
        best_score = 0.0

    return UnsupervisedSelectionResult(
        labels=best_labels,
        selected_algorithm=best_algorithm,
        candidate_scores=scored_candidates,
        best_silhouette_score=round(float(best_score), 4),
    )


def apply_ml_algorithm(
    feature_frame: pd.DataFrame,
    model: Any | None = None,
    preprocessor: Any | None = None,
) -> np.ndarray:
    """Apply the ML algorithm to a preprocessed feature frame.

    Replace the body of this function with your own training/inference logic if you
    want to customize the model behavior. The current implementation keeps the app
    working with the saved joblib artifacts and returns predicted cluster labels.
    """

    if model is None:
        raise NotImplementedError("Implement your ML algorithm here.")

    input_matrix: Any = feature_frame

    if preprocessor is not None:
        input_matrix = preprocessor.transform(feature_frame)

    predictions = model.predict(input_matrix)
    return np.asarray(predictions)


def _prepare_matrix(feature_frame: pd.DataFrame) -> np.ndarray:
    if feature_frame.empty:
        return np.empty((0, 0), dtype=float)

    matrix = feature_frame.copy()
    for column in matrix.columns:
        matrix[column] = pd.to_numeric(matrix[column], errors="coerce")

    matrix = matrix.fillna(matrix.median(numeric_only=True)).fillna(0.0)
    return matrix.to_numpy(dtype=float)


def _fit_kmeans(matrix: np.ndarray) -> np.ndarray | None:
    cluster_count = min(4, len(matrix))
    if cluster_count < 2:
        return None

    try:
        estimator = KMeans(n_clusters=cluster_count, random_state=42, n_init=10)
        return estimator.fit_predict(matrix)
    except Exception:
        return None


def _fit_agglomerative(matrix: np.ndarray) -> np.ndarray | None:
    cluster_count = min(4, len(matrix))
    if cluster_count < 2:
        return None

    try:
        estimator = AgglomerativeClustering(n_clusters=cluster_count)
        return estimator.fit_predict(matrix)
    except Exception:
        return None


def _fit_dbscan(matrix: np.ndarray) -> np.ndarray | None:
    try:
        if len(matrix) < 3:
            return None

        estimator = DBSCAN(eps=0.7, min_samples=3)
        labels = estimator.fit_predict(matrix)
        if len(set(labels)) <= 1:
            return None
        return labels
    except Exception:
        return None


def _safe_silhouette_score(matrix: np.ndarray, labels: np.ndarray) -> float:
    unique_labels = set(labels)
    if len(unique_labels) < 2:
        return 0.0

    try:
        sample_size = min(5000, len(matrix)) if len(matrix) > 5000 else None
        return float(silhouette_score(matrix, labels, sample_size=sample_size, random_state=42))
    except Exception:
        return 0.0
