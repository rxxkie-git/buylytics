from __future__ import annotations

from typing import Any

import pandas as pd

from .inference import SEGMENT_COLORS
from .preprocess import PREVIEW_COLUMNS, PREVIEW_COLUMN_IDS, to_serializable_records


def build_preview_payload(
    customers: pd.DataFrame,
    quality: dict[str, int],
    upload_meta: dict[str, Any],
) -> dict[str, Any]:
    preview_rows = customers[PREVIEW_COLUMN_IDS].head(200)

    return {
        "rows": to_serializable_records(preview_rows),
        "columns": PREVIEW_COLUMNS,
        "quality": quality,
        "uploadMeta": upload_meta,
    }


def build_dashboard_payload(
    customers: pd.DataFrame,
    upload_meta: dict[str, Any],
    quality: dict[str, int],
    segment_mix: list[dict[str, Any]],
    model_meta: dict[str, Any],
) -> dict[str, Any]:
    total_customers = len(customers)
    total_revenue = float(customers["monetary"].sum())
    total_orders = float(customers["totalOrders"].sum())
    avg_purchase_value = total_revenue / max(total_orders, 1)
    repeat_purchase_rate = (
        (customers["frequency"] > 1).sum() / max(total_customers, 1)
    ) * 100

    readiness_score = 100
    if total_customers > 0 and quality["totalColumns"] > 0:
        readiness_score = max(
            0,
            round(
                100
                - (
                    quality["missingCells"]
                    / (quality["totalRows"] * quality["totalColumns"])
                    * 100
                )
            ),
        )

    return {
        "kpis": [
            {
                "id": "total-customers",
                "label": "Total Customers",
                "value": int(total_customers),
                "delta": f"+{repeat_purchase_rate:.1f}%",
                "tone": "success",
                "description": "Active profiles in the latest uploaded dataset",
            },
            {
                "id": "total-revenue",
                "label": "Total Revenue",
                "value": int(round(total_revenue)),
                "delta": "+10.0%",
                "tone": "primary",
                "description": "Monetary contribution captured for clustering",
            },
            {
                "id": "avg-purchase-value",
                "label": "Avg Purchase Value",
                "value": round(avg_purchase_value, 2),
                "delta": "+4.0%",
                "tone": "secondary",
                "description": "Average value per transaction across customers",
            },
        ],
        "purchaseTrends": _build_purchase_trends(customers),
        "frequencyDistribution": _build_frequency_distribution(customers),
        "segmentMix": [
            {
                "name": item["name"],
                "value": item["value"],
                "fill": item["fill"],
            }
            for item in segment_mix
        ],
        "summaryCards": [
            {
                "title": "Model Readiness",
                "value": f"{readiness_score}%",
                "subtitle": "Dataset completeness estimate before model scoring.",
            },
            {
                "title": "Total Items Present",
                "value": str(quality.get("totalItems", 0)),
                "subtitle": "Unique products or items identified in dataset.",
            },
            {
                "title": "Missing Value Rows",
                "value": quality["rowsWithMissingValues"],
                "subtitle": "Rows with at least one empty or null field.",
            },
            {
                "title": "Repeat Purchase Rate",
                "value": f"{repeat_purchase_rate:.0f}%",
                "subtitle": "Customers with more than one repeat purchase.",
            },
        ],
        "modelMeta": model_meta,
        "uploadMeta": upload_meta,
    }


def build_analysis_payload(
    customers: pd.DataFrame,
    cluster_distribution: list[dict[str, Any]],
    cluster_profiles: list[dict[str, Any]],
    silhouette_score: float,
    elbow_method: list[dict[str, Any]],
    model_meta: dict[str, Any],
) -> dict[str, Any]:
    sample_customers = customers if len(customers) <= 2000 else customers.sample(n=2000, random_state=42)

    ids = sample_customers["id"].tolist() if "id" in sample_customers.columns else [None] * len(sample_customers)
    clusters = sample_customers["cluster"].tolist()
    segments = sample_customers["segmentShortLabel"].tolist() if "segmentShortLabel" in sample_customers.columns else clusters
    full_segments = sample_customers["segment"].tolist() if "segment" in sample_customers.columns else clusters
    recencies = sample_customers.get("recency", pd.Series(0.0, index=sample_customers.index)).astype(float).tolist()
    frequencies = sample_customers.get("frequency", pd.Series(0.0, index=sample_customers.index)).astype(float).tolist()
    monetaries = sample_customers.get("monetary", pd.Series(0.0, index=sample_customers.index)).astype(float).tolist()

    scatter_data = [
        {
            "customerId": cid,
            "cluster": cl,
            "segment": seg,
            "recency": r,
            "frequency": f,
            "monetary": m,
            "fill": SEGMENT_COLORS.get(fseg, "#64748B"),
        }
        for cid, cl, seg, fseg, r, f, m in zip(ids, clusters, segments, full_segments, recencies, frequencies, monetaries)
    ]

    return {
        "totalClusters": len(cluster_distribution),
        "optimalK": len(cluster_distribution),
        "silhouetteScore": silhouette_score,
        "clusterDistribution": cluster_distribution,
        "scatterData": scatter_data,
        "elbowMethod": elbow_method,
        "clusterProfiles": cluster_profiles,
        "modelMeta": model_meta,
    }


def build_results_payload(
    customers: pd.DataFrame,
    segment_mix: list[dict[str, Any]],
    model_meta: dict[str, Any],
) -> dict[str, Any]:
    segment_cards = [
        {
            "name": item["name"],
            "value": item["value"],
            "fill": item["fill"],
            "segment": item["segment"],
        }
        for item in segment_mix
    ]

    customer_sample = customers.head(2000) if len(customers) > 2000 else customers

    return {
        "segments": segment_cards,
        "customers": to_serializable_records(customer_sample),
        "modelMeta": model_meta,
    }


def build_insights_payload(
    customers: pd.DataFrame,
    quality: dict[str, int],
    model_meta: dict[str, Any],
) -> dict[str, Any]:
    total_revenue = float(customers["monetary"].sum())
    sorted_customers = customers.sort_values("monetary", ascending=False)

    top_customer_count = max(1, int(round(len(customers) * 0.2)))
    top_revenue = float(sorted_customers.head(top_customer_count)["monetary"].sum())
    top_revenue_share = (top_revenue / total_revenue * 100) if total_revenue else 0

    inactive_customers = int((customers["recency"] >= 45).sum())
    high_risk_customers = int((customers["churnRisk"] == "High").sum())
    at_risk_revenue = float(
        customers.loc[customers["segment"] == "At Risk Customers", "monetary"].sum()
    )
    at_risk_share = (at_risk_revenue / total_revenue * 100) if total_revenue else 0

    loyal_upsell_count = int(
        (
            (customers["segment"] == "Loyal Customers")
            & (customers["frequency"] >= customers["frequency"].median())
        ).sum()
    )

    cluster_snapshot = (
        customers.groupby(["cluster", "segmentShortLabel"], as_index=False)
        .agg(customers=("id", "count"))
        .sort_values("cluster")
    )

    clusters = [
        {
            "cluster": row["cluster"],
            "segment": row["segmentShortLabel"],
            "customers": int(row["customers"]),
        }
        for _, row in cluster_snapshot.iterrows()
    ]

    return {
        "highlights": [
            {
                "title": "Revenue Concentration",
                "statement": f"Top 20% customers generate {top_revenue_share:.0f}% of revenue.",
                "metric": f"{top_revenue_share:.0f}%",
                "tone": "primary",
            },
            {
                "title": "Inactive Customers",
                "statement": f"{inactive_customers} customers have not purchased in the last 45+ days.",
                "metric": inactive_customers,
                "tone": "warning",
            },
            {
                "title": "At-Risk Revenue",
                "statement": f"At-risk customers account for {at_risk_share:.0f}% of tracked revenue.",
                "metric": f"{at_risk_share:.0f}%",
                "tone": "secondary",
            },
        ],
        "recommendations": [
            {
                "title": "Protect High-Value Customers",
                "summary": "Launch priority rewards and concierge communication for the highest value cohort.",
                "actions": [
                    "Trigger a premium loyalty flow after every purchase above the cohort median.",
                    "Offer early access campaigns and personalized bundles to retain share of wallet.",
                    "Track purchase-gap anomalies weekly to catch disengagement earlier.",
                ],
            },
            {
                "title": "Re-activate At-Risk Customers",
                "summary": "Use win-back campaigns for customers with growing recency and healthy historic spend.",
                "actions": [
                    "Send time-boxed discount codes to customers with recency above 45 days.",
                    "Recommend previously purchased categories to reduce decision friction.",
                    "Escalate top-spend at-risk users to CRM or call-center outreach.",
                ],
            },
            {
                "title": "Grow Low-Value Customers",
                "summary": "Nurture smaller accounts into repeat buyers with onboarding and cross-sell journeys.",
                "actions": [
                    "Bundle first three purchases with guided product education.",
                    "Push free-shipping thresholds just above average order value.",
                    "Use drip campaigns to encourage second and third orders within 30 days.",
                ],
            },
        ],
        "opportunityAreas": [
            {
                "label": "Upsell Opportunity",
                "value": f"{loyal_upsell_count} customers",
                "detail": "Loyal customers with higher frequency and moderate spend lift potential.",
            },
            {
                "label": "Retention Priority",
                "value": f"{inactive_customers} customers",
                "detail": "Inactive and at-risk customers should be targeted in the next campaign cycle.",
            },
            {
                "label": "Data Quality Action",
                "value": f"{quality['missingCells']} fields",
                "detail": "Missing values should be cleaned or imputed before retraining.",
            },
        ],
        "trainedModelSummary": {
            "selectedAlgorithm": model_meta.get("selectedAlgorithm", "unknown"),
            "bestSilhouetteScore": model_meta.get("bestSilhouetteScore", 0),
            "candidateScores": model_meta.get("candidateScores", []),
        },
        "customerRiskSnapshot": {
            "highRiskCustomers": high_risk_customers,
            "inactiveCustomers": inactive_customers,
            "totalCustomers": int(len(customers)),
            "clusters": clusters,
        },
    }


def _build_purchase_trends(customers: pd.DataFrame) -> list[dict[str, Any]]:
    working = customers.copy()
    working["_purchaseDate"] = pd.to_datetime(
        working["lastPurchaseDate"], errors="coerce", utc=True
    )
    working["_purchaseDate"] = working["_purchaseDate"].dt.tz_convert(None)

    if working["_purchaseDate"].notna().sum() == 0:
        return []

    working["_month"] = working["_purchaseDate"].dt.to_period("M")

    grouped = (
        working.groupby("_month", as_index=False)
        .agg(revenue=("monetary", "sum"), orders=("totalOrders", "sum"))
        .sort_values("_month")
        .tail(6)
    )

    trends: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        trends.append(
            {
                "month": row["_month"].strftime("%b"),
                "revenue": int(round(float(row["revenue"]))),
                "orders": int(round(float(row["orders"]))),
            }
        )

    return trends


def _build_frequency_distribution(customers: pd.DataFrame) -> list[dict[str, Any]]:
    frequency = customers["frequency"]

    return [
        {"bucket": "1-4", "customers": int(((frequency >= 1) & (frequency <= 4)).sum())},
        {"bucket": "5-8", "customers": int(((frequency >= 5) & (frequency <= 8)).sum())},
        {"bucket": "9-12", "customers": int(((frequency >= 9) & (frequency <= 12)).sum())},
        {
            "bucket": "13-16",
            "customers": int(((frequency >= 13) & (frequency <= 16)).sum()),
        },
        {"bucket": "17+", "customers": int((frequency >= 17).sum())},
    ]
