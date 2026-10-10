from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import String, DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "external_product_id", name="uq_product_business_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    external_product_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    business: Mapped["Business"] = relationship(back_populates="products")
    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="product")
    inventory_rows: Mapped[list["Inventory"]] = relationship(back_populates="product")
