"""Asynchronous analytics job lifecycle.

    POST /api/v1/analytics/jobs      -> row created as `queued`, event published,
                                        202-style response with a job id
    worker consumes analytics.jobs   -> `running` -> `completed` | `failed`
    GET  /api/v1/analytics/jobs/{id} -> current state

The API never runs the analysis on the request thread. It writes the ``queued``
row, commits so the id is immediately pollable, then publishes. Committing before
publishing matters: if the publish fails the job is visibly stuck in ``queued``
rather than silently lost, and a client polling the id gets an honest answer.

The worker opens its **own** session. A request-scoped session would already be
closed by the time an inline task ran, and reusing one across threads is not
safe.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.analytics import job_types
from app.cache import invalidate_prefixes, keys as cache_keys
from app.config import settings
from app.core.concurrency import run_blocking
from app.core.exceptions import AppError, ConfigurationError, NotFoundError, ValidationError
from app.core.logging import get_logger, logging_context
from app.db.models.analytics import AnalyticsRun
from app.events import EventType, make_event, topics
from app.repositories.analytics_repository import AnalyticsRepository

logger = get_logger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: ``pending`` is the Phase 1 spelling of ``queued``. Accepted on read so rows
#: written before Phase 2 still display correctly.
PENDING_STATUSES = (STATUS_QUEUED, "pending")

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)


def serialise_run(run: AnalyticsRun) -> dict[str, Any]:
    """Job record shaped for the API. Contains no credentials."""
    status = STATUS_QUEUED if run.status == "pending" else run.status

    return {
        "job_id": run.id,
        "job_type": run.job_type,
        "status": status,
        "import_id": run.dataset_import_id,
        "requested_by": run.requested_by,
        "created_at": (run.created_at or run.started_at).isoformat()
        if (run.created_at or run.started_at)
        else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "result": run.result_metadata,
    }


def create_job(
    session: Session,
    *,
    business_id: str,
    job_type: str,
    requested_by: str | None = None,
    dataset_import_id: str | None = None,
) -> AnalyticsRun:
    """Insert a ``queued`` job row. Does not commit."""
    if not job_types.is_supported(job_type):
        raise ValidationError(
            f"Unknown analytics job type '{job_type}'.",
            details={"supported": list(job_types.SUPPORTED_JOB_TYPES)},
        )

    return AnalyticsRepository(session).create_analytics_run(
        business_id=business_id,
        dataset_import_id=dataset_import_id,
        job_type=job_type,
        status=STATUS_QUEUED,
        requested_by=requested_by,
    )


async def _publish_request(
    *,
    business_id: str,
    run_id: str,
    job_type: str,
    dataset_import_id: str | None,
    reference_date: datetime | None,
) -> Any:
    """Publish the ``analytics.job.requested`` event for a queued run."""
    from app.events.producer import publish_event

    event = make_event(
        event_type=EventType.ANALYTICS_JOB_REQUESTED,
        business_id=business_id,
        payload={
            "run_id": run_id,
            "job_type": job_type,
            "dataset_import_id": dataset_import_id,
            "reference_date": reference_date.isoformat() if reference_date else None,
        },
    )

    result = await publish_event(topics.ANALYTICS_JOBS, event)

    if not result.delivered:
        logger.warning(
            "Analytics job queued but not dispatched; it will stay queued until a "
            "worker picks it up or an event transport is enabled",
            extra={"job_id": run_id, "transport": result.transport},
        )

    await invalidate_prefixes([cache_keys.namespace_prefix(business_id, cache_keys.JOBS_NS)])

    return result


def _undispatched_note() -> str:
    return (
        "No event transport is active, so this job has not been dispatched. "
        "Start the worker (KAFKA_ENABLED=true) or set EVENTS_INLINE_FALLBACK=true."
    )


async def submit_job(
    session: Session,
    *,
    business_id: str,
    job_type: str,
    requested_by: str | None = None,
    dataset_import_id: str | None = None,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Queue a job on the caller's session and publish it to the bus.

    ``business_id`` must come from the authenticated business context. Nothing in
    this function reads a tenant id out of a request body.

    The synchronous database work is offloaded to a worker thread. This function
    has to be ``async`` because publishing is awaited, but ``create_job``,
    ``commit`` and ``session.get`` are blocking SQLAlchemy calls — three network
    round-trips to Supabase that, left here, would run on the event loop and stall
    every other request in the process for their duration. The session is only
    ever touched by one thread at a time because these awaits are sequential.
    """
    def _create() -> str:
        run = create_job(
            session,
            business_id=business_id,
            job_type=job_type,
            requested_by=requested_by,
            dataset_import_id=dataset_import_id,
        )
        run_id = run.id

        # Commit before publishing so a consumer — possibly running before this
        # function returns, with the inline transport — can see the row.
        session.commit()
        return run_id

    run_id = await run_blocking(
        _create,
        operation="analytics_job.create",
        timeout_seconds=float(settings.db_statement_timeout_seconds) + 5.0,
    )

    result = await _publish_request(
        business_id=business_id,
        run_id=run_id,
        job_type=job_type,
        dataset_import_id=dataset_import_id,
        reference_date=reference_date,
    )

    def _load() -> dict[str, Any]:
        run = session.get(AnalyticsRun, run_id)

        if run is None:  # pragma: no cover - the row was just committed
            raise NotFoundError(f"Analytics job {run_id} disappeared after creation")

        return serialise_run(run)

    payload = await run_blocking(
        _load,
        operation="analytics_job.reload",
        timeout_seconds=float(settings.db_statement_timeout_seconds) + 5.0,
    )
    payload["transport"] = result.transport
    payload["dispatched"] = result.delivered

    if not result.delivered:
        payload["note"] = _undispatched_note()

    return payload


def _create_job_committed(
    *,
    business_id: str,
    job_type: str,
    requested_by: str | None,
    dataset_import_id: str | None,
) -> dict[str, Any]:
    """Create and commit a queued job on a private session. Blocking."""
    session = _open_session()

    try:
        run = create_job(
            session,
            business_id=business_id,
            job_type=job_type,
            requested_by=requested_by,
            dataset_import_id=dataset_import_id,
        )
        session.commit()
        return serialise_run(run)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def queue_job(
    *,
    business_id: str,
    job_type: str,
    requested_by: str | None = None,
    dataset_import_id: str | None = None,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Queue a job from a context that has no session — an event handler, say.

    The insert runs on a worker thread. Under the inline transport a handler
    executes on the API event loop, and synchronous SQLAlchemy there would block
    every other request.
    """
    payload = await asyncio.to_thread(
        _create_job_committed,
        business_id=business_id,
        job_type=job_type,
        requested_by=requested_by,
        dataset_import_id=dataset_import_id,
    )

    result = await _publish_request(
        business_id=business_id,
        run_id=payload["job_id"],
        job_type=job_type,
        dataset_import_id=dataset_import_id,
        reference_date=reference_date,
    )

    payload["transport"] = result.transport
    payload["dispatched"] = result.delivered

    if not result.delivered:
        payload["note"] = _undispatched_note()

    return payload


def get_job(session: Session, *, business_id: str, job_id: str) -> dict[str, Any]:
    """Fetch one job, scoped to the tenant.

    A job id belonging to another business is reported as not found — the same
    response an unknown id gets, so ids cannot be probed for existence.
    """
    run = AnalyticsRepository(session).get_run(business_id=business_id, run_id=job_id)

    if run is None:
        raise NotFoundError("Analytics job not found")

    return serialise_run(run)


def list_jobs(
    session: Session,
    *,
    business_id: str,
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    runs = AnalyticsRepository(session).list_runs(
        business_id=business_id, job_type=job_type, status=status, limit=limit
    )
    return [serialise_run(run) for run in runs]


def cancel_job(session: Session, *, business_id: str, job_id: str) -> dict[str, Any]:
    """Cancel a queued or running job for a business."""
    repository = AnalyticsRepository(session)
    cancelled = repository.cancel_run(business_id=business_id, run_id=job_id)
    session.commit()

    run = repository.get_run(business_id=business_id, run_id=job_id)
    if run is None:
        raise NotFoundError("Analytics job not found")

    return serialise_run(run)


def reap_stale_jobs(session: Session, *, business_id: str | None = None, max_age_seconds: int = 600) -> int:
    """Reap stuck jobs for a business or globally. Commits changes."""
    reaped = AnalyticsRepository(session).reap_stale_runs(business_id=business_id, max_age_seconds=max_age_seconds)
    if reaped > 0:
        session.commit()
    return reaped



# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------


def _open_session() -> Session:
    from app.db.session import SessionLocal

    if SessionLocal is None:
        raise ConfigurationError(
            "DATABASE_URL is not configured, so analytics jobs cannot run.",
            code="database_not_configured",
        )

    return SessionLocal()


def _claim_run(session: Session, *, business_id: str, run_id: str) -> AnalyticsRun | None:
    """Move a job to ``running``, or return ``None`` if it should not run.

    The transition is a conditional ``UPDATE`` in the database (see
    :meth:`AnalyticsRepository.claim_run`), not a read-then-write here, so exactly
    one of two workers racing on the same redelivered message wins. The
    ``processed_events`` ledger also guards this at the transport level, but it
    deliberately re-claims a row still marked ``processing``, so it is not
    sufficient on its own.

    Losing the race and finding a finished job are the same outcome — do nothing —
    but they are logged differently, because one is routine and the other means
    two workers were handed the same message.
    """
    repository = AnalyticsRepository(session)

    claimed = repository.claim_run(
        business_id=business_id,
        run_id=run_id,
        claimable_statuses=PENDING_STATUSES,
        running_status=STATUS_RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    session.commit()

    if not claimed:
        # The UPDATE matched nothing. Re-read only to say why, and treat a missing
        # row and an unclaimable one as the same refusal.
        run = repository.get_run(business_id=business_id, run_id=run_id)

        if run is None:
            logger.warning("Analytics job not found for this business", extra={"job_id": run_id})
        else:
            logger.info(
                "Analytics job was not claimable; skipping",
                extra={"job_id": run_id, "status": run.status},
            )

        return None

    return repository.get_run(business_id=business_id, run_id=run_id)


def execute_job(
    *,
    business_id: str,
    run_id: str,
    job_type: str,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Run a queued job to completion. Synchronous; owns its session.

    Never raises for a data problem: an expected failure (no orders, too few
    customers, unknown job type) is recorded on the row as ``failed`` with the
    reason, because the caller is an event handler whose only alternative is to
    dead-letter the message. Unexpected exceptions are also recorded, then
    re-raised so the handler can dead-letter them for investigation.
    """
    session = _open_session()

    try:
        run = _claim_run(session, business_id=business_id, run_id=run_id)

        if run is None:
            return {"job_id": run_id, "status": "skipped"}

        effective_job_type = run.job_type or job_type

        try:
            # Imported here rather than at module scope: the engine pulls in
            # pandas and the ML stack, which the API process does not need until
            # a job actually runs.
            from app.analytics import engine

            metadata = engine.run_job(
                effective_job_type,
                session,
                business_id=business_id,
                run_id=run_id,
                reference_date=reference_date,
            )
        except AppError as exc:
            session.rollback()
            _finish_run(
                session,
                run_id=run_id,
                status=STATUS_FAILED,
                error_message=f"{exc.code}: {exc.message}",
                metadata={"error_code": exc.code, "details": exc.details},
            )
            logger.warning(
                "Analytics job failed",
                extra={"job_id": run_id, "job_type": effective_job_type, "error_code": exc.code},
            )
            return {"job_id": run_id, "status": STATUS_FAILED, "error": exc.message, "code": exc.code}
        except Exception as exc:
            session.rollback()
            _finish_run(
                session,
                run_id=run_id,
                status=STATUS_FAILED,
                error_message=f"unexpected_error: {type(exc).__name__}",
                metadata={"error_code": "unexpected_error"},
            )
            logger.exception(
                "Analytics job crashed",
                extra={"job_id": run_id, "job_type": effective_job_type},
            )
            raise

        _finish_run(
            session,
            run_id=run_id,
            status=STATUS_COMPLETED,
            error_message=None,
            metadata=metadata,
        )

        logger.info(
            "Analytics job completed",
            extra={"job_id": run_id, "job_type": effective_job_type},
        )

        return {"job_id": run_id, "status": STATUS_COMPLETED, "result": metadata}
    finally:
        session.close()


def _finish_run(
    session: Session,
    *,
    run_id: str,
    status: str,
    error_message: str | None,
    metadata: dict[str, Any] | None,
) -> None:
    """Write the terminal state. A bookkeeping failure must not mask the result."""
    try:
        AnalyticsRepository(session).update_analytics_run(
            run_id,
            status=status,
            completed_at=datetime.now(timezone.utc),
            error_message=error_message,
            result_metadata=metadata,
        )
        session.commit()
    except Exception as exc:  # pragma: no cover - defensive
        session.rollback()
        logger.error(
            "Could not record analytics job outcome",
            extra={"job_id": run_id, "intended_status": status, "reason": str(exc)},
        )


async def run_job_from_event(
    *,
    business_id: str,
    run_id: str,
    job_type: str,
    reference_date: datetime | None = None,
) -> dict[str, Any]:
    """Execute a job off the event bus, then invalidate the tenant's caches.

    The blocking work is pushed to a worker thread so an inline-transport run
    cannot stall the API event loop.
    """
    with logging_context(business_id=business_id, job_id=run_id):
        outcome = await asyncio.to_thread(
            execute_job,
            business_id=business_id,
            run_id=run_id,
            job_type=job_type,
            reference_date=reference_date,
        )

        prefixes = list(cache_keys.analytics_write_prefixes(business_id))
        prefixes.append(cache_keys.namespace_prefix(business_id, cache_keys.JOBS_NS))
        await invalidate_prefixes(prefixes)

        await _announce_outcome(business_id=business_id, run_id=run_id, job_type=job_type, outcome=outcome)

        return outcome


async def _announce_outcome(
    *,
    business_id: str,
    run_id: str,
    job_type: str,
    outcome: dict[str, Any],
) -> None:
    """Publish a completion or failure event.

    Downstream consumers (notifications, a future webhook fan-out) subscribe to
    these instead of polling the jobs table.
    """
    status = outcome.get("status")

    if status not in TERMINAL_STATUSES:
        return

    event_type = (
        EventType.ANALYTICS_JOB_COMPLETED
        if status == STATUS_COMPLETED
        else EventType.ANALYTICS_JOB_FAILED
    )

    payload: dict[str, Any] = {"run_id": run_id, "job_type": job_type, "status": status}

    if status == STATUS_FAILED:
        payload["error_code"] = outcome.get("code", "unknown")

    from app.events.producer import publish_event

    await publish_event(
        topics.ANALYTICS_JOBS,
        make_event(event_type=event_type, business_id=business_id, payload=payload, source="worker"),
    )
