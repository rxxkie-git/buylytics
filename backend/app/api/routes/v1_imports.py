"""Import/upload API routes for v1.

Each endpoint spools the upload to a temporary file, hands it to the
orchestrator, and deletes the temp file on the way out — including when the
import fails.

Source column names do not have to match the internal canonical fields.
:mod:`app.ingestion.column_mapping` renames common real-world headers
(``order_id``, ``Order ID``, ``customer number``) onto them before validation, so
a Shopify or POS export imports without being edited first. ``POST
/{dataset}/preview`` returns that mapping without writing anything, which is what
the UI shows before the user confirms.

The tenant always comes from the authenticated business context. Nothing here
reads a business id from the request.

Why the handlers stay ``async def`` while doing no sync work themselves
----------------------------------------------------------------------
These four handlers must be ``async def`` because they ``await`` the upload
stream before anything else can happen. That makes them run on the event loop —
so every synchronous thing they used to do (parse a 40 MB CSV with pandas, run
tens of thousands of SQLAlchemy round-trips) froze the entire process, not just
this request. ``/health`` returning nothing but a literal dict went unresponsive
during an import, which is the unmistakable signature of a blocked loop.

The fix is that all of it now goes through :func:`run_import_blocking`: the
synchronous body runs in a worker thread, bounded by a concurrency limiter and a
wall-clock budget, and the loop stays free to serve everything else.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.config import settings
from app.core.concurrency import (
    BlockingWorkBusy,
    BlockingWorkTimeout,
    run_import_blocking,
)
from app.core.logging import get_logger, logging_context
from app.core.multi_tenancy import BusinessContext, get_business_context
from app.events import EventType, make_event, topics
from app.events.producer import publish_event
from app.ingestion.column_mapping import (
    ColumnMappingError,
    apply_mapping,
    expected_columns,
    mapping_summary,
)
from app.ingestion.file_parser import FileParser
from app.ingestion.importer import ImportOrchestrator

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

#: Uploads larger than this are refused before anything is written to disk. An
#: unbounded upload is a way to fill the container's filesystem.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

ALLOWED_SUFFIXES = {".csv", ".tsv", ".xls", ".xlsx"}

#: URL segment -> dataset key used by the mapping tables.
PREVIEW_DATASETS = {
    "orders": "order",
    "customers": "customer",
    "products": "product",
    "inventory": "inventory",
}

#: Rows returned by the preview. Enough to see that the mapping is right, small
#: enough that previewing a 40 MB export is not itself a payload problem.
PREVIEW_ROWS = 5


async def _spool_upload(file: UploadFile) -> Path:
    """Write an upload to a temporary file and return its path.

    Read in chunks rather than with a single ``await file.read()``: the whole
    point of the size limit is to avoid holding an arbitrarily large body in
    memory, and reading it all at once to measure it defeats that.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    suffix = Path(file.filename).suffix.lower()

    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or 'none'}'. Allowed: {allowed}",
        )

    written = 0
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = Path(handle.name)

    try:
        with handle:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)

                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                    )

                handle.write(chunk)
    except BaseException:
        # The temp file exists by this point, so it has to be removed here —
        # the caller never learns its name when this raises.
        tmp_path.unlink(missing_ok=True)
        raise

    if written == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    return tmp_path


def _source_type(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    return suffix[1:] if suffix else "file"


def _overload_error(exc: BlockingWorkBusy, dataset: str) -> HTTPException:
    """503 for work that never started, with ``Retry-After``.

    Deliberately distinct from the 504 below. This request did no work at all, so
    retrying immediately is safe and correct — whereas a request that timed out
    mid-import may still have its database work land, and telling the client to
    retry that would risk double-importing.
    """
    logger.warning(
        "import_rejected_at_capacity",
        extra={"dataset": dataset, "operation": exc.operation},
    )
    return HTTPException(
        status_code=503,
        detail=(
            "The server is already processing its maximum number of imports. "
            "Please retry in a few seconds."
        ),
        headers={"Retry-After": "10"},
    )


def _timeout_error(exc: BlockingWorkTimeout, dataset: str) -> HTTPException:
    """504 naming the budget, instead of letting nginx invent one.

    An honest message: the work may still be running. A Python-side timeout frees
    the request but cannot kill the thread (see ``app.core.concurrency``), so the
    wording avoids promising the import did not happen.
    """
    logger.error(
        "import_failed",
        extra={
            "dataset": dataset,
            "reason": "timeout",
            "operation": exc.operation,
            "timeout_seconds": exc.timeout_seconds,
        },
    )
    return HTTPException(
        status_code=504,
        detail=(
            f"This file took longer than {exc.timeout_seconds:.0f}s to process and the "
            "request was released. Processing may still be completing in the "
            "background — check the import history before retrying, and consider "
            "splitting the file."
        ),
    )


def _pool_exhausted_error(dataset: str, reason: str) -> HTTPException:
    """503 for a database connection that could never be obtained.

    ``sqlalchemy.exc.TimeoutError`` means the pool had no free connection within
    ``DB_POOL_TIMEOUT_SECONDS``. That is not a 500 — nothing is wrong with the
    request, and the same request will succeed once the pool drains — and it must
    not be a hang, which is what it was before ``pool_timeout`` was configured.

    Distinguished from :func:`_overload_error` because the remedy differs: that
    one means *this process* is at its import limit, this one means the *database*
    is out of connections, which is usually another client's fault.
    """
    logger.error(
        "import_failed",
        extra={"dataset": dataset, "reason": "db_pool_exhausted", "detail": reason},
    )
    return HTTPException(
        status_code=503,
        detail=(
            "No database connection was available within the pool timeout, so this "
            "import was not started. Retry shortly."
        ),
        headers={"Retry-After": "15"},
    )


#: Import outcome -> the event the rest of the platform listens for.
#:
#: Publishing this is what makes an import visible beyond its own HTTP response:
#: :func:`app.events.handlers.import_handlers.refresh_after_import` drops the
#: tenant's cached reads and queues a full analytics refresh for customer and
#: order data. Without it the dashboard keeps serving pre-import cache entries and
#: RFM never recomputes, so an import that wrote every row still looks like it did
#: nothing. The handler existed and documented a payload contract "produced by the
#: import routes"; the routes never produced it.
_OUTCOME_EVENTS = {
    "completed": EventType.IMPORT_COMPLETED,
    "partial": EventType.IMPORT_COMPLETED,
    "failed": EventType.IMPORT_FAILED,
}


async def _publish_import_outcome(
    *,
    business_id: str,
    dataset: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Announce the import on the event bus. Never raises, always bounded.

    Awaited inside the request rather than fired at a background task, on purpose.
    ``publish_event`` is bounded by ``KAFKA_PUBLISH_TIMEOUT_SECONDS`` and has a
    circuit breaker, so the worst case is a known number of seconds — while a
    background task's failure would be swallowed with nothing to report, which
    is one of the specific hazards this audit was asked to remove.

    The return value goes into the HTTP response so the client can tell "imported
    and analytics queued" from "imported, and the event bus is down so the
    dashboard may lag". Reporting that honestly is the point; silently degrading
    is what made the platform hard to trust.
    """
    status = str(result.get("status") or "failed").lower()
    event_type = _OUTCOME_EVENTS.get(status, EventType.IMPORT_COMPLETED)

    payload: dict[str, Any] = {
        "import_id": result.get("import_id"),
        "dataset_type": dataset,
        "rows_total": result.get("row_count"),
        "rows_imported": result.get("rows_processed"),
        "rows_failed": result.get("rows_failed"),
    }

    if event_type == EventType.IMPORT_FAILED:
        # A short reason, not the row that failed and not the filename. Either can
        # carry customer data, and this payload is written to a Kafka topic.
        payload["error"] = str(result.get("error") or "import_reported_failure")[:200]

    logger.info(
        "import_event_publish_started",
        extra={
            "import_id": result.get("import_id"),
            "dataset": dataset,
            "event_type": event_type,
            "topic": topics.RAW_IMPORTS,
        },
    )

    started = time.perf_counter()

    try:
        outcome = await publish_event(
            topics.RAW_IMPORTS,
            make_event(event_type=event_type, business_id=business_id, payload=payload),
        )
        delivered = bool(outcome.delivered)
        transport = outcome.transport
        error = outcome.error
    except Exception as exc:  # pragma: no cover - publish_event already swallows
        # Reachable only through the inline transport, where a *handler* runs on
        # this task. A broken handler must not turn a successful import into a
        # 500: the rows are already committed and the client needs to know that.
        delivered = False
        transport = "unknown"
        error = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "import_event_publish_failed",
            extra={"import_id": result.get("import_id"), "dataset": dataset},
        )

    logger.info(
        "import_event_publish_completed",
        extra={
            "import_id": result.get("import_id"),
            "dataset": dataset,
            "event_type": event_type,
            "delivered": delivered,
            "transport": transport,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            # A transport error string, never the payload.
            "publish_error": error,
        },
    )

    return {"published": delivered, "transport": transport}


async def _run_import(
    file: UploadFile,
    business_context: BusinessContext,
    method_name: str,
    dataset: str,
) -> dict[str, Any]:
    """Spool, import in a worker thread, clean up.

    ``tmp_path`` is bound before the ``try`` block. In the Phase 1 version it was
    assigned inside it, so a failure during spooling left the name unbound and the
    ``finally`` raised ``UnboundLocalError`` — replacing the real error with a
    confusing one.

    The orchestrator call is synchronous and always was; what changed is that it
    no longer runs on the event loop. ``run_import_blocking`` puts it in a thread
    with a concurrency limit, so several large imports cannot between them consume
    every thread and every database connection in the process.
    """
    tmp_path = await _spool_upload(file)
    size_bytes = tmp_path.stat().st_size
    started = time.perf_counter()

    def _do_import() -> dict[str, Any]:
        orchestrator = ImportOrchestrator(business_context.session)
        importer = getattr(orchestrator, method_name)

        return importer(
            business_id=business_context.business_id,
            file_path=tmp_path,
            source_type=_source_type(file),
        )

    # business_id is bound for the whole operation, so every log line emitted by
    # the parser, validator and orchestrator underneath carries it without each
    # of them having to be passed the context. anyio copies the contextvars into
    # the worker thread, so this survives the hop off the loop.
    with logging_context(business_id=business_context.business_id):
        logger.info(
            "import_started",
            extra={
                "dataset": dataset,
                # Size and type only. The filename can carry a customer or company
                # name, and none of the file's contents belong in a log line.
                "file_bytes": size_bytes,
                "source_type": _source_type(file),
            },
        )

        try:
            result = await run_import_blocking(
                _do_import,
                operation=f"import.{dataset}",
                timeout_seconds=settings.import_timeout_seconds,
            )
        except BlockingWorkBusy as exc:
            raise _overload_error(exc, dataset) from None
        except BlockingWorkTimeout as exc:
            raise _timeout_error(exc, dataset) from None
        except SQLAlchemyTimeoutError as exc:
            # The pool had no connection to give within DB_POOL_TIMEOUT_SECONDS.
            # Mapped explicitly because the default handler would call it a 500,
            # and a caller cannot tell from a 500 that retrying is the right move.
            raise _pool_exhausted_error(dataset, str(exc)) from None
        except ValueError as exc:
            # A file that cannot be read or mapped at all. The orchestrator raises
            # this for unreadable input; it is the user's file, not a server fault.
            logger.warning(
                "import_failed",
                extra={"dataset": dataset, "reason": "unreadable"},
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        duration_ms = round((time.perf_counter() - started) * 1000, 1)

        # Published after the import has committed and before the response is
        # returned. Ordering matters: a consumer woken by this event reads the
        # tenant's tables, and it must not be able to read them before the rows
        # are there.
        dispatch = await _publish_import_outcome(
            business_id=business_context.business_id,
            dataset=dataset,
            result=result,
        )

        if result.get("status") == "failed":
            logger.warning(
                "import_failed",
                extra={
                    "import_id": result.get("import_id"),
                    "dataset": dataset,
                    "reason": "orchestrator_reported_failure",
                    "duration_ms": duration_ms,
                },
            )
        else:
            logger.info(
                "import_completed",
                extra={
                    "import_id": result.get("import_id"),
                    "dataset": dataset,
                    "rows_processed": result.get("rows_processed"),
                    "rows_failed": result.get("rows_failed"),
                    "duration_ms": duration_ms,
                },
            )

        # Merged rather than overwritten: the orchestrator owns the import result,
        # this only adds what happened to the announcement.
        result.setdefault("events", {}).update(dispatch)

        return result


def _build_preview(tmp_path: Path, dataset: str, key: str) -> dict[str, Any]:
    """The preview's synchronous body. Runs in a worker thread; writes nothing.

    Reads at most ``PREVIEW_ROWS`` rows. This is the change that made previewing a
    large export possible at all: it used to parse the entire file to populate
    ``row_count`` and then discard all but five rows, so a 200,000-row upload paid
    the full parse cost — measured at ~170x the bounded read — to display a
    five-row table.

    ``row_count`` now comes from :meth:`FileParser.count_data_rows`, which counts
    newline bytes instead of building a DataFrame. It returns ``None`` when it
    cannot know cheaply, and that is passed through as ``None`` rather than being
    flattened to ``0`` — a confident zero on a file full of rows is worse than an
    admitted unknown.

    Creates no ``DatasetImport`` row, runs no analytics and publishes no events, so
    a user can re-check a mapping as often as they like.
    """
    outcome = FileParser.parse_file_detailed(tmp_path, max_rows=PREVIEW_ROWS)
    frame = outcome.frame
    available = [str(column) for column in frame.columns]

    try:
        mapped, resolved = apply_mapping(frame, key)
    except ColumnMappingError as mapping_error:
        # Handled in here, not in the caller, because the caller only sees whatever
        # this thread returns or raises — and this is a normal, expected answer
        # ("your columns don't match"), not a server error.
        return {
            "dataset": dataset,
            "status": "unmappable",
            "row_count": FileParser.count_data_rows(tmp_path),
            "expected_columns": expected_columns(key),
            **mapping_error.to_payload(),
        }

    # Stringified so a Timestamp or NaN cannot break JSON encoding on a page
    # whose only job is to show the user what their own file looks like.
    sample = [
        {name: ("" if value is None or value != value else str(value)) for name, value in row.items()}
        for row in mapped.head(PREVIEW_ROWS).to_dict(orient="records")
    ]

    return {
        "dataset": dataset,
        "status": "ready",
        "row_count": FileParser.count_data_rows(tmp_path),
        "available_columns": available,
        "expected_columns": expected_columns(key),
        "sample_rows": sample,
        "malformed_rows_skipped": outcome.malformed_rows_skipped,
        **mapping_summary(resolved),
    }


@router.post("/{dataset}/preview")
async def preview_columns(
    dataset: str,
    file: UploadFile = File(...),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Show how a file's columns map onto the canonical fields. Writes nothing.

    This is the "CSV column -> Application field" table the UI displays before
    the user commits. It reads the header row and at most ``PREVIEW_ROWS`` rows,
    creates no ``DatasetImport`` record and touches no tenant data.

    A file whose required fields cannot be identified comes back as
    ``status: "unmappable"`` with the same structured detail the real import
    would return — the point is to find that out before uploading 40 MB.
    """
    key = PREVIEW_DATASETS.get(dataset)

    if key is None:
        allowed = ", ".join(sorted(PREVIEW_DATASETS))
        raise HTTPException(status_code=404, detail=f"Unknown dataset '{dataset}'. Allowed: {allowed}")

    tmp_path = await _spool_upload(file)

    with logging_context(business_id=business_context.business_id):
        logger.info(
            "import_validation_started",
            extra={
                "dataset": dataset,
                "stage": "preview",
                "file_bytes": tmp_path.stat().st_size,
            },
        )

        try:
            result = await run_import_blocking(
                _build_preview,
                tmp_path,
                dataset,
                key,
                operation=f"preview.{dataset}",
                timeout_seconds=settings.preview_timeout_seconds,
            )
        except BlockingWorkBusy as exc:
            raise _overload_error(exc, dataset) from None
        except BlockingWorkTimeout as exc:
            raise _timeout_error(exc, dataset) from None
        except HTTPException:
            raise
        except Exception as caught:
            # A file pandas cannot read at all: corrupt, wrong extension, one column
            # of binary. The user needs to know it was unreadable, not that a column
            # was missing. 422 rather than 500 — the request was well-formed, the
            # file it carried was not.
            logger.warning(
                "import_validation_failed",
                extra={"dataset": dataset, "error_type": type(caught).__name__},
            )
            raise HTTPException(status_code=422, detail=f"Could not read the file: {caught}") from caught
        finally:
            # Safe even for an abandoned thread on timeout: on Linux, unlinking a
            # file another thread already opened leaves that thread's descriptor
            # valid, and if it had not opened it yet it fails fast — which is the
            # outcome we want for orphaned work.
            tmp_path.unlink(missing_ok=True)

        logger.info(
            "import_validation_completed",
            extra={
                "dataset": dataset,
                "stage": "preview",
                "preview_status": result.get("status"),
                "row_count": result.get("row_count"),
            },
        )

        return result


@router.post("/customers", status_code=201)
async def upload_customers(
    file: UploadFile = File(...),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Upload customer data from a file (CSV, TSV, XLS, XLSX).

    If the file carries genuine ``recency``, ``frequency`` and ``monetary``
    columns those values are kept and reported as ``rfm_rows_from_import``. They
    are stored as passed-through data and are only used for customers who have no
    measured orders.
    """
    return await _run_import(file, business_context, "import_customers", "customers")


@router.post("/products", status_code=201)
async def upload_products(
    file: UploadFile = File(...),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Upload product data from a file (CSV, TSV, XLS, XLSX)."""
    return await _run_import(file, business_context, "import_products", "products")


@router.post("/inventory", status_code=201)
async def upload_inventory(
    file: UploadFile = File(...),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Upload inventory data from a file (CSV, TSV, XLS, XLSX)."""
    return await _run_import(file, business_context, "import_inventory", "inventory")


@router.post("/orders", status_code=201)
async def upload_orders(
    file: UploadFile = File(...),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Upload order data from a file (CSV, TSV, XLS, XLSX).

    This is the import that makes RFM real: recency, frequency and monetary are
    measured from these rows rather than taken on trust from a spreadsheet column.

    Rows must supply an order id and a customer id, but not under those exact
    names: ``order_id``, ``Order ID``, ``order number`` and ``external_order_id``
    are all accepted, and the mapping actually used comes back as
    ``column_mapping``. When a required field matches no column the response is
    ``status: "failed"`` naming the field and listing the file's own columns,
    rather than a completed import of zero rows.

    A referenced customer who is not on file yet is created carrying only that
    external id, and the count is returned as ``customers_created`` — dropping the
    order instead would understate that customer's value permanently.

    The raw ORM rows are not returned. That used to be enforced here, by popping
    the row list off the payload on this route alone — which is exactly why this
    endpoint worked in the UI and the other three returned a 500 after a successful
    commit. The guarantee now lives in ``ImportOrchestrator``, which returns counts
    for every dataset, so no route has to remember to strip anything.
    """
    return await _run_import(file, business_context, "import_orders", "orders")

