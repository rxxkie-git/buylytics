"""Phase 2: event ledger, webhook deliveries, analytics job bookkeeping

Revision ID: b7f4c1e29a3d
Revises: 8dd5ad2ce9e4
Create Date: 2026-08-15

Strictly additive. Nothing is dropped, no table is recreated, and no existing
column changes type. The Phase 1 schema is already live with real data in
Supabase, so this migration is written to be run against a populated database:

* Two new tables: ``processed_events`` (the consumer's idempotency ledger) and
  ``webhook_events`` (the inbound delivery audit trail).
* New nullable columns on the three analytics tables.
* Three ``NOT NULL`` foreign keys relaxed to nullable. Relaxing a constraint can
  never invalidate an existing row, which is why this direction is safe and the
  reverse is not.
* Indexes the models already declare but the initial migration did not create,
  plus two composite indexes on ``orders`` that the RFM aggregation needs.

Every step is guarded by an inspector check, so the migration is idempotent: it
can be re-run after a partial failure, and it will not collide with a schema that
was bootstrapped with ``Base.metadata.create_all`` instead of Alembic.

The ``downgrade`` drops only what this revision created. It deliberately does
**not** restore the ``NOT NULL`` constraints on ``dataset_import_id`` — by then
rows written by the order-aggregated RFM path legitimately have no import to
point at, and forcing the constraint back would either fail or require deleting
real analytics results.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7f4c1e29a3d"
down_revision = "8dd5ad2ce9e4"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _dialect() -> str:
    return op.get_bind().dialect.name


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False

    return column in {col["name"] for col in _inspector().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False

    return name in {index["name"] for index in _inspector().get_indexes(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _create_index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    """Create an index unless one of that name already exists.

    The name is wrapped in ``op.f`` so it is treated as final. Today
    ``Base.metadata`` has no naming convention, so this is a no-op — but if one is
    ever added, an unwrapped name would be rewritten by it and would no longer
    match the string ``_has_index`` looks for, silently breaking the guard.
    ``op.f`` also matches the style of the initial migration.
    """
    if not _has_index(table, name):
        op.create_index(op.f(name), table, columns, unique=unique)


def _drop_index(name: str, table: str) -> None:
    if _has_index(table, name):
        op.drop_index(op.f(name), table_name=table)


def _make_nullable(table: str, column: str, type_: sa.types.TypeEngine) -> None:
    """Relax a NOT NULL foreign key.

    SQLite cannot alter a column in place, so batch mode recreates the table
    there. On PostgreSQL ``batch_alter_table`` emits a plain ``ALTER COLUMN``,
    which is a metadata-only change and does not rewrite the table.
    """
    if not _has_column(table, column):
        return

    current = next(col for col in _inspector().get_columns(table) if col["name"] == column)

    if current.get("nullable"):
        return

    if _dialect() == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=type_, nullable=True)
    else:
        op.alter_column(table, column, existing_type=type_, nullable=True)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    _create_processed_events()
    _create_webhook_events()
    _extend_customer_rfm()
    _extend_customer_segments()
    _extend_analytics_runs()
    _add_order_aggregation_indexes()


def _create_processed_events() -> None:
    """Idempotency ledger for consumed events.

    ``event_id`` is the primary key so uniqueness is enforced by PostgreSQL. Two
    workers racing on the same redelivered message cannot both win the insert,
    which is what turns Kafka's at-least-once delivery into effectively-once
    processing.

    No foreign key to ``businesses``: this is operational bookkeeping, and a
    referential-integrity error here must never be able to stall the consumer.
    """
    if _has_table("processed_events"):
        return

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="processing"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )
    _create_index("ix_processed_events_event_type", "processed_events", ["event_type"])
    _create_index("ix_processed_events_business_id", "processed_events", ["business_id"])
    _create_index("ix_processed_events_created_at", "processed_events", ["created_at"])
    # Serves the "what failed for this tenant" query the ops UI needs.
    _create_index(
        "ix_processed_events_business_status", "processed_events", ["business_id", "status"]
    )


def _create_webhook_events() -> None:
    """Audit trail for inbound webhook deliveries.

    The unique constraint is the whole replay-protection mechanism. It includes
    ``business_id`` so two tenants whose upstream systems happen to use the same
    delivery id do not collide, and so one tenant can never suppress another's
    event by sending it first.

    ``payload`` stores the verified request body. Signature and authorization
    headers are never persisted — only the boolean outcome of verification.
    """
    if _has_table("webhook_events"):
        return

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column(
            "signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="received"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "source",
            "external_event_id",
            name="uq_webhook_business_source_event",
        ),
    )
    _create_index("ix_webhook_events_business_id", "webhook_events", ["business_id"])
    _create_index("ix_webhook_events_source", "webhook_events", ["source"])
    _create_index("ix_webhook_events_event_type", "webhook_events", ["event_type"])
    _create_index("ix_webhook_events_external_event_id", "webhook_events", ["external_event_id"])
    _create_index("ix_webhook_events_received_at", "webhook_events", ["received_at"])
    # The delivery list is always "this tenant, newest first".
    _create_index(
        "ix_webhook_events_business_received", "webhook_events", ["business_id", "received_at"]
    )


def _extend_customer_rfm() -> None:
    """Quintile scores, provenance and a run reference.

    ``source`` records whether the numbers were aggregated from orders or passed
    through from genuine columns in an uploaded file. Keeping it means the UI can
    say where a figure came from instead of implying every number is equally
    well-founded.
    """
    _make_nullable("customer_rfm", "dataset_import_id", sa.String(length=36))

    _add_column("customer_rfm", sa.Column("r_score", sa.Integer(), nullable=True))
    _add_column("customer_rfm", sa.Column("f_score", sa.Integer(), nullable=True))
    _add_column("customer_rfm", sa.Column("m_score", sa.Integer(), nullable=True))
    _add_column("customer_rfm", sa.Column("rfm_segment", sa.String(length=100), nullable=True))
    _add_column("customer_rfm", sa.Column("source", sa.String(length=20), nullable=True))
    _add_column("customer_rfm", sa.Column("analytics_run_id", sa.String(length=36), nullable=True))

    _create_index("ix_customer_rfm_rfm_segment", "customer_rfm", ["rfm_segment"])
    _create_index("ix_customer_rfm_analytics_run_id", "customer_rfm", ["analytics_run_id"])
    # Every read of this table is scoped to one tenant and one customer.
    _create_index(
        "ix_customer_rfm_business_customer", "customer_rfm", ["business_id", "customer_id"]
    )


def _extend_customer_segments() -> None:
    """Churn risk, model version and a run reference.

    ``model_version`` is what makes a segmentation result auditable: without it,
    two rows written by different model versions are indistinguishable and a
    regression is impossible to attribute.
    """
    _make_nullable("customer_segments", "dataset_import_id", sa.String(length=36))

    _add_column("customer_segments", sa.Column("churn_risk", sa.String(length=20), nullable=True))
    _add_column(
        "customer_segments", sa.Column("model_version", sa.String(length=50), nullable=True)
    )
    _add_column(
        "customer_segments", sa.Column("analytics_run_id", sa.String(length=36), nullable=True)
    )

    # Declared on the model but missing from the initial migration.
    _create_index("ix_customer_segments_segment", "customer_segments", ["segment"])
    _create_index(
        "ix_customer_segments_analytics_run_id", "customer_segments", ["analytics_run_id"]
    )
    _create_index(
        "ix_customer_segments_business_customer",
        "customer_segments",
        ["business_id", "customer_id"],
    )


def _extend_analytics_runs() -> None:
    """Job bookkeeping for the asynchronous analytics lifecycle.

    ``created_at`` is ``NOT NULL``, so it is added with a server default and then
    backfilled from ``started_at``. Phase 1 set ``started_at`` at row creation, so
    that value is the truthful creation time for existing rows — better than
    stamping every historical run with the moment this migration ran.
    """
    _make_nullable("analytics_runs", "dataset_import_id", sa.String(length=36))

    created_at_is_new = not _has_column("analytics_runs", "created_at")

    _add_column(
        "analytics_runs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    _add_column("analytics_runs", sa.Column("requested_by", sa.String(length=255), nullable=True))
    _add_column("analytics_runs", sa.Column("event_id", sa.String(length=64), nullable=True))
    _add_column("analytics_runs", sa.Column("result_metadata", sa.JSON(), nullable=True))

    if created_at_is_new:
        op.execute(
            "UPDATE analytics_runs SET created_at = started_at WHERE started_at IS NOT NULL"
        )

    # Declared on the model but missing from the initial migration.
    _create_index("ix_analytics_runs_job_type", "analytics_runs", ["job_type"])
    _create_index("ix_analytics_runs_status", "analytics_runs", ["status"])
    _create_index("ix_analytics_runs_created_at", "analytics_runs", ["created_at"])
    _create_index("ix_analytics_runs_event_id", "analytics_runs", ["event_id"])
    # The jobs page lists one tenant's runs, newest first, optionally by type.
    _create_index(
        "ix_analytics_runs_business_created", "analytics_runs", ["business_id", "created_at"]
    )
    _create_index(
        "ix_analytics_runs_business_type_status",
        "analytics_runs",
        ["business_id", "job_type", "status"],
    )


def _add_order_aggregation_indexes() -> None:
    """Composite indexes for the RFM aggregation.

    RFM groups a tenant's orders by customer and takes max(order_date),
    count(*) and sum(total_amount). The single-column indexes from Phase 1 make
    PostgreSQL choose between filtering by tenant and grouping by customer;
    these composites let it do both from one index.
    """
    _create_index("ix_orders_business_customer", "orders", ["business_id", "customer_id"])
    _create_index("ix_orders_business_order_date", "orders", ["business_id", "order_date"])


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    _drop_index("ix_orders_business_order_date", "orders")
    _drop_index("ix_orders_business_customer", "orders")

    for name in (
        "ix_analytics_runs_business_type_status",
        "ix_analytics_runs_business_created",
        "ix_analytics_runs_event_id",
        "ix_analytics_runs_created_at",
        "ix_analytics_runs_status",
        "ix_analytics_runs_job_type",
    ):
        _drop_index(name, "analytics_runs")

    for column in ("result_metadata", "event_id", "requested_by", "created_at"):
        if _has_column("analytics_runs", column):
            op.drop_column("analytics_runs", column)

    for name in (
        "ix_customer_segments_business_customer",
        "ix_customer_segments_analytics_run_id",
        "ix_customer_segments_segment",
    ):
        _drop_index(name, "customer_segments")

    for column in ("analytics_run_id", "model_version", "churn_risk"):
        if _has_column("customer_segments", column):
            op.drop_column("customer_segments", column)

    for name in (
        "ix_customer_rfm_business_customer",
        "ix_customer_rfm_analytics_run_id",
        "ix_customer_rfm_rfm_segment",
    ):
        _drop_index(name, "customer_rfm")

    for column in ("analytics_run_id", "source", "rfm_segment", "m_score", "f_score", "r_score"):
        if _has_column("customer_rfm", column):
            op.drop_column("customer_rfm", column)

    if _has_table("webhook_events"):
        op.drop_table("webhook_events")

    if _has_table("processed_events"):
        op.drop_table("processed_events")

    # dataset_import_id is intentionally left nullable. See the module docstring.
