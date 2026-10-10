from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    memberships: Mapped[list["BusinessMembership"]] = relationship(back_populates="business")
    customers: Mapped[list["Customer"]] = relationship(back_populates="business")
    products: Mapped[list["Product"]] = relationship(back_populates="business")
    orders: Mapped[list["Order"]] = relationship(back_populates="business")
    inventories: Mapped[list["Inventory"]] = relationship(back_populates="business")
    imports: Mapped[list["DatasetImport"]] = relationship(back_populates="business")
    analytics_runs: Mapped[list["AnalyticsRun"]] = relationship(back_populates="business")
    rfm_entries: Mapped[list["CustomerRFM"]] = relationship(back_populates="business")
    segment_entries: Mapped[list["CustomerSegment"]] = relationship(back_populates="business")


class BusinessMembership(Base):
    __tablename__ = "business_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    business: Mapped[Business] = relationship(back_populates="memberships")
