"""The import screen must be able to say *what* went wrong, and *what is missing*.

Two contracts, both spanning the backend and the frontend, which is why they are
checked in one file: each half is individually plausible and useless without the
other, and nothing else in the suite would notice them drifting apart.

**Contract 1 — a 500 names its exception class.**
A customers import failed in the UI with "An unexpected error occurred" and
nothing else. That message was true and useless: it gave no way to distinguish a
serialization failure from a dead database from one bad row, so the only route to
the cause was reading container logs. The body still carries no traceback, no SQL
and no connection string — ``str(exc)`` from a driver embeds the statement *and
its bound parameters*, which for this application means customer rows. But
``type(exc).__name__`` is source code: written by a developer, identical for every
tenant, carrying no data at all. So the backend adds it, and the frontend has to
read the same key it is written under and actually render it.

**Contract 2 — an import says which fields it left empty.**
``unmapped_columns`` names source columns that were ignored, which a user can
shrug at. The opposite direction is the one that matters: application fields no
column supplied, which will be NULL for every row of an import the UI is about to
call successful. This project's own ``dataset/df_Orders.csv`` is the case in
point — it has no amount column of any kind, so ``total_amount`` is empty for all
89,316 orders. The import succeeds, the row count is right, and revenue and RFM
monetary are silently zero. ``fields_without_columns`` is what stops "Imported"
being the whole story, so it has to reach the response *and* the screen.

Pure stdlib. ``app.ingestion.column_mapping`` imports nothing but ``re``, ``typing``
and ``dataclasses``, so the behavioural half runs in any environment.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
sys.path.insert(0, str(BACKEND))

from app.ingestion.column_mapping import (  # noqa: E402
    DATASETS,
    mapping_summary,
    resolve_columns,
)

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1

    if condition:
        print(f"  ok   {name}")
        return

    failures.append(name)
    print(f"  FAIL {name}" + (f": {detail}" if detail else ""))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function_source(tree: ast.AST, source: str, name: str) -> str | None:
    """Source text of one function, so an assertion cannot match a neighbour."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    return None


def function_code(tree: ast.AST, source: str, name: str) -> str | None:
    """Same, with the docstring removed.

    Needed because the assertions below are about what the *code* does, and the
    docstrings here discuss the very things being ruled out — the handler's own
    docstring explains why ``str(exc)`` must not reach the body, which a naive
    substring scan reads as the violation it is warning against.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != name:
            continue

        body = node.body[1:] if ast.get_docstring(node) else node.body
        return "\n".join(ast.get_source_segment(source, stmt) or "" for stmt in body)

    return None


# ===========================================================================
print("\n[1] Backend: the 500 body names the exception class and nothing else")
# ===========================================================================

errors_path = BACKEND / "app" / "core" / "errors.py"
errors_src = read(errors_path)
errors_tree = ast.parse(errors_src)
handler = function_code(errors_tree, errors_src, "unhandled_exception_handler")

check("unhandled_exception_handler exists", handler is not None)
handler = handler or ""

check(
    "the 500 payload carries the exception class name",
    'payload["error"]["type"] = type(exc).__name__' in handler,
    "the frontend has nothing to show without it",
)
check(
    "the generic message is still generic",
    '"An unexpected error occurred"' in handler,
    "the fix is to add a type, not to start returning internal detail",
)

# The whole safety argument rests on this: the class name is source code, the
# message is data. A regression here would leak SQL and bound parameters — i.e.
# customer rows — into a browser, and it would look like a helpful improvement.
check(
    "str(exc) never reaches the response body",
    "str(exc)" not in handler,
    "a driver's message embeds the statement and its bound parameters",
)
for leak in ("format_exc", "exc.args", "traceback", "repr(exc)"):
    check(
        f"the response body does not include {leak}",
        leak not in handler.split("return JSONResponse")[-1],
        "internal detail belongs in the log, not the body",
    )
check(
    "the traceback still reaches the log",
    "logger.exception(" in handler,
    "the operator needs the detail the body deliberately omits",
)

# ===========================================================================
print("\n[2] Backend: every import and the preview report fields left empty")
# ===========================================================================

check(
    "mapping_summary reports fields no column supplied",
    '"fields_without_columns":' in read(BACKEND / "app" / "ingestion" / "column_mapping.py"),
    "matched with the colon: the bare name is a prefix of any renamed variant",
)

# Behavioural, not textual: run the real resolver over the real headers of this
# project's own orders file and confirm the gap is reported rather than inferred.
ORDERS_HEADER = [
    "order_id",
    "customer_id",
    "order_status",
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_timestamp",
    "order_estimated_delivery_date",
]
resolved = resolve_columns(ORDERS_HEADER, "order")
summary = mapping_summary(resolved)

# Presence is asserted before anything indexes the key, and everything below uses
# .get(), so a renamed or dropped key reports a named failure instead of dying in
# a KeyError three checks later with the remaining checks never run at all.
check(
    "mapping_summary always includes the key, even when nothing is missing",
    "fields_without_columns" in summary,
    f"keys: {sorted(summary)}",
)
gaps = summary.get("fields_without_columns") or []
empty = {entry["field"] for entry in gaps}

check(
    "df_Orders headers report total_amount as a field with no column",
    "total_amount" in empty,
    f"reported empty: {sorted(empty)} — this is the 89,316-orders-worth-nothing case",
)
check(
    "a field that WAS mapped is not reported as empty",
    "order_date" not in empty and resolved.mapping.get("order_date") == "order_purchase_timestamp",
    f"mapping: {resolved.mapping}",
)
check(
    "required fields are never listed as optional gaps",
    not (empty & {spec.name for spec in DATASETS["order"] if spec.required}),
    "a missing required field is a mapping failure, reported through a different path",
)
check(
    "every reported gap carries a human label for the UI to print",
    bool(gaps) and all(entry.get("label") for entry in gaps),
)

# A file that supplies everything must report nothing, or the warning becomes
# noise that gets ignored on the import that matters. Using the canonical field
# names as the header also proves they round-trip: a file exported by this
# application must import back into it.
full = resolve_columns([spec.name for spec in DATASETS["order"]], "order")
check(
    "a file that supplies every field reports no gaps",
    mapping_summary(full).get("fields_without_columns") == [],
    f"{mapping_summary(full).get('fields_without_columns')}",
)

# Found while writing the check above, which passed a duplicated header by
# mistake: two columns claiming one field is *ambiguous*, so the resolver refuses
# to guess and leaves the field unmapped. Worth pinning, because the interesting
# part is that the gap report covers it — the user is told "Total amount will be
# empty" rather than silently getting one of the two columns.
duplicated = resolve_columns(
    [spec.name for spec in DATASETS["order"]] + ["total_amount"], "order"
)
check(
    "a duplicated header is treated as ambiguous rather than guessed",
    "total_amount" in duplicated.ambiguous and "total_amount" not in duplicated.mapping,
    f"ambiguous={duplicated.ambiguous} mapping={duplicated.mapping}",
)
check(
    "and the resulting gap is reported like any other",
    "total_amount"
    in {e["field"] for e in mapping_summary(duplicated).get("fields_without_columns") or []},
    "an ambiguous column that is silently dropped is the same NULL with no warning",
)

importer_path = BACKEND / "app" / "ingestion" / "importer.py"
importer_src = read(importer_path)
importer_tree = ast.parse(importer_src)

# `**mapping_summary(resolved)` in each import is what carries the new key without
# four separate edits. Asserting it per function so a regression in one import
# cannot hide behind the other three.
for name in ("import_customers", "import_products", "import_inventory", "import_orders"):
    body = function_source(importer_tree, importer_src, name) or ""
    check(
        f"{name} spreads mapping_summary into its response",
        "**mapping_summary(resolved)" in body,
        "otherwise this import alone stops reporting empty fields",
    )

routes_path = BACKEND / "app" / "api" / "routes" / "v1_imports.py"
routes_src = read(routes_path)
check(
    "the preview response spreads mapping_summary too",
    "**mapping_summary(resolved)" in routes_src,
    "the warning is most useful before the user confirms, not after",
)

# FastAPI filters a response through `response_model` when one is declared, which
# would drop `fields_without_columns` silently — the endpoint would keep returning
# 200 and the UI would quietly stop warning. There is no response_model on these
# routes today; this check is here so adding one is a deliberate act.
check(
    "no response_model silently filters the import responses",
    "response_model" not in routes_src,
    "a response_model must list fields_without_columns or it will be stripped",
)

# ===========================================================================
print("\n[3] Frontend: the same keys are read, and rendered")
# ===========================================================================

client_path = FRONTEND / "src" / "api" / "client.js"
import_page_path = FRONTEND / "src" / "pages" / "app" / "ImportPage.jsx"

if not client_path.is_file() or not import_page_path.is_file():
    check("the frontend files exist to be checked", False, f"missing under {FRONTEND}")
else:
    client_src = read(client_path)
    page_src = read(import_page_path)

    # Contract 1, frontend half. Both ends of one wire: the backend writes
    # `error.type`, so this must read `error.type` and no other spelling.
    check(
        "toApiError reads the exception type from the envelope the backend writes",
        re.search(r"exceptionType:\s*body\?\.error\?\.type", client_src) is not None,
        "a mismatched key here is invisible — the field just stays null",
    )
    check(
        "ApiError declares exceptionType so it survives construction",
        "exceptionType" in client_src.split("constructor(")[1].split(")")[0]
        or "this.exceptionType = exceptionType" in client_src,
        "reading it in toApiError is pointless if the constructor drops it",
    )
    check(
        "the import page renders the exception type it was given",
        "error.exceptionType" in page_src,
        "carrying the value to the page and not showing it fixes nothing",
    )
    check(
        "the import page still shows the request id to correlate with the log",
        "error.requestId" in page_src,
    )

    # Contract 2, frontend half. Before confirming *and* after importing: the
    # first prevents the surprise, the second explains it if it happened anyway.
    occurrences = page_src.count("fields_without_columns")
    check(
        "the import page reads fields_without_columns",
        occurrences >= 1,
        "the backend reports the gap and the page ignores it",
    )
    check(
        "it is shown both in the preview and in the import result",
        occurrences >= 2,
        f"found {occurrences} use(s); expected the mapping table and the result panel",
    )
    check(
        "the gap is rendered by a shared component rather than duplicated prose",
        page_src.count("<EmptyFields") >= 2 and "const EmptyFields" in page_src,
        "two copies of this warning will drift apart",
    )

    # The words matter here. "No column found for Order total" tells the user what
    # to do; a field name like `total_amount` does not.
    #
    # Sliced to the component's own body: the first attempt split on `\n};`, which
    # an arrow function returning JSX never contains, so the "block" ran on into
    # MappingTable — whose `key={entry.field}` then failed a check about text the
    # user sees. Stop at the next top-level `const`.
    after = page_src.split("const EmptyFields", 1)[-1]
    next_decl = re.search(r"\nconst \w+", after)
    empty_fields_block = after[: next_decl.start()] if next_decl else after

    check(
        "the EmptyFields component was isolated for inspection",
        0 < len(empty_fields_block) < len(after),
        "the slice found no following declaration, so it is not a component body",
    )
    check(
        "the warning prints the human label, not the internal field name",
        "entry.label" in empty_fields_block and "entry.field" not in empty_fields_block,
        "the label is what the user can match against their own spreadsheet",
    )
    check(
        "the warning says the consequence, not just the fact",
        "every row" in empty_fields_block,
        "'no column for Order total' is only half the sentence",
    )

    # The reason this exists at all: "Imported" must not be the whole story.
    result_panel = page_src.split("{result ? (")[-1]
    check(
        "the success panel itself carries the warning",
        "<EmptyFields" in result_panel,
        "a warning shown only before confirmation is missed by anyone who clicks through",
    )


print("\n" + "=" * 74)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print(f"ALL {checks} ERROR-SURFACING CHECKS PASSED")
print("=" * 74)
