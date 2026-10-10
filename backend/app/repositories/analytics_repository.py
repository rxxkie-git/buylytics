from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.orm import Session

from app.analytics.scoring import SOURCE_IMPORT, SOURCE_ORDERS
from app.db.models.analytics import AnalyticsRun, CustomerRFM, CustomerSegment
from app.repositories.bulk import bind_safe_chunk_size, chunked


class AnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_rfm(
        self,
        *,
        business_id: str | UUID,
        customer_id: str | UUID,
        dataset_import_id: str | UUID | None = None,
        **fields: object,
    ) -> CustomerRFM:
        row = CustomerRFM(
            business_id=str(business_id),
            customer_id=str(customer_id),
            dataset_import_id=str(dataset_import_id) if dataset_import_id is not None else None,
            **fields,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_segments(
        self,
        *,
        business_id: str | UUID,
        customer_id: str | UUID,
        dataset_import_id: str | UUID | None = None,
        **fields: object,
    ) -> CustomerSegment:
        row = CustomerSegment(
            business_id=str(business_id),
            customer_id=str(customer_id),
            dataset_import_id=str(dataset_import_id) if dataset_import_id is not None else None,
            **fields,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def create_analytics_run(
        self,
        *,
        business_id: str | UUID,
        dataset_import_id: str | UUID | None = None,
        job_type: str,
        status: str = "queued",
        requested_by: str | None = None,
        event_id: str | None = None,
    ) -> AnalyticsRun:
        run = AnalyticsRun(
            business_id=str(business_id),
            dataset_import_id=str(dataset_import_id) if dataset_import_id is not None else None,
            job_type=job_type,
            status=status,
            started_at=datetime.now(timezone.utc),
            requested_by=requested_by,
            event_id=event_id,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def update_analytics_run(self, run_id: str | UUID, **fields: object) -> AnalyticsRun | None:
        run = self.session.get(AnalyticsRun, str(run_id))
        if run is None:
            return None
        for key, value in fields.items():
            setattr(run, key, value)
        self.session.flush()
        return run

    def get_rfm(self, *, business_id: str | UUID, customer_id: str | UUID | None = None) -> list[CustomerRFM]:
        stmt = select(CustomerRFM).where(CustomerRFM.business_id == str(business_id))
        if customer_id is not None:
            stmt = stmt.where(CustomerRFM.customer_id == str(customer_id))
        return list(self.session.execute(stmt).scalars().all())

    def get_segments(self, *, business_id: str | UUID, customer_id: str | UUID | None = None) -> list[CustomerSegment]:
        stmt = select(CustomerSegment).where(CustomerSegment.business_id == str(business_id))
        if customer_id is not None:
            stmt = stmt.where(CustomerSegment.customer_id == str(customer_id))
        return list(self.session.execute(stmt).scalars().all())

    # ------------------------------------------------------------------
    # Phase 2: tenant-scoped job reads and snapshot replacement
    # ------------------------------------------------------------------

    def get_run(self, *, business_id: str | UUID, run_id: str | UUID) -> AnalyticsRun | None:
        """Fetch one run, scoped to the tenant.

        The business filter is part of the query rather than checked afterwards,
        so a guessed job ID from another tenant returns nothing.
        """
        stmt = select(AnalyticsRun).where(
            AnalyticsRun.id == str(run_id),
            AnalyticsRun.business_id == str(business_id),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def claim_run(
        self,
        *,
        business_id: str | UUID,
        run_id: str | UUID,
        claimable_statuses: tuple[str, ...],
        running_status: str,
        started_at: datetime | None = None,
    ) -> bool:
        """Take ownership of a job atomically. ``True`` if this caller won it.

        A single ``UPDATE ... WHERE status IN (claimable)``, so the database
        decides the winner. The previous implementation read the row, checked the
        status in Python, then assigned and committed; between the read and the
        write a second worker could read the same ``queued`` row and pass the same
        check, and both would run the job. Kafka delivery is at-least-once and a
        consumer-group rebalance can replay an in-flight message, so that is an
        expected sequence rather than a rare interleaving.

        Duplicated execution is not harmless here: a job replaces the tenant's RFM
        and segment snapshot, so two concurrent runs interleave two sets of writes
        over the same rows.

        The claim deliberately does *not* accept a row already in ``running``. That
        makes a crashed worker's job stay unclaimable rather than become a job two
        workers may run, which is the safer of the two failures; recovering it needs
        a lease with an expiry, which is recorded as a known limitation.
        """
        stmt = (
            update(AnalyticsRun)
            .where(
                AnalyticsRun.id == str(run_id),
                AnalyticsRun.business_id == str(business_id),
                AnalyticsRun.status.in_(list(claimable_statuses)),
            )
            .values(
                status=running_status,
                started_at=started_at or datetime.now(timezone.utc),
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )

        return self.session.execute(stmt).rowcount == 1

    def reap_stale_runs(
        self,
        *,
        business_id: str | UUID | None = None,
        max_age_seconds: int = 600,
    ) -> int:
        """Reap runs stuck in 'running' (or undispatched 'queued' older than 24h).

        Transitions them to 'failed' with a clear timeout error message.
        """
        now = datetime.now(timezone.utc)
        running_cutoff = now - timedelta(seconds=max(30, max_age_seconds))

        stmt_running = (
            update(AnalyticsRun)
            .where(
                AnalyticsRun.status == "running",
                AnalyticsRun.started_at < running_cutoff,
            )
            .values(
                status="failed",
                completed_at=now,
                error_message="timeout: job execution exceeded maximum lease time",
            )
            .execution_options(synchronize_session=False)
        )
        if business_id is not None:
            stmt_running = stmt_running.where(AnalyticsRun.business_id == str(business_id))

        reaped = int(self.session.execute(stmt_running).rowcount or 0)

        queued_cutoff = now - timedelta(hours=24)
        stmt_queued = (
            update(AnalyticsRun)
            .where(
                AnalyticsRun.status.in_(["queued", "pending"]),
                AnalyticsRun.started_at < queued_cutoff,
            )
            .values(
                status="failed",
                completed_at=now,
                error_message="timeout: job queued for over 24h without being picked up",
            )
            .execution_options(synchronize_session=False)
        )
        if business_id is not None:
            stmt_queued = stmt_queued.where(AnalyticsRun.business_id == str(business_id))

        reaped += int(self.session.execute(stmt_queued).rowcount or 0)
        self.session.flush()
        return reaped

    def cancel_run(
        self,
        *,
        business_id: str | UUID,
        run_id: str | UUID,
    ) -> bool:
        """Cancel an active or queued job for a tenant."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(AnalyticsRun)
            .where(
                AnalyticsRun.id == str(run_id),
                AnalyticsRun.business_id == str(business_id),
                AnalyticsRun.status.in_(["queued", "pending", "running"]),
            )
            .values(
                status="failed",
                completed_at=now,
                error_message="cancelled by user",
            )
            .execution_options(synchronize_session=False)
        )
        updated = self.session.execute(stmt).rowcount == 1
        self.session.flush()
        return updated

    def list_runs(
        self,
        *,
        business_id: str | UUID,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        auto_reap: bool = True,
    ) -> list[AnalyticsRun]:
        if auto_reap:
            self.reap_stale_runs(business_id=business_id)

        stmt = select(AnalyticsRun).where(AnalyticsRun.business_id == str(business_id))

        if job_type:
            stmt = stmt.where(AnalyticsRun.job_type == job_type)

        if status:
            stmt = stmt.where(AnalyticsRun.status == status)

        stmt = stmt.order_by(AnalyticsRun.started_at.desc()).limit(max(1, min(limit, 200)))
        return list(self.session.execute(stmt).scalars().all())

    def latest_completed_run(self, *, business_id: str | UUID, job_type: str) -> AnalyticsRun | None:
        stmt = (
            select(AnalyticsRun)
            .where(
                AnalyticsRun.business_id == str(business_id),
                AnalyticsRun.job_type == job_type,
                AnalyticsRun.status == "completed",
            )
            .order_by(AnalyticsRun.completed_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def replace_rfm_snapshot(
        self,
        *,
        business_id: str | UUID,
        rows: list[dict[str, object]],
        analytics_run_id: str | None = None,
    ) -> int:
        """Replace the orders-derived RFM snapshot for one business.

        RFM is current-state, not a time series, so a run replaces the previous
        snapshot instead of appending to it.

        Rows sourced from an upload (``source='import'``) are preserved unless
        the same customer now has real orders — measured transactions take
        precedence over a spreadsheet column.
        """
        business = str(business_id)
        customer_ids = [str(row["customer_id"]) for row in rows if row.get("customer_id")]

        # Two statements rather than one ``OR``, because the second disjunct is one
        # bind parameter per customer in the snapshot — 88,907 on this project's own
        # df_Orders.csv, past PostgreSQL's 65,535 limit for a single statement.
        #
        # Splitting a DELETE across its disjuncts is safe in a way that splitting a
        # SELECT's ``OR`` would not be: deletion is set removal, so removing A then
        # removing B leaves exactly the same table as removing ``A OR B`` at once.
        # Both run in the caller's transaction, so a failure between them rolls back.
        self.session.execute(
            delete(CustomerRFM).where(
                CustomerRFM.business_id == business,
                or_(CustomerRFM.source != SOURCE_IMPORT, CustomerRFM.source.is_(None)),
            )
        )

        for chunk in chunked(customer_ids, bind_safe_chunk_size()):
            self.session.execute(
                delete(CustomerRFM).where(
                    CustomerRFM.business_id == business,
                    CustomerRFM.customer_id.in_(list(chunk)),
                )
            )

        calculated_at = datetime.now(timezone.utc)
        insert_rows: list[dict[str, Any]] = []

        for row in rows:
            payload = dict(row)
            payload.pop("business_id", None)
            customer_id = str(payload.pop("customer_id"))
            insert_rows.append(
                {
                    "id": str(uuid4()),
                    "business_id": business,
                    "customer_id": customer_id,
                    "analytics_run_id": analytics_run_id,
                    "calculated_at": calculated_at,
                    **payload,
                }
            )

        batch_size = max(100, min(bind_safe_chunk_size(), 1000))
        for chunk in chunked(insert_rows, batch_size):
            self.session.execute(insert(CustomerRFM).values(list(chunk)))

        self.session.flush()
        return len(rows)

    def replace_segment_snapshot(
        self,
        *,
        business_id: str | UUID,
        rows: list[dict[str, object]],
        analytics_run_id: str | None = None,
    ) -> int:
        """Replace the segmentation snapshot for one business."""
        business = str(business_id)

        self.session.execute(delete(CustomerSegment).where(CustomerSegment.business_id == business))

        calculated_at = datetime.now(timezone.utc)
        insert_rows: list[dict[str, Any]] = []

        for row in rows:
            payload = dict(row)
            payload.pop("business_id", None)
            customer_id = str(payload.pop("customer_id"))
            insert_rows.append(
                {
                    "id": str(uuid4()),
                    "business_id": business,
                    "customer_id": customer_id,
                    "analytics_run_id": analytics_run_id,
                    "calculated_at": calculated_at,
                    **payload,
                }
            )

        batch_size = max(100, min(bind_safe_chunk_size(), 1000))
        for chunk in chunked(insert_rows, batch_size):
            self.session.execute(insert(CustomerSegment).values(list(chunk)))

        self.session.flush()
        return len(rows)

    # ------------------------------------------------------------------
    # Phase 2: RFM passed through from an upload
    # ------------------------------------------------------------------

    def get_import_sourced_rfm(
        self,
        *,
        business_id: str | UUID,
        exclude_customer_ids: set[str] | None = None,
    ) -> list[CustomerRFM]:
        """Rows whose values came from real columns in an uploaded file.

        ``exclude_customer_ids`` drops customers who now have measured orders, so
        a caller can build "orders where we have them, upload where we do not"
        without the two sources ever double-counting a customer.

        The exclusion is applied in Python, not as a SQL ``NOT IN``. The set is one
        id per customer with orders — 88,907 on this project's own df_Orders.csv,
        past PostgreSQL's 65,535 bind parameters — and unlike an ``IN``, a ``NOT IN``
        cannot be chunked: ``x NOT IN (A ∪ B)`` is not the union of ``x NOT IN A``
        and ``x NOT IN B``, so per-chunk statements would each return rows the other
        chunk excludes. Filtering the fetched rows is the version that stays correct.
        The transfer is bounded by the tenant's import-sourced rows, which the caller
        materialises into a list anyway.
        """
        stmt = select(CustomerRFM).where(
            CustomerRFM.business_id == str(business_id),
            CustomerRFM.source == SOURCE_IMPORT,
        )

        rows = self.session.execute(stmt).scalars().all()

        if not exclude_customer_ids:
            return list(rows)

        excluded = {str(value) for value in exclude_customer_ids}

        return [row for row in rows if str(row.customer_id) not in excluded]

    def upsert_import_rfm(
        self,
        *,
        business_id: str | UUID,
        rows: list[dict[str, object]],
    ) -> int:
        """Store RFM values read straight from an upload.

        Only ``source='import'`` rows for the same customers are removed first, so
        re-uploading a file refreshes its own numbers and leaves any
        orders-derived row for a different customer untouched.

        Scores are deliberately **not** assigned here. A quintile is only
        meaningful relative to a population, and the population is not known until
        the RFM job scores measured and passed-through customers together. Writing
        a score now would produce two incomparable scales in one table.
        """
        business = str(business_id)
        customer_ids = {str(row["customer_id"]) for row in rows if row.get("customer_id")}

        if not customer_ids:
            return 0

        # Chunked for the same reason every statement in ``repositories/bulk.py``
        # is: PostgreSQL accepts at most 65,535 bind parameters per statement, and
        # this ``IN`` is sized by the file. A 50,000-customer upload carrying real
        # RFM columns would send 50,000 parameters in one DELETE — under the limit
        # by luck, not by design, and over it for a larger file. The failure mode is
        # a driver error partway through an import that has already written rows.
        for chunk in chunked(sorted(customer_ids), bind_safe_chunk_size()):
            self.session.execute(
                delete(CustomerRFM).where(
                    CustomerRFM.business_id == business,
                    CustomerRFM.source == SOURCE_IMPORT,
                    CustomerRFM.customer_id.in_(list(chunk)),
                )
            )

        calculated_at = datetime.now(timezone.utc)
        insert_rows: list[dict[str, Any]] = []

        for row in rows:
            payload = dict(row)
            payload.pop("business_id", None)
            customer_id = payload.pop("customer_id", None)

            if not customer_id:
                continue

            # Scoring belongs to the RFM job; never persist a score from here.
            for score_field in ("r_score", "f_score", "m_score", "rfm_segment"):
                payload.pop(score_field, None)

            payload["source"] = SOURCE_IMPORT

            insert_rows.append(
                {
                    "id": str(uuid4()),
                    "business_id": business,
                    "customer_id": str(customer_id),
                    "calculated_at": calculated_at,
                    **payload,
                }
            )

        batch_size = max(100, min(bind_safe_chunk_size(), 1000))
        for chunk in chunked(insert_rows, batch_size):
            self.session.execute(insert(CustomerRFM).values(list(chunk)))

        self.session.flush()
        return len(insert_rows)

    def count_rfm_by_source(self, *, business_id: str | UUID) -> dict[str, int]:
        """How many stored RFM rows came from orders versus an upload."""
        stmt = (
            select(CustomerRFM.source, func.count())
            .where(CustomerRFM.business_id == str(business_id))
            .group_by(CustomerRFM.source)
        )
        return {(source or SOURCE_ORDERS): int(count) for source, count in self.session.execute(stmt)}
