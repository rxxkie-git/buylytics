from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.events import WebhookEvent


class WebhookRepository:
    """Persistence for inbound webhook deliveries.

    Replay protection is enforced by the database, not by application logic. The
    unique constraint over ``(business_id, source, external_event_id)`` means two
    concurrent deliveries of the same event cannot both be accepted, however the
    requests interleave — a read-then-write check would let both through.

    Nothing in this class stores a signature, an authorization header or any
    other credential. Only the boolean outcome of verification is persisted.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def claim_delivery(
        self,
        *,
        business_id: str | UUID,
        source: str,
        external_event_id: str,
        event_type: str,
        signature_verified: bool,
        payload: dict | None = None,
    ) -> tuple[WebhookEvent, bool]:
        """Record a delivery.

        Returns ``(row, accepted)``. ``accepted`` is ``False`` when this is a
        replay of a delivery already on file, in which case the existing row is
        returned and the caller must not publish anything.
        """
        record = WebhookEvent(
            business_id=str(business_id),
            source=source,
            event_type=event_type,
            external_event_id=str(external_event_id),
            signature_verified=signature_verified,
            status="received",
            payload=payload,
        )

        # A nested transaction so that losing the uniqueness race rolls back only
        # this insert, leaving the caller's transaction usable.
        savepoint = self.session.begin_nested()
        try:
            self.session.add(record)
            savepoint.commit()
            return record, True
        except IntegrityError:
            savepoint.rollback()

        existing = self.get_by_external_id(
            business_id=business_id, source=source, external_event_id=external_event_id
        )

        if existing is None:  # pragma: no cover - row removed between the two calls
            return record, True

        return existing, False

    def get_by_external_id(
        self,
        *,
        business_id: str | UUID,
        source: str,
        external_event_id: str,
    ) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(
            WebhookEvent.business_id == str(business_id),
            WebhookEvent.source == source,
            WebhookEvent.external_event_id == str(external_event_id),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get(self, *, business_id: str | UUID, webhook_event_id: str) -> WebhookEvent | None:
        """Fetch one delivery, scoped to the tenant."""
        stmt = select(WebhookEvent).where(
            WebhookEvent.id == str(webhook_event_id),
            WebhookEvent.business_id == str(business_id),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def mark_status(
        self,
        *,
        webhook_event_id: str,
        status: str,
        error_message: str | None = None,
    ) -> WebhookEvent | None:
        record = self.session.get(WebhookEvent, str(webhook_event_id))

        if record is None:
            return None

        record.status = status
        record.processed_at = datetime.now(timezone.utc)
        record.error_message = error_message
        self.session.flush()
        return record

    def list_recent(
        self,
        *,
        business_id: str | UUID,
        source: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WebhookEvent]:
        stmt = select(WebhookEvent).where(WebhookEvent.business_id == str(business_id))

        if source:
            stmt = stmt.where(WebhookEvent.source == source)

        if status:
            stmt = stmt.where(WebhookEvent.status == status)

        stmt = stmt.order_by(WebhookEvent.received_at.desc()).limit(max(1, min(limit, 200)))
        return list(self.session.execute(stmt).scalars().all())

    def counts_by_status(self, *, business_id: str | UUID) -> dict[str, int]:
        from sqlalchemy import func

        stmt = (
            select(WebhookEvent.status, func.count(WebhookEvent.id))
            .where(WebhookEvent.business_id == str(business_id))
            .group_by(WebhookEvent.status)
        )
        return {str(status): int(count) for status, count in self.session.execute(stmt)}

    def purge_older_than(self, *, business_id: str | UUID | None = None, days: int = 90) -> int:
        """Drop old delivery records.

        The audit trail only needs to outlive the replay window and any dispute
        about whether a delivery arrived. Keeping raw payloads forever is a
        liability, not an asset — they contain customer data.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        stmt = delete(WebhookEvent).where(WebhookEvent.received_at < cutoff)

        if business_id is not None:
            stmt = stmt.where(WebhookEvent.business_id == str(business_id))

        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
