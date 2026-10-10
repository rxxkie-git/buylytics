"""Inbound webhook ingestion.

Modules
-------
``signature``
    HMAC-SHA256 verification with constant-time comparison and a replay window.
    Standard library only.
``mapping``
    Translation from an external sender's event names to the internal event
    catalogue.
``records``
    Normalising one delivery onto internal column names, with the same required
    fields the CSV import path enforces.
``service``
    Orchestration: verify, record, persist, publish. Needs SQLAlchemy.

Only the dependency-free modules are re-exported here, so the verification and
normalisation rules can be imported and tested without the database layer
present. ``service`` is imported from its own module.

Request flow::

    POST /api/v1/webhooks/{source}
      -> verify HMAC over "{business_id}.{timestamp}.{raw body}"
      -> confirm the business exists
      -> insert webhook_events row (unique on business+source+external id)
         -> duplicate? return 200 with duplicate=true, publish nothing
      -> normalise and upsert the customer / order / inventory row
      -> commit
      -> publish the mapped domain event
      -> 202 Accepted

The publish deliberately follows the commit. Announcing a change before it is
durable lets a consumer invalidate the cache and immediately re-read the old row,
which restores stale data under a fresh TTL — a persistent wrong answer rather
than a momentary one.
"""

from __future__ import annotations

from app.webhooks.mapping import (
    SOURCE_GENERIC,
    SUPPORTED_SOURCES,
    EventRoute,
    event_payload,
    extract_event_type,
    extract_external_id,
    is_supported_source,
    resolve_route,
    supported_event_types,
)
from app.webhooks.records import (
    KIND_CUSTOMER,
    KIND_INVENTORY,
    KIND_ORDER,
    NormalisedRecord,
    derive_stock_status,
    normalise_customer,
    normalise_for_event,
    normalise_inventory,
    normalise_order,
    persistable_event_types,
)
from app.webhooks.signature import (
    REASON_MALFORMED_SIGNATURE,
    REASON_MALFORMED_TIMESTAMP,
    REASON_MISSING_SECRET,
    REASON_MISSING_SIGNATURE,
    REASON_MISSING_TIMESTAMP,
    REASON_OK,
    REASON_SIGNATURE_MISMATCH,
    REASON_TIMESTAMP_OUT_OF_RANGE,
    SIGNATURE_SCHEME,
    SignatureCheck,
    canonical_message,
    check_timestamp,
    compute_signature,
    parse_signature_header,
    parse_timestamp,
    signature_header_value,
    verify,
)

__all__ = [
    "EventRoute",
    "KIND_CUSTOMER",
    "KIND_INVENTORY",
    "KIND_ORDER",
    "NormalisedRecord",
    "REASON_MALFORMED_SIGNATURE",
    "REASON_MALFORMED_TIMESTAMP",
    "REASON_MISSING_SECRET",
    "REASON_MISSING_SIGNATURE",
    "REASON_MISSING_TIMESTAMP",
    "REASON_OK",
    "REASON_SIGNATURE_MISMATCH",
    "REASON_TIMESTAMP_OUT_OF_RANGE",
    "SIGNATURE_SCHEME",
    "SOURCE_GENERIC",
    "SUPPORTED_SOURCES",
    "SignatureCheck",
    "canonical_message",
    "check_timestamp",
    "compute_signature",
    "derive_stock_status",
    "event_payload",
    "extract_event_type",
    "extract_external_id",
    "is_supported_source",
    "normalise_customer",
    "normalise_for_event",
    "normalise_inventory",
    "normalise_order",
    "parse_signature_header",
    "parse_timestamp",
    "persistable_event_types",
    "resolve_route",
    "signature_header_value",
    "supported_event_types",
    "verify",
]
