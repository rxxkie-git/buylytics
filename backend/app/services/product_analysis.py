"""Product sales analysis and demand prediction pipeline."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

LOGGER = logging.getLogger(__name__)

PRODUCT_HINTS = [
    "product",
    "product_name",
    "product_id",
    "item",
    "sku",
    "stock_keeping_unit",
]
QUANTITY_HINTS = [
    "quantity",
    "qty",
    "units",
    "units_sold",
    "sold_quantity",
    "volume",
]
REVENUE_HINTS = [
    "sales",
    "revenue",
    "amount",
    "monetary",
    "turnover",
    "total_sales",
    "total_revenue",
    "gross_sales",
    "net_sales",
]
PRICE_HINTS = [
    "price",
    "unit_price",
    "selling_price",
    "sale_price",
    "mrp",
]
DATE_HINTS = [
    "date",
    "order_date",
    "purchase_date",
    "transaction_date",
    "timestamp",
    "time",
]

SUPPORTED_FREQUENCIES = {"auto", "daily", "monthly"}

CLUSTER_FEATURE_OPTIONS = {
    "default": ["total_quantity_sold", "total_revenue", "sales_frequency"],
    "quantity_revenue": ["total_quantity_sold", "total_revenue"],
    "quantity_frequency": ["total_quantity_sold", "sales_frequency"],
    "revenue_frequency": ["total_revenue", "sales_frequency"],
    "quantity": ["total_quantity_sold"],
    "revenue": ["total_revenue"],
    "frequency": ["sales_frequency"],
}


@dataclass
class DetectedColumns:
    product: str | None
    quantity: str | None
    revenue: str | None
    price: str | None
    date: str | None
    date_component_prefix: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "quantity": self.quantity,
            "revenue": self.revenue,
            "price": self.price,
            "date": self.date,
            "dateComponentPrefix": self.date_component_prefix,
        }


def analyze_sales(frame: pd.DataFrame, top_n: int = 10) -> dict[str, Any]:
    """Compute grouped sales metrics and product ranking."""
    base = _prepare_sales_base(frame)
    grouped = _build_product_performance_frame(base)

    if grouped.empty:
        return {
            "available": False,
            "message": "No product-level sales records available for analysis.",
            "productPerformanceTable": [],
            "topSellingProducts": [],
            "leastSellingProducts": [],
            "summary": {
                "productsAnalyzed": 0,
                "totalQuantitySold": 0.0,
                "totalRevenue": 0.0,
                "averageSalesPerProduct": 0.0,
            },
            "detectedColumns": base["detected_columns"].to_dict(),
            "warnings": base["warnings"],
        }

    ranked = grouped.sort_values("total_revenue", ascending=False).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)

    top = ranked.head(top_n).copy()
    least = ranked.tail(top_n).sort_values("total_revenue", ascending=True).reset_index(drop=True)

    return {
        "available": True,
        "productPerformanceTable": _to_serializable_records(ranked),
        "topSellingProducts": _to_serializable_records(top),
        "leastSellingProducts": _to_serializable_records(least),
        "summary": {
            "productsAnalyzed": int(len(ranked)),
            "totalQuantitySold": round(float(ranked["total_quantity_sold"].sum()), 4),
            "totalRevenue": round(float(ranked["total_revenue"].sum()), 4),
            "averageSalesPerProduct": round(float(ranked["total_revenue"].mean()), 4),
        },
        "detectedColumns": base["detected_columns"].to_dict(),
        "warnings": base["warnings"],
    }


def cluster_products(
    frame: pd.DataFrame,
    cluster_by: str = "default",
    cluster_count: int = 6,
) -> dict[str, Any]:
    """Cluster products into fast/medium/slow moving groups using K-Means."""
    base = _prepare_sales_base(frame)
    grouped = _build_product_performance_frame(base)

    if grouped.empty:
        return {
            "available": False,
            "message": "No product-level records available for clustering.",
            "clusterBy": cluster_by,
            "optimalClusters": 0,
            "elbowMethod": [],
            "clusteredProducts": [],
            "clusterProfiles": [],
            "datasetWithClusterLabel": [],
            "detectedColumns": base["detected_columns"].to_dict(),
            "warnings": base["warnings"],
        }

    if len(grouped) == 1:
        single = grouped.copy()
        single["cluster_id"] = 0
        single["movement_label"] = "Medium-moving"
        row_level = base["row_level"].copy()
        row_level["cluster_id"] = 0
        row_level["movement_label"] = "Medium-moving"

        return {
            "available": True,
            "optimalClusters": 1,
            "elbowMethod": [{"k": 1, "inertia": 0.0}],
            "clusteredProducts": _to_serializable_records(single),
            "clusterProfiles": [
                {
                    "cluster_id": 0,
                    "movement_label": "Medium-moving",
                    "products": 1,
                    "avg_quantity_sold": round(float(single["total_quantity_sold"].mean()), 4),
                    "avg_total_revenue": round(float(single["total_revenue"].mean()), 4),
                    "avg_frequency": round(float(single["sales_frequency"].mean()), 4),
                }
            ],
            "datasetWithClusterLabel": _to_serializable_records(row_level.head(1000)),
            "datasetWithClusterLabelCount": int(len(row_level)),
            "detectedColumns": base["detected_columns"].to_dict(),
            "warnings": base["warnings"] + ["Only one product found, clustering is degenerate."],
        }

    feature_columns = _get_cluster_feature_columns(cluster_by)
    feature_matrix = grouped[feature_columns].to_numpy(dtype=float)
    scaled_matrix = StandardScaler().fit_transform(feature_matrix)

    max_k = min(cluster_count, len(grouped))
    inertias: list[dict[str, Any]] = []
    for cluster_count in range(1, max_k + 1):
        model = KMeans(n_clusters=cluster_count, n_init=10, random_state=42)
        model.fit(scaled_matrix)
        inertias.append({"k": cluster_count, "inertia": round(float(model.inertia_), 6)})

    optimal_k = _select_optimal_k(inertias)
    if len(grouped) >= 2:
        optimal_k = max(optimal_k, 2)
    if len(grouped) >= 3:
        optimal_k = max(optimal_k, 3)
    optimal_k = min(optimal_k, len(grouped))

    model = KMeans(n_clusters=optimal_k, n_init=20, random_state=42)
    cluster_ids = model.fit_predict(scaled_matrix)
    grouped = grouped.copy()
    grouped["cluster_id"] = cluster_ids

    cluster_profiles = (
        grouped.groupby("cluster_id", as_index=False)
        .agg(
            products=("product", "count"),
            avg_quantity_sold=("total_quantity_sold", "mean"),
            avg_total_revenue=("total_revenue", "mean"),
            avg_frequency=("sales_frequency", "mean"),
        )
        .sort_values("cluster_id")
        .reset_index(drop=True)
    )

    profile_score = (
        _normalize_series(cluster_profiles["avg_total_revenue"]) * 0.45
        + _normalize_series(cluster_profiles["avg_quantity_sold"]) * 0.35
        + _normalize_series(cluster_profiles["avg_frequency"]) * 0.20
    )
    cluster_profiles["cluster_score"] = profile_score

    movement_mapping = _movement_labels_for_profiles(cluster_profiles)
    grouped["movement_label"] = grouped["cluster_id"].map(movement_mapping)
    cluster_profiles["movement_label"] = cluster_profiles["cluster_id"].map(movement_mapping)

    row_level = base["row_level"].merge(
        grouped[["product", "cluster_id", "movement_label"]],
        on="product",
        how="left",
    )

    grouped = grouped.sort_values(["total_revenue", "total_quantity_sold"], ascending=False).reset_index(drop=True)

    return {
        "available": True,
        "clusterBy": cluster_by,
        "optimalClusters": int(optimal_k),
        "elbowMethod": inertias,
        "featureColumns": feature_columns,
        "clusteredProducts": _to_serializable_records(grouped),
        "clusterProfiles": _to_serializable_records(cluster_profiles.sort_values("cluster_score", ascending=False)),
        "datasetWithClusterLabel": _to_serializable_records(row_level.head(1000)),
        "datasetWithClusterLabelCount": int(len(row_level)),
        "detectedColumns": base["detected_columns"].to_dict(),
        "warnings": base["warnings"],
    }


def analyze_demand(
    frame: pd.DataFrame,
    frequency: str = "auto",
    moving_average_window: int = 3,
) -> dict[str, Any]:
    """Build demand trend aggregates and moving-average growth analysis."""
    if frequency not in SUPPORTED_FREQUENCIES:
        raise ValueError("frequency must be one of: auto, daily, monthly")

    base = _prepare_sales_base(frame)
    date_series = _extract_event_datetime(base["source_frame"], base["detected_columns"])

    if date_series.notna().sum() == 0:
        return {
            "available": False,
            "message": "No valid date information found for demand trend analysis.",
            "frequency": None,
            "timeSeries": [],
            "summary": {},
            "warnings": base["warnings"] + ["Date column not detected."],
            "detectedColumns": base["detected_columns"].to_dict(),
        }

    demand_frame = base["row_level"].copy()
    demand_frame["event_date"] = date_series
    demand_frame = demand_frame.dropna(subset=["event_date"]).sort_values("event_date")

    span_days = int((demand_frame["event_date"].max() - demand_frame["event_date"].min()).days)
    effective_frequency = frequency
    if effective_frequency == "auto":
        effective_frequency = "monthly" if span_days >= 120 else "daily"

    if effective_frequency == "monthly":
        demand_frame["period"] = demand_frame["event_date"].dt.to_period("M").dt.to_timestamp()
    else:
        demand_frame["period"] = demand_frame["event_date"].dt.floor("D")

    grouped = (
        demand_frame.groupby("period", as_index=False)
        .agg(
            total_sales=("revenue", "sum"),
            total_quantity=("quantity", "sum"),
            transaction_count=("product", "count"),
        )
        .sort_values("period")
        .reset_index(drop=True)
    )

    window = max(1, min(moving_average_window, len(grouped)))
    grouped["moving_average"] = grouped["total_sales"].rolling(window=window, min_periods=1).mean()
    grouped["growth_rate_pct"] = (
        grouped["total_sales"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0) * 100
    )

    slope = _trend_slope(grouped["total_sales"].to_numpy(dtype=float))
    trend_label = "Stable"
    if slope > 0.01:
        trend_label = "Increasing"
    elif slope < -0.01:
        trend_label = "Decreasing"

    grouped["period"] = grouped["period"].dt.strftime("%Y-%m-%d")

    return {
        "available": True,
        "frequency": effective_frequency,
        "timeSeries": _to_serializable_records(grouped),
        "summary": {
            "periodsAnalyzed": int(len(grouped)),
            "trendDirection": trend_label,
            "trendSlope": round(float(slope), 6),
            "averageGrowthPct": round(float(grouped["growth_rate_pct"].mean()), 4),
            "maxSales": round(float(grouped["total_sales"].max()), 4),
            "minSales": round(float(grouped["total_sales"].min()), 4),
        },
        "detectedColumns": base["detected_columns"].to_dict(),
        "warnings": base["warnings"],
    }


def predict_sales(
    frame: pd.DataFrame,
    forecast_periods: int = 12,
    frequency: str = "auto",
) -> dict[str, Any]:
    """Forecast future sales from historical time series using ARIMA or Linear Regression."""
    if forecast_periods < 1:
        raise ValueError("forecast_periods must be greater than zero")

    demand = analyze_demand(frame, frequency=frequency)
    if not demand.get("available"):
        return {
            "available": False,
            "message": "Forecast unavailable because demand analysis could not build a time series.",
            "modelUsed": None,
            "frequency": demand.get("frequency"),
            "forecast": [],
            "history": demand.get("timeSeries", []),
            "warnings": demand.get("warnings", []),
        }

    history = pd.DataFrame(demand["timeSeries"]).copy()
    if history.empty or len(history) < 2:
        return {
            "available": False,
            "message": "At least two historical periods are required for prediction.",
            "modelUsed": None,
            "frequency": demand.get("frequency"),
            "forecast": [],
            "history": demand.get("timeSeries", []),
            "warnings": demand.get("warnings", []),
        }

    history["period"] = pd.to_datetime(history["period"], errors="coerce")
    history["total_sales"] = pd.to_numeric(history["total_sales"], errors="coerce").fillna(0.0)
    history = history.dropna(subset=["period"]).sort_values("period").reset_index(drop=True)

    model_used = "LinearRegression"
    forecast_values: np.ndarray
    confidence_intervals: list[dict[str, float]] = []

    arima_result = _try_arima_forecast(history["total_sales"], forecast_periods)
    if arima_result is not None:
        model_used = "ARIMA(1,1,1)"
        forecast_values = arima_result["forecast"]
        confidence_intervals = arima_result["confidenceIntervals"]
    else:
        x_train = np.arange(len(history), dtype=float).reshape(-1, 1)
        y_train = history["total_sales"].to_numpy(dtype=float)
        model = LinearRegression()
        model.fit(x_train, y_train)
        x_future = np.arange(len(history), len(history) + forecast_periods, dtype=float).reshape(-1, 1)
        forecast_values = model.predict(x_future)

    forecast_values = np.maximum(forecast_values, 0.0)
    future_dates = _build_future_dates(
        last_date=history["period"].iloc[-1],
        periods=forecast_periods,
        frequency=demand["frequency"] or "daily",
    )

    forecast_records: list[dict[str, Any]] = []
    for index, future_date in enumerate(future_dates):
        record = {
            "period": future_date.strftime("%Y-%m-%d"),
            "predicted_sales": round(float(forecast_values[index]), 6),
        }
        if index < len(confidence_intervals):
            record["confidence_lower"] = round(float(confidence_intervals[index]["lower"]), 6)
            record["confidence_upper"] = round(float(confidence_intervals[index]["upper"]), 6)
        forecast_records.append(record)

    return {
        "available": True,
        "modelUsed": model_used,
        "frequency": demand["frequency"],
        "forecastPeriods": int(forecast_periods),
        "history": demand["timeSeries"],
        "forecast": forecast_records,
        "warnings": demand.get("warnings", []),
    }


def run_product_analytics(
    frame: pd.DataFrame,
    forecast_periods: int = 12,
    frequency: str = "auto",
    cluster_by: str = "default",
    cluster_count: int = 6,
) -> dict[str, Any]:
    """Run the complete product analytics pipeline."""
    sales_analysis = analyze_sales(frame)
    clustering = cluster_products(frame, cluster_by=cluster_by, cluster_count=cluster_count)
    demand_analysis = analyze_demand(frame, frequency=frequency)
    forecast = predict_sales(frame, forecast_periods=forecast_periods, frequency=frequency)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "salesAnalysis": sales_analysis,
        "productClustering": clustering,
        "demandAnalysis": demand_analysis,
        "salesForecast": forecast,
        "visualizations": _build_visualization_payloads(
            sales_analysis=sales_analysis,
            clustering=clustering,
            demand_analysis=demand_analysis,
            forecast=forecast,
        ),
    }


def _prepare_sales_base(frame: pd.DataFrame) -> dict[str, Any]:
    working = frame.copy()
    working = working.replace([np.inf, -np.inf], np.nan)
    working = working.dropna(how="all")

    detected_columns = _detect_columns(working)
    warnings: list[str] = []

    product_series = _extract_product_series(working, detected_columns)
    if detected_columns.product is None:
        warnings.append("Product column not explicitly detected; inferred surrogate grouping was used.")

    quantity_series = _extract_numeric_series(working, detected_columns.quantity, default=1.0)
    if detected_columns.quantity is None:
        warnings.append("Quantity column not detected; default quantity=1 was used.")

    revenue_series = _extract_revenue_series(working, detected_columns, quantity_series)
    if detected_columns.revenue is None and detected_columns.price is None:
        warnings.append("Revenue/price column not detected; revenue inferred from quantity values.")

    event_date = _extract_event_datetime(working, detected_columns)

    row_level = pd.DataFrame(
        {
            "product": product_series,
            "quantity": quantity_series,
            "revenue": revenue_series,
            "event_date": event_date,
        },
        index=working.index,
    )

    row_level["quantity"] = pd.to_numeric(row_level["quantity"], errors="coerce").fillna(0.0)
    row_level["revenue"] = pd.to_numeric(row_level["revenue"], errors="coerce").fillna(0.0)
    row_level["product"] = row_level["product"].astype("string").fillna("Unknown Product")
    row_level["product"] = row_level["product"].str.strip().replace("", "Unknown Product")

    return {
        "source_frame": working,
        "row_level": row_level.reset_index(drop=True),
        "detected_columns": detected_columns,
        "warnings": warnings,
    }


def _build_product_performance_frame(base: dict[str, Any]) -> pd.DataFrame:
    grouped = (
        base["row_level"]
        .groupby("product", as_index=False)
        .agg(
            total_quantity_sold=("quantity", "sum"),
            total_revenue=("revenue", "sum"),
            sales_frequency=("product", "count"),
            average_sales=("revenue", "mean"),
            first_sale_date=("event_date", "min"),
            last_sale_date=("event_date", "max"),
        )
    )

    if "first_sale_date" in grouped.columns and "last_sale_date" in grouped.columns:
        grouped["first_sale_date"] = pd.to_datetime(grouped["first_sale_date"], errors="coerce")
        grouped["last_sale_date"] = pd.to_datetime(grouped["last_sale_date"], errors="coerce")
        grouped["active_days"] = (
            grouped["last_sale_date"] - grouped["first_sale_date"]
        ).dt.days.fillna(0).astype(int)
        grouped["active_days"] = grouped["active_days"].clip(lower=0) + 1
        grouped["average_daily_revenue"] = grouped.apply(
            lambda row: float(row["total_revenue"]) / row["active_days"]
            if row["active_days"] > 0 else 0.0,
            axis=1,
        )
    else:
        grouped["active_days"] = 0
        grouped["average_daily_revenue"] = grouped["total_revenue"]

    grouped = grouped.sort_values("total_revenue", ascending=False).reset_index(drop=True)
    return grouped


def _get_cluster_feature_columns(cluster_by: str) -> list[str]:
    normalized = _normalize_name(cluster_by or "default")
    feature_columns = CLUSTER_FEATURE_OPTIONS.get(normalized)
    if not feature_columns:
        feature_columns = CLUSTER_FEATURE_OPTIONS["default"]
    return feature_columns


def _detect_columns(frame: pd.DataFrame) -> DetectedColumns:
    columns = [str(column) for column in frame.columns]

    product = _find_column_by_hints(columns, PRODUCT_HINTS)
    quantity = _find_column_by_hints(columns, QUANTITY_HINTS)
    revenue = _find_column_by_hints(columns, REVENUE_HINTS)
    price = _find_column_by_hints(columns, PRICE_HINTS)
    date = _find_datetime_column(frame, columns)
    date_component_prefix = _find_date_component_prefix(columns)

    return DetectedColumns(
        product=product,
        quantity=quantity,
        revenue=revenue,
        price=price,
        date=date,
        date_component_prefix=date_component_prefix,
    )


def _find_column_by_hints(columns: list[str], hints: list[str]) -> str | None:
    normalized_to_original = {_normalize_name(column): column for column in columns}

    for hint in hints:
        normalized_hint = _normalize_name(hint)
        if normalized_hint in normalized_to_original:
            return normalized_to_original[normalized_hint]

    for column in columns:
        normalized_column = _normalize_name(column)
        for hint in hints:
            normalized_hint = _normalize_name(hint)
            if normalized_hint and normalized_hint in normalized_column:
                return column

    return None


def _find_datetime_column(frame: pd.DataFrame, columns: list[str]) -> str | None:
    hinted = _find_column_by_hints(columns, DATE_HINTS)
    if hinted is not None:
        return hinted

    for column in columns:
        series = frame[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            return column

        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            sample = series.dropna().head(25)
            if sample.empty:
                continue
            parsed = pd.to_datetime(sample, errors="coerce")
            if float(parsed.notna().mean()) >= 0.75:
                return column

    return None


def _find_date_component_prefix(columns: list[str]) -> str | None:
    normalized = {_normalize_name(column): column for column in columns}
    candidates: set[str] = set()
    for column in normalized:
        if column.endswith("_year"):
            candidates.add(column[: -len("_year")])

    for candidate in candidates:
        has_month = f"{candidate}_month" in normalized
        has_day = f"{candidate}_day" in normalized
        if has_month and has_day:
            return candidate
    return None


def _extract_product_series(frame: pd.DataFrame, detected_columns: DetectedColumns) -> pd.Series:
    if detected_columns.product and detected_columns.product in frame.columns:
        return frame[detected_columns.product].astype("string")

    one_hot_product_columns = [
        column
        for column in frame.columns
        if _normalize_name(column).startswith("product_")
        and pd.api.types.is_numeric_dtype(frame[column])
    ]
    if one_hot_product_columns:
        one_hot_frame = frame[one_hot_product_columns].fillna(0.0)
        index_max = one_hot_frame.idxmax(axis=1)
        decoded = index_max.astype("string").str.replace(r"^product_+", "", regex=True)
        return decoded.replace("", "Unknown Product")

    return pd.Series(["Unknown Product"] * len(frame), index=frame.index, dtype="string")


def _extract_numeric_series(frame: pd.DataFrame, column_name: str | None, default: float = 0.0) -> pd.Series:
    if column_name is None or column_name not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index, dtype=float)

    series = frame[column_name]
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(default)

    text = series.astype("string").str.replace(r"[,\$%]", "", regex=True).str.strip()
    return pd.to_numeric(text, errors="coerce").fillna(default)


def _extract_revenue_series(
    frame: pd.DataFrame,
    detected_columns: DetectedColumns,
    quantity_series: pd.Series,
) -> pd.Series:
    if detected_columns.revenue and detected_columns.revenue in frame.columns:
        return _extract_numeric_series(frame, detected_columns.revenue, default=0.0)

    if detected_columns.price and detected_columns.price in frame.columns:
        price_series = _extract_numeric_series(frame, detected_columns.price, default=0.0)
        return quantity_series * price_series

    return quantity_series.astype(float)


def _extract_event_datetime(frame: pd.DataFrame, detected_columns: DetectedColumns) -> pd.Series:
    if detected_columns.date and detected_columns.date in frame.columns:
        series = frame[detected_columns.date]
        
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            clean = series.dropna().astype(str).str.strip()
            if not clean.empty and clean.str.isnumeric().mean() > 0.8:
                series = pd.to_numeric(series, errors="coerce")

        if pd.api.types.is_numeric_dtype(series):
            clean_series = series.dropna()
            if not clean_series.empty:
                max_val = clean_series.max()
                min_val = clean_series.min()
                if max_val > 1e11:
                    return pd.to_datetime(series, unit="ms", errors="coerce")
                elif max_val > 1e8:
                    return pd.to_datetime(series, unit="s", errors="coerce")
                elif max_val > 1e7 and max_val < 3e7:  # likely YYYYMMDD e.g. 20230101
                    return pd.to_datetime(series, format="%Y%m%d", errors="coerce")
                elif max_val < 1e5:
                    if min_val >= 1900 and max_val <= 2100:  # literal years like 2020
                        return pd.to_datetime(series, format="%Y", errors="coerce")
                    return pd.to_datetime(series, unit="D", origin="1899-12-30", errors="coerce")
        return pd.to_datetime(series, errors="coerce")

    if detected_columns.date_component_prefix:
        year_column = _column_from_normalized(frame.columns, f"{detected_columns.date_component_prefix}_year")
        month_column = _column_from_normalized(frame.columns, f"{detected_columns.date_component_prefix}_month")
        day_column = _column_from_normalized(frame.columns, f"{detected_columns.date_component_prefix}_day")

        if year_column and month_column and day_column:
            year = pd.to_numeric(frame[year_column], errors="coerce").fillna(1970).round().astype(int)
            month = pd.to_numeric(frame[month_column], errors="coerce").fillna(1).round().astype(int)
            day = pd.to_numeric(frame[day_column], errors="coerce").fillna(1).round().astype(int)

            month = month.clip(1, 12)
            day = day.clip(1, 28)

            return pd.to_datetime(
                {
                    "year": year,
                    "month": month,
                    "day": day,
                },
                errors="coerce",
            )

    generic_year = _column_from_normalized(frame.columns, "year")
    generic_month = _column_from_normalized(frame.columns, "month")
    generic_day = _column_from_normalized(frame.columns, "day")
    if generic_year and generic_month and generic_day:
        year = pd.to_numeric(frame[generic_year], errors="coerce").fillna(1970).round().astype(int)
        month = pd.to_numeric(frame[generic_month], errors="coerce").fillna(1).round().astype(int)
        day = pd.to_numeric(frame[generic_day], errors="coerce").fillna(1).round().astype(int)
        month = month.clip(1, 12)
        day = day.clip(1, 28)
        return pd.to_datetime(
            {"year": year, "month": month, "day": day},
            errors="coerce",
        )

    return pd.Series([pd.NaT] * len(frame), index=frame.index)


def _column_from_normalized(columns: pd.Index | list[str], normalized_target: str) -> str | None:
    for column in columns:
        if _normalize_name(str(column)) == normalized_target:
            return str(column)
    return None


def _select_optimal_k(elbow_points: list[dict[str, Any]]) -> int:
    if not elbow_points:
        return 1
    if len(elbow_points) <= 2:
        return int(elbow_points[-1]["k"])

    inertias = [float(point["inertia"]) for point in elbow_points]
    k_values = [int(point["k"]) for point in elbow_points]
    baseline = inertias[0] - inertias[-1]
    if baseline <= 0:
        return k_values[min(2, len(k_values) - 1)]

    for index in range(1, len(inertias)):
        improvement = inertias[index - 1] - inertias[index]
        relative_improvement = improvement / max(baseline, 1e-9)
        if relative_improvement < 0.10:
            return k_values[index]

    return k_values[min(2, len(k_values) - 1)]


def _movement_labels_for_profiles(cluster_profiles: pd.DataFrame) -> dict[int, str]:
    ordered = cluster_profiles.sort_values("cluster_score", ascending=False).reset_index(drop=True)
    cluster_ids = ordered["cluster_id"].tolist()
    mapping: dict[int, str] = {}

    if len(cluster_ids) == 1:
        mapping[int(cluster_ids[0])] = "Medium-moving"
        return mapping

    if len(cluster_ids) == 2:
        mapping[int(cluster_ids[0])] = "Fast-moving"
        mapping[int(cluster_ids[1])] = "Slow-moving"
        return mapping

    for index, cluster_id in enumerate(cluster_ids):
        if index == 0:
            mapping[int(cluster_id)] = "Fast-moving"
        elif index == len(cluster_ids) - 1:
            mapping[int(cluster_id)] = "Slow-moving"
        else:
            mapping[int(cluster_id)] = "Medium-moving"
    return mapping


def _normalize_series(series: pd.Series) -> pd.Series:
    min_value = float(series.min())
    max_value = float(series.max())
    if max_value == min_value:
        return pd.Series([0.5] * len(series), index=series.index)
    return (series - min_value) / (max_value - min_value)


def _trend_slope(values: np.ndarray) -> float:
    if values.size <= 1:
        return 0.0
    x_axis = np.arange(values.size, dtype=float)
    slope = np.polyfit(x_axis, values.astype(float), deg=1)[0]
    return float(slope)


def _try_arima_forecast(series: pd.Series, forecast_periods: int) -> dict[str, Any] | None:
    if len(series) < 6:
        return None

    try:
        from statsmodels.tsa.arima.model import ARIMA
    except Exception:
        return None

    try:
        model = ARIMA(series.astype(float), order=(1, 1, 1))
        fitted = model.fit()
        forecast_result = fitted.get_forecast(steps=forecast_periods)
        forecast_values = np.asarray(forecast_result.predicted_mean)

        confidence_frame = forecast_result.conf_int(alpha=0.2)
        intervals: list[dict[str, float]] = []
        for _, row in confidence_frame.iterrows():
            intervals.append(
                {
                    "lower": max(float(row.iloc[0]), 0.0),
                    "upper": max(float(row.iloc[1]), 0.0),
                }
            )

        return {"forecast": forecast_values, "confidenceIntervals": intervals}
    except Exception as exc:  # pragma: no cover - model stability fallback
        LOGGER.warning("ARIMA fallback to LinearRegression due to error: %s", exc)
        return None


def _build_future_dates(last_date: pd.Timestamp, periods: int, frequency: str) -> list[pd.Timestamp]:
    if frequency == "monthly":
        start = last_date + pd.offsets.MonthBegin(1)
        dates = pd.date_range(start=start, periods=periods, freq="MS")
    else:
        start = last_date + pd.Timedelta(days=1)
        dates = pd.date_range(start=start, periods=periods, freq="D")
    return [pd.Timestamp(value) for value in dates]


def _build_visualization_payloads(
    sales_analysis: dict[str, Any],
    clustering: dict[str, Any],
    demand_analysis: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    top_products = sales_analysis.get("topSellingProducts", [])
    clustered_products = clustering.get("clusteredProducts", [])
    demand_series = demand_analysis.get("timeSeries", [])
    forecast_series = forecast.get("forecast", [])
    history_series = forecast.get("history", [])

    return {
        "topProductsBarChart": {
            "chartType": "bar",
            "xField": "product",
            "yField": "total_revenue",
            "title": "Top Selling Products by Revenue",
            "data": top_products[:10],
        },
        "clusterScatterPlot": {
            "chartType": "scatter",
            "xField": "total_quantity_sold",
            "yField": "total_revenue",
            "colorField": "movement_label",
            "sizeField": "sales_frequency",
            "title": "Product Movement Clusters",
            "data": clustered_products,
        },
        "demandTimeSeriesChart": {
            "chartType": "line",
            "xField": "period",
            "yField": "total_sales",
            "secondaryYField": "moving_average",
            "title": "Demand Trend Over Time",
            "data": demand_series,
        },
        "forecastChart": {
            "chartType": "line",
            "xField": "period",
            "historyField": "total_sales",
            "forecastField": "predicted_sales",
            "title": "Historical Sales and Forecast",
            "history": history_series,
            "forecast": forecast_series,
        },
    }


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _to_serializable_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []

    serializable_records: list[dict[str, Any]] = []
    for raw_row in frame.to_dict(orient="records"):
        row: dict[str, Any] = {}
        for key, value in raw_row.items():
            if pd.isna(value):
                row[str(key)] = None
            elif isinstance(value, (np.integer,)):
                row[str(key)] = int(value)
            elif isinstance(value, (np.floating,)):
                row[str(key)] = float(value)
            elif isinstance(value, (pd.Timestamp, datetime)):
                row[str(key)] = value.isoformat()
            else:
                row[str(key)] = value
        serializable_records.append(row)

    return serializable_records
