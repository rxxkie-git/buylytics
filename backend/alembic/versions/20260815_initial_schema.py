"""initial_schema

Revision ID: 8dd5ad2ce9e4
Revises: 
Create Date: 2026-08-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8dd5ad2ce9e4"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "business_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_business_memberships_business_id"), "business_memberships", ["business_id"], unique=False)
    op.create_index(op.f("ix_business_memberships_user_id"), "business_memberships", ["user_id"], unique=False)

    op.create_table(
        "dataset_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_type", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dataset_imports_business_id"), "dataset_imports", ["business_id"], unique=False)

    op.create_table(
        "customers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("external_customer_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "external_customer_id", name="uq_customer_business_external_id"),
    )
    op.create_index(op.f("ix_customers_business_id"), "customers", ["business_id"], unique=False)
    op.create_index(op.f("ix_customers_external_customer_id"), "customers", ["external_customer_id"], unique=False)

    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("external_product_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "external_product_id", name="uq_product_business_external_id"),
    )
    op.create_index(op.f("ix_products_business_id"), "products", ["business_id"], unique=False)
    op.create_index(op.f("ix_products_external_product_id"), "products", ["external_product_id"], unique=False)

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("external_order_id", sa.String(length=255), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("order_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "external_order_id", name="uq_order_business_external_id"),
    )
    op.create_index(op.f("ix_orders_business_id"), "orders", ["business_id"], unique=False)
    op.create_index(op.f("ix_orders_customer_id"), "orders", ["customer_id"], unique=False)
    op.create_index(op.f("ix_orders_order_date"), "orders", ["order_date"], unique=False)
    op.create_index(op.f("ix_orders_external_order_id"), "orders", ["external_order_id"], unique=False)

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)
    op.create_index(op.f("ix_order_items_product_id"), "order_items", ["product_id"], unique=False)

    op.create_table(
        "inventory",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reorder_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("supplier", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_inventory_business_id"), "inventory", ["business_id"], unique=False)
    op.create_index(op.f("ix_inventory_product_id"), "inventory", ["product_id"], unique=False)

    op.create_table(
        "customer_rfm",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_import_id", sa.String(length=36), nullable=False),
        sa.Column("recency", sa.Float(), nullable=True),
        sa.Column("frequency", sa.Float(), nullable=True),
        sa.Column("monetary", sa.Float(), nullable=True),
        sa.Column("total_orders", sa.Integer(), nullable=True),
        sa.Column("average_order_value", sa.Float(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["dataset_import_id"], ["dataset_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customer_rfm_business_id"), "customer_rfm", ["business_id"], unique=False)
    op.create_index(op.f("ix_customer_rfm_customer_id"), "customer_rfm", ["customer_id"], unique=False)
    op.create_index(op.f("ix_customer_rfm_dataset_import_id"), "customer_rfm", ["dataset_import_id"], unique=False)
    op.create_index(op.f("ix_customer_rfm_calculated_at"), "customer_rfm", ["calculated_at"], unique=False)

    op.create_table(
        "customer_segments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_import_id", sa.String(length=36), nullable=False),
        sa.Column("segment", sa.String(length=100), nullable=True),
        sa.Column("cluster", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["dataset_import_id"], ["dataset_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customer_segments_business_id"), "customer_segments", ["business_id"], unique=False)
    op.create_index(op.f("ix_customer_segments_customer_id"), "customer_segments", ["customer_id"], unique=False)
    op.create_index(op.f("ix_customer_segments_dataset_import_id"), "customer_segments", ["dataset_import_id"], unique=False)
    op.create_index(op.f("ix_customer_segments_calculated_at"), "customer_segments", ["calculated_at"], unique=False)

    op.create_table(
        "analytics_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_import_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["dataset_import_id"], ["dataset_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analytics_runs_business_id"), "analytics_runs", ["business_id"], unique=False)
    op.create_index(op.f("ix_analytics_runs_dataset_import_id"), "analytics_runs", ["dataset_import_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_analytics_runs_dataset_import_id"), table_name="analytics_runs")
    op.drop_index(op.f("ix_analytics_runs_business_id"), table_name="analytics_runs")
    op.drop_table("analytics_runs")

    op.drop_index(op.f("ix_customer_segments_calculated_at"), table_name="customer_segments")
    op.drop_index(op.f("ix_customer_segments_dataset_import_id"), table_name="customer_segments")
    op.drop_index(op.f("ix_customer_segments_customer_id"), table_name="customer_segments")
    op.drop_index(op.f("ix_customer_segments_business_id"), table_name="customer_segments")
    op.drop_table("customer_segments")

    op.drop_index(op.f("ix_customer_rfm_calculated_at"), table_name="customer_rfm")
    op.drop_index(op.f("ix_customer_rfm_dataset_import_id"), table_name="customer_rfm")
    op.drop_index(op.f("ix_customer_rfm_customer_id"), table_name="customer_rfm")
    op.drop_index(op.f("ix_customer_rfm_business_id"), table_name="customer_rfm")
    op.drop_table("customer_rfm")

    op.drop_index(op.f("ix_inventory_product_id"), table_name="inventory")
    op.drop_index(op.f("ix_inventory_business_id"), table_name="inventory")
    op.drop_table("inventory")

    op.drop_index(op.f("ix_order_items_product_id"), table_name="order_items")
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")

    op.drop_index(op.f("ix_orders_external_order_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_order_date"), table_name="orders")
    op.drop_index(op.f("ix_orders_customer_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_business_id"), table_name="orders")
    op.drop_table("orders")

    op.drop_index(op.f("ix_products_external_product_id"), table_name="products")
    op.drop_index(op.f("ix_products_business_id"), table_name="products")
    op.drop_table("products")

    op.drop_index(op.f("ix_customers_external_customer_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_business_id"), table_name="customers")
    op.drop_table("customers")

    op.drop_index(op.f("ix_dataset_imports_business_id"), table_name="dataset_imports")
    op.drop_table("dataset_imports")

    op.drop_index(op.f("ix_business_memberships_user_id"), table_name="business_memberships")
    op.drop_index(op.f("ix_business_memberships_business_id"), table_name="business_memberships")
    op.drop_table("business_memberships")

    op.drop_table("businesses")
