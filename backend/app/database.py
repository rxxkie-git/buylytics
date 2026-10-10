from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import Settings


DEFAULT_INVENTORY_ITEMS: list[dict[str, Any]] = [
    {"id": "SKU-1001", "itemName": "Wireless Mouse", "category": "Electronics", "quantity": 45, "price": 29.99, "supplier": "Logitech", "status": "In Stock"},
    {"id": "SKU-1002", "itemName": "Mechanical Keyboard", "category": "Electronics", "quantity": 8, "price": 89.99, "supplier": "Corsair", "status": "Low Stock"},
    {"id": "SKU-1003", "itemName": "USB-C Cable (1m)", "category": "Electronics", "quantity": 120, "price": 9.99, "supplier": "Anker", "status": "In Stock"},
    {"id": "SKU-1004", "itemName": "Bluetooth Speaker", "category": "Electronics", "quantity": 0, "price": 49.99, "supplier": "JBL", "status": "Out of Stock"},
]


@dataclass
class InMemoryRepository:
    """Minimal runtime repository used while the persistence layer is intentionally abstract.

    This is a local placeholder only. It avoids any SQLite-specific implementation and
    can later be replaced by a concrete repository adapter without altering the route layer.
    """

    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventory_items: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _initialize_schema(self) -> None:
        self.inventory_items = {item["id"]: dict(item) for item in DEFAULT_INVENTORY_ITEMS}

    def save_dataset(self, payload: dict[str, Any]) -> None:
        dataset_id = payload["datasetId"]
        payload = dict(payload)
        if "updatedAt" not in payload:
            payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
        self.datasets[dataset_id] = payload

    def get_dataset(
        self,
        dataset_id: str | None = None,
        dataset_type: str | None = None,
    ) -> dict[str, Any] | None:
        if dataset_id:
            return self.datasets.get(dataset_id)

        if dataset_type:
            matches = [
                payload for payload in self.datasets.values()
                if payload.get("datasetType") == dataset_type
            ]
            if not matches:
                return None
            return max(matches, key=lambda item: str(item.get("createdAt") or ""))

        if not self.datasets:
            return None
        return max(self.datasets.values(), key=lambda item: str(item.get("createdAt") or ""))

    def get_inventory_items(self) -> list[dict[str, Any]]:
        return [dict(item) for item in sorted(self.inventory_items.values(), key=lambda item: item["id"]) ]

    def inventory_item_exists(self, item_id: str) -> bool:
        return item_id.lower() in {key.lower() for key in self.inventory_items}

    def add_inventory_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item["id"])
        if self.inventory_item_exists(item_id):
            raise ValueError(f"Item with SKU '{item_id}' already exists.")

        item_copy = dict(item)
        item_copy["status"] = item_copy.get("status") or _item_status(int(item_copy.get("quantity", 0)))
        self.inventory_items[item_id] = item_copy
        return dict(item_copy)

    def update_inventory_item(self, item_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        matches = [key for key in self.inventory_items if key.lower() == item_id.lower()]
        if not matches:
            return None
        stored = self.inventory_items[matches[0]]
        for key, value in updates.items():
            if key == "itemName":
                stored["itemName"] = value
            elif key == "quantity":
                stored["quantity"] = int(value)
            else:
                stored[key] = value
        if "status" not in updates:
            stored["status"] = _item_status(int(stored.get("quantity", 0)))
        return dict(stored)

    def delete_inventory_item(self, item_id: str) -> bool:
        for key in list(self.inventory_items):
            if key.lower() == item_id.lower():
                del self.inventory_items[key]
                return True
        return False

    def replace_inventory(self, items: list[dict[str, Any]]) -> None:
        self.inventory_items = {}
        for item in items:
            normalized = dict(item)
            normalized["status"] = normalized.get("status") or _item_status(int(normalized.get("quantity", 0)))
            self.inventory_items[str(normalized["id"])] = normalized

    def upsert_inventory_items(self, items: list[dict[str, Any]]) -> tuple[int, int]:
        added = 0
        updated = 0

        for item in items:
            item_id = str(item["id"])
            if item_id.lower() in {key.lower() for key in self.inventory_items}:
                self.update_inventory_item(item_id, item)
                updated += 1
            else:
                self.add_inventory_item(item)
                added += 1

        return added, updated


def _item_status(quantity: int) -> str:
    if quantity <= 0:
        return "Out of Stock"
    if quantity <= 10:
        return "Low Stock"
    return "In Stock"


def initialize_repository(settings: Settings) -> InMemoryRepository:
    """Create the runtime repository without binding the app to SQLite or another concrete backend."""
    repository = InMemoryRepository()
    repository._initialize_schema()
    _ = settings
    return repository

