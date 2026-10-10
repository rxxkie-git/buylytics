"""RFM passthrough and union-scoring checks.

Runnable here because the functions under test are pure Python and pandas is
installed. SQLAlchemy is not, so the two places where the merge rule is expressed
in SQL are verified by source inspection instead, and that is called out as such.

What this proves:

1.  Alias headers from real uploads ("Total Spend", "total-spend", "TOTALSPEND")
    resolve to the right dimension.
2.  Blank cells become NaN in pandas, and NaN is treated as absent rather than as
    zero — the difference between "unknown" and "never bought anything".
3.  Partial RFM is refused outright. Two of three columns produces no row.
4.  Orders and passthrough customers are scored as one population, so a "5" means
    the same thing on every row of the table.
5.  A customer with both orders and uploaded columns is counted once, from orders.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Resolved from this file's location so the checks run from any checkout, on any
# machine. An earlier revision hardcoded an absolute sandbox path, which made the
# whole file silently unrunnable anywhere else.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd  # noqa: E402

from app.analytics.scoring import (  # noqa: E402
    SOURCE_IMPORT,
    SOURCE_ORDERS,
    RfmRow,
    has_real_rfm_columns,
    rfm_from_import_record,
    score_rfm,
    segment_distribution,
)

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
        failures.append(label)


def group(title: str) -> None:
    print(f"\n[{title}]")


print("=" * 74)
print("RFM PASSTHROUGH AND UNION SCORING")
print("=" * 74)


# ---------------------------------------------------------------------------
# 1. Alias headers, through real pandas parsing
# ---------------------------------------------------------------------------

group("1 alias headers survive a real CSV round-trip")

csv = (
    "external_customer_id,name,Recency,Total Spend,purchase_count\n"
    "C1,Ada,10,1500.50,7\n"
    "C2,Grace,45,300,2\n"
    "C3,Alan,120,90.25,1\n"
)
frame = pd.read_csv(pd.io.common.StringIO(csv))
records = frame.to_dict(orient="records")

check("three rows parsed", len(records) == 3)
check("every row is recognised as carrying real RFM", all(has_real_rfm_columns(r) for r in records))

row = rfm_from_import_record("cust-1", records[0])
check("a row is produced", row is not None)
check("recency read from 'Recency'", row is not None and row.recency == 10.0)
check("monetary read from 'Total Spend' (space-separated alias)", row is not None and row.monetary == 1500.50)
check("frequency read from 'purchase_count'", row is not None and row.frequency == 7)
check("source is marked as import, not orders", row is not None and row.source == SOURCE_IMPORT)
check(
    "average order value is derived, not invented",
    row is not None and row.average_order_value == round(1500.50 / 7, 2),
)
check("no score is assigned at passthrough time", row is not None and row.r_score is None)
check("no segment label is assigned at passthrough time", row is not None and row.rfm_segment is None)

for header in ("total-spend", "TOTALSPEND", "Total_Spend", "lifetime_value"):
    variant = {"recency": 5, "frequency": 3, header: 600}
    built = rfm_from_import_record("c", variant)
    check(f"monetary alias {header!r} resolves", built is not None and built.monetary == 600.0)


# ---------------------------------------------------------------------------
# 2. Blank cells are unknown, not zero
# ---------------------------------------------------------------------------

group("2 a blank cell is absent, never zero")

blank_csv = (
    "external_customer_id,recency,frequency,monetary\n"
    "C1,10,5,500\n"
    "C2,,3,200\n"          # recency missing
    "C3,20,,150\n"         # frequency missing
    "C4,30,4,\n"           # monetary missing
)
blank_frame = pd.read_csv(pd.io.common.StringIO(blank_csv))
blank_records = blank_frame.to_dict(orient="records")

check(
    "pandas really does produce NaN for the blanks",
    any(isinstance(v, float) and v != v for r in blank_records for v in r.values()),
    "if this fails the rest of the group proves nothing",
)
check("the complete row is accepted", has_real_rfm_columns(blank_records[0]))
check("a row missing recency is refused", not has_real_rfm_columns(blank_records[1]))
check("a row missing frequency is refused", not has_real_rfm_columns(blank_records[2]))
check("a row missing monetary is refused", not has_real_rfm_columns(blank_records[3]))

for index, missing in ((1, "recency"), (2, "frequency"), (3, "monetary")):
    check(
        f"no row is built when {missing} is blank",
        rfm_from_import_record("c", blank_records[index]) is None,
        "a NaN treated as 0.0 would be silently fabricated data",
    )

check(
    "a monetary of exactly 0 is kept (a real customer who spent nothing)",
    (built := rfm_from_import_record("c", {"recency": 1, "frequency": 1, "monetary": 0})) is not None
    and built.monetary == 0.0,
)
check(
    "a negative monetary is refused",
    rfm_from_import_record("c", {"recency": 1, "frequency": 1, "monetary": -5}) is None,
)
check(
    "a negative recency is refused",
    rfm_from_import_record("c", {"recency": -1, "frequency": 1, "monetary": 5}) is None,
)
check(
    "a frequency below 1 is refused (nobody has bought a fraction of a time)",
    rfm_from_import_record("c", {"recency": 1, "frequency": 0, "monetary": 5}) is None,
)
check(
    "a non-numeric monetary is refused, not coerced",
    rfm_from_import_record("c", {"recency": 1, "frequency": 1, "monetary": "n/a"}) is None,
)
check(
    "an infinite value is refused",
    rfm_from_import_record("c", {"recency": 1, "frequency": 1, "monetary": float("inf")}) is None,
)


# ---------------------------------------------------------------------------
# 3. Union scoring: one population, one scale
# ---------------------------------------------------------------------------

group("3 orders and passthrough rows are scored as one population")


def orders_row(cid: str, recency: float, frequency: int, monetary: float) -> RfmRow:
    return RfmRow(
        customer_id=cid,
        frequency=frequency,
        monetary=monetary,
        total_orders=frequency,
        average_order_value=round(monetary / frequency, 2),
        recency=recency,
        source=SOURCE_ORDERS,
    )


# Five measured customers, all low spenders.
measured = [orders_row(f"m{i}", recency=i * 10, frequency=i, monetary=i * 10.0) for i in range(1, 6)]
# Five passed-through customers, all high spenders.
passthrough = [
    rfm_from_import_record(f"i{i}", {"recency": i, "frequency": i + 10, "monetary": 10_000.0 * i})
    for i in range(1, 6)
]
check("all passthrough rows were built", all(r is not None for r in passthrough))
passthrough = [r for r in passthrough if r is not None]

# Score each source alone, to capture what the naive approach would produce.
alone_measured = score_rfm([orders_row(r.customer_id, r.recency, r.frequency, r.monetary) for r in measured])
top_alone = max(r.m_score or 0 for r in alone_measured)
check(
    "scored alone, the low-spending cohort contains an m_score of 5",
    top_alone == 5,
    "this is the incomparable scale the union avoids",
)

union = score_rfm(list(measured) + list(passthrough))
check("the union keeps every row", len(union) == 10)

measured_scored = [r for r in union if r.source == SOURCE_ORDERS]
passthrough_scored = [r for r in union if r.source == SOURCE_IMPORT]
check("both sources are present after scoring", len(measured_scored) == 5 and len(passthrough_scored) == 5)
check(
    "every row got a score",
    all(r.m_score is not None and r.r_score is not None and r.f_score is not None for r in union),
)
check(
    "every row got a segment label",
    all(r.rfm_segment for r in union),
)
check(
    "scored together, the low-spending cohort no longer contains an m_score of 5",
    max(r.m_score for r in measured_scored) < 5,
    f"m_scores={[r.m_score for r in measured_scored]}",
)
check(
    "no orders customer outranks a passthrough customer on monetary",
    max(r.m_score for r in measured_scored) <= min(r.m_score for r in passthrough_scored),
    f"import={[r.m_score for r in passthrough_scored]} orders={[r.m_score for r in measured_scored]}",
)
check(
    "the top monetary quintile is entirely the high spenders",
    {r.source for r in union if r.m_score == 5} == {SOURCE_IMPORT},
)
check(
    "the bottom monetary quintile is entirely the low spenders",
    {r.source for r in union if r.m_score == 1} == {SOURCE_ORDERS},
)

# The real invariant of scoring one population: rank order is never violated.
# A straddling boundary bucket is expected — 10 rows over 5 quintiles puts two
# per bucket, so one bucket must contain the largest measured value and the
# smallest passed-through one. That is the point, not a defect.
by_monetary = sorted(union, key=lambda r: r.monetary)
check(
    "m_score is monotone in monetary across the whole union",
    all(a.m_score <= b.m_score for a, b in zip(by_monetary, by_monetary[1:])),
    f"{[(r.monetary, r.m_score) for r in by_monetary]}",
)
by_frequency = sorted(union, key=lambda r: r.frequency)
check(
    "f_score is monotone in frequency across the whole union",
    all(a.f_score <= b.f_score for a, b in zip(by_frequency, by_frequency[1:])),
)
by_recency = sorted(union, key=lambda r: r.recency)
check(
    "r_score is inverted in recency (more recent scores higher)",
    all(a.r_score >= b.r_score for a, b in zip(by_recency, by_recency[1:])),
    f"{[(r.recency, r.r_score) for r in by_recency]}",
)
check(
    "provenance is preserved through scoring",
    {r.source for r in union} == {SOURCE_ORDERS, SOURCE_IMPORT},
)
check(
    "scores stay inside 1..5",
    all(1 <= r.r_score <= 5 and 1 <= r.f_score <= 5 and 1 <= r.m_score <= 5 for r in union),
)

distribution = segment_distribution(union)
check("a distribution is produced", bool(distribution))
check(
    "the distribution counts every scored customer exactly once",
    sum(entry["customers"] for entry in distribution) == 10,
    f"{distribution}",
)
check(
    "the shares sum to 1",
    abs(sum(entry["share"] for entry in distribution) - 1.0) < 1e-6,
    f"{distribution}",
)
check(
    "no segment is reported twice",
    len({entry["segment"] for entry in distribution}) == len(distribution),
)


# ---------------------------------------------------------------------------
# 4. An unscorable row survives without being bucketed on a guess
# ---------------------------------------------------------------------------

group("4 a customer with no recency is stored but not scored")

no_recency = RfmRow(
    customer_id="dateless",
    frequency=3,
    monetary=300.0,
    total_orders=3,
    average_order_value=100.0,
    recency=None,
    source=SOURCE_ORDERS,
)
mixed = score_rfm([*measured, no_recency])
target = next(r for r in mixed if r.customer_id == "dateless")

check("the row is not dropped", any(r.customer_id == "dateless" for r in mixed))
check("it receives no r_score", target.r_score is None)
check("it receives no segment label", target.rfm_segment is None)
check("its measured values are untouched", target.frequency == 3 and target.monetary == 300.0)
check(
    "it is excluded from the segment distribution rather than counted as a segment",
    sum(entry["customers"] for entry in segment_distribution(mixed)) == len(measured),
)
check(
    "to_model_fields carries the null scores through to the database",
    target.to_model_fields()["r_score"] is None
    and target.to_model_fields()["rfm_segment"] is None
    and target.to_model_fields()["recency"] is None,
)


# ---------------------------------------------------------------------------
# 5. Precedence, verified where it is written in SQL
# ---------------------------------------------------------------------------

group("5 orders take precedence over an upload (source inspection)")

print("       Wiring that has no observable output of its own. The rule itself is")
print("       executed against a real database in section 6.")

engine_src = (BACKEND / "app" / "analytics" / "engine.py").read_text()
repo_src = (BACKEND / "app" / "repositories" / "analytics_repository.py").read_text()

check(
    "run_rfm_job excludes measured customers from the passthrough set",
    "exclude_customer_ids=measured_ids" in engine_src,
)
check(
    "measured_ids is derived from the orders result, not from a parameter",
    "measured_ids = {row.customer_id for row in result.rows}" in engine_src,
)
check(
    "the union is rescored before being written",
    "scored = score_rfm(combined)" in engine_src
    and engine_src.index("scored = score_rfm(combined)") < engine_src.index("replace_rfm_snapshot"),
    "scoring must precede persistence or unscored rows reach the table",
)
check(
    "replace_rfm_snapshot preserves import rows for customers without orders",
    "CustomerRFM.source != SOURCE_IMPORT" in repo_src,
)
check(
    "upsert_import_rfm strips scores before persisting",
    'for score_field in ("r_score", "f_score", "m_score", "rfm_segment")' in repo_src,
)
check(
    "upsert_import_rfm only ever deletes import-sourced rows",
    repo_src.count("CustomerRFM.source == SOURCE_IMPORT") >= 2,
)

# Every query in the repository must be tenant-scoped.
repo_tree = ast.parse(repo_src)
unscoped: list[str] = []
for node in ast.walk(repo_tree):
    if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
        continue
    body = ast.get_source_segment(repo_src, node) or ""
    if "select(" in body or "delete(" in body:
        if "business_id ==" not in body:
            unscoped.append(node.name)

check(
    "every repository query filters on business_id",
    not unscoped,
    f"unscoped: {unscoped}",
)

# The RFM job must not raise when orders are absent but a real upload exists.
check(
    "an empty orders result alone no longer aborts the job",
    "if not combined:" in engine_src and "if not result.rows:" not in engine_src,
)
check(
    "the insufficient-data message mentions both sources",
    "no uploaded file carried real" in engine_src,
)

importer_src = (BACKEND / "app" / "ingestion" / "importer.py").read_text()
check(
    "the customers import calls the passthrough",
    "_store_import_rfm(business_id, df, customers)" in importer_src,
)
check(
    "the passthrough reads the raw frame, not the stripped validated records",
    "df.to_dict(orient=\"records\")" in importer_src,
)
check(
    "the orders import reports how many shell customers it created",
    "\"customers_created\": customers_created" in importer_src,
)
# A failed import must not leave a half-open transaction behind, because the very
# next statement is the one that writes the failure record — and on a poisoned
# session that write fails too, losing the only evidence of what went wrong.
#
# Counting occurrences of the rollback call was the earlier form of this check and
# it broke as soon as the column-mapping failure path was factored into a shared
# helper: the number went from 4 to 5 while the property being checked was still
# true. So resolve it per function instead. `_mapping_failure` is included because
# all four import paths delegate their `ColumnMappingError` handling to it.
_importer_tree = ast.parse(importer_src)


def _function(name: str) -> ast.FunctionDef | None:
    for node in ast.walk(_importer_tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _calls_inside_loops(function: ast.FunctionDef) -> set[str]:
    """Method names called from inside a ``for``/``while`` body in ``function``."""
    names: set[str] = set()

    for node in ast.walk(function):
        if not isinstance(node, (ast.For, ast.While)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                names.add(inner.func.attr)

    return names


_import_orders = _function("import_orders")
check("import_orders exists to be checked", _import_orders is not None)

# Orders arrive keyed on the customer id the *source system* uses, and the orders
# table stores this application's own id, so the import has to translate. These two
# checks used to assert on `get_customer_by_external_id` and a hand-rolled
# `resolved[...]` cache, which is the per-row implementation the batching work
# deliberately removed — so they failed while the property they exist to protect
# was still true. Asserting the property instead: a translation happens, and it
# happens in bulk.
_orders_calls = {
    node.func.attr
    for node in ast.walk(_import_orders or ast.Module(body=[], type_ignores=[]))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
}
check(
    "the orders import resolves external customer ids to internal ones",
    "customer_ids_by_external_ids" in _orders_calls
    and 'payload["customer_id"] = customer_id' in importer_src,
    f"calls: {sorted(_orders_calls)}",
)

# The real content of the old "caches resolutions so one customer is created once"
# check, expressed as the two things that actually make it true. Substring matching
# could not have caught a regression that moved the insert back inside the loop;
# this can.
check(
    "the orders import never queries or writes customers from inside a row loop",
    not (
        {"customer_ids_by_external_ids", "bulk_upsert_customers", "get_customer_by_external_id"}
        & _calls_inside_loops(_import_orders)
    )
    if _import_orders
    else False,
    f"called inside a loop: {sorted(_calls_inside_loops(_import_orders)) if _import_orders else '?'}",
)
check(
    "one external customer id produces one customer, however many orders cite it",
    "dict.fromkeys(external_customer_ids)" in importer_src,
    "the unknown-customer list must be deduplicated before the batch insert",
)

_rollback_owners = {
    node.name
    for node in ast.walk(_importer_tree)
    if isinstance(node, ast.FunctionDef)
    and any(
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and inner.func.attr == "rollback"
        for inner in ast.walk(node)
    )
}
_needs_rollback = {
    "import_customers",
    "import_products",
    "import_inventory",
    "import_orders",
    "_mapping_failure",
}
check(
    "every import path rolls back before recording a failure",
    _needs_rollback <= _rollback_owners,
    f"missing a rollback in: {sorted(_needs_rollback - _rollback_owners)}",
)
check(
    "the four import paths delegate mapping failures to the rolling-back helper",
    importer_src.count("except ColumnMappingError as mapping_error:") == 4
    and importer_src.count("return self._mapping_failure(import_record.id, mapping_error)") == 4,
    f"{importer_src.count('except ColumnMappingError as mapping_error:')} handlers, "
    f"{importer_src.count('return self._mapping_failure(import_record.id, mapping_error)')} "
    f"delegations; expected 4 of each",
)


# ---------------------------------------------------------------------------
# 6. The same precedence rule, executed instead of read
# ---------------------------------------------------------------------------

group("6 orders take precedence over an upload (executed against a real database)")

# Two checks used to live in section 5 as substring searches for `notin_` and for
# `CustomerRFM.customer_id.in_(customer_ids)`. Both of those spellings were removed
# deliberately: each built one bind parameter per customer, and PostgreSQL rejects a
# statement with more than 65,535 of them, so the exclusion moved into Python and the
# DELETE was split per chunk. The checks failed while the rule they exist to protect
# was still true — the same trap documented for the orders-import checks below. So the
# rule is executed here instead, which is both stronger and immune to the next
# refactor of the SQL.
#
# SQLAlchemy stays optional: it is a hard dependency of the application, but this file
# also holds 70-odd pure-Python checks that must still run on a machine that only has
# pandas. A missing SQLAlchemy therefore prints a notice and counts nothing, rather
# than skipping the file or quietly passing.

try:
    from sqlalchemy import create_engine
    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import Session
except ImportError as exc:
    print(f"       NOT VERIFIED HERE: SQLAlchemy is not installed ({exc}).")
    print("       Nothing in this section ran, and nothing is counted as passing.")
else:
    from datetime import datetime, timezone

    from app.analytics.engine import run_rfm_job
    from app.db.base import Base
    from app.db.models.analytics import CustomerRFM
    from app.db.models.business import Business
    from app.db.models.customer import Customer
    from app.db.models.order import Order
    from app.repositories.analytics_repository import AnalyticsRepository

    #: Deliberately different from the measured total, so "which source won" is
    #: readable off the stored value rather than inferred.
    UPLOADED_MONETARY = 999.0
    MEASURED_TOTAL = 150.0
    UPLOAD_ONLY_MONETARY = 400.0

    sql_engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(sql_engine)
    session = Session(bind=sql_engine, future=True)

    try:
        business = Business(name="Precedence")
        session.add(business)
        session.flush()
        bid = str(business.id)

        def _customer(external: str) -> str:
            row = Customer(business_id=bid, external_customer_id=external)
            session.add(row)
            session.flush()
            return str(row.id)

        both = _customer("has-both")
        upload_only = _customer("upload-only")
        orders_only = _customer("orders-only")

        reference = datetime(2024, 6, 1, tzinfo=timezone.utc)

        for index, (customer_id, amount) in enumerate(
            ((both, 100.0), (both, 50.0), (orders_only, 20.0))
        ):
            session.add(
                Order(
                    business_id=bid,
                    customer_id=customer_id,
                    external_order_id=f"order-{index}",
                    order_date=reference,
                    status="pending",
                    total_amount=amount,
                )
            )

        repository = AnalyticsRepository(session)
        repository.upsert_import_rfm(
            business_id=bid,
            rows=[
                {
                    "customer_id": both,
                    "recency": 12,
                    "frequency": 4,
                    "monetary": UPLOADED_MONETARY,
                },
                {
                    "customer_id": upload_only,
                    "recency": 30,
                    "frequency": 2,
                    "monetary": UPLOAD_ONLY_MONETARY,
                },
            ],
        )
        session.commit()

        # The exclusion on its own, before the job exercises it: this is the function
        # whose SQL NOT IN became a Python filter, so it is worth checking directly.
        everything = {
            str(row.customer_id) for row in repository.get_import_sourced_rfm(business_id=bid)
        }
        remaining = {
            str(row.customer_id)
            for row in repository.get_import_sourced_rfm(
                business_id=bid, exclude_customer_ids={both}
            )
        }

        check(
            "both uploaded rows are returned when nothing is excluded",
            everything == {both, upload_only},
            f"got {len(everything)} row(s)",
        )
        check(
            "get_import_sourced_rfm returns every row except the excluded customer",
            remaining == {upload_only},
            f"expected only the upload-only customer, got {len(remaining)} row(s)",
        )

        metadata = run_rfm_job(session, business_id=bid, reference_date=reference)
        session.commit()

        def snapshot() -> dict[str, list[CustomerRFM]]:
            rows = list(
                session.execute(
                    sa_select(CustomerRFM).where(CustomerRFM.business_id == bid)
                ).scalars()
            )
            grouped: dict[str, list[CustomerRFM]] = {}
            for row in rows:
                grouped.setdefault(str(row.customer_id), []).append(row)
            return grouped

        stored = snapshot()
        total_rows = sum(len(rows) for rows in stored.values())

        check(
            "the customer with both orders and an upload is stored once, not twice",
            len(stored.get(both, [])) == 1,
            f"{len(stored.get(both, []))} rows for that customer; two means the exclusion "
            "did not apply and the customer was counted from both sources",
        )
        check(
            "and the stored figures are the measured ones, not the uploaded ones",
            bool(stored.get(both))
            and stored[both][0].source == SOURCE_ORDERS
            and float(stored[both][0].monetary) == MEASURED_TOTAL,
            f"source={stored[both][0].source if stored.get(both) else '?'} "
            f"monetary={float(stored[both][0].monetary) if stored.get(both) else '?'} "
            f"(uploaded was {UPLOADED_MONETARY}, orders sum to {MEASURED_TOTAL})",
        )
        check(
            "the customer with only an upload keeps their uploaded figures",
            len(stored.get(upload_only, [])) == 1
            and stored[upload_only][0].source == SOURCE_IMPORT
            and float(stored[upload_only][0].monetary) == UPLOAD_ONLY_MONETARY,
            f"rows={len(stored.get(upload_only, []))}",
        )
        check(
            "and is scored on the same scale as the measured customers",
            bool(stored.get(upload_only))
            and stored[upload_only][0].m_score is not None
            and bool(stored[upload_only][0].rfm_segment),
            "an unscored row here means the passthrough was written outside the union",
        )
        check(
            "the customer with only orders is stored from orders",
            len(stored.get(orders_only, [])) == 1
            and stored[orders_only][0].source == SOURCE_ORDERS,
        )
        check(
            "the snapshot holds exactly one row per customer",
            total_rows == 3,
            f"{total_rows} rows for 3 customers",
        )
        check(
            "the job reports the passthrough it performed",
            metadata.get("rows_written") == 3 and metadata.get("customers_from_import") == 1,
            f"rows_written={metadata.get('rows_written')} "
            f"customers_from_import={metadata.get('customers_from_import')}",
        )

        # A refresh is where a partial DELETE shows up: rows accumulate, or the
        # upload-sourced row is lost.
        run_rfm_job(session, business_id=bid, reference_date=reference)
        session.commit()

        refreshed = snapshot()
        refreshed_rows = sum(len(rows) for rows in refreshed.values())
        import_sourced = sum(
            1 for rows in refreshed.values() for row in rows if row.source == SOURCE_IMPORT
        )

        check(
            "a second run replaces the snapshot rather than adding to it",
            refreshed_rows == 3,
            f"{refreshed_rows} rows after the second run",
        )
        check(
            "and the upload-sourced row survives the replacement exactly once",
            import_sourced == 1,
            f"{import_sourced} import-sourced rows after the second run",
        )
    finally:
        session.close()
        sql_engine.dispose()


print("\n" + "=" * 74)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print(f"ALL {checks} PASSTHROUGH CHECKS PASSED")
print("=" * 74)
