from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("business_id", "external_customer_id", name="uq_customer_business_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    external_customer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    business: Mapped["Business"] = relationship(back_populates="customers")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    rfm_entries: Mapped[list["CustomerRFM"]] = relationship(back_populates="customer")
    segment_entries: Mapped[list["CustomerSegment"]] = relationship(back_populates="customer")
