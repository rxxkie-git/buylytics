from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessedEvent(Base):
    """Idempotency ledger for consumed events.

    Kafka delivers at least once, so the same event can arrive twice — after a
    rebalance, a redeploy, or an uncommitted offset. The consumer claims each
    ``event_id`` here before running handlers; a duplicate delivery loses the
    insert race and is skipped.

    ``event_id`` is the primary key on purpose: the uniqueness guarantee lives
    in PostgreSQL, not in application logic, so two worker processes competing
    for the same message cannot both win.

    Deliberately not foreign-keyed to ``businesses``. This is operational
    bookkeeping, and referential integrity failures here must never be able to
    stall message processing.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    business_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class WebhookEvent(Base):
    """Audit record for an inbound webhook delivery.

    One row per accepted delivery. Retries from the sender carry the same
    ``external_event_id``, and the unique constraint over
    ``(business_id, source, external_event_id)`` turns a replay into a
    recognised duplicate instead of double-processing.

    ``payload`` holds the verified request body — tenant-owned business data,
    stored in that tenant's rows. Signature and authorization headers are
    **never** persisted here; only the boolean outcome of verification is.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "source",
            "external_event_id",
            name="uq_webhook_business_source_event",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
