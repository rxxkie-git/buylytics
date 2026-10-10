"""Order read routes for v1.

Added in Phase 2 alongside the orders import. Orders are what RFM is measured from,
so being able to see the rows the metrics were computed over is the difference
between a number an operator trusts and a number they have to take on faith.

Writes stay out of the API surface: orders arrive through the import endpoints and
webhooks, both of which already carry validation and idempotency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.multi_tenancy import BusinessContext, get_business_context
from app.services.order_service import OrderService

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.get("")
def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, max_length=50),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """A page of orders for the authenticated user's business.

    ``business_id`` is not a parameter. It comes from the authenticated context, so
    there is nothing here a client could set to read another tenant's orders.
    """
    service = OrderService(business_context.session)
    return service.list_orders(
        business_context.business_id,
        limit=limit,
        offset=offset,
        status=status,
    )


@router.get("/{order_id}")
def get_order(
    order_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """One order with its line items."""
    service = OrderService(business_context.session)
    detail = service.get_order_detail(business_context.business_id, order_id)

    if detail is None:
        raise HTTPException(status_code=404, detail="Order not found")

    return detail
