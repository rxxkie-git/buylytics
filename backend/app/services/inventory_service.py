"""Inventory management service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.inventory import Inventory
from app.repositories.inventory_repository import InventoryRepository


class InventoryService:
    """Service for inventory management."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = InventoryRepository(session)

    def create_or_get_inventory(
        self,
        business_id: str,
        product_id: str,
        **fields: Any,
    ) -> Inventory:
        """Create inventory or get existing for a product."""
        # Check if inventory already exists for this product
        existing = self.repository.session.execute(
            self.repository.session.query(Inventory).filter(
                Inventory.business_id == business_id,
                Inventory.product_id == product_id,
            )
        ).scalar_one_or_none()

        if existing:
            return existing

        return self.repository.create(business_id=business_id, product_id=product_id, **fields)

    def get_inventory(self, inventory_id: str) -> Inventory | None:
        """Get inventory by ID."""
        return self.repository.get_by_id(inventory_id)

    def list_inventory(self, business_id: str) -> list[Inventory]:
        """List all inventory for a business."""
        return self.repository.list_by_business(business_id)

    def update_inventory(self, inventory_id: str, **fields: Any) -> Inventory | None:
        """Update inventory."""
        return self.repository.update(inventory_id, **fields)

    def delete_inventory(self, inventory_id: str) -> bool:
        """Delete inventory."""
        return self.repository.delete(inventory_id)

    def bulk_upsert_inventory(self, business_id: str, inventory_rows: list[dict[str, Any]]) -> list[Inventory]:
        """Upsert multiple inventory rows at once."""
        return self.repository.bulk_upsert(business_id, inventory_rows)

    def get_low_stock_items(self, business_id: str) -> list[Inventory]:
        """Get inventory items below reorder level."""
        return [
            item
            for item in self.list_inventory(business_id)
            if item.quantity <= item.reorder_level
        ]
