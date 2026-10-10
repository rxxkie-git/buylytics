"""Map real-world spreadsheet headers onto the canonical import fields.

    SOURCE FILE -> normalise headers -> alias match -> canonical rename -> validate

The domain model keeps its ``external_*`` names. Nobody exporting from Shopify,
WooCommerce, Amazon or a POS has heard of them, so this module sits in front of
:class:`app.ingestion.file_parser.DataValidator` and renames what it can.

Why this exists
---------------
``DataValidator`` checks required fields per row: ``required - {columns that are
not null in this row}``. When the column is absent from the file entirely that
subtraction fails for every row, so a 38,000-row orders export whose only sin is
a column called ``order_id`` imported as "0 rows processed, 38,279 skipped".
That message describes a data problem. The problem was a header.

Deterministic, not clever
-------------------------
Matching is a lookup in an ordered alias tuple — no fuzzy distance, no model, no
network call. Two consequences are deliberate:

*Precedence is declared, not guessed.* The canonical name is always rank 0, so a
file that already uses ``external_order_id`` behaves exactly as it did before
this module existed. A file with both ``external_order_id`` and ``order_id``
resolves to the canonical one rather than whichever pandas happened to order
first.

*A real tie is refused.* When two different headers land in the same alias slot —
``Order ID`` and ``order id`` both normalise to ``order_id`` — this reports an
ambiguity instead of silently picking one. Picking one would be a coin flip
dressed up as a decision.

One source column may satisfy two canonical fields. A products file keyed only by
``sku`` maps that column to both ``external_product_id`` and ``sku``, because the
alternative is telling the user their catalogue has no product identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# Header normalisation
# --------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^a-z0-9]+")

#: Word boundary inside a run of letters with no separator: ``itemName`` and
#: ``orderId``. Applied before lowercasing, because case *is* the separator.
#:
#: Two alternatives, both needed. ``(?<=[a-z0-9])(?=[A-Z])`` splits the ordinary
#: camelCase hump (``item|Name``, ``order|Id``). ``(?<=[A-Z])(?=[A-Z][a-z])``
#: splits the tail off an acronym run (``SKU|Code``) without breaking the run
#: itself, so a header that is entirely an acronym — ``SKU``, ``ID`` — is left
#: alone rather than shredded into ``s_k_u``.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def normalize_header(raw: Any) -> str:
    """Reduce a spreadsheet header to a comparison key.

    ``"  Order ID "``, ``"order-id"``, ``"Order_Id"`` and ``"ORDER ID"`` all
    become ``"order_id"``. Anything that is not a letter or digit becomes a
    single underscore, which folds spaces, hyphens, dots, slashes and the
    stray ``#`` that spreadsheet headers collect.

    camelCase counts as a separator too, so ``itemName`` becomes ``item_name``
    rather than ``itemname``. This project's own
    ``dataset/untitled folder/inventory_master_dataset.csv`` is headed
    ``id,itemName,category,quantity,price,supplier``, and without this the product
    name silently mapped to nothing — the alias list holds ``item_name``, which
    ``itemname`` does not match. Headers of that shape are ordinary in any CSV
    exported from JSON, so the fix belongs in normalisation rather than in a
    growing list of un-underscored spellings.
    """
    text = str(raw or "").strip()
    text = _CAMEL_BOUNDARY.sub("_", text).lower()
    text = _NON_WORD.sub("_", text)
    return text.strip("_")


# --------------------------------------------------------------------------
# Canonical field definitions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalField:
    """One internal field and the source headers that may stand in for it.

    ``aliases`` is ordered: earlier wins. The canonical name is first in every
    list, which is what makes this layer a no-op for files that already use the
    internal names.
    """

    name: str
    label: str
    required: bool
    aliases: tuple[str, ...]


def _field(name: str, label: str, *aliases: str, required: bool = False) -> CanonicalField:
    # The canonical name is always the highest-precedence alias; declaring it in
    # each tuple by hand would be one copy-paste error away from a field that
    # cannot match its own name.
    ordered = (name,) + tuple(a for a in aliases if a != name)
    return CanonicalField(name=name, label=label, required=required, aliases=ordered)


#: Ordered because resolution is greedy: required identifiers claim their column
#: before optional fields get a turn.
ORDER_FIELDS: tuple[CanonicalField, ...] = (
    _field(
        "external_order_id",
        "Order ID",
        "order_id",
        "orderid",
        "order_no",
        "order_number",
        "order_num",
        "order_ref",
        "order_reference",
        "invoice_no",
        "invoice_number",
        "invoice_id",
        "transaction_id",
        "receipt_no",
        "order",
        "id",
        required=True,
    ),
    _field(
        "external_customer_id",
        "Customer ID",
        "customer_id",
        "customerid",
        "customer_no",
        "customer_number",
        "customer_num",
        "client_id",
        "buyer_id",
        "user_id",
        "customer",
        "client",
        required=True,
    ),
    _field(
        "order_date",
        "Order Date",
        "order_date",
        "purchase_date",
        # This project's own ``dataset/df_Orders.csv`` is headed
        # ``order_purchase_timestamp``. Without the alias it matched no field, so
        # ``order_date`` was NULL for all 89,316 rows and the import still reported
        # "completed" — which is how RFM recency ends up computed from nothing while
        # the UI says every figure comes from your imported data. The file also
        # carries ``order_approved_at``, ``order_delivered_timestamp`` and
        # ``order_estimated_delivery_date``; only the purchase timestamp is the date
        # the order was placed, so only it is listed.
        "order_purchase_timestamp",
        "purchase_timestamp",
        "transaction_date",
        "invoice_date",
        "order_datetime",
        "ordered_at",
        "created_at",
        "date",
        "timestamp",
    ),
    _field(
        "total_amount",
        "Total Amount",
        "total_amount",
        "order_total",
        "grand_total",
        "net_amount",
        "total_price",
        "total_value",
        "revenue",
        "sales",
        "subtotal",
        "total",
        "amount",
        "price",
        "value",
    ),
    _field(
        "status",
        "Status",
        "order_status",
        "fulfillment_status",
        "financial_status",
        "payment_status",
        "state",
        "status",
    ),
)

CUSTOMER_FIELDS: tuple[CanonicalField, ...] = (
    _field(
        "external_customer_id",
        "Customer ID",
        "customer_id",
        "customerid",
        "customer_no",
        "customer_number",
        "customer_num",
        "client_id",
        "user_id",
        "customer",
        "client",
        "id",
        required=True,
    ),
    _field(
        "name",
        "Name",
        "customer_name",
        "full_name",
        "fullname",
        "client_name",
        "contact_name",
        "display_name",
        "name",
    ),
    _field("email", "Email", "email_address", "e_mail", "contact_email", "mail", "email"),
    _field(
        "phone",
        "Phone",
        "phone_number",
        "mobile_number",
        "contact_number",
        "telephone",
        "mobile",
        "tel",
        "phone",
    ),
    _field("city", "City", "customer_city", "billing_city", "shipping_city", "town", "city"),
    # RFM passthrough. Kept tight on purpose: renaming the wrong column to
    # `recency` would feed a wrong number straight into the scoring engine.
    _field("recency", "Recency", "recency_days", "days_since_last_order", "days_since_purchase", "last_order_days"),
    _field(
        "frequency",
        "Frequency",
        "order_count",
        "orders_count",
        "num_orders",
        "no_of_orders",
        "purchase_count",
        "total_orders",
        "orders",
    ),
    _field(
        "monetary",
        "Monetary",
        "monetary_value",
        "total_spend",
        "total_spent",
        "lifetime_value",
        "ltv",
        "total_revenue",
        "total_sales",
        "revenue",
        "spend",
    ),
)

PRODUCT_FIELDS: tuple[CanonicalField, ...] = (
    _field(
        "external_product_id",
        "Product ID",
        "product_id",
        "productid",
        "product_no",
        "product_number",
        "item_id",
        "item_no",
        "item_code",
        "product_code",
        "sku",
        "sku_code",
        "product",
        "id",
        required=True,
    ),
    _field("name", "Name", "product_name", "item_name", "product_title", "title", "name"),
    _field("sku", "SKU", "sku_code", "product_code", "item_code", "barcode", "sku"),
    _field(
        "category",
        "Category",
        "product_category",
        # ``dataset/df_Products.csv`` is headed ``product_category_name``, which
        # matched nothing: 89,316 products imported with a NULL category, and the
        # product-analysis page groups by category.
        "product_category_name",
        "item_category",
        "product_type",
        "category_name",
        "department",
        "type",
        "category",
    ),
    _field("price", "Price", "unit_price", "selling_price", "list_price", "mrp", "rate", "price"),
)

INVENTORY_FIELDS: tuple[CanonicalField, ...] = (
    _field(
        "external_product_id",
        "Product ID",
        "product_id",
        "productid",
        "product_no",
        "product_number",
        "item_id",
        "item_no",
        "item_code",
        "product_code",
        "sku",
        "sku_code",
        "product",
        "id",
        required=True,
    ),
    _field(
        "quantity",
        "Quantity",
        "stock_quantity",
        "quantity_on_hand",
        "qty_on_hand",
        "qty_available",
        "inventory_quantity",
        "stock_on_hand",
        "current_stock",
        "stock_qty",
        "on_hand",
        "available",
        "units",
        "stock",
        "qty",
        "quantity",
    ),
    _field(
        "reorder_level",
        "Reorder Level",
        "reorder_point",
        "reorder_threshold",
        "minimum_stock",
        "min_stock",
        "min_quantity",
        "safety_stock",
        "threshold",
        "reorder_level",
    ),
    _field("supplier", "Supplier", "supplier_name", "vendor_name", "manufacturer", "vendor", "brand", "supplier"),
    _field("status", "Status", "stock_status", "availability", "state", "status"),
)

DATASETS: dict[str, tuple[CanonicalField, ...]] = {
    "order": ORDER_FIELDS,
    "customer": CUSTOMER_FIELDS,
    "product": PRODUCT_FIELDS,
    "inventory": INVENTORY_FIELDS,
}


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


@dataclass
class ResolvedColumns:
    """What the headers of one file were understood to mean."""

    dataset: str
    #: canonical field name -> source header exactly as it appeared in the file
    mapping: dict[str, str] = dataclass_field(default_factory=dict)
    #: canonical field name -> the tied source headers, when a choice was refused
    ambiguous: dict[str, list[str]] = dataclass_field(default_factory=dict)
    #: required canonical fields no column could be found for
    missing_required: list[str] = dataclass_field(default_factory=list)
    #: source headers that matched nothing and will be ignored
    unmapped: list[str] = dataclass_field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return not self.missing_required and not self.ambiguous

    def as_preview(self) -> list[dict[str, str]]:
        """Rows for the UI's "CSV column -> Application field" table."""
        labels = {f.name: f.label for f in DATASETS[self.dataset]}
        return [
            {"source": source, "field": name, "label": labels.get(name, name)}
            for name, source in self.mapping.items()
        ]

    def empty_fields(self) -> list[dict[str, str]]:
        """Optional fields no column supplied, so they will be NULL for every row.

        The counterpart to :attr:`unmapped`, and the more useful direction of the
        two. ``unmapped`` names source columns that were ignored, which a user can
        usually shrug at. This names *application* fields that will be empty after
        an import the UI is about to call successful.

        The case that motivated it is this project's own ``dataset/df_Orders.csv``:
        it has no amount column of any kind, so ``total_amount`` is NULL for all
        89,316 rows. The import genuinely succeeded, the row count was genuinely
        right, and revenue and RFM monetary were genuinely empty — with nothing
        anywhere saying why. Required fields are excluded because a missing one is
        already a hard refusal via :attr:`missing_required`.
        """
        return [
            {"field": spec.name, "label": spec.label}
            for spec in DATASETS[self.dataset]
            if not spec.required and spec.name not in self.mapping
        ]


def resolve_columns(headers: Iterable[Any], dataset: str) -> ResolvedColumns:
    """Work out which source header supplies each canonical field.

    Greedy over fields in declared order, so the required identifier claims the
    best column available before optional fields compete for it. A field left
    unclaimed afterwards may reuse a column already taken by another field, which
    is how a products file keyed only by ``sku`` gets both an id and a SKU.
    """
    if dataset not in DATASETS:
        raise ValueError(f"Unknown dataset '{dataset}'. Known: {', '.join(sorted(DATASETS))}")

    fields = DATASETS[dataset]

    # Normalised key -> the raw headers that reduce to it. A list because two
    # different raw headers can collide, and that collision is the ambiguity
    # this function refuses to resolve.
    by_key: dict[str, list[str]] = {}
    original: list[str] = []

    for header in headers:
        raw = str(header)
        original.append(raw)
        key = normalize_header(raw)

        if key:
            by_key.setdefault(key, []).append(raw)

    resolved = ResolvedColumns(dataset=dataset)
    claimed: set[str] = set()
    deferred: list[CanonicalField] = []

    for spec in fields:
        best_rank: int | None = None
        candidates: list[str] = []

        for rank, alias in enumerate(spec.aliases):
            raws = [raw for raw in by_key.get(alias, []) if raw not in claimed]

            if not raws:
                continue

            if best_rank is None:
                best_rank = rank
                candidates = raws
            break

        if best_rank is None:
            deferred.append(spec)
            continue

        if len(candidates) > 1:
            # Two headers, same meaning, no basis to prefer either.
            resolved.ambiguous[spec.name] = sorted(candidates)
            continue

        resolved.mapping[spec.name] = candidates[0]
        claimed.add(candidates[0])

    # Second pass: a field with nothing left of its own may share a column.
    for spec in deferred:
        for alias in spec.aliases:
            raws = by_key.get(alias, [])

            if len(raws) == 1:
                resolved.mapping[spec.name] = raws[0]
                break
            if len(raws) > 1:
                resolved.ambiguous[spec.name] = sorted(raws)
                break

        if spec.required and spec.name not in resolved.mapping and spec.name not in resolved.ambiguous:
            resolved.missing_required.append(spec.name)

    used = set(resolved.mapping.values())
    resolved.unmapped = [raw for raw in original if raw not in used]

    return resolved


# --------------------------------------------------------------------------
# Failure reporting
# --------------------------------------------------------------------------


class ColumnMappingError(Exception):
    """Raised when a file's headers cannot be understood.

    Carries everything the UI needs to be actionable: which canonical field is
    missing, which headers the file actually has, and what would have been
    accepted. A bare "import failed" would send the user back to the same file
    with no idea what to change.
    """

    def __init__(self, resolved: ResolvedColumns, available: Sequence[str]) -> None:
        self.resolved = resolved
        self.available = [str(column) for column in available]
        self.dataset = resolved.dataset
        self.missing_required = list(resolved.missing_required)
        self.ambiguous = {name: list(values) for name, values in resolved.ambiguous.items()}
        super().__init__(self._message())

    def _message(self) -> str:
        labels = {f.name: f.label for f in DATASETS[self.dataset]}
        parts: list[str] = []

        for name in self.missing_required:
            accepted = ", ".join(_suggestions(self.dataset, name))
            parts.append(
                f"No column in this file supplies {labels.get(name, name)} ({name}). "
                f"Rename one of your columns to any of: {accepted}."
            )

        for name, tied in self.ambiguous.items():
            columns = ", ".join(f"'{value}'" for value in tied)
            parts.append(
                f"{labels.get(name, name)} ({name}) could come from {columns}, which mean the same thing. "
                "Remove or rename one so the choice is unambiguous."
            )

        columns = ", ".join(f"'{value}'" for value in self.available) or "none"
        parts.append(f"Columns found in the file: {columns}.")

        return " ".join(parts)

    def to_payload(self) -> dict[str, Any]:
        """Structured form for the API response."""
        labels = {f.name: f.label for f in DATASETS[self.dataset]}

        return {
            "error": str(self),
            "error_code": "column_mapping_failed",
            "available_columns": self.available,
            "missing_fields": [
                {
                    "field": name,
                    "label": labels.get(name, name),
                    "accepted_columns": _suggestions(self.dataset, name),
                }
                for name in self.missing_required
            ],
            "ambiguous_fields": [
                {"field": name, "label": labels.get(name, name), "columns": tied}
                for name, tied in self.ambiguous.items()
            ],
            "column_mapping": self.resolved.as_preview(),
        }


def _suggestions(dataset: str, field_name: str, limit: int = 6) -> list[str]:
    """A short list of accepted header names, for an error message."""
    for spec in DATASETS[dataset]:
        if spec.name == field_name:
            return list(spec.aliases[:limit])
    return []


def expected_columns(dataset: str) -> dict[str, list[dict[str, Any]]]:
    """Every canonical field and its accepted headers. Used by the UI."""
    return {
        "required": [
            {"field": f.name, "label": f.label, "accepted_columns": list(f.aliases)}
            for f in DATASETS[dataset]
            if f.required
        ],
        "optional": [
            {"field": f.name, "label": f.label, "accepted_columns": list(f.aliases)}
            for f in DATASETS[dataset]
            if not f.required
        ],
    }


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


def apply_mapping(df: Any, dataset: str) -> tuple[Any, ResolvedColumns]:
    """Rename a parsed frame's columns to the canonical names.

    Returns the renamed frame and the resolution, and raises
    :class:`ColumnMappingError` when a required field is missing or a choice was
    refused. Raising here rather than returning an empty frame is the whole
    point: ``DataValidator`` would otherwise skip every row and report a data
    problem for what is a naming problem.

    Unmapped columns are dropped. ``DataValidator`` reads canonical names only,
    so anything left over is inert — and keeping it risks a source header called
    ``status`` shadowing a mapped one.
    """
    headers = list(getattr(df, "columns", []))
    resolved = resolve_columns(headers, dataset)

    if not resolved.is_usable:
        raise ColumnMappingError(resolved, headers)

    # Build the frame column by column. `df.rename` cannot express one source
    # column feeding two canonical fields, which is exactly the sku case.
    renamed = df.loc[:, []].copy()

    for name, source in resolved.mapping.items():
        renamed[name] = df[source]

    return renamed, resolved


def mapping_summary(resolved: ResolvedColumns) -> dict[str, Any]:
    """The mapping, shaped for an import response."""
    return {
        "column_mapping": resolved.as_preview(),
        "unmapped_columns": list(resolved.unmapped),
        # Named from the application's side, so "imported successfully" can never
        # be the only thing the user is told about a file that filled half the
        # fields. See ``ResolvedColumns.empty_fields``.
        "fields_without_columns": resolved.empty_fields(),
    }


__all__ = [
    "CanonicalField",
    "ColumnMappingError",
    "DATASETS",
    "ResolvedColumns",
    "apply_mapping",
    "expected_columns",
    "mapping_summary",
    "normalize_header",
    "resolve_columns",
]
