"""Executable checks for the webhook ingestion layer.

    python3 tests/check_webhooks.py

Covers signature verification, replay windows, header parsing, source/event
routing, record normalisation and the composed decision in
``app.webhooks.service.evaluate_delivery``. All of it is standard library, so
this runs with nothing installed.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Resolved from this file's location rather than relying on the working directory.
# The previous revision imported `app` only when run from backend/, so a copy of it
# had to live there - which is how a stray checker ended up beside the package.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.webhooks import mapping, records, signature
from app.webhooks.service import (
    REASON_INVALID_PAYLOAD,
    REASON_MISSING_BUSINESS,
    REASON_UNSUPPORTED_EVENT,
    REASON_UNSUPPORTED_SOURCE,
    derive_external_id,
    evaluate_delivery,
)

PASSED = 0
FAILED: list[str] = []
SECRET = "whsec_test_only_not_a_real_secret"
OTHER_SECRET = "whsec_a_completely_different_value"
BIZ_A = "11111111-1111-1111-1111-111111111111"
BIZ_B = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
TS = str(int(NOW.timestamp()))


def group(title: str) -> None:
    print(f"\n[{title}]")


def ok(label: str, condition: bool, note: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL {label} {note}")


def body_of(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def sign(body: bytes, *, business_id: str = BIZ_A, ts: str = TS, secret: str = SECRET) -> str:
    return signature.signature_header_value(secret, business_id, ts, body)


# ---------------------------------------------------------------------------
group("1 canonical message and digest")
# ---------------------------------------------------------------------------

body = b'{"event_type":"customer.created","id":"c-1"}'
message = signature.canonical_message(BIZ_A, TS, body)
ok(
    "canonical message is business.timestamp.body with no re-encoding",
    message == f"{BIZ_A}.{TS}.".encode() + body,
)
ok("an empty body still signs", signature.canonical_message(BIZ_A, TS, b"").endswith(b"."))
digest = signature.compute_signature(SECRET, BIZ_A, TS, body)
ok("digest is 64 hex chars", len(digest) == 64 and int(digest, 16) >= 0)
ok(
    "digest is deterministic",
    digest == signature.compute_signature(SECRET, BIZ_A, TS, body),
)
ok(
    "a different secret gives a different digest",
    digest != signature.compute_signature(OTHER_SECRET, BIZ_A, TS, body),
)
ok(
    "a different business gives a different digest",
    digest != signature.compute_signature(SECRET, BIZ_B, TS, body),
)
ok(
    "a one-byte body change gives a different digest",
    digest != signature.compute_signature(SECRET, BIZ_A, TS, body + b" "),
)
ok(
    "header value carries the scheme prefix",
    signature.signature_header_value(SECRET, BIZ_A, TS, body) == f"sha256={digest}",
)
ok("the secret never appears in the header value", SECRET not in sign(body))

# ---------------------------------------------------------------------------
group("2 signature header parsing")
# ---------------------------------------------------------------------------

ok("sha256= prefix is accepted", signature.parse_signature_header(f"sha256={digest}") == [digest])
ok("v1= prefix is accepted", signature.parse_signature_header(f"v1={digest}") == [digest])
ok("bare hex is accepted", signature.parse_signature_header(digest) == [digest])
ok(
    "uppercase hex is normalised",
    signature.parse_signature_header(digest.upper()) == [digest],
)
ok(
    "a comma list yields every candidate (secret rotation)",
    signature.parse_signature_header(f"sha256={digest},sha256={'a' * 64}") == [digest, "a" * 64],
)
ok("None yields nothing", signature.parse_signature_header(None) == [])
ok("an empty header yields nothing", signature.parse_signature_header("   ") == [])
ok("a short digest is dropped", signature.parse_signature_header("sha256=abc") == [])
ok("a non-hex digest is dropped", signature.parse_signature_header("z" * 64) == [])
ok(
    "an unknown scheme is dropped",
    signature.parse_signature_header(f"md5={digest}") == [],
)
ok(
    "a garbage entry is dropped but a good one survives",
    signature.parse_signature_header(f"garbage, sha256={digest}") == [digest],
)

# ---------------------------------------------------------------------------
group("3 timestamp parsing and the replay window")
# ---------------------------------------------------------------------------

ok("epoch seconds parse", signature.parse_timestamp(TS) == NOW)
ok(
    "ISO-8601 with Z parses",
    signature.parse_timestamp("2026-08-15T12:00:00Z") == NOW,
)
ok(
    "ISO-8601 with an offset parses to UTC",
    signature.parse_timestamp("2026-08-15T17:30:00+05:30") == NOW,
)
ok(
    "a naive ISO timestamp is assumed UTC",
    signature.parse_timestamp("2026-08-15T12:00:00") == NOW,
)
ok("None does not parse", signature.parse_timestamp(None) is None)
ok("prose does not parse", signature.parse_timestamp("last tuesday") is None)
ok("an empty string does not parse", signature.parse_timestamp("  ") is None)

fresh = signature.check_timestamp(TS, tolerance_seconds=300, now=NOW)
ok("a current timestamp is inside the window", fresh.verified)
inside = signature.check_timestamp(
    str(int((NOW - timedelta(seconds=299)).timestamp())), tolerance_seconds=300, now=NOW
)
ok("299s old is inside a 300s window", inside.verified)
stale = signature.check_timestamp(
    str(int((NOW - timedelta(seconds=301)).timestamp())), tolerance_seconds=300, now=NOW
)
ok(
    "301s old is rejected",
    not stale.verified and stale.reason == signature.REASON_TIMESTAMP_OUT_OF_RANGE,
)
future = signature.check_timestamp(
    str(int((NOW + timedelta(seconds=3600)).timestamp())), tolerance_seconds=300, now=NOW
)
ok(
    "the window is symmetric: a future timestamp is rejected too",
    not future.verified and future.reason == signature.REASON_TIMESTAMP_OUT_OF_RANGE,
)
ok(
    "a missing timestamp is distinguished from a malformed one",
    signature.check_timestamp(None, tolerance_seconds=300, now=NOW).reason
    == signature.REASON_MISSING_TIMESTAMP
    and signature.check_timestamp("nonsense", tolerance_seconds=300, now=NOW).reason
    == signature.REASON_MALFORMED_TIMESTAMP,
)
ok(
    "check_timestamp defaults to the real clock",
    signature.check_timestamp(str(int(time.time())), tolerance_seconds=300).verified,
)

# ---------------------------------------------------------------------------
group("4 verify(): the security boundary")
# ---------------------------------------------------------------------------

good = signature.verify(
    SECRET,
    business_id=BIZ_A,
    timestamp=TS,
    body=body,
    provided_signature=sign(body),
    tolerance_seconds=300,
    now=NOW,
)
ok("a correctly signed delivery verifies", good.verified and bool(good))

tampered = signature.verify(
    SECRET,
    business_id=BIZ_A,
    timestamp=TS,
    body=body.replace(b"c-1", b"c-2"),
    provided_signature=sign(body),
    tolerance_seconds=300,
    now=NOW,
)
ok(
    "a tampered body is rejected",
    not tampered.verified and tampered.reason == signature.REASON_SIGNATURE_MISMATCH,
)

wrong_secret = signature.verify(
    OTHER_SECRET,
    business_id=BIZ_A,
    timestamp=TS,
    body=body,
    provided_signature=sign(body),
    tolerance_seconds=300,
    now=NOW,
)
ok(
    "the wrong secret is rejected",
    not wrong_secret.verified and wrong_secret.reason == signature.REASON_SIGNATURE_MISMATCH,
)

cross_tenant = signature.verify(
    SECRET,
    business_id=BIZ_B,
    timestamp=TS,
    body=body,
    provided_signature=sign(body, business_id=BIZ_A),
    tolerance_seconds=300,
    now=NOW,
)
ok(
    "a signature for tenant A cannot be replayed against tenant B",
    not cross_tenant.verified and cross_tenant.reason == signature.REASON_SIGNATURE_MISMATCH,
)

replayed_ts = signature.verify(
    SECRET,
    business_id=BIZ_A,
    timestamp=TS,
    body=body,
    provided_signature=sign(body),
    tolerance_seconds=300,
    now=NOW + timedelta(hours=2),
)
ok(
    "a captured request expires once it leaves the window",
    not replayed_ts.verified and replayed_ts.reason == signature.REASON_TIMESTAMP_OUT_OF_RANGE,
)

ok(
    "no configured secret is reported as configuration, not mismatch",
    signature.verify(
        "",
        business_id=BIZ_A,
        timestamp=TS,
        body=body,
        provided_signature=sign(body),
        tolerance_seconds=300,
        now=NOW,
    ).reason
    == signature.REASON_MISSING_SECRET,
)
ok(
    "a missing signature header is rejected",
    signature.verify(
        SECRET,
        business_id=BIZ_A,
        timestamp=TS,
        body=body,
        provided_signature=None,
        tolerance_seconds=300,
        now=NOW,
    ).reason
    == signature.REASON_MISSING_SIGNATURE,
)
ok(
    "a missing business header is rejected before any HMAC work",
    signature.verify(
        SECRET,
        business_id="  ",
        timestamp=TS,
        body=body,
        provided_signature=sign(body),
        tolerance_seconds=300,
        now=NOW,
    ).reason
    == signature.REASON_MISSING_SIGNATURE,
)
ok(
    "a malformed signature is rejected as malformed, not as a mismatch",
    signature.verify(
        SECRET,
        business_id=BIZ_A,
        timestamp=TS,
        body=body,
        provided_signature="sha256=nope",
        tolerance_seconds=300,
        now=NOW,
    ).reason
    == signature.REASON_MALFORMED_SIGNATURE,
)
ok(
    "structural checks run before the timestamp check",
    signature.verify(
        SECRET,
        business_id=BIZ_A,
        timestamp="nonsense",
        body=body,
        provided_signature="sha256=nope",
        tolerance_seconds=300,
        now=NOW,
    ).reason
    == signature.REASON_MALFORMED_SIGNATURE,
)
ok(
    "a rotation list verifies when one candidate is right",
    signature.verify(
        SECRET,
        business_id=BIZ_A,
        timestamp=TS,
        body=body,
        provided_signature=f"sha256={'b' * 64},{sign(body)}",
        tolerance_seconds=300,
        now=NOW,
    ).verified,
)
ok(
    "a zero tolerance still accepts an exactly-current timestamp",
    signature.verify(
        SECRET,
        business_id=BIZ_A,
        timestamp=TS,
        body=body,
        provided_signature=sign(body),
        tolerance_seconds=0,
        now=NOW,
    ).verified,
)

# ---------------------------------------------------------------------------
group("5 no rejection leaks the secret or the expected digest")
# ---------------------------------------------------------------------------

leak_probes = [
    signature.verify(
        SECRET,
        business_id=BIZ_A,
        timestamp=ts,
        body=b_,
        provided_signature=sig,
        tolerance_seconds=300,
        now=NOW,
    )
    for ts, b_, sig in (
        (TS, body, f"sha256={'c' * 64}"),
        (TS, body, "sha256=short"),
        ("nonsense", body, sign(body)),
        ("0", body, sign(body)),
        (TS, b"", sign(body)),
    )
]
expected = signature.compute_signature(SECRET, BIZ_A, TS, body)
ok(
    "every rejection reason is one of the declared codes",
    all(
        probe.reason
        in {
            signature.REASON_SIGNATURE_MISMATCH,
            signature.REASON_MALFORMED_SIGNATURE,
            signature.REASON_MALFORMED_TIMESTAMP,
            signature.REASON_TIMESTAMP_OUT_OF_RANGE,
        }
        for probe in leak_probes
    ),
)
ok(
    "no detail string contains the secret",
    all(SECRET not in (probe.detail or "") for probe in leak_probes),
)
ok(
    "no detail string contains the expected digest",
    all(expected not in (probe.detail or "") for probe in leak_probes),
)
ok(
    "SignatureCheck reprs do not carry the secret either",
    all(SECRET not in repr(probe) for probe in leak_probes),
)

# ---------------------------------------------------------------------------
group("6 constant-time comparison is actually used")
# ---------------------------------------------------------------------------

source = (__import__("pathlib").Path("app/webhooks/signature.py")).read_text()
ok("hmac.compare_digest appears in the module", "hmac.compare_digest" in source)
ok(
    "no '== expected' style comparison of digests",
    "== expected" not in source and "expected ==" not in source,
)

# ---------------------------------------------------------------------------
group("7 source and event routing")
# ---------------------------------------------------------------------------

ok("generic is supported", mapping.is_supported_source("generic"))
ok("case and whitespace are normalised", mapping.is_supported_source("  GENERIC "))
ok("an unknown source is not supported", not mapping.is_supported_source("shopify"))
ok("an empty source is not supported", not mapping.is_supported_source(""))
route = mapping.resolve_route("generic", "order.created")
ok(
    "order.created routes to the orders topic",
    route is not None and route.event_type == "order.created" and "order" in route.topic,
)
ok(
    "an unmapped event type has no route",
    mapping.resolve_route("generic", "customer.deleted") is None,
)
ok(
    "an external sender cannot inject an internal-only event type",
    mapping.resolve_route("generic", "analytics.job.requested") is None
    and mapping.resolve_route("generic", "customers.imported") is None,
)
ok(
    "five event types are mapped",
    mapping.supported_event_types() == [
        "customer.created",
        "customer.updated",
        "inventory.updated",
        "order.created",
        "order.updated",
    ],
)
ok(
    "the header wins over the body for the event type",
    mapping.extract_event_type({"event_type": "order.created"}, "customer.created")
    == "customer.created",
)
ok(
    "the body is used when no header is sent",
    mapping.extract_event_type({"type": "Order.Created"}) == "order.created",
)
ok("a body with no event type yields None", mapping.extract_event_type({"id": "x"}) is None)
ok(
    "external ids are read from the header first",
    mapping.extract_external_id({"id": "body-id"}, "header-id") == "header-id",
)
ok(
    "external ids fall back through conventional field names",
    mapping.extract_external_id({"delivery_id": "d-9"}) == "d-9"
    and mapping.extract_external_id({"eventId": 1234}) == "1234",
)
ok(
    "no external id yields None so the caller can derive one",
    mapping.extract_external_id({"name": "x"}) is None,
)
ok(
    "a nested data envelope is unwrapped",
    mapping.event_payload({"event_type": "order.created", "data": {"id": "o-1"}}) == {"id": "o-1"},
)
ok(
    "a flat body has its envelope keys stripped",
    mapping.event_payload(
        {"event_type": "order.created", "event_id": "e1", "business_id": "spoof", "total": 10}
    )
    == {"total": 10},
)

# ---------------------------------------------------------------------------
group("8 record normalisation mirrors the CSV validator")
# ---------------------------------------------------------------------------

customer = records.normalise_customer({"customer_id": "c-77", "email": "  A@B.COM ", "city": "Pune"})
ok("customer normalises", customer.valid)
ok(
    "customer id aliases resolve and email is lowercased",
    customer.fields["external_customer_id"] == "c-77"
    and customer.fields["email"] == "a@b.com"
    and customer.fields["city"] == "Pune",
)
ok(
    "first/last name are joined when no full name is sent",
    records.normalise_customer(
        {"id": "c-1", "first_name": "Asha", "last_name": "Rao"}
    ).fields["name"]
    == "Asha Rao",
)
no_id = records.normalise_customer({"email": "x@y.com"})
ok(
    "a customer with no external id is rejected, not given a generated one",
    not no_id.valid and "external_customer_id is required" in no_id.errors[0],
)
ok(
    "the rejection names the aliases it looked for",
    "customer_id" in no_id.errors[0],
)
ok(
    "absent optional fields are omitted rather than blanked",
    "name" not in records.normalise_customer({"id": "c-2"}).fields,
)

order = records.normalise_order(
    {
        "order_id": "o-5",
        "customer_id": "c-77",
        "order_date": "2026-07-01T09:30:00Z",
        "total_amount": "₹1,250.50",
        "status": "PAID",
    }
)
ok("order normalises", order.valid)
ok(
    "money survives a currency symbol and separators",
    order.fields["total_amount"] == 1250.50,
)
ok(
    "order date parses to an aware UTC datetime",
    order.fields["order_date"] == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
)
ok("status is carried through", order.fields["status"] == "PAID")
ok(
    "status defaults to pending when absent",
    records.normalise_order({"order_id": "o-6", "customer_id": "c-1"}).fields["status"]
    == "pending",
)
ok(
    "'id' on an order body means the order, never the customer",
    records.normalise_order({"id": "o-7", "customer_id": "c-9"}).fields
    == {"external_order_id": "o-7", "external_customer_id": "c-9", "status": "pending"},
)
orphan = records.normalise_order({"id": "o-8", "total": 10})
ok(
    "an order with no customer reference is rejected",
    not orphan.valid
    and any("external_customer_id is required" in err for err in orphan.errors),
)
ok(
    "a nested customer object supplies the reference",
    records.normalise_order(
        {"order_id": "o-9", "customer": {"customer_id": "c-3"}}
    ).fields["external_customer_id"]
    == "c-3",
)
ok(
    "an unparseable date is dropped rather than guessed",
    "order_date"
    not in records.normalise_order(
        {"order_id": "o-10", "customer_id": "c-1", "order_date": "some time last week"}
    ).fields,
)
ok(
    "an epoch order date parses",
    records.normalise_order(
        {"order_id": "o-11", "customer_id": "c-1", "created_at": 1767225600}
    ).fields["order_date"]
    == datetime(2026, 1, 1, tzinfo=timezone.utc),
)

inv = records.normalise_inventory(
    {"sku": "SKU-1", "quantity": "7", "reorder_level": 10, "supplier": "Acme"}
)
ok("inventory normalises", inv.valid)
ok(
    "quantity and reorder level are ints",
    inv.fields["quantity"] == 7 and inv.fields["reorder_level"] == 10,
)
ok(
    "a negative quantity is floored at zero, not accepted as negative stock",
    records.normalise_inventory({"sku": "S", "quantity": -4}).fields["quantity"] == 0,
)
ok(
    "inventory with no quantity is rejected rather than defaulted to zero",
    not records.normalise_inventory({"sku": "S"}).valid,
)
ok(
    "a non-numeric quantity is rejected",
    not records.normalise_inventory({"sku": "S", "quantity": "lots"}).valid,
)
ok(
    "stock status is derived from real numbers",
    records.derive_stock_status(0, 5) == "Out of Stock"
    and records.derive_stock_status(3, 5) == "Low Stock"
    and records.derive_stock_status(50, 5) == "In Stock"
    and records.derive_stock_status(50, 0) == "In Stock",
)
ok(
    "normalise_for_event dispatches on the internal event type",
    records.normalise_for_event("customer.updated", {"id": "c-1"}).kind == records.KIND_CUSTOMER
    and records.normalise_for_event("order.created", {"id": "o", "customer_id": "c"}).kind
    == records.KIND_ORDER,
)
ok(
    "an event with no persistence mapping returns None rather than inventing one",
    records.normalise_for_event("analytics.job.completed", {}) is None,
)
ok(
    "a non-object payload is reported as an error",
    not records.normalise_for_event("customer.created", ["not", "a", "dict"]).valid,
)

# ---------------------------------------------------------------------------
group("9 evaluate_delivery(): the composed decision")
# ---------------------------------------------------------------------------

payload = {"event_type": "customer.created", "event_id": "evt-1", "data": {"customer_id": "c-1"}}
raw = body_of(payload)


def evaluate(**overrides):
    kwargs = dict(
        source="generic",
        business_id=BIZ_A,
        raw_body=raw,
        provided_signature=sign(raw),
        timestamp=TS,
        secret=SECRET,
        tolerance_seconds=300,
        require_signature=True,
        now=NOW,
    )
    kwargs.update(overrides)
    return evaluate_delivery(**kwargs)


accepted = evaluate()
ok("a well-formed signed delivery is accepted", accepted.accepted and bool(accepted))
ok("it is routed to the internal event type", accepted.event_type == "customer.created")
ok("it carries a topic", bool(accepted.topic))
ok("the tenant comes from the verified header", accepted.business_id == BIZ_A)
ok("signature_verified is recorded as True", accepted.signature_verified)
ok("the sender's own id is used when present", accepted.external_event_id == "evt-1")
ok("the id is not marked derived", not accepted.external_id_derived)
ok("the nested data envelope is unwrapped", accepted.payload == {"customer_id": "c-1"})
ok("the accepted status code is 202", accepted.status_code == 202)

bad_source = evaluate(source="shopify")
ok(
    "an unknown source is refused with 404 before verification",
    not bad_source.accepted
    and bad_source.status_code == 404
    and bad_source.reason == REASON_UNSUPPORTED_SOURCE,
)
ok(
    "the refusal lists the sources that do exist",
    bad_source.details.get("supported_sources") == ["generic"],
)

no_biz = evaluate(business_id=None)
ok(
    "a missing business header is 401",
    not no_biz.accepted and no_biz.status_code == 401 and no_biz.reason == REASON_MISSING_BUSINESS,
)

spoofed = evaluate(business_id=BIZ_B)
ok(
    "claiming another tenant fails verification",
    not spoofed.accepted
    and spoofed.status_code == 401
    and spoofed.reason == signature.REASON_SIGNATURE_MISMATCH,
)

tampered_body = evaluate(raw_body=body_of({**payload, "data": {"customer_id": "c-EVIL"}}))
ok(
    "a body edited after signing is rejected",
    not tampered_body.accepted and tampered_body.reason == signature.REASON_SIGNATURE_MISMATCH,
)

bad_json = b"{not json"
ok(
    "invalid JSON is rejected with 400 only after the signature passes",
    (
        lambda d: not d.accepted
        and d.status_code == 400
        and d.reason == REASON_INVALID_PAYLOAD
        and d.signature_verified
    )(evaluate(raw_body=bad_json, provided_signature=sign(bad_json))),
)

array_body = b'["not","an","object"]'
ok(
    "a JSON array body is rejected",
    (
        lambda d: not d.accepted and d.reason == REASON_INVALID_PAYLOAD
    )(evaluate(raw_body=array_body, provided_signature=sign(array_body))),
)

unmapped = body_of({"event_type": "customer.deleted", "id": "x"})
unmapped_decision = evaluate(raw_body=unmapped, provided_signature=sign(unmapped))
ok(
    "an unmapped event type is rejected with 400",
    not unmapped_decision.accepted
    and unmapped_decision.status_code == 400
    and unmapped_decision.reason == REASON_UNSUPPORTED_EVENT,
)
ok(
    "the refusal lists the event types that are mapped",
    unmapped_decision.details.get("supported_event_types") == mapping.supported_event_types(),
)

no_id_body = body_of({"event_type": "customer.created", "data": {"customer_id": "c-1"}})
derived_decision = evaluate(raw_body=no_id_body, provided_signature=sign(no_id_body))
ok("a delivery with no sender id is still accepted", derived_decision.accepted)
ok("the derived id is flagged as derived", derived_decision.external_id_derived)
ok(
    "the derived id is a content hash of the canonical message",
    derived_decision.external_event_id == derive_external_id(BIZ_A, TS, no_id_body),
)
ok(
    "identical bytes derive the same id (so a replay is caught)",
    derive_external_id(BIZ_A, TS, no_id_body) == derive_external_id(BIZ_A, TS, no_id_body),
)
ok(
    "a later timestamp derives a different id (a genuine repeat is not swallowed)",
    derive_external_id(BIZ_A, TS, no_id_body)
    != derive_external_id(BIZ_A, str(int(TS) + 60), no_id_body),
)
ok(
    "another tenant's identical body derives a different id",
    derive_external_id(BIZ_A, TS, no_id_body) != derive_external_id(BIZ_B, TS, no_id_body),
)
ok(
    "the derived id contains no secret-derived material",
    SECRET not in derived_decision.external_event_id
    and expected not in derived_decision.external_event_id,
)

header_typed = body_of({"event_id": "evt-2", "customer_id": "c-1"})
ok(
    "a sender that only sets the event type header still routes",
    (
        lambda d: d.accepted and d.event_type == "customer.created"
    )(
        evaluate(
            raw_body=header_typed,
            provided_signature=sign(header_typed),
            event_type_header="customer.created",
        )
    ),
)
ok(
    "the event id header overrides the body",
    evaluate(event_id_header="hdr-9").external_event_id == "hdr-9",
)
ok(
    "an external id longer than the column is truncated, not rejected",
    len(evaluate(event_id_header="x" * 500).external_event_id) == 255,
)

unsigned_allowed = evaluate(provided_signature=None, require_signature=False)
ok(
    "with signatures not required an unsigned delivery is accepted",
    unsigned_allowed.accepted,
)
ok(
    "...but it is recorded as unverified, so the audit trail stays honest",
    not unsigned_allowed.signature_verified,
)
ok(
    "a bad signature is still rejected even when signatures are optional",
    not evaluate(provided_signature=f"sha256={'d' * 64}", require_signature=False).accepted,
)
ok(
    "an unconfigured secret is surfaced as its own reason code",
    (
        lambda d: not d.accepted and d.reason == signature.REASON_MISSING_SECRET
    )(evaluate(secret="")),
)

# ---------------------------------------------------------------------------
group("10 decision logging is safe")
# ---------------------------------------------------------------------------

fields = accepted.log_fields()
ok(
    "log fields carry the tenant and event type",
    fields["business_id"] == BIZ_A and fields["event_type"] == "customer.created",
)
ok("log fields never include the payload", "payload" not in fields)
ok(
    "log fields never include a signature, timestamp or secret",
    not any(key in fields for key in ("signature", "timestamp", "secret", "detail"))
    and SECRET not in json.dumps(fields, default=str),
)
ok(
    "a rejected decision logs a reason and no echo of the input",
    (
        lambda f: f["reason"] == signature.REASON_SIGNATURE_MISMATCH
        and "detail" not in f
        and "raw_body" not in f
    )(spoofed.log_fields()),
)

# ---------------------------------------------------------------------------
group("11 tenant isolation of a full two-tenant exchange")
# ---------------------------------------------------------------------------

a_body = body_of({"event_type": "order.created", "event_id": "shared-id", "data": {"id": "o-1", "customer_id": "c-1"}})
a_decision = evaluate_delivery(
    source="generic",
    business_id=BIZ_A,
    raw_body=a_body,
    provided_signature=sign(a_body, business_id=BIZ_A),
    timestamp=TS,
    secret=SECRET,
    tolerance_seconds=300,
    require_signature=True,
    now=NOW,
)
b_decision = evaluate_delivery(
    source="generic",
    business_id=BIZ_B,
    raw_body=a_body,
    provided_signature=sign(a_body, business_id=BIZ_B),
    timestamp=TS,
    secret=SECRET,
    tolerance_seconds=300,
    require_signature=True,
    now=NOW,
)
ok("both tenants can send the same event id", a_decision.accepted and b_decision.accepted)
ok(
    "each delivery is attributed to its own tenant",
    a_decision.business_id == BIZ_A and b_decision.business_id == BIZ_B,
)
ok(
    "a shared external id is not a cross-tenant collision (the unique key includes the business)",
    a_decision.external_event_id == b_decision.external_event_id == "shared-id",
)
ok(
    "a payload field claiming another tenant is stripped from the event payload",
    "business_id" not in mapping.event_payload({"event_type": "order.created", "business_id": BIZ_B, "total": 5}),
)

# ---------------------------------------------------------------------------
print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED after {PASSED} passing:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)

print(f"ALL {PASSED} WEBHOOK CHECKS PASSED")
