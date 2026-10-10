"""Order management service."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.order import Order, OrderItem
from app.repositories.order_repository import OrderRepository


class OrderService:
    """Service for order data management."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = OrderRepository(session)

    def create_order(
        self,
        business_id: str,
        external_order_id: str,
        customer_id: str,
        **fields: Any,
    ) -> Order:
        """Create a new order."""
        return self.repository.create(
            business_id=business_id,
            external_order_id=external_order_id,
            customer_id=customer_id,
            **fields,
        )

    def get_order(self, order_id: str) -> Order | None:
        """Get order by ID."""
        return self.repository.get_by_id(order_id)

    def get_order_by_external_id(
        self,
        business_id: str,
        external_order_id: str,
    ) -> Order | None:
        """Get order by external ID within a business."""
        return self.repository.get_by_external_id(business_id=business_id, external_order_id=external_order_id)

    def bulk_upsert_orders(self, business_id: str, orders: list[dict[str, Any]]) -> list[Order]:
        """Upsert multiple orders at once."""
        return self.repository.bulk_upsert(business_id, orders)

    def add_order_item(
        self,
        order_id: str,
        product_id: str,
        quantity: int,
        unit_price: Decimal | float,
        total_amount: Decimal | float,
    ) -> OrderItem:
        """Add an item to an order."""
        return self.repository.create_item(
            order_id=order_id,
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            total_amount=total_amount,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    MAX_PAGE_SIZE = 500

    def list_orders(
        self,
        business_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        """A page of orders with the totals needed to render the page around it.

        Returns plain dictionaries rather than ORM rows. The route then has no lazy
        relationships left to touch, which is what keeps the N+1 fixed in the
        repository from reappearing one layer up.
        """
        limit = max(1, min(int(limit), self.MAX_PAGE_SIZE))
        offset = max(0, int(offset))

        rows = self.repository.list_for_business(
            business_id, limit=limit, offset=offset, status=status
        )
        total = self.repository.count_for_business(business_id, status=status)

        return {
            "count": len(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
            "status_filter": status,
            "statuses": self.repository.distinct_statuses(business_id),
            "orders": [
                {
                    "id": order.id,
                    "external_order_id": order.external_order_id,
                    "customer_id": order.customer_id,
                    "customer_name": customer_name,
                    "order_date": order.order_date.isoformat() if order.order_date else None,
                    # Serialised as a string, not a float: these are Numeric(12, 2)
                    # and binary floating point cannot represent every two-decimal
                    # value exactly. The frontend parses it once for display.
                    "total_amount": str(order.total_amount) if order.total_amount is not None else None,
                    "status": order.status,
                    "item_count": item_count,
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                }
                for order, customer_name, item_count in rows
            ],
        }

    def get_order_detail(self, business_id: str, order_id: str) -> dict[str, Any] | None:
        """One order with its line items, or ``None`` if it is not this tenant's.

        The tenant check is here rather than left to the route so that every caller
        gets it. Returning ``None`` for both "does not exist" and "belongs to someone
        else" is deliberate: distinguishing them would confirm to one tenant that an
        id exists in another.
        """
        order = self.repository.get_by_id(order_id)

        if order is None or str(order.business_id) != str(business_id):
            return None

        items = self.repository.items_for_order(order.id)

        return {
            "id": order.id,
            "external_order_id": order.external_order_id,
            "customer_id": order.customer_id,
            "order_date": order.order_date.isoformat() if order.order_date else None,
            "total_amount": str(order.total_amount) if order.total_amount is not None else None,
            "status": order.status,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_price": str(item.unit_price),
                    "total_amount": str(item.total_amount),
                }
                for item in items
            ],
        }
