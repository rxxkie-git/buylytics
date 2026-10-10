from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.events import ProcessedEvent


class EventRepository:
    """Persistence for the event idempotency ledger.

    The uniqueness guarantee is enforced by the primary key on
    ``processed_events.event_id`` rather than by a read-then-write check, so two
    workers racing on the same redelivered message cannot both proceed.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def claim_event(
        self,
        *,
        event_id: str,
        event_type: str,
        business_id: str,
        topic: str,
    ) -> bool:
        """Attempt to claim an event for processing.

        Returns ``True`` when this caller won the claim and should run handlers,
        ``False`` when the event was already handled.

        A row left in ``processing`` (previous worker crashed mid-handler) is
        re-claimable: its attempt counter increments and processing resumes.
        Handlers are required to be idempotent, so replaying a partially applied
        event is safe.
        """
        record = ProcessedEvent(
            event_id=event_id,
            event_type=event_type,
            business_id=business_id,
            topic=topic,
            status="processing",
            attempts=1,
        )

        savepoint = self.session.begin_nested()
        try:
            self.session.add(record)
            savepoint.commit()
            return True
        except IntegrityError:
            savepoint.rollback()

        existing = self.session.get(ProcessedEvent, event_id)

        if existing is None:  # pragma: no cover - deleted between the two calls
            return True

        if existing.status == "completed":
            return False

        existing.status = "processing"
        existing.attempts = (existing.attempts or 0) + 1
        existing.error_message = None
        self.session.flush()
        return True

    def complete_event(
        self,
        *,
        event_id: str,
        status: str,
        error_message: str | None = None,
    ) -> ProcessedEvent | None:
        record = self.session.get(ProcessedEvent, event_id)

        if record is None:
            return None

        record.status = status
        record.processed_at = datetime.now(timezone.utc)
        record.error_message = error_message
        self.session.flush()
        return record

    def get_event(self, event_id: str) -> ProcessedEvent | None:
        return self.session.get(ProcessedEvent, event_id)

    def list_failed(self, *, business_id: str, limit: int = 50) -> list[ProcessedEvent]:
        stmt = (
            select(ProcessedEvent)
            .where(
                ProcessedEvent.business_id == str(business_id),
                ProcessedEvent.status == "failed",
            )
            .order_by(ProcessedEvent.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def purge_older_than(self, *, days: int = 30) -> int:
        """Drop completed ledger rows past the replay window.

        The ledger only needs to cover the broker's retention period; keeping it
        forever would grow without bound.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        stmt = delete(ProcessedEvent).where(
            ProcessedEvent.status == "completed",
            ProcessedEvent.created_at < cutoff,
        )
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
