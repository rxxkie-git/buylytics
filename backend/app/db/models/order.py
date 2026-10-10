from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("business_id", "external_order_id", name="uq_order_business_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    external_order_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    order_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    business: Mapped["Business"] = relationship(back_populates="orders")
    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(back_populates="order_items")
