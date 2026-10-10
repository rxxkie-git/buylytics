"""Normalising a single webhook record onto the internal schema.

Standard library only. The import path validates whole DataFrames with
:class:`app.ingestion.file_parser.DataValidator`; a webhook carries one record,
and loading pandas to inspect one dict would be absurd. The required fields and
the type coercions here mirror that validator deliberately, so a customer
arriving by webhook is held to exactly the same standard as one arriving by CSV.

Two rules govern this module:

* **Nothing is invented.** A record missing its external identifier is rejected,
  not given a generated one — a synthetic id would create a duplicate customer
  on every retry and quietly corrupt RFM.
* **Aliases are explicit.** External senders name things differently, so a short,
  documented alias list is accepted per field. Anything outside that list is
  ignored rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

#: What a normalised record is for.
KIND_CUSTOMER = "customer"
KIND_ORDER = "order"
KIND_INVENTORY = "inventory"


@dataclass(frozen=True)
class NormalisedRecord:
    """A webhook body reduced to internal column names."""

    kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    def __bool__(self) -> bool:
        return self.valid


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def _first(payload: Mapping[str, Any], names: Iterable[str]) -> Any:
    """First present, non-blank value among ``names``."""
    for name in names:
        if name not in payload:
            continue

        value = payload[name]

        if value is None:
            continue

        if isinstance(value, str) and not value.strip():
            continue

        return value

    return None


def _as_text(value: Any, *, limit: int = 255) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # Column widths are 255; truncating beats a database error that would reject
    # an otherwise good delivery.
    return text[:limit]


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _as_amount(value: Any) -> float | None:
    """Parse a money value, tolerating currency symbols and thousands separators."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    cleaned = "".join(char for char in text if char.isdigit() or char in ".-")

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    """Parse a timestamp field as ISO-8601 or Unix epoch seconds, in UTC."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # A bare epoch string.
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Alias tables
# ---------------------------------------------------------------------------

_CUSTOMER_ID_KEYS = (
    "external_customer_id",
    "externalCustomerId",
    "customer_id",
    "customerId",
    "customer_reference",
    "id",
)

_ORDER_ID_KEYS = (
    "external_order_id",
    "externalOrderId",
    "order_id",
    "orderId",
    "order_number",
    "orderNumber",
    "id",
)

_PRODUCT_ID_KEYS = (
    "external_product_id",
    "externalProductId",
    "product_id",
    "productId",
    "sku",
    "id",
)


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------


def normalise_customer(payload: Mapping[str, Any]) -> NormalisedRecord:
    external_id = _as_text(_first(payload, _CUSTOMER_ID_KEYS))

    if not external_id:
        return NormalisedRecord(
            kind=KIND_CUSTOMER,
            errors=(
                "external_customer_id is required (accepted aliases: "
                + ", ".join(_CUSTOMER_ID_KEYS)
                + ")",
            ),
        )

    fields: dict[str, Any] = {"external_customer_id": external_id}

    name = _as_text(_first(payload, ("name", "full_name", "fullName", "customer_name")))
    first_name = _as_text(_first(payload, ("first_name", "firstName")))
    last_name = _as_text(_first(payload, ("last_name", "lastName")))

    if not name and (first_name or last_name):
        name = " ".join(part for part in (first_name, last_name) if part)[:255]

    if name:
        fields["name"] = name

    email = _as_text(_first(payload, ("email", "email_address", "emailAddress")))
    if email:
        fields["email"] = email.lower()

    phone = _as_text(_first(payload, ("phone", "phone_number", "phoneNumber", "mobile")))
    if phone:
        fields["phone"] = phone

    city = _as_text(_first(payload, ("city", "town", "locality")))
    if city:
        fields["city"] = city

    return NormalisedRecord(kind=KIND_CUSTOMER, fields=fields)


def normalise_order(payload: Mapping[str, Any]) -> NormalisedRecord:
    external_id = _as_text(_first(payload, _ORDER_ID_KEYS))

    # The customer link is looked up separately, so ``id`` is excluded here: on an
    # order body ``id`` means the order, and treating it as the customer would
    # attach every order to a customer that does not exist.
    customer_ref = _as_text(_first(payload, _CUSTOMER_ID_KEYS[:-1]))

    if not customer_ref:
        nested = payload.get("customer")
        if isinstance(nested, Mapping):
            customer_ref = _as_text(_first(nested, _CUSTOMER_ID_KEYS))

    errors: list[str] = []

    if not external_id:
        errors.append(
            "external_order_id is required (accepted aliases: " + ", ".join(_ORDER_ID_KEYS) + ")"
        )

    if not customer_ref:
        errors.append(
            "external_customer_id is required on an order so it can be attributed to a customer"
        )

    if errors:
        return NormalisedRecord(kind=KIND_ORDER, errors=tuple(errors))

    fields: dict[str, Any] = {
        "external_order_id": external_id,
        "external_customer_id": customer_ref,
        "status": _as_text(
            _first(payload, ("status", "order_status", "financial_status", "state")), limit=50
        )
        or "pending",
    }

    order_date = parse_datetime(
        _first(payload, ("order_date", "orderDate", "created_at", "createdAt", "date", "placed_at"))
    )
    if order_date is not None:
        fields["order_date"] = order_date

    total = _as_amount(
        _first(
            payload,
            ("total_amount", "totalAmount", "total", "total_price", "totalPrice", "amount", "grand_total"),
        )
    )
    if total is not None:
        fields["total_amount"] = total

    return NormalisedRecord(kind=KIND_ORDER, fields=fields)


def normalise_inventory(payload: Mapping[str, Any]) -> NormalisedRecord:
    external_id = _as_text(_first(payload, _PRODUCT_ID_KEYS))

    if not external_id:
        return NormalisedRecord(
            kind=KIND_INVENTORY,
            errors=(
                "external_product_id is required (accepted aliases: "
                + ", ".join(_PRODUCT_ID_KEYS)
                + ")",
            ),
        )

    quantity = _as_int(
        _first(
            payload,
            ("quantity", "quantity_available", "available", "stock", "on_hand", "inventory_quantity"),
        )
    )

    if quantity is None:
        return NormalisedRecord(
            kind=KIND_INVENTORY,
            errors=(
                "quantity is required on an inventory update and must be a whole number",
            ),
        )

    fields: dict[str, Any] = {
        "external_product_id": external_id,
        "quantity": max(0, quantity),
    }

    reorder_level = _as_int(_first(payload, ("reorder_level", "reorderLevel", "threshold", "reorder_point")))
    if reorder_level is not None:
        fields["reorder_level"] = max(0, reorder_level)

    reserved = _as_int(_first(payload, ("reserved_quantity", "reservedQuantity", "reserved")))
    if reserved is not None:
        fields["reserved_quantity"] = max(0, reserved)

    supplier = _as_text(_first(payload, ("supplier", "vendor", "supplier_name")))
    if supplier:
        fields["supplier"] = supplier

    status = _as_text(_first(payload, ("status", "stock_status")), limit=50)
    if status:
        fields["status"] = status

    # A product row has to exist before inventory can reference it.
    product_name = _as_text(_first(payload, ("product_name", "name", "title")))
    if product_name:
        fields["product_name"] = product_name

    sku = _as_text(_first(payload, ("sku", "product_sku")))
    if sku:
        fields["sku"] = sku

    return NormalisedRecord(kind=KIND_INVENTORY, fields=fields)


#: Internal event type -> the normaliser that turns its payload into columns.
#:
#: An event type absent from this table is still verified, recorded and
#: published; it simply has no direct persistence, which is the correct outcome
#: for something like an analytics notification.
_NORMALISERS = {
    "customer.created": normalise_customer,
    "customer.updated": normalise_customer,
    "order.created": normalise_order,
    "order.updated": normalise_order,
    "inventory.updated": normalise_inventory,
}


def normalise_for_event(event_type: str, payload: Mapping[str, Any]) -> NormalisedRecord | None:
    """Normalise a payload for an internal event type, or ``None`` if it has no mapping."""
    normaliser = _NORMALISERS.get((event_type or "").strip().lower())

    if normaliser is None:
        return None

    if not isinstance(payload, Mapping):
        return NormalisedRecord(kind="unknown", errors=("payload must be a JSON object",))

    return normaliser(payload)


def persistable_event_types() -> list[str]:
    return sorted(_NORMALISERS)


def derive_stock_status(quantity: int, reorder_level: int) -> str:
    """Human-facing stock label derived from real numbers."""
    if quantity <= 0:
        return "Out of Stock"

    if reorder_level > 0 and quantity <= reorder_level:
        return "Low Stock"

    return "In Stock"


__all__ = [
    "KIND_CUSTOMER",
    "KIND_INVENTORY",
    "KIND_ORDER",
    "NormalisedRecord",
    "derive_stock_status",
    "normalise_customer",
    "normalise_for_event",
    "normalise_inventory",
    "normalise_order",
    "parse_datetime",
    "persistable_event_types",
]
