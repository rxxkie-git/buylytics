from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.inventory import Inventory
from app.repositories.bulk import bulk_upsert_by_key


class InventoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, business_id: str | UUID, product_id: str | UUID, **fields: object) -> Inventory:
        inventory = Inventory(business_id=str(business_id), product_id=str(product_id), **fields)
        self.session.add(inventory)
        self.session.flush()
        return inventory

    def get_by_id(self, inventory_id: str | UUID) -> Inventory | None:
        return self.session.get(Inventory, str(inventory_id))

    def list_by_business(self, business_id: str | UUID) -> list[Inventory]:
        stmt = select(Inventory).where(Inventory.business_id == str(business_id)).order_by(Inventory.updated_at.desc())
        return list(self.session.execute(stmt).scalars().all())

    def update(self, inventory_id: str | UUID, **fields: object) -> Inventory | None:
        inventory = self.get_by_id(inventory_id)
        if inventory is None:
            return None
        for key, value in fields.items():
            setattr(inventory, key, value)
        self.session.flush()
        return inventory

    def delete(self, inventory_id: str | UUID) -> bool:
        inventory = self.get_by_id(inventory_id)
        if inventory is None:
            return False
        self.session.delete(inventory)
        self.session.flush()
        return True

    def bulk_upsert(self, business_id: str | UUID, inventory_rows: Iterable[dict[str, object]]) -> list[Inventory]:
        """Upsert inventory with one SELECT per chunk instead of one per row.

        Keyed on ``product_id`` rather than an external id — inventory rows are
        already resolved to internal product ids by the importer — but otherwise
        identical to the other three. See ``app.repositories.bulk``.
        """
        return bulk_upsert_by_key(
            self.session,
            Inventory,
            business_id=business_id,
            rows=inventory_rows,
            key_field="product_id",
        )
