from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx"}

PREVIEW_COLUMNS = [
    {"id": "id", "label": "Customer ID"},
    {"id": "customerName", "label": "Customer Name"},
    {"id": "segment", "label": "Segment"},
    {"id": "recency", "label": "Recency"},
    {"id": "frequency", "label": "Frequency"},
    {"id": "monetary", "label": "Monetary"},
    {"id": "avgOrderValue", "label": "Avg Order Value"},
    {"id": "preferredChannel", "label": "Channel"},
    {"id": "lastPurchaseDate", "label": "Last Purchase"},
]

PREVIEW_COLUMN_IDS = [column["id"] for column in PREVIEW_COLUMNS]


_COLUMN_CANDIDATES = {
    "id": ["id", "customer_id", "customerid", "cust_id"],
    "customer_name": ["customer_name", "customername", "name", "full_name"],
    "city": ["city", "location", "region", "state"],
    "recency": [
        "recency",
        "days_since_last_purchase",
        "days_since_last_order",
        "last_seen_days",
    ],
    "frequency": [
        "frequency",
        "purchase_frequency",
        "order_frequency",
        "order_count",
        "orders",
    ],
    "monetary": [
        "monetary",
        "amount",
        "revenue",
        "spend",
        "sales",
        "total_spend",
        "total_revenue",
    ],
    "total_orders": ["total_orders", "transactions", "order_count", "orders"],
    "avg_order_value": ["avg_order_value", "average_order_value", "aov"],
    "preferred_channel": [
        "preferred_channel",
        "channel",
        "source_channel",
        "purchase_channel",
    ],
    "last_purchase_date": [
        "last_purchase_date",
        "last_order_date",
        "purchase_date",
        "last_txn_date",
    ],
    "item_id": [
        "item_id",
        "product_id",
        "stockcode",
        "item",
        "product",
        "sku",
        "description",
    ],
}


def read_tabular_file(file_name: str, file_bytes: bytes) -> pd.DataFrame:
    extension = Path(file_name or "").suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Only CSV, XLS, and XLSX files are supported.")

    if not file_bytes:
        raise ValueError("Uploaded file is empty.")

    file_buffer = BytesIO(file_bytes)

    if extension == ".csv":
        frame = pd.read_csv(file_buffer)
    else:
        frame = pd.read_excel(file_buffer)

    frame = frame.dropna(how="all")

    if frame.empty:
        raise ValueError("No usable rows found in the uploaded file.")

    return frame.reset_index(drop=True)


def build_customer_frame(raw_frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    frame = raw_frame.copy()
    frame.columns = [_normalize_column_name(column) for column in frame.columns]
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame = frame.reset_index(drop=True)

    row_count = len(frame)
    row_index = pd.Series(range(row_count), dtype=int)

    id_source = _pick_series(frame, "id")
    name_source = _pick_series(frame, "customer_name")
    city_source = _pick_series(frame, "city")
    recency_source = _pick_series(frame, "recency")
    frequency_source = _pick_series(frame, "frequency")
    monetary_source = _pick_series(frame, "monetary")
    total_orders_source = _pick_series(frame, "total_orders")
    avg_order_value_source = _pick_series(frame, "avg_order_value")
    preferred_channel_source = _pick_series(frame, "preferred_channel")
    last_purchase_source = _pick_series(frame, "last_purchase_date")

    ids = _to_clean_string_series(
        id_source,
        row_count,
        fallback=[f"CUST-{1001 + index}" for index in row_index],
    )
    customer_names = _to_clean_string_series(
        name_source,
        row_count,
        fallback=[f"Customer {index + 1}" for index in row_index],
    )
    cities = _to_clean_string_series(city_source, row_count, fallback=["Unknown"] * row_count)

    recency = _to_numeric_series(recency_source, row_count)
    frequency = _to_numeric_series(frequency_source, row_count)
    total_orders = _to_numeric_series(total_orders_source, row_count)
    avg_order_value = _to_numeric_series(avg_order_value_source, row_count)
    monetary = _to_numeric_series(monetary_source, row_count)

    parsed_last_purchase = _to_datetime_series(last_purchase_source, row_count)

    if parsed_last_purchase.notna().any():
        today = pd.Timestamp(datetime.now(timezone.utc).date())
        recency_from_date = (today - parsed_last_purchase).dt.days
        recency = recency.fillna(recency_from_date)

    synthetic_recency = ((row_index * 3) % 90) + 1
    recency = recency.fillna(synthetic_recency)
    recency = recency.clip(lower=1).round().astype(int)

    frequency = frequency.fillna(total_orders)
    synthetic_frequency = (row_index % 8) + 1
    frequency = frequency.fillna(synthetic_frequency)
    frequency = frequency.clip(lower=1).round().astype(int)

    total_orders = total_orders.fillna(frequency + 2)
    total_orders = total_orders.clip(lower=1).round().astype(int)

    inferred_monetary = avg_order_value * total_orders
    synthetic_monetary = (
        frequency * 185 + np.maximum(40 - recency, 0) * 16 + (row_index % 5) * 45 + 120
    )

    monetary = monetary.fillna(inferred_monetary)
    monetary = monetary.fillna(synthetic_monetary)
    monetary = monetary.clip(lower=50).round(2)

    avg_order_value = avg_order_value.fillna(monetary / total_orders.replace(0, np.nan))
    avg_order_value = avg_order_value.fillna(0).round(2)

    preferred_channel = _to_clean_string_series(
        preferred_channel_source,
        row_count,
        fallback=[None] * row_count,
    )

    fallback_last_purchase = (
        pd.Timestamp(datetime.now(timezone.utc).date()) - pd.to_timedelta(recency, unit="D")
    ).dt.strftime("%Y-%m-%d")

    if parsed_last_purchase.notna().any():
        formatted_dates = parsed_last_purchase.dt.strftime("%Y-%m-%d")
        last_purchase = formatted_dates.where(formatted_dates.notna(), fallback_last_purchase)
    else:
        last_purchase = fallback_last_purchase

    churn_risk = recency.apply(_to_churn_risk)

    customers = pd.DataFrame(
        {
            "id": ids,
            "customerName": customer_names,
            "city": cities,
            "preferredChannel": preferred_channel,
            "segment": "Unassigned",
            "segmentShortLabel": "Unassigned",
            "cluster": "Cluster 1",
            "recency": recency,
            "frequency": frequency,
            "monetary": monetary,
            "totalOrders": total_orders,
            "avgOrderValue": avg_order_value,
            "lastPurchaseDate": last_purchase,
            "churnRisk": churn_risk,
        }
    )

    preview_subset = customers[PREVIEW_COLUMN_IDS]
    missing_mask = preview_subset.isna().copy()
    for col in preview_subset.columns:
        series = preview_subset[col]
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            missing_mask[col] = missing_mask[col] | (series.astype(str).str.strip() == "")

    item_source = _pick_series(frame, "item_id")
    total_items = int(item_source.nunique()) if item_source is not None else 0

    quality = {
        "totalRows": int(len(customers)),
        "totalColumns": len(PREVIEW_COLUMNS),
        "missingCells": int(missing_mask.sum().sum()),
        "rowsWithMissingValues": int(missing_mask.any(axis=1).sum()),
        "totalItems": total_items,
    }

    return customers, quality


def to_serializable_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for raw_row in frame.to_dict(orient="records"):
        cleaned_row: dict[str, Any] = {}

        for key, value in raw_row.items():
            if value is None or (isinstance(value, float) and np.isnan(value)):
                cleaned_row[key] = None
            elif isinstance(value, np.generic):
                cleaned_row[key] = value.item()
            elif isinstance(value, pd.Timestamp):
                cleaned_row[key] = value.strftime("%Y-%m-%d")
            elif pd.isna(value):
                cleaned_row[key] = None
            else:
                cleaned_row[key] = value

        records.append(cleaned_row)

    return records


def _normalize_column_name(value: Any) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _pick_series(frame: pd.DataFrame, field_name: str) -> pd.Series | None:
    for candidate in _COLUMN_CANDIDATES[field_name]:
        if candidate in frame.columns:
            return frame[candidate]
    return None


def _to_clean_string_series(
    source: pd.Series | None,
    row_count: int,
    fallback: list[str | None],
) -> pd.Series:
    fallback_series = pd.Series(fallback)

    if source is None:
        return fallback_series

    text_series = source.astype(str).str.strip()
    text_series = text_series.replace({"": np.nan, "nan": np.nan, "None": np.nan, "NaT": np.nan})
    text_series = text_series.where(text_series.notna(), fallback_series)

    if len(text_series) != row_count:
        return fallback_series

    return text_series


def _to_numeric_series(source: pd.Series | None, row_count: int) -> pd.Series:
    if source is None:
        return pd.Series([np.nan] * row_count, dtype=float)

    parsed = pd.to_numeric(source, errors="coerce")

    if len(parsed) != row_count:
        return pd.Series([np.nan] * row_count, dtype=float)

    return parsed.reset_index(drop=True)


def _to_datetime_series(source: pd.Series | None, row_count: int) -> pd.Series:
    if source is None:
        return pd.Series([pd.NaT] * row_count)

    parsed = pd.to_datetime(source, errors="coerce", utc=True)
    parsed = parsed.dt.tz_convert(None)

    if len(parsed) != row_count:
        return pd.Series([pd.NaT] * row_count)

    return parsed.reset_index(drop=True)


def _to_churn_risk(recency: int) -> str:
    if recency > 60:
        return "High"
    if recency > 30:
        return "Medium"
    return "Low"
