"""Product API routes for v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.multi_tenancy import BusinessContext, get_business_context
from app.services.product_service import ProductService

router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.get("")
def list_products(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """List all products for the authenticated user's business."""
    service = ProductService(business_context.session)
    products = service.list_products(business_context.business_id)
    return {
        "count": len(products),
        "products": [
            {
                "id": p.id,
                "external_product_id": p.external_product_id,
                "name": p.name,
                "sku": p.sku,
                "category": p.category,
                "price": str(p.price) if p.price else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in products
        ],
    }


@router.get("/{product_id}")
def get_product(
    product_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Get a specific product."""
    service = ProductService(business_context.session)
    product = service.get_product(product_id)

    if not product or product.business_id != business_context.business_id:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "id": product.id,
        "external_product_id": product.external_product_id,
        "name": product.name,
        "sku": product.sku,
        "category": product.category,
        "price": str(product.price) if product.price else None,
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }
