"""Product management service."""

from __future__ import annotations

from typing import Any
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.db.models.product import Product
from app.repositories.product_repository import ProductRepository


class ProductService:
    """Service for product data management."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ProductRepository(session)

    def create_product(
        self,
        business_id: str,
        external_product_id: str,
        **fields: Any,
    ) -> Product:
        """Create a new product."""
        return self.repository.create(business_id=business_id, external_product_id=external_product_id, **fields)

    def get_product(self, product_id: str) -> Product | None:
        """Get product by ID."""
        return self.repository.get_by_id(product_id)

    def get_product_by_external_id(
        self,
        business_id: str,
        external_product_id: str,
    ) -> Product | None:
        """Get product by external ID within a business."""
        return self.repository.get_by_external_id(business_id=business_id, external_product_id=external_product_id)

    def product_ids_by_external_ids(
        self,
        business_id: str,
        external_product_ids: Iterable[str],
    ) -> dict[str, str]:
        """Resolve many external ids to internal ids at once.

        Exists so the inventory import can resolve every referenced product in a
        handful of chunked queries instead of one query per row.
        """
        return self.repository.ids_by_external_ids(
            business_id=business_id,
            external_product_ids=external_product_ids,
        )

    def list_products(self, business_id: str) -> list[Product]:
        """List all products for a business."""
        return self.repository.list_by_business(business_id)

    def bulk_upsert_products(self, business_id: str, products: list[dict[str, Any]]) -> list[Product]:
        """Upsert multiple products at once."""
        return self.repository.bulk_upsert(business_id, products)
