"""Two workers, one job: only one may ever claim it.

Kafka delivery is at-least-once and a consumer-group rebalance can replay a
message that is still in flight, so "two workers are handed the same job" is an
expected sequence, not a rare interleaving. An analytics job replaces the
tenant's RFM and segment snapshot, so two concurrent runs interleave two sets of
writes over the same rows.

These tests drive the real repository against SQLite through SQLAlchemy, from two
independent sessions, which is the only way to observe the property that matters:
that the *database* picks the winner. A test that called the claim twice on one
session would pass just as happily against the read-then-write implementation
this replaced, because a single session sees its own uncommitted write.

Requires SQLAlchemy; skipped without it, like the other pytest suites here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.models.analytics import AnalyticsRun  # noqa: E402
from app.db.models.business import Business  # noqa: E402
from app.repositories.analytics_repository import AnalyticsRepository  # noqa: E402
from app.services.analytics_job_service import (  # noqa: E402
    PENDING_STATUSES,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
)

TENANT = "biz-claim-1"
OTHER_TENANT = "biz-claim-2"


@pytest.fixture
def sessions():
    """Two sessions over one SQLite database, standing in for two workers.

    ``StaticPool`` with a shared in-memory database is what makes the two
    sessions see the same data; the default pool would give each connection its
    own empty database and every race test would pass vacuously.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    setup = factory()
    setup.add(Business(id=TENANT, name="Claim Tenant"))
    setup.add(Business(id=OTHER_TENANT, name="Other Tenant"))
    setup.commit()
    setup.close()

    worker_a, worker_b = factory(), factory()
    yield worker_a, worker_b
    worker_a.close()
    worker_b.close()
    engine.dispose()


def _queue_job(session, *, job_id: str, business_id: str = TENANT, status: str = STATUS_QUEUED) -> None:
    session.add(
        AnalyticsRun(
            id=job_id,
            business_id=business_id,
            job_type="rfm",
            status=status,
        )
    )
    session.commit()


def _claim(session, job_id: str, *, business_id: str = TENANT) -> bool:
    claimed = AnalyticsRepository(session).claim_run(
        business_id=business_id,
        run_id=job_id,
        claimable_statuses=PENDING_STATUSES,
        running_status=STATUS_RUNNING,
    )
    session.commit()
    return claimed


def test_only_one_of_two_workers_claims_the_same_job(sessions) -> None:
    """The core property: two racers, exactly one winner."""
    worker_a, worker_b = sessions
    _queue_job(worker_a, job_id="job-race")

    first = _claim(worker_a, "job-race")
    second = _claim(worker_b, "job-race")

    assert first is True, "the first worker to claim a queued job must win it"
    assert second is False, (
        "the second worker claimed a job that was already running - "
        "both workers would now execute it and interleave their snapshot writes"
    )

    row = AnalyticsRepository(worker_b).get_run(business_id=TENANT, run_id="job-race")
    assert row.status == STATUS_RUNNING


def test_a_running_job_is_not_claimable(sessions) -> None:
    """``running`` is excluded from the claimable set, not just the terminal ones.

    The implementation this replaced tested ``status in TERMINAL_STATUSES``, and
    ``running`` is not terminal, so a redelivered message re-claimed a job that
    was already executing. That is the exact defect this asserts is gone.
    """
    worker_a, _ = sessions
    _queue_job(worker_a, job_id="job-running", status=STATUS_RUNNING)

    assert _claim(worker_a, "job-running") is False
    assert STATUS_RUNNING not in PENDING_STATUSES


@pytest.mark.parametrize("terminal", [STATUS_COMPLETED, STATUS_FAILED])
def test_a_finished_job_is_not_reclaimed(sessions, terminal: str) -> None:
    worker_a, _ = sessions
    _queue_job(worker_a, job_id=f"job-{terminal}", status=terminal)

    assert _claim(worker_a, f"job-{terminal}") is False

    row = AnalyticsRepository(worker_a).get_run(business_id=TENANT, run_id=f"job-{terminal}")
    assert row.status == terminal, "a finished job's status must survive a redelivery"


def test_the_legacy_pending_status_is_still_claimable(sessions) -> None:
    """Phase 1 rows use ``pending`` where Phase 2 writes ``queued``.

    ``serialise_run`` maps one onto the other for the API, so a claim that
    accepted only ``queued`` would silently strand every job created before the
    rename - visible as a job that reports ``queued`` forever and never runs.
    """
    worker_a, _ = sessions
    assert "pending" in PENDING_STATUSES

    _queue_job(worker_a, job_id="job-legacy", status="pending")
    assert _claim(worker_a, "job-legacy") is True


def test_a_claim_cannot_cross_tenants(sessions) -> None:
    """The tenant is in the WHERE clause, so a guessed job id claims nothing."""
    worker_a, worker_b = sessions
    _queue_job(worker_a, job_id="job-tenant", business_id=TENANT)

    assert _claim(worker_b, "job-tenant", business_id=OTHER_TENANT) is False

    row = AnalyticsRepository(worker_a).get_run(business_id=TENANT, run_id="job-tenant")
    assert row.status == STATUS_QUEUED, "the owning tenant's job must be untouched"


def test_claiming_clears_a_previous_error_message(sessions) -> None:
    """A retry must not report the last attempt's error while it runs."""
    worker_a, _ = sessions
    _queue_job(worker_a, job_id="job-retry")

    run = AnalyticsRepository(worker_a).get_run(business_id=TENANT, run_id="job-retry")
    run.error_message = "previous attempt failed"
    worker_a.commit()

    assert _claim(worker_a, "job-retry") is True

    worker_a.expire_all()
    row = AnalyticsRepository(worker_a).get_run(business_id=TENANT, run_id="job-retry")
    assert row.error_message is None


def test_a_missing_job_claims_nothing(sessions) -> None:
    worker_a, _ = sessions
    assert _claim(worker_a, "job-does-not-exist") is False
