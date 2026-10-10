"""Chunked bulk upsert shared by the four import repositories.

Why this exists
---------------
Every repository had written the same loop:

    for payload in rows:
        existing = self.get_by_external_id(...)   # one SELECT
        if existing: setattr(...)
        else: self.create(...)                    # one INSERT + one flush

which is one database round-trip per row, executed sequentially. Against a
Supabase instance across the internet at ~20 ms of latency, the 38,279-row file
from the bug report costs 38,279 round-trips — roughly a quarter of an hour of
pure waiting, during which (before the offload in ``app.core.concurrency``) the
event loop was held and the entire API was unresponsive.

The work itself was never the problem; the *shape* of it was. Matching rows
against what already exists needs one SELECT per chunk, not one per row, and the
inserts need one flush at the end rather than one each.

Chunked rather than one single statement because a query's bind parameters are
finite: PostgreSQL accepts at most 65,535, so an unbounded ``IN`` clause turns a
large import from slow into a hard failure. ``import_batch_size`` (default 1000)
keeps each statement comfortably inside that and bounds peak memory too.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

#: PostgreSQL's hard ceiling on bind parameters in one statement. The chunk size
#: is validated against it so a misconfigured batch size fails loudly here rather
#: than as an opaque driver error mid-import.
_MAX_BIND_PARAMS = 65_535


def chunked(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Yield consecutive slices of ``items``."""
    step = max(1, size)

    for start in range(0, len(items), step):
        yield items[start : start + step]


def bind_safe_chunk_size() -> int:
    """Rows per statement, clamped to something PostgreSQL will accept.

    Public because it is not only ``upsert_many`` that has to respect the bind
    parameter ceiling — ``AnalyticsRepository.upsert_import_rfm`` builds an ``IN``
    whose width is set by the uploaded file, and that has to be chunked by the same
    number rather than by a second, independently-drifting constant.
    """
    configured = int(getattr(settings, "import_batch_size", 1000) or 1000)
    # Leave room for the business_id parameter and any driver overhead.
    return max(1, min(configured, _MAX_BIND_PARAMS - 10))


def dedupe_by_key(rows: Iterable[dict[str, Any]], key_field: str) -> list[dict[str, Any]]:
    """Collapse rows sharing a key, last occurrence winning.

    A single export listing the same customer twice is ordinary — an edit followed
    by a correction, or two rows for one person. The per-row version inserted the
    first and then found it when handling the second, so it happened to converge.
    Batch inserts do not have that accident available: both rows would be new at
    the point the chunk is written, producing two records for one external id, or a
    unique-constraint violation that fails the whole import.

    Last-wins matches the old behaviour, where the later row overwrote the earlier
    one's fields.
    """
    merged: dict[str, dict[str, Any]] = {}

    for payload in rows:
        merged[str(payload[key_field])] = payload

    return list(merged.values())


def bulk_upsert_by_key(
    session: Session,
    model: type[Any],
    *,
    business_id: str | UUID,
    rows: Iterable[dict[str, Any]],
    key_field: str,
    extra_defaults: dict[str, Any] | None = None,
) -> list[Any]:
    """Upsert ``rows`` matching on ``key_field`` within one business.

    Uses PostgreSQL / SQLite native ``ON CONFLICT DO UPDATE`` to guarantee
    atomicity and prevent unique-constraint violations across concurrent runs or
    re-imports.

    Args:
        session: Active session. Flushed once at the end, never committed — the
            caller owns the transaction boundary.
        model: The ORM class.
        business_id: Tenant scope. Every query is filtered by it, so a key from
            one tenant can never match or update another's row.
        rows: Payload dicts, each containing ``key_field``.
        key_field: Column used to decide update-vs-insert (e.g. ``external_customer_id``).
        extra_defaults: Applied to newly created rows only.

    Returns:
        The affected instances, in deduplicated input order.
    """
    from datetime import datetime, timezone
    from uuid import uuid4
    from sqlalchemy import inspect
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    deduped = dedupe_by_key(rows, key_field)

    if not deduped:
        return []

    try:
        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else "sqlite"
    except Exception:
        dialect = getattr(getattr(session, "bind", None), "dialect", None)
        dialect = getattr(dialect, "name", "sqlite")

    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert

    mapper = inspect(model)
    all_col_keys = {c.key for c in mapper.column_attrs}
    update_col_keys = [
        c.key for c in mapper.column_attrs
        if c.key not in {"id", "business_id", "created_at"}
    ]

    now = datetime.now(timezone.utc)
    b_id = str(business_id)

    # Clamp chunk size based on column count so bind parameter limit (65,535) is never reached
    num_cols = max(1, len(all_col_keys))
    max_chunk = min(bind_safe_chunk_size(), 65000 // num_cols)

    for chunk in chunked(deduped, max_chunk):
        batch_dicts = []
        for payload in chunk:
            d = dict(extra_defaults or {})
            d.update(payload)
            d["business_id"] = b_id
            if "id" not in d or not d["id"]:
                d["id"] = str(uuid4())
            if "created_at" in all_col_keys and "created_at" not in d:
                d["created_at"] = now
            if "updated_at" in all_col_keys and "updated_at" not in d:
                d["updated_at"] = now
            row_dict = {k: v for k, v in d.items() if k in all_col_keys}
            batch_dicts.append(row_dict)

        stmt = insert_fn(model).values(batch_dicts)

        conflict_elements = ["business_id", key_field]
        set_dict = {}
        for col_name in update_col_keys:
            if hasattr(stmt.excluded, col_name):
                set_dict[col_name] = getattr(stmt.excluded, col_name)
        if "updated_at" in all_col_keys and "updated_at" not in set_dict:
            set_dict["updated_at"] = now

        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=conflict_elements,
            set_=set_dict,
        )
        session.execute(upsert_stmt)

    session.flush()

    # Query back the affected rows to return ORM instances in input order
    key_column = getattr(model, key_field)
    keys = [str(payload[key_field]) for payload in deduped]
    results_map: dict[str, Any] = {}
    for chunk in chunked(keys, bind_safe_chunk_size()):
        query = select(model).where(
            model.business_id == b_id,
            key_column.in_(list(chunk)),
        )
        for inst in session.execute(query).scalars().all():
            results_map[str(getattr(inst, key_field))] = inst

    affected = [results_map[k] for k in keys if k in results_map]

    logger.debug(
        "bulk_upsert_completed",
        extra={
            "model": model.__name__,
            "rows_in": len(deduped),
            "rows_affected": len(affected),
            "chunk_size": max_chunk,
        },
    )

    return affected


def fetch_ids_by_external_ids(
    session: Session,
    model: type[Any],
    *,
    business_id: str | UUID,
    external_field: str,
    external_ids: Iterable[str],
) -> dict[str, str]:
    """Map external id -> internal id for one business, in chunks.

    Replaces the per-row ``get_by_external_id`` lookups the order and inventory
    imports used to do while resolving foreign keys.
    """
    unique = list(dict.fromkeys(str(value) for value in external_ids))

    if not unique:
        return {}

    key_column = getattr(model, external_field)
    found: dict[str, str] = {}

    for chunk in chunked(unique, bind_safe_chunk_size()):
        statement = select(key_column, model.id).where(
            model.business_id == str(business_id),
            key_column.in_(list(chunk)),
        )

        for external_value, internal_id in session.execute(statement).all():
            found[str(external_value)] = str(internal_id)

    return found
