"""Inventory API routes for v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.multi_tenancy import BusinessContext, get_business_context
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


@router.get("")
def list_inventory(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """List all inventory for the authenticated user's business."""
    service = InventoryService(business_context.session)
    inventory = service.list_inventory(business_context.business_id)
    return {
        "count": len(inventory),
        "inventory": [
            {
                "id": i.id,
                "product_id": i.product_id,
                "quantity": i.quantity,
                "reserved_quantity": i.reserved_quantity,
                "reorder_level": i.reorder_level,
                "supplier": i.supplier,
                "status": i.status,
                "updated_at": i.updated_at.isoformat() if i.updated_at else None,
            }
            for i in inventory
        ],
    }


@router.get("/low-stock")
def get_low_stock_items(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Get inventory items below reorder level."""
    service = InventoryService(business_context.session)
    low_stock = service.get_low_stock_items(business_context.business_id)
    return {
        "count": len(low_stock),
        "items": [
            {
                "id": i.id,
                "product_id": i.product_id,
                "quantity": i.quantity,
                "reorder_level": i.reorder_level,
                "supplier": i.supplier,
            }
            for i in low_stock
        ],
    }


@router.get("/{inventory_id}")
def get_inventory_item(
    inventory_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Get a specific inventory item."""
    service = InventoryService(business_context.session)
    item = service.get_inventory(inventory_id)

    if not item or item.business_id != business_context.business_id:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    return {
        "id": item.id,
        "product_id": item.product_id,
        "quantity": item.quantity,
        "reserved_quantity": item.reserved_quantity,
        "reorder_level": item.reorder_level,
        "supplier": item.supplier,
        "status": item.status,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
