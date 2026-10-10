from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supplier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    business: Mapped["Business"] = relationship(back_populates="inventories")
    product: Mapped["Product"] = relationship(back_populates="inventory_rows")
