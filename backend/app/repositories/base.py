from __future__ import annotations

from typing import Any, Protocol


class RepositoryProtocol(Protocol):
    """Repository contract for future persistence layers.

    The platform will eventually need separate repositories for:
    - business
    - customer
    - product
    - order
    - inventory
    - analytics
    - import data

    This interface is intentionally small and open-ended so the application can
    evolve without locking itself to SQLite or any other specific database.
    """

    def save_dataset(self, payload: dict[str, Any]) -> None: ...

    def get_dataset(
        self,
        dataset_id: str | None = None,
        dataset_type: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get_inventory_items(self) -> list[dict[str, Any]]: ...

    def add_inventory_item(self, item: dict[str, Any]) -> dict[str, Any]: ...

    def update_inventory_item(
        self,
        item_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    def delete_inventory_item(self, item_id: str) -> bool: ...

    def replace_inventory(self, items: list[dict[str, Any]]) -> None: ...

    def upsert_inventory_items(self, items: list[dict[str, Any]]) -> tuple[int, int]: ...
