from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CustomerRFM(Base):
    """Per-customer recency / frequency / monetary values.

    ``dataset_import_id`` is nullable as of Phase 2. RFM is now computed by
    aggregating every order a business owns, not by scanning a single uploaded
    file, so a row is not necessarily attributable to one import. Rows written
    by the older per-import path keep their reference.

    ``recency``/``frequency``/``monetary`` are either measured from real orders
    or passed through from genuine columns in an uploaded file. They are never
    synthesised — a customer with no order history is omitted from this table
    rather than given invented values.
    """

    __tablename__ = "customer_rfm"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    dataset_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dataset_imports.id"), nullable=True, index=True)
    recency: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    monetary: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_order_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 2: quintile scores (1-5) and the resulting RFM label.
    r_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    f_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    m_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rfm_segment: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    #: Where the numbers came from: ``orders`` (aggregated) or ``import`` (real
    #: columns present in the uploaded file). Kept so the UI can be honest
    #: about provenance.
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    analytics_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    business: Mapped["Business"] = relationship(back_populates="rfm_entries")
    customer: Mapped["Customer"] = relationship(back_populates="rfm_entries")
    dataset_import: Mapped["DatasetImport | None"] = relationship(back_populates="rfm_entries")


class CustomerSegment(Base):
    """Cluster assignment for a customer, produced by the segmentation job."""

    __tablename__ = "customer_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    dataset_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dataset_imports.id"), nullable=True, index=True)
    segment: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    cluster: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 2: how the assignment was produced, so results are auditable.
    churn_risk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    analytics_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    business: Mapped["Business"] = relationship(back_populates="segment_entries")
    customer: Mapped["Customer"] = relationship(back_populates="segment_entries")
    dataset_import: Mapped["DatasetImport | None"] = relationship(back_populates="segment_entries")


class AnalyticsRun(Base):
    """Lifecycle record for one asynchronous analytics job.

    Status transitions are ``queued`` -> ``running`` -> ``completed`` | ``failed``.
    The row is created synchronously by the API so the caller immediately gets a
    job ID to poll; the worker owns every transition after that.

    ``dataset_import_id`` is nullable as of Phase 2 because RFM and segmentation
    now run over a business's whole order history rather than a single upload.
    """

    __tablename__ = "analytics_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), ForeignKey("businesses.id"), nullable=False, index=True)
    dataset_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dataset_imports.id"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase 2 job bookkeeping.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Event that carried this job onto the bus — links a job to its message.
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Summary counts and metrics for the run. Never contains credentials.
    result_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    business: Mapped["Business"] = relationship(back_populates="analytics_runs")
    dataset_import: Mapped["DatasetImport | None"] = relationship(back_populates="analytics_runs")
