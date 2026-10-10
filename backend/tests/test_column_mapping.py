"""Column mapping: real-world headers onto the canonical import fields.

Runs under pytest and under ``tests/run_all.py``. Needs pandas but no database,
no FastAPI and no network, because the whole point of keeping
:mod:`app.ingestion.column_mapping` free of SQLAlchemy is that it can be tested
like this.

The case that motivated all of it is :func:`test_alternate_headers_no_longer_skip_every_row`:
before the mapping layer, an orders export whose id column was called ``order_id``
imported as "0 rows processed, N skipped", because ``DataValidator`` subtracts the
row's non-null columns from the required set and that subtraction fails identically
for every row when the column is simply absent. That is a header problem reported
as a data problem, and it is worth a test that fails loudly if it ever returns.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion.column_mapping import (  # noqa: E402
    ColumnMappingError,
    apply_mapping,
    expected_columns,
    mapping_summary,
    normalize_header,
    resolve_columns,
)
from app.ingestion.file_parser import DataValidator  # noqa: E402


# ---------------------------------------------------------------------------
# Header normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("order_id", "order_id"),
        ("Order ID", "order_id"),
        ("  ORDER  ID  ", "order_id"),
        ("order-id", "order_id"),
        ("Order.Id", "order_id"),
        ("Order #", "order"),
        ("total amount", "total_amount"),
        ("", ""),
        (None, ""),
        # camelCase, where case itself is the word separator. Lowercasing first
        # collapses `itemName` to `itemname`, which matches no alias, so the
        # boundary has to be inserted before the case is thrown away.
        # `dataset/untitled folder/inventory_master_dataset.csv` is headed
        # `id,itemName,category,quantity,price,supplier` and imported with a NULL
        # product name until this worked.
        ("itemName", "item_name"),
        ("orderId", "order_id"),
        ("NumWebPurchases", "num_web_purchases"),
        ("customerID", "customer_id"),
        # An acronym keeps its integrity: split before the last capital only when a
        # lowercase letter follows it, so `SKUCode` becomes two words while a header
        # that is nothing but an acronym stays one.
        ("SKU", "sku"),
        ("SKUCode", "sku_code"),
        ("HTTPResponseCode", "http_response_code"),
        # Digits are word characters, not separators: a version or size suffix must
        # not be torn off the name it belongs to.
        ("item2Name", "item2_name"),
        ("Price2024", "price2024"),
    ],
)
def test_normalize_header(raw, expected):
    assert normalize_header(raw) == expected


# ---------------------------------------------------------------------------
# Canonical files must be completely unaffected
# ---------------------------------------------------------------------------


def test_canonical_headers_map_to_themselves():
    """The layer is a no-op for files that already use the internal names."""
    headers = ["external_order_id", "external_customer_id", "order_date", "total_amount", "status"]
    resolved = resolve_columns(headers, "order")

    assert resolved.is_usable
    assert resolved.mapping == {column: column for column in headers}
    assert resolved.unmapped == []


def test_canonical_name_beats_a_friendly_alias():
    """With both present the canonical column wins, deterministically."""
    resolved = resolve_columns(["order_id", "external_order_id", "customer_id"], "order")

    assert resolved.mapping["external_order_id"] == "external_order_id"
    assert "order_id" in resolved.unmapped


# ---------------------------------------------------------------------------
# Common alternate headers
# ---------------------------------------------------------------------------


def test_plain_business_csv():
    """The first example from the brief: order_id,customer_id,order_date,total_amount,status."""
    resolved = resolve_columns(
        ["order_id", "customer_id", "order_date", "total_amount", "status"], "order"
    )

    assert resolved.is_usable
    assert resolved.mapping == {
        "external_order_id": "order_id",
        "external_customer_id": "customer_id",
        "order_date": "order_date",
        "total_amount": "total_amount",
        "status": "status",
    }


def test_capitalisation_and_spacing():
    """The second example: Order ID,Customer ID,Date,Amount,Status."""
    resolved = resolve_columns(["Order ID", "Customer ID", "Date", "Amount", "Status"], "order")

    assert resolved.is_usable
    assert resolved.mapping["external_order_id"] == "Order ID"
    assert resolved.mapping["external_customer_id"] == "Customer ID"
    assert resolved.mapping["order_date"] == "Date"
    assert resolved.mapping["total_amount"] == "Amount"


def test_platform_export_headers():
    """Headers of the shape a hosted store exports, plus a column we ignore."""
    resolved = resolve_columns(
        ["Order Number", "Buyer ID", "Created At", "Grand Total", "Financial Status", "Notes"],
        "order",
    )

    assert resolved.is_usable
    assert resolved.mapping["external_order_id"] == "Order Number"
    assert resolved.mapping["external_customer_id"] == "Buyer ID"
    assert resolved.mapping["order_date"] == "Created At"
    assert resolved.unmapped == ["Notes"]


def test_products_keyed_only_by_sku():
    """One column may satisfy two fields rather than leaving the id unmapped."""
    resolved = resolve_columns(["SKU", "Product Name", "Category", "Unit Price"], "product")

    assert resolved.is_usable
    assert resolved.mapping["external_product_id"] == "SKU"
    assert resolved.mapping["sku"] == "SKU"


def test_inventory_alternate_headers():
    resolved = resolve_columns(["Item Code", "Qty On Hand", "Reorder Point", "Vendor"], "inventory")

    assert resolved.is_usable
    assert resolved.mapping["external_product_id"] == "Item Code"
    assert resolved.mapping["quantity"] == "Qty On Hand"
    assert resolved.mapping["reorder_level"] == "Reorder Point"
    assert resolved.mapping["supplier"] == "Vendor"
    assert resolved.unmapped == []


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_missing_required_field_is_named():
    resolved = resolve_columns(["order_id", "order_date", "total_amount"], "order")

    assert not resolved.is_usable
    assert resolved.missing_required == ["external_customer_id"]


def test_ambiguous_headers_are_refused_not_guessed():
    """Two headers with the same meaning produce a report, not a coin flip."""
    resolved = resolve_columns(["Order ID", "order id", "customer_id"], "order")

    assert not resolved.is_usable
    assert resolved.ambiguous == {"external_order_id": ["Order ID", "order id"]}
    assert "external_order_id" not in resolved.mapping


def test_error_payload_is_actionable():
    """The message has to name the field, the file's columns and a way out."""
    with pytest.raises(ColumnMappingError) as caught:
        apply_mapping(pd.DataFrame([{"foo": 1, "bar": 2}]), "order")

    error = caught.value
    payload = error.to_payload()

    assert payload["error_code"] == "column_mapping_failed"
    assert payload["available_columns"] == ["foo", "bar"]

    missing = {entry["field"] for entry in payload["missing_fields"]}
    assert missing == {"external_order_id", "external_customer_id"}

    # An error that says "missing external_order_id" and stops there sends the
    # user back to the same file with nothing to change.
    for entry in payload["missing_fields"]:
        assert "order_id" in entry["accepted_columns"] or "customer_id" in entry["accepted_columns"]

    assert "foo" in str(error)


def test_ambiguity_reaches_the_payload():
    with pytest.raises(ColumnMappingError) as caught:
        apply_mapping(pd.DataFrame([{"Order ID": 1, "order id": 1, "customer_id": "C1"}]), "order")

    payload = caught.value.to_payload()
    assert payload["ambiguous_fields"] == [
        {"field": "external_order_id", "label": "Order ID", "columns": ["Order ID", "order id"]}
    ]


def test_unknown_dataset_is_rejected():
    with pytest.raises(ValueError):
        resolve_columns(["a"], "not_a_dataset")


# ---------------------------------------------------------------------------
# apply_mapping
# ---------------------------------------------------------------------------


def test_apply_mapping_renames_and_keeps_every_row():
    df = pd.DataFrame(
        [
            {"Order ID": "10001", "Customer ID": "C001", "Date": "2026-01-05", "Amount": 1200.50, "Status": "completed"},
            {"Order ID": "10002", "Customer ID": "C002", "Date": "2026-01-06", "Amount": 80.00, "Status": "completed"},
        ]
    )

    mapped, resolved = apply_mapping(df, "order")

    assert list(mapped.columns) == [
        "external_order_id",
        "external_customer_id",
        "order_date",
        "total_amount",
        "status",
    ]
    assert len(mapped) == 2
    assert mapped.iloc[0]["external_order_id"] == "10001"
    assert resolved.as_preview()[0] == {
        "source": "Order ID",
        "field": "external_order_id",
        "label": "Order ID",
    }


def test_apply_mapping_drops_unmapped_columns():
    """Leftover columns are inert, and keeping them risks shadowing a mapped one."""
    df = pd.DataFrame([{"order_id": "1", "customer_id": "C1", "internal_note": "ignore me"}])
    mapped, resolved = apply_mapping(df, "order")

    assert "internal_note" not in mapped.columns
    assert resolved.unmapped == ["internal_note"]


# ---------------------------------------------------------------------------
# The regression this whole module exists for
# ---------------------------------------------------------------------------


def test_alternate_headers_no_longer_skip_every_row():
    """A header problem must not be reported as 38,279 bad rows.

    ``DataValidator`` is unchanged; it is the mapping in front of it that turns
    this from "0 processed, N skipped" into a clean import.
    """
    raw = pd.DataFrame(
        [
            {"order_id": f"100{n}", "customer_id": "C001", "order_date": "2026-01-05", "total_amount": 10.0}
            for n in range(40)
        ]
    )

    # What used to happen: every row looks like it is missing both ids.
    before, before_errors = DataValidator.validate_order_data(raw)
    assert before == []
    assert len(before_errors) == 40

    # What happens now.
    mapped, _ = apply_mapping(raw, "order")
    after, after_errors = DataValidator.validate_order_data(mapped)

    assert len(after) == 40
    assert after_errors == []
    assert after[0]["external_order_id"] == "1000"
    assert after[0]["external_customer_id"] == "C001"


def test_row_level_nulls_are_still_reported_per_row():
    """Mapping must not paper over a genuinely empty required cell."""
    df = pd.DataFrame(
        [
            {"order_id": "1", "customer_id": "C1"},
            {"order_id": None, "customer_id": "C2"},
        ]
    )

    mapped, _ = apply_mapping(df, "order")
    valid, errors = DataValidator.validate_order_data(mapped)

    assert len(valid) == 1
    assert len(errors) == 1
    assert "external_order_id" in errors[0]


# ---------------------------------------------------------------------------
# RFM passthrough must survive the rename
# ---------------------------------------------------------------------------


def test_full_rfm_columns_survive_mapping():
    """All three present and usable, so the row is a passthrough candidate."""
    from app.analytics.scoring import has_real_rfm_columns, rfm_from_import_record

    df = pd.DataFrame([{"Customer ID": "C1", "Recency": 12, "Frequency": 4, "Monetary": 900.0}])
    mapped, _ = apply_mapping(df, "customer")

    record = mapped.to_dict(orient="records")[0]
    assert has_real_rfm_columns(record)

    row = rfm_from_import_record("cust-1", record)
    assert row is not None
    assert row.frequency == 4
    assert row.monetary == 900.0
    assert row.source == "import"


def test_partial_rfm_stays_partial_after_mapping():
    """Two of three is still partial: reported, never half-filled."""
    from app.analytics.scoring import has_real_rfm_columns, rfm_from_import_record

    df = pd.DataFrame([{"customer_id": "C1", "Total Spend": 900.0, "Order Count": 4}])
    mapped, _ = apply_mapping(df, "customer")

    assert "monetary" in mapped.columns
    assert "frequency" in mapped.columns
    assert "recency" not in mapped.columns

    record = mapped.to_dict(orient="records")[0]
    assert not has_real_rfm_columns(record)
    assert rfm_from_import_record("cust-1", record) is None


# ---------------------------------------------------------------------------
# Metadata the UI reads
# ---------------------------------------------------------------------------


def test_expected_columns_separates_required_from_optional():
    spec = expected_columns("order")

    required = {entry["field"] for entry in spec["required"]}
    optional = {entry["field"] for entry in spec["optional"]}

    assert required == {"external_order_id", "external_customer_id"}
    assert optional == {"order_date", "total_amount", "status"}

    # Every canonical name must be listed as an accepted header for its own
    # field, or a canonical file would stop importing.
    for entry in spec["required"] + spec["optional"]:
        assert entry["field"] in entry["accepted_columns"]


# ---------------------------------------------------------------------------
# This project's own dataset/ files
# ---------------------------------------------------------------------------
#
# The headers below are copied from the real files. Three imports produced NULL
# columns for an import that reported complete success, which is the worst kind of
# bug because there is nothing on screen to investigate.


def test_kaggle_style_orders_take_the_purchase_timestamp():
    """`dataset/df_Orders.csv`: five date columns, only one of which is the order date.

    Before this alias every one of the 89,316 orders imported with a NULL
    ``order_date``, so RFM recency had nothing to compute from. Approval, delivery
    and estimated-delivery are deliberately *not* aliases: they are all plausible
    dates and none of them is when the order was placed.
    """
    resolved = resolve_columns(
        [
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_timestamp",
            "order_estimated_delivery_date",
        ],
        "order",
    )

    assert resolved.is_usable
    assert resolved.mapping["order_date"] == "order_purchase_timestamp"
    assert "order_approved_at" in resolved.unmapped
    assert "order_delivered_timestamp" in resolved.unmapped


def test_kaggle_style_products_take_the_category_name():
    """`dataset/df_Products.csv` calls it ``product_category_name``.

    Without the alias 27,451 products imported with a NULL category, and the
    product analysis page groups by category — so it rendered one enormous
    "uncategorised" bucket and looked broken rather than unmapped.
    """
    resolved = resolve_columns(
        ["product_id", "product_category_name", "product_weight_g", "product_length_cm"],
        "product",
    )

    assert resolved.is_usable
    assert resolved.mapping["category"] == "product_category_name"


def test_camelcase_inventory_export_maps_name_and_stock():
    """`dataset/untitled folder/inventory_master_dataset.csv`, both ways round.

    One file carrying product attributes *and* stock levels, which is why it is
    imported twice — as products, then as inventory. ``itemName`` only resolves
    because normalisation splits on the case boundary.
    """
    headers = ["id", "itemName", "category", "quantity", "price", "supplier"]

    as_product = resolve_columns(headers, "product")
    assert as_product.is_usable
    assert as_product.mapping["name"] == "itemName"
    assert as_product.mapping["category"] == "category"
    assert as_product.mapping["price"] == "price"

    as_inventory = resolve_columns(headers, "inventory")
    assert as_inventory.is_usable
    assert as_inventory.mapping["quantity"] == "quantity"
    assert as_inventory.mapping["supplier"] == "supplier"


# ---------------------------------------------------------------------------
# Fields no column supplied
# ---------------------------------------------------------------------------


def test_empty_fields_names_optional_fields_with_no_source():
    """The counterpart to ``unmapped``, and the more consequential direction.

    ``unmapped`` names source columns that were ignored, which a user can shrug
    at. This names application fields that will be NULL for every row of an import
    the UI is about to call successful — the difference between "we skipped a
    column you don't need" and "your revenue figures will all be zero".
    """
    resolved = resolve_columns(["order_id", "customer_id", "order_purchase_timestamp"], "order")
    gaps = {entry["field"]: entry["label"] for entry in resolved.empty_fields()}

    # No amount column of any kind exists in df_Orders.csv, so this is the real
    # case: a completely successful import worth nothing at all.
    assert "total_amount" in gaps
    assert "status" in gaps
    assert gaps["total_amount"], "the UI prints the label, so it must not be blank"

    # Mapped fields are not gaps, and required fields never appear here — a missing
    # required field is a mapping *failure*, reported through ColumnMappingError.
    assert "order_date" not in gaps
    assert "external_order_id" not in gaps
    assert "external_customer_id" not in gaps


def test_empty_fields_is_silent_when_nothing_is_missing():
    """Otherwise the warning becomes noise and gets ignored when it matters."""
    resolved = resolve_columns(
        ["external_order_id", "external_customer_id", "order_date", "total_amount", "status"],
        "order",
    )

    assert resolved.empty_fields() == []


def test_empty_fields_covers_a_column_dropped_for_ambiguity():
    """An ambiguous column produces the same NULL as a missing one, so it is reported.

    Two headers claiming one field is refused rather than guessed. That refusal is
    correct, but silently it looks identical to the column not existing — hence it
    must show up in the same place.
    """
    resolved = resolve_columns(
        ["order_id", "customer_id", "total_amount", "total amount"],
        "order",
    )

    assert "total_amount" in resolved.ambiguous
    assert "total_amount" not in resolved.mapping
    assert "total_amount" in {entry["field"] for entry in resolved.empty_fields()}


def test_mapping_summary_carries_the_gaps_to_the_api():
    """One dict spread into all four import responses and the preview.

    Checked here because the UI reads these exact keys: a rename would leave the
    endpoint returning 200 while the screen quietly stopped warning anyone.
    """
    resolved = resolve_columns(["order_id", "customer_id", "order_date", "note"], "order")
    summary = mapping_summary(resolved)

    assert set(summary) == {"column_mapping", "unmapped_columns", "fields_without_columns"}
    assert summary["unmapped_columns"] == ["note"]
    assert "total_amount" in {entry["field"] for entry in summary["fields_without_columns"]}

    # column_mapping is the "your column -> imported as" table, so every row needs
    # both halves plus the label the table prints.
    for entry in summary["column_mapping"]:
        assert {"field", "source", "label"} <= set(entry)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
