from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.customer import Customer
from app.db.models.order import Order, OrderItem
from app.repositories.bulk import bulk_upsert_by_key


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, business_id: str | UUID, external_order_id: str, customer_id: str | UUID, **fields: object) -> Order:
        order = Order(business_id=str(business_id), external_order_id=external_order_id, customer_id=str(customer_id), **fields)
        self.session.add(order)
        self.session.flush()
        return order

    def get_by_id(self, order_id: str | UUID) -> Order | None:
        return self.session.get(Order, str(order_id))

    def get_by_external_id(self, *, business_id: str | UUID, external_order_id: str) -> Order | None:
        stmt = select(Order).where(
            Order.business_id == str(business_id),
            Order.external_order_id == external_order_id,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def bulk_upsert(self, business_id: str | UUID, orders: Iterable[dict[str, object]]) -> list[Order]:
        """Upsert orders with one SELECT per chunk instead of one per row.

        See ``app.repositories.bulk``. The previous version issued a SELECT and a
        flush for every order in the file.
        """
        return bulk_upsert_by_key(
            self.session,
            Order,
            business_id=business_id,
            rows=orders,
            key_field="external_order_id",
        )

    def create_item(self, *, order_id: str | UUID, product_id: str | UUID, quantity: int, unit_price: float | str, total_amount: float | str) -> OrderItem:
        item = OrderItem(
            order_id=str(order_id),
            product_id=str(product_id),
            quantity=quantity,
            unit_price=unit_price,
            total_amount=total_amount,
        )
        self.session.add(item)
        self.session.flush()
        return item

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_for_business(
        self,
        business_id: str | UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
    ) -> list[tuple[Order, str | None, int]]:
        """Return ``(order, customer_name, item_count)`` rows for one business.

        The customer name and item count come back in the same statement rather than
        being read from ``order.customer`` and ``order.items`` per row. Those
        relationships are lazy, so a page of 100 orders would otherwise issue 201
        queries — the classic N+1 — and the orders page needs both columns for every
        row it renders.

        Ordered newest first by ``order_date``, falling back to ``created_at`` for
        rows imported without a date, so an order with no date still appears in a
        stable position instead of drifting between pages.
        """
        item_counts = (
            select(OrderItem.order_id, func.count(OrderItem.id).label("item_count"))
            .group_by(OrderItem.order_id)
            .subquery()
        )

        stmt = (
            select(Order, Customer.name, func.coalesce(item_counts.c.item_count, 0))
            .outerjoin(Customer, Customer.id == Order.customer_id)
            .outerjoin(item_counts, item_counts.c.order_id == Order.id)
            .where(Order.business_id == str(business_id))
            .order_by(
                func.coalesce(Order.order_date, Order.created_at).desc(),
                Order.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        if status:
            stmt = stmt.where(Order.status == status)

        return [(row[0], row[1], int(row[2] or 0)) for row in self.session.execute(stmt).all()]

    def count_for_business(self, business_id: str | UUID, *, status: str | None = None) -> int:
        """Total matching rows, so the page can show "showing 100 of 4,812"."""
        stmt = select(func.count(Order.id)).where(Order.business_id == str(business_id))

        if status:
            stmt = stmt.where(Order.status == status)

        return int(self.session.execute(stmt).scalar_one() or 0)

    def distinct_statuses(self, business_id: str | UUID) -> list[str]:
        """Statuses actually present for this tenant, for the filter dropdown.

        Read from the data rather than hardcoded: order status arrives from whatever
        file or webhook the tenant sends, so a fixed list would offer filters that
        match nothing and omit the ones they use.
        """
        stmt = (
            select(Order.status)
            .where(Order.business_id == str(business_id))
            .distinct()
            .order_by(Order.status)
        )
        return [row[0] for row in self.session.execute(stmt).all() if row[0]]

    def items_for_order(self, order_id: str | UUID) -> list[OrderItem]:
        stmt = select(OrderItem).where(OrderItem.order_id == str(order_id))
        return list(self.session.execute(stmt).scalars().all())
