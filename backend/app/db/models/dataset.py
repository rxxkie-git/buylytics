from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import String, DateTime, Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DatasetImport(Base):
    __tablename__ = "dataset_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    dataset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    business: Mapped["Business"] = relationship(back_populates="imports")
    rfm_entries: Mapped[list["CustomerRFM"]] = relationship(back_populates="dataset_import")
    segment_entries: Mapped[list["CustomerSegment"]] = relationship(back_populates="dataset_import")
    analytics_runs: Mapped[list["AnalyticsRun"]] = relationship(back_populates="dataset_import")
