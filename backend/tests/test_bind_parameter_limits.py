"""No statement may exceed PostgreSQL's bind parameter ceiling.

PostgreSQL's wire protocol carries the parameter count in a signed 16-bit field, so
65,535 is a hard limit that no server version or setting raises. A query building
its ``IN`` clause from a column of the uploaded file therefore has a row count past
which it stops working — and the failure is not a slow query, it is an immediate
error before any work happens.

Three statements on the RFM path had exactly that shape, all of them one parameter
per customer:

* ``rfm._recover_monetary_from_items`` — ``Order.customer_id.in_(missing)``, where
  ``missing`` is every customer whose orders carry no total. On this project's own
  ``dataset/df_Orders.csv``, which has no amount column at all, that is all 88,907
  of them.
* ``AnalyticsRepository.replace_rfm_snapshot`` — the ``IN`` inside the ``DELETE``
  that clears the previous snapshot, one parameter per customer being written.
* ``AnalyticsRepository.get_import_sourced_rfm`` — a ``NOT IN`` holding every
  customer who has measured orders.

None of this is reachable by a test that asserts on results, because the tests run
on SQLite and SQLite does not have the limit: the interpreter this was written
against accepted a 250,000-parameter ``IN`` without complaint. So the assertion here
is on the *statements themselves*, captured from a real run through
``before_cursor_execute``, which is the same hook the driver uses and sees exactly
what would go on the wire.

Staying under 65,535 with a small fixture proves nothing on its own — the old code
did that too. The assertion that has teeth is
:func:`test_where_clause_width_does_not_grow_with_the_number_of_customers`: the
same job is run over two populations and the widest ``WHERE`` must come out
*identical*. A width that tracks the row count is a limit waiting for a big enough
tenant, and that test sees it at 200 rows instead of at 65,536.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.analytics.engine import run_rfm_job
from app.db.base import Base
from app.db.models.analytics import CustomerRFM
from app.db.models.business import Business
from app.db.models.customer import Customer
from app.db.models.order import Order
from app.repositories import bulk

#: PostgreSQL's ceiling, restated here so the test fails for the documented reason
#: rather than by importing the constant it is meant to be checking.
POSTGRES_MAX_BIND_PARAMS = 65_535

#: Small enough to make chunking observable, larger than 1 so an off-by-one in the
#: chunker shows up as a wrong width rather than as a wrong result.
BATCH_SIZE = 25

#: Comfortably more than one batch, and more than a squeaked-through boundary case.
CUSTOMERS = 300


@contextmanager
def batch_size(size: int):
    """Temporarily narrow the chunk size every query builder reads.

    ``settings`` is a frozen dataclass, so this swaps the object that
    ``app.repositories.bulk`` holds — the module whose ``bind_safe_chunk_size`` is
    the single source of the width — rather than mutating global config or relying
    on an environment variable read at import time.
    """
    original = bulk.settings
    bulk.settings = dataclasses.replace(original, import_batch_size=size)

    try:
        assert bulk.bind_safe_chunk_size() == size, "the patch did not take effect"
        yield
    finally:
        bulk.settings = original


class _Recorder:
    """Every statement issued on a connection, with its parameter count."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, int]] = []

    def attach(self, engine) -> None:
        @event.listens_for(engine, "before_cursor_execute")
        def _record(_conn, _cursor, statement, parameters, _context, executemany):
            if executemany:
                # One INSERT repeated per row: the driver sends each set separately,
                # so the per-statement width is the width of one set.
                width = len(parameters[0]) if parameters else 0
            elif isinstance(parameters, dict):
                width = len(parameters)
            else:
                width = len(parameters or ())

            self.statements.append((statement, width))

    @property
    def widest(self) -> tuple[str, int]:
        if not self.statements:
            return ("", 0)
        return max(self.statements, key=lambda entry: entry[1])

    @property
    def widest_where_clause(self) -> tuple[str, int]:
        """The widest statement whose width is set by a ``WHERE`` clause.

        ``INSERT``s are excluded deliberately. Their per-row width is the number of
        columns in the table — 36 for ``customer_rfm`` — which is fixed by the
        schema and says nothing about how many rows are being processed. The
        statements this module is about are the ``SELECT``s and ``DELETE``s whose
        ``IN`` lists were built from a customer column, so those are what the
        row-count-independence assertions have to look at.
        """
        candidates = [
            entry
            for entry in self.statements
            if not entry[0].lstrip().upper().startswith("INSERT")
        ]

        if not candidates:
            return ("", 0)

        return max(candidates, key=lambda entry: entry[1])

    def over(self, limit: int) -> list[tuple[str, int]]:
        return [entry for entry in self.statements if entry[1] > limit]


def _recorded_run(customers: int, *, with_totals: bool = False) -> tuple[_Recorder, dict]:
    """Run the real RFM job over ``customers`` customers, recording every statement.

    ``with_totals=False`` leaves ``orders.total_amount`` null, which is the
    situation in the project's own orders export and the one that makes
    ``_recover_monetary_from_items`` build its widest ``IN``.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    recorder = _Recorder()
    session = Session(bind=engine, future=True)

    try:
        business = Business(name=f"Bind limits {customers}")
        session.add(business)
        session.flush()
        business_id = str(business.id)

        reference = datetime(2024, 6, 1, tzinfo=timezone.utc)

        for index in range(customers):
            customer = Customer(business_id=business_id, external_customer_id=f"cust-{index}")
            session.add(customer)
            session.flush()
            session.add(
                Order(
                    business_id=business_id,
                    customer_id=customer.id,
                    external_order_id=f"order-{index}",
                    order_date=reference - timedelta(days=index % 90),
                    # ``pending`` is what ``file_parser`` writes when a file has no
                    # status column, which is the case for df_Orders.csv. The column
                    # is NOT NULL, so a faithful fixture has to supply it.
                    status="pending",
                    total_amount=(10 + index) if with_totals else None,
                )
            )

        session.commit()

        # Attached only now, so this setup's own inserts are not counted.
        recorder.attach(engine)

        with batch_size(BATCH_SIZE):
            metadata = run_rfm_job(session, business_id=business_id, reference_date=reference)
            session.commit()

        stored = int(
            session.execute(
                select(func.count())
                .select_from(CustomerRFM)
                .where(CustomerRFM.business_id == business_id)
            ).scalar_one()
        )
        metadata = dict(metadata)
        metadata["_stored_rows"] = stored

        return recorder, metadata
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def recorded_rfm_run():
    return _recorded_run(CUSTOMERS)


def test_no_statement_exceeds_the_postgres_bind_parameter_limit(recorded_rfm_run):
    recorder, _metadata = recorded_rfm_run
    offenders = recorder.over(POSTGRES_MAX_BIND_PARAMS)

    assert recorder.statements, "no statements were captured; the recorder is not attached"
    assert not offenders, (
        f"{len(offenders)} statement(s) would be rejected by PostgreSQL: "
        + "; ".join(f"{width} params in {statement[:90]}" for statement, width in offenders)
    )


def test_no_where_clause_is_as_wide_as_the_customer_population(recorded_rfm_run):
    """No filtering statement may carry one parameter per customer.

    Under the previous code the widest ``WHERE`` was 300-odd parameters for 300
    customers; chunked, it is one batch plus the handful of fixed predicates
    (the tenant id, the empty string in ``coalesce(status, '')``, and the eight
    excluded order statuses).
    """
    recorder, _metadata = recorded_rfm_run
    statement, width = recorder.widest_where_clause

    assert width, "no SELECT or DELETE was captured; the job did not run as expected"
    assert width < CUSTOMERS, (
        f"a statement carried {width} parameters for {CUSTOMERS} customers, so its width "
        f"tracks the row count and will pass {POSTGRES_MAX_BIND_PARAMS} on a large "
        f"tenant: {statement[:200]}"
    )
    # Slack over the batch size covers those fixed predicates; the exact invariant
    # is asserted by the two-population test below, which needs no bound at all.
    assert width <= 2 * BATCH_SIZE, (
        f"widest filtering statement was {width} parameters against a batch size of "
        f"{BATCH_SIZE}, which is more overhead than the fixed predicates explain: "
        f"{statement[:200]}"
    )


def test_where_clause_width_does_not_grow_with_the_number_of_customers():
    """The invariant, stated exactly: width is a function of the batch, not the rows.

    Two populations, one batch size, and the widest ``WHERE`` must be the *same
    number* in both. This is what makes the queries safe at any tenant size, and it
    needs no arbitrary threshold: under the previous code the two runs reported
    roughly 200 and 400 parameters, and chunked they report an identical width.
    """
    small, large = 200, 400

    small_recorder, _ = _recorded_run(small)
    large_recorder, _ = _recorded_run(large)

    _, small_width = small_recorder.widest_where_clause
    large_statement, large_width = large_recorder.widest_where_clause

    assert small_width and large_width, "no filtering statements were captured"
    assert small_width == large_width, (
        f"widest WHERE was {small_width} parameters for {small} customers but "
        f"{large_width} for {large}: the width still scales with the row count, so it "
        f"reaches {POSTGRES_MAX_BIND_PARAMS} on a large enough tenant. Widest at "
        f"{large} rows: {large_statement[:200]}"
    )


def test_the_job_still_produces_the_right_snapshot(recorded_rfm_run):
    """Chunking must not change the answer.

    A chunked query that quietly dropped a chunk would satisfy every width
    assertion above, so the row counts are checked too.
    """
    _recorder, metadata = recorded_rfm_run

    assert metadata["rows_written"] == CUSTOMERS
    assert metadata["customers_with_orders"] == CUSTOMERS
    assert metadata["_stored_rows"] == CUSTOMERS, (
        f"the job reported {metadata['rows_written']} rows written but the table holds "
        f"{metadata['_stored_rows']}"
    )
    # Every order total was null and there are no order items, so nothing can be
    # recovered - and the job must say so rather than inventing a number.
    assert metadata["monetary_recovered_from_order_items"] == 0


def test_replacing_the_snapshot_twice_does_not_duplicate_it():
    """The chunked DELETE must clear the previous snapshot completely.

    ``replace_rfm_snapshot`` was one ``DELETE ... WHERE a OR b`` and is now a
    ``DELETE`` per disjunct plus one per chunk. A skipped chunk would leave the first
    run's rows behind, so the table would grow on every refresh — which the width
    assertions cannot see. Uses real order totals, so the rows being replaced carry
    the monetary values a live snapshot would.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(bind=engine, future=True)

    try:
        business = Business(name="Repeat refresh")
        session.add(business)
        session.flush()
        business_id = str(business.id)

        reference = datetime(2024, 6, 1, tzinfo=timezone.utc)

        for index in range(CUSTOMERS):
            customer = Customer(business_id=business_id, external_customer_id=f"cust-{index}")
            session.add(customer)
            session.flush()
            session.add(
                Order(
                    business_id=business_id,
                    customer_id=customer.id,
                    external_order_id=f"order-{index}",
                    order_date=reference - timedelta(days=index % 90),
                    status="pending",
                    total_amount=10 + index,
                )
            )

        session.commit()

        def stored() -> int:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(CustomerRFM)
                    .where(CustomerRFM.business_id == business_id)
                ).scalar_one()
            )

        with batch_size(BATCH_SIZE):
            run_rfm_job(session, business_id=business_id, reference_date=reference)
            session.commit()
            first = stored()

            run_rfm_job(session, business_id=business_id, reference_date=reference)
            session.commit()
            second = stored()

        assert first == CUSTOMERS, f"first run stored {first} rows for {CUSTOMERS} customers"
        assert second == CUSTOMERS, (
            f"the second run left {second} rows where {CUSTOMERS} were expected, so the "
            "chunked DELETE did not clear the whole previous snapshot"
        )
    finally:
        session.close()
        engine.dispose()
