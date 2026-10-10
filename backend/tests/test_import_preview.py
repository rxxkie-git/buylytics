"""End-to-end checks on ``POST /api/v1/imports/{dataset}/preview``.

    python3 tests/test_import_preview.py

This executes the real endpoint function against real CSV bytes. Only two
collaborators are replaced — ``app.core.multi_tenancy`` and
``app.ingestion.importer`` — and neither is on the preview code path; they are
stubbed because importing them drags in SQLAlchemy and a database session for an
endpoint that deliberately touches neither. ``FileParser``, ``apply_mapping``,
``mapping_summary``, ``expected_columns`` and ``ColumnMappingError`` are the real
implementations.

Why this file exists alongside ``test_column_mapping.py``: that one tests the
mapping resolver in isolation, which is where the interesting logic lives. This one
tests the *response the browser receives*, because the resolver being right does not
prove the payload is shaped the way ``ImportPage.jsx`` reads it. The two shapes
drifted once already during development — ``column_mapping`` is a list of
``{source, field, label}`` rows, and indexing it as a dict is the mistake this
pins down.

The behaviour being protected, in the user's words: never again report "Imported
with skipped rows / 0 rows processed, 38,279 skipped" when the only problem is
column naming.

Runs under pytest if it is installed, or standalone with python3.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

try:
    import pandas  # noqa: F401
except ImportError:  # pragma: no cover
    print("SKIPPED: pandas is not installed, so no file can be parsed")
    sys.exit(0)

try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    print("SKIPPED: fastapi is not installed, so the route module cannot be imported")
    sys.exit(0)


# --------------------------------------------------------------------------
# Stub the two off-path collaborators before importing the route module.
# --------------------------------------------------------------------------
class _Context:
    """Stands in for BusinessContext. The tenant is fixed and never read from input."""

    def __init__(self, business_id: str = "biz_test", session: object = None) -> None:
        self.business_id = business_id
        self.session = session


if "app.core.multi_tenancy" not in sys.modules:
    _mt = types.ModuleType("app.core.multi_tenancy")
    _mt.BusinessContext = _Context
    _mt.get_business_context = lambda: _Context()
    sys.modules["app.core.multi_tenancy"] = _mt

if "app.ingestion.importer" not in sys.modules:
    _imp = types.ModuleType("app.ingestion.importer")
    _imp.ImportOrchestrator = type("ImportOrchestrator", (), {"__init__": lambda self, session: None})
    sys.modules["app.ingestion.importer"] = _imp

from fastapi import HTTPException  # noqa: E402

import app.api.routes.v1_imports as route  # noqa: E402


class _Upload:
    """Duck-typed upload.

    The endpoint only ever touches ``.filename`` and ``await .read(n)``, so this is
    the whole contract. Constructing a real ``UploadFile`` would mean building a
    ``SpooledTemporaryFile`` to test code that immediately spools it to disk again.
    """

    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def preview(dataset: str, csv_text: str, filename: str = "export.csv") -> dict:
    return asyncio.run(
        route.preview_columns(
            dataset=dataset,
            file=_Upload(filename, csv_text.encode()),
            business_context=_Context(),
        )
    )


def mapped(result: dict) -> dict[str, str]:
    """``column_mapping`` is a list of rows; index it by canonical field name."""
    return {row["field"]: row["source"] for row in result["column_mapping"]}


def route_paths() -> set[str]:
    """Registered paths, tolerating either a real APIRouter or a stubbed one."""
    paths = set()

    for entry in route.router.routes:
        path = getattr(entry, "path", None)
        paths.add(path if path is not None else entry[1])

    return paths


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_the_preview_and_upload_routes_are_registered() -> None:
    paths = route_paths()

    assert "/api/v1/imports/{dataset}/preview" in paths, paths

    # The preview must not have displaced the real uploads. Two path segments
    # against one means they cannot shadow each other, but that is worth pinning.
    for dataset in ("orders", "customers", "products", "inventory"):
        assert f"/api/v1/imports/{dataset}" in paths, paths


def test_canonical_headers_still_work() -> None:
    """The internal names remain valid input. Nothing was taken away."""
    result = preview(
        "orders",
        "external_order_id,external_customer_id,order_date,total_amount,status\n"
        "A-1,C-1,2024-01-05,120.50,completed\n",
    )

    assert result["status"] == "ready"
    assert result["row_count"] == 1
    assert mapped(result)["external_order_id"] == "external_order_id"
    assert mapped(result)["external_customer_id"] == "external_customer_id"
    assert len(result["sample_rows"]) == 1


def test_a_store_export_maps_without_being_edited() -> None:
    """The file that used to import zero rows."""
    result = preview(
        "orders",
        "order_id,customer_id,order_date,total_amount,status\n"
        "1001,55,2024-03-02,89.99,paid\n"
        "1002,56,2024-03-03,12.00,paid\n",
    )

    assert result["status"] == "ready"
    assert mapped(result)["external_order_id"] == "order_id"
    assert mapped(result)["external_customer_id"] == "customer_id"
    assert result["row_count"] == 2


def test_capitalisation_and_spacing_are_tolerated() -> None:
    result = preview(
        "orders",
        "Order ID , Customer ID ,  Date  , Amount , Status \nX9,C9,2024-05-01,10,completed\n",
    )

    assert result["status"] == "ready"
    assert mapped(result)["external_order_id"] in {"Order ID ", "Order ID"}
    assert mapped(result).get("total_amount") in {" Amount ", "Amount"}


def test_a_missing_required_field_is_named_not_skipped() -> None:
    """The whole point. A header problem is a report, not an import of nothing."""
    result = preview("orders", "order_id,order_date,total_amount\nA-1,2024-01-05,120.50\n")

    assert result["status"] == "unmappable"
    assert result["error_code"] == "column_mapping_failed"

    missing = {field["field"] for field in result["missing_fields"]}
    assert missing == {"external_customer_id"}

    # The user's own column names come back, so the message is about their file
    # rather than about the schema.
    assert result["available_columns"] == ["order_id", "order_date", "total_amount"]

    # And a way out: what to rename a column to.
    accepted = next(f["accepted_columns"] for f in result["missing_fields"])
    assert "customer_id" in accepted

    # The row count proves the file was read. "0 rows" was the misleading part.
    assert result["row_count"] == 1
    assert "sample_rows" not in result


def test_a_genuine_tie_is_refused_rather_than_guessed() -> None:
    """Two headers that normalise to the same name. No coin flip."""
    result = preview("orders", "Order ID,order id,customer_id\nA-1,A-1,C-1\n")

    assert result["status"] == "unmappable"
    assert result["ambiguous_fields"][0]["field"] == "external_order_id"
    assert set(result["ambiguous_fields"][0]["columns"]) == {"Order ID", "order id"}
    assert "external_order_id" not in mapped(result)


def test_different_rank_aliases_are_not_a_tie() -> None:
    """``order_id`` and ``order_number`` are both accepted spellings of one field.

    A file carrying both is common — an internal id and a human-facing number — and
    refusing it would be user-hostile. Declared precedence picks ``order_id``, and
    the runner-up is reported as ignored rather than silently dropped, so the user
    can see the choice that was made.
    """
    result = preview(
        "orders",
        "order_id,order_number,customer_id,order_date,total_amount\nA-1,9001,C-1,2024-01-05,120.50\n",
    )

    assert result["status"] == "ready"
    assert mapped(result)["external_order_id"] == "order_id"
    assert result["unmapped_columns"] == ["order_number"]


def test_an_unreadable_file_is_never_a_500_and_never_zero_rows() -> None:
    """Garbage in a .csv is a report, not a crash.

    pandas will happily read binary as a single wide column, so these do not raise —
    they come back naming both missing fields, which is the actionable answer. What
    matters is the invariant: a 4xx or a structured report, never an unhandled
    exception, and never a "completed" import of nothing.
    """
    for label, payload in (
        ("binary", "\x00\x01\x02binary garbage"),
        ("a PNG renamed to .csv", "\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"),
        ("one column of prose", "just some notes\nline two\n"),
    ):
        try:
            result = preview("orders", payload, filename="junk.csv")
        except HTTPException as exc:
            assert 400 <= exc.status_code < 500, f"{label}: {exc.status_code}"
            continue

        assert result["status"] == "unmappable", f"{label}: {result['status']}"
        assert result["missing_fields"], label


def test_bad_requests_are_rejected_before_anything_is_read() -> None:
    for label, dataset, payload, filename, expected in (
        ("unknown dataset", "nonsense", "a,b\n1,2\n", "x.csv", 404),
        ("empty upload", "orders", "", "empty.csv", 400),
        ("unsupported extension", "orders", "order_id\n1\n", "notes.pdf", 400),
    ):
        try:
            preview(dataset, payload, filename=filename)
        except HTTPException as exc:
            assert exc.status_code == expected, f"{label}: got {exc.status_code}"
        else:
            raise AssertionError(f"{label}: no HTTPException raised")


def test_the_preview_writes_nothing_and_derives_the_tenant_from_auth() -> None:
    """Source inspection, because "wrote nothing" is a property of the code.

    A preview is called on every file selection, so it has to be free of side
    effects — no import record, no rows, nothing to clean up after a user who
    changes their mind. And the tenant comes from the authenticated context; a
    business id from the request body would be a cross-tenant read.
    """
    source = (BACKEND / "app" / "api" / "routes" / "v1_imports.py").read_text(encoding="utf-8")
    start = source.index("async def preview_columns")
    body = source[start : source.index('@router.post("/customers"')]

    for forbidden in ("commit(", "ImportOrchestrator", ".add(", ".delete("):
        assert forbidden not in body, f"preview calls {forbidden}"

    assert "business_context: BusinessContext = Depends(get_business_context)" in body

    # One handler for ColumnMappingError, covering the whole body. An earlier
    # revision caught it around ``apply_mapping`` only and re-raised anything from
    # outside — and since no exception handler is registered for that class, the
    # re-raise produced a bare 500 for what is a column-naming problem.
    #
    # Checked with ast rather than substring search: the handler's own explanatory
    # comment contains the word "raise", which a naive `"raise" not in block` test
    # trips over. That false positive is why this is parsed.
    import ast

    handlers = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "ColumnMappingError"
    ]
    assert len(handlers) == 1, f"expected exactly one ColumnMappingError handler, found {len(handlers)}"

    statements = handlers[0].body
    assert not any(isinstance(node, ast.Raise) for node in ast.walk(handlers[0])), (
        "the mapping error must be turned into a response, not re-raised into a 500"
    )
    assert any(isinstance(node, ast.Return) for node in statements), "the handler must return a payload"


def test_no_spooled_upload_is_left_behind() -> None:
    """The temp file has to go, on the success path and on the error path."""
    import tempfile

    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("tmp*"))

    preview("orders", "order_id,customer_id,order_date,total_amount\nA,C,2024-01-01,5\n")
    try:
        preview("unknown_dataset", "a,b\n1,2\n")
    except HTTPException:
        pass

    leaked = {path for path in tmp.glob("tmp*") if path not in before and path.suffix == ".csv"}
    assert not leaked, f"left behind: {leaked}"


def main() -> int:
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0

    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failed += 1
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok   {test.__name__}")

    print()
    if failed:
        print(f"{failed} of {len(tests)} IMPORT PREVIEW CHECKS FAILED")
        return 1

    print(f"ALL {len(tests)} IMPORT PREVIEW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
