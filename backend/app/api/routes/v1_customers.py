"""Customer API routes for v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.multi_tenancy import BusinessContext, get_business_context
from app.services.customer_service import CustomerService

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


@router.get("")
def list_customers(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """List paginated customers with their RFM metrics for the authenticated user's business."""
    service = CustomerService(business_context.session)
    return service.list_customers_with_rfm(
        business_context.business_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{customer_id}")
def get_customer(
    customer_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Get a specific customer."""
    service = CustomerService(business_context.session)
    customer = service.get_customer(customer_id)

    if not customer or customer.business_id != business_context.business_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    return {
        "id": customer.id,
        "external_customer_id": customer.external_customer_id,
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        "city": customer.city,
        "created_at": customer.created_at.isoformat() if customer.created_at else None,
        "updated_at": customer.updated_at.isoformat() if customer.updated_at else None,
    }
