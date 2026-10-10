from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from ..auth import get_current_user, ClerkUser

from ..services.inference import rerun_customer_clustering
from ..services.product_analysis import run_product_analytics

router = APIRouter(tags=["analytics"])


def _load_customer_dataset(request: Request, dataset_id: str | None) -> dict[str, Any]:
    repository = request.app.state.dataset_repository
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Storage backend is not configured. Use an implementation-specific repository or keep this in memory for local development.",
        )
    dataset = repository.get_dataset(dataset_id=dataset_id, dataset_type="customer")

    if dataset is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset not found. Upload a file first and retry.",
        )

    return dataset


def _load_product_dataset(request: Request, dataset_id: str | None) -> dict[str, Any]:
    repository = request.app.state.dataset_repository
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Storage backend is not configured. Use an implementation-specific repository or keep this in memory for local development.",
        )
    dataset = repository.get_dataset(dataset_id=dataset_id, dataset_type="product")

    if dataset is None:
        raise HTTPException(
            status_code=404,
            detail="Product dataset not found. Upload a product file first and retry.",
        )

    return dataset


def _load_product_analysis_frame(dataset: dict[str, Any]) -> pd.DataFrame:
    product_data = dataset.get("productData", {})
    artifact_paths = product_data.get("artifactPaths", {})

    candidate_paths = [
        artifact_paths.get("cleanedFilePath"),
        artifact_paths.get("processedFilePath"),
    ]

    for raw_path in candidate_paths:
        if not raw_path:
            continue

        file_path = Path(str(raw_path))
        if file_path.exists() and file_path.is_file():
            if file_path.suffix.lower() == ".csv":
                return pd.read_csv(file_path)
            if file_path.suffix.lower() in {".xls", ".xlsx"}:
                return pd.read_excel(file_path)

    cleaned_preview = product_data.get("cleanedPreviewRecords")
    if isinstance(cleaned_preview, list) and cleaned_preview:
        return pd.DataFrame(cleaned_preview)

    processed_preview = product_data.get("processedPreviewRecords")
    if isinstance(processed_preview, list) and processed_preview:
        return pd.DataFrame(processed_preview)

    legacy_records = product_data.get("records")
    if isinstance(legacy_records, list) and legacy_records:
        return pd.DataFrame(legacy_records)

    raise HTTPException(
        status_code=422,
        detail="No readable product dataset records found for analytics.",
    )


@router.get("/preview")
def get_preview_data(
    request: Request,
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataset = _load_customer_dataset(request, dataset_id)
        return dataset["preview"]
    except HTTPException as e:
        if e.status_code == 503:
            return {}
        raise


@router.get("/dashboard")
def get_dashboard_data(
    request: Request,
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataset = _load_customer_dataset(request, dataset_id)
        return dataset["dashboard"]
    except HTTPException as e:
        if e.status_code == 503:
            # Return empty dashboard data when DB not available
            return {
                "kpis": [],
                "charts": [],
                "summary": {},
            }
        raise


@router.get("/analyze")
def get_analysis_results(
    request: Request,
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    cluster_by: str = Query(default="default", alias="clusterBy"),
    cluster_count: int = Query(default=0, alias="clusterCount", ge=0, le=12),
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataset = _load_customer_dataset(request, dataset_id)
        analysis_result = dataset["analysis"]

        if cluster_by == "default" and (cluster_count == 0 or cluster_count == int(analysis_result.get("optimalK", 0))):
            return analysis_result

        customer_rows = dataset.get("results", {}).get("customers")
        if not isinstance(customer_rows, list) or not customer_rows:
            return analysis_result

        customer_frame = pd.DataFrame(customer_rows)
        return rerun_customer_clustering(
            customer_frame,
            cluster_by=cluster_by,
            cluster_count=cluster_count or int(analysis_result.get("optimalK", 4)),
        )
    except HTTPException as e:
        if e.status_code == 503:
            return {}
        raise


@router.get("/results")
def get_results_data(
    request: Request,
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    cluster_by: str = Query(default="default", alias="clusterBy"),
    cluster_count: int = Query(default=0, alias="clusterCount", ge=0, le=12),
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataset = _load_customer_dataset(request, dataset_id)
        if cluster_by == "default" and cluster_count == 0:
            return dataset["results"]

        customer_rows = dataset.get("results", {}).get("customers")
        if not isinstance(customer_rows, list) or not customer_rows:
            return dataset["results"]

        customer_frame = pd.DataFrame(customer_rows)
        cluster_result = rerun_customer_clustering(
            customer_frame,
            cluster_by=cluster_by,
            cluster_count=cluster_count or int(dataset.get("analysis", {}).get("optimalK", 4)),
        )

        return {
            "segments": [
                {
                    "segment": item["cluster"],
                    "value": item["customers"],
                }
                for item in cluster_result.get("clusterDistribution", [])
            ],
            "customers": customer_frame.to_dict(orient="records"),
            "modelMeta": cluster_result.get("modelMeta", {}),
            "clusterBy": cluster_by,
            "clusterCount": int(cluster_count or int(dataset.get("analysis", {}).get("optimalK", 4))),
        }
    except HTTPException as e:
        if e.status_code == 503:
            return {}
        raise


@router.get("/insights")
def get_insights_data(
    request: Request,
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        dataset = _load_customer_dataset(request, dataset_id)
        return dataset["insights"]
    except HTTPException as e:
        if e.status_code == 503:
            return {}
        raise


from sqlalchemy import func, select
from app.core.multi_tenancy import BusinessContext, get_business_context
from app.db.models.order import Order, OrderItem
from app.db.models.product import Product


@router.get("/product-analysis")
def get_product_analysis_data(
    request: Request,
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    forecast_periods: int = Query(default=12, alias="forecastPeriods", ge=1, le=120),
    frequency: str = Query(default="auto"),
    force_refresh: bool = Query(default=False, alias="forceRefresh"),
    cluster_by: str = Query(default="default", alias="clusterBy"),
    cluster_count: int = Query(default=6, alias="clusterCount", ge=2, le=12),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict[str, Any]:
    frequency_key = frequency.strip().lower()
    if frequency_key not in {"auto", "daily", "monthly"}:
        raise HTTPException(
            status_code=400,
            detail="frequency must be one of: auto, daily, monthly",
        )

    # 1. First check if there is an in-memory dataset repository upload
    dataset = None
    if dataset_id is not None:
        try:
            dataset = _load_product_dataset(request, dataset_id)
        except Exception:
            dataset = None

    if dataset is not None:
        if (
            not force_refresh
            and dataset.get("productAnalysis")
            and frequency_key == "auto"
            and int(forecast_periods) == 12
        ):
            return dataset["productAnalysis"]

        frame = _load_product_analysis_frame(dataset)
        analysis_payload = run_product_analytics(
            frame=frame,
            forecast_periods=int(forecast_periods),
            frequency=frequency_key,
            cluster_by=cluster_by,
            cluster_count=int(cluster_count),
        )

        dataset["productAnalysis"] = analysis_payload
        dataset["updatedAt"] = datetime.now(timezone.utc)
        if request.app.state.dataset_repository is not None:
            request.app.state.dataset_repository.save_dataset(dataset)
        return analysis_payload

    # 2. Phase 2 fallback: load products and order items from PostgreSQL
    session = business_context.session
    business_id = business_context.business_id

    # Query order items with order and product details
    items_stmt = (
        select(
            func.coalesce(Product.name, Product.sku, OrderItem.product_id).label("product"),
            OrderItem.quantity,
            func.coalesce(OrderItem.total_amount, OrderItem.quantity * OrderItem.unit_price, 0).label("revenue"),
            OrderItem.unit_price,
            func.coalesce(Order.order_date, Order.created_at).label("order_date"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .outerjoin(Product, Product.id == OrderItem.product_id)
        .where(Order.business_id == business_id)
    )
    rows = session.execute(items_stmt).all()

    if rows:
        records = [
            {
                "product": r.product,
                "quantity": float(r.quantity) if r.quantity is not None else 1.0,
                "revenue": float(r.revenue) if r.revenue is not None else 0.0,
                "unit_price": float(r.unit_price) if r.unit_price is not None else 0.0,
                "order_date": r.order_date.isoformat() if r.order_date else None,
            }
            for r in rows
        ]
        frame = pd.DataFrame(records)
        return run_product_analytics(
            frame=frame,
            forecast_periods=int(forecast_periods),
            frequency=frequency_key,
            cluster_by=cluster_by,
            cluster_count=int(cluster_count),
        )

    # Fallback if no order items: query products
    products = session.execute(
        select(Product).where(Product.business_id == business_id)
    ).scalars().all()

    if products:
        records = [
            {
                "product": p.name or p.sku or p.id,
                "quantity": 1.0,
                "revenue": float(p.price) if p.price is not None else 0.0,
                "unit_price": float(p.price) if p.price is not None else 0.0,
                "order_date": p.created_at.isoformat() if p.created_at else None,
            }
            for p in products
        ]
        frame = pd.DataFrame(records)
        return run_product_analytics(
            frame=frame,
            forecast_periods=int(forecast_periods),
            frequency=frequency_key,
            cluster_by=cluster_by,
            cluster_count=int(cluster_count),
        )

    # If no data is available
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "salesAnalysis": {
            "available": False,
            "summary": {
                "productsAnalyzed": 0,
                "totalQuantitySold": 0,
                "totalRevenue": 0,
                "averageSalesPerProduct": 0,
            },
            "productPerformanceTable": [],
            "topSellingProducts": [],
            "leastSellingProducts": [],
            "warnings": ["No product or sales records found for this business."],
        },
        "productClustering": {
            "available": False,
            "optimalClusters": 0,
            "elbowMethod": [],
            "clusteredProducts": [],
            "clusterProfiles": [],
            "warnings": [],
        },
        "demandAnalysis": {
            "available": False,
            "frequency": None,
            "timeSeries": [],
            "summary": {},
            "warnings": [],
        },
        "salesForecast": {
            "available": False,
            "modelUsed": None,
            "frequency": None,
            "forecastPeriods": 0,
            "history": [],
            "forecast": [],
            "warnings": [],
        },
        "visualizations": {
            "topProductsBarChart": {"data": []},
            "clusterScatterPlot": {"data": []},
            "demandTimeSeriesChart": {"data": []},
            "forecastChart": {"history": [], "forecast": []},
        },
    }
