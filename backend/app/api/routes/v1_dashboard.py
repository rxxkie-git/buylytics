"""Dashboard read API.

Every endpoint derives its tenant from the authenticated business context. None
of them accepts a ``business_id`` parameter, so there is no request a client can
construct that reads another tenant's data — the identifier simply is not part of
the input surface.

Responses are aggregates, not row dumps. That keeps payloads small enough to
cache and means a dashboard request cannot be turned into a bulk export of
customer records.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.multi_tenancy import BusinessContext, get_business_context
from app.services import dashboard_service

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Headline counts: customers, orders, revenue, stock health, analytics state."""
    return await dashboard_service.get_summary(
        business_context.session, business_id=business_context.business_id
    )


@router.get("/revenue-trend")
async def revenue_trend(
    days: int = Query(
        dashboard_service.DEFAULT_TREND_DAYS,
        ge=1,
        le=dashboard_service.MAX_TREND_DAYS,
        description="Length of the window in days.",
    ),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Daily revenue and order count, zero-filled across the whole window."""
    return await dashboard_service.get_revenue_trend(
        business_context.session, business_id=business_context.business_id, days=days
    )


@router.get("/rfm-distribution")
async def rfm_distribution(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Customers per RFM segment, with per-segment averages and provenance."""
    return await dashboard_service.get_rfm_distribution(
        business_context.session, business_id=business_context.business_id
    )


@router.get("/segment-distribution")
async def segment_distribution(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Cluster sizes and churn-risk counts from the segmentation snapshot."""
    return await dashboard_service.get_segment_distribution(
        business_context.session, business_id=business_context.business_id
    )


@router.get("/top-customers")
async def top_customers(
    limit: int = Query(10, ge=1, le=100),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Highest-value customers from the stored RFM snapshot."""
    customers = await dashboard_service.get_top_customers(
        business_context.session, business_id=business_context.business_id, limit=limit
    )
    return {"count": len(customers), "results": customers}


@router.get("/inventory-summary")
async def inventory_summary(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Stock health counts against the configured low-stock threshold."""
    return await dashboard_service.get_inventory_summary(
        business_context.session, business_id=business_context.business_id
    )
