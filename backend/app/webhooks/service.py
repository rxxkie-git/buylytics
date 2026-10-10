"""Webhook orchestration: verify, persist, publish.

The one place where an unauthenticated HTTP request turns into tenant-owned data,
so the ordering below is deliberate and should not be rearranged:

1. **Is the source known?** An unknown source is refused before any work.
2. **Is the signature valid?** Nothing is parsed, stored or published until the
   HMAC over ``"{business_id}.{timestamp}.{raw body}"`` checks out. The tenant is
   read *out of verified material*, never trusted from a bare header.
3. **Does the tenant exist?** A signed request naming a business that is not in
   the database is a configuration error, not a data write.
4. **Is this a replay?** The unique index on
   ``(business_id, source, external_event_id)`` decides, not application logic.
5. **Persist**, in one transaction with the audit row.
6. **Publish**, after the commit, so caches are only invalidated once the data
   they describe is actually durable.

Step 6 comes last on purpose. Publishing before the commit would let a consumer
invalidate the cache and re-read the old row, restoring stale data under a fresh
TTL — the one ordering that produces a *persistently* wrong dashboard rather than
a briefly wrong one.

Verification and normalisation live in :mod:`app.webhooks.signature`,
:mod:`app.webhooks.mapping` and :mod:`app.webhooks.records`, none of which import
SQLAlchemy. :func:`evaluate_delivery` composes them and is likewise
database-free, so the security decision can be tested on its own.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.cache import keys as cache_keys
from app.config import settings
from app.core.concurrency import run_blocking
from app.core.exceptions import (
    ConfigurationError,
    NotFoundError,
    ValidationError,
    WebhookVerificationError,
)
from app.core.logging import get_logger
from app.events import EventType, make_event
from app.webhooks import mapping, records, signature

logger = get_logger(__name__)

STATUS_RECEIVED = "received"
STATUS_PROCESSED = "processed"
STATUS_DUPLICATE = "duplicate"
STATUS_FAILED = "failed"

REASON_UNSUPPORTED_SOURCE = "unsupported_source"
REASON_UNSUPPORTED_EVENT = "unsupported_event_type"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_MISSING_BUSINESS = "missing_business_id"


@dataclass(frozen=True)
class WebhookDecision:
    """Everything decided about a delivery before the database is touched."""

    accepted: bool
    status_code: int = 202
    reason: str = signature.REASON_OK
    detail: str | None = None
    business_id: str = ""
    source: str = ""
    inbound_event_type: str = ""
    event_type: str = ""
    topic: str = ""
    external_event_id: str = ""
    external_id_derived: bool = False
    signature_verified: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.accepted

    def log_fields(self) -> dict[str, Any]:
        """Safe structured-log fields.

        No payload, no signature, no timestamp header — a rejected delivery is
        exactly when an attacker would like to see their input echoed back.
        """
        return {
            "webhook_source": self.source,
            "business_id": self.business_id or None,
            "inbound_event_type": self.inbound_event_type or None,
            "event_type": self.event_type or None,
            "signature_verified": self.signature_verified,
            "reason": self.reason,
        }


def signature_required() -> bool:
    """Whether a signature must be present.

    ``WEBHOOK_REQUIRE_SIGNATURE=false`` is a local-development affordance for
    poking the endpoint with curl. It is overridden in production regardless of
    what the environment says: an unauthenticated write path into tenant data is
    not something a stray environment variable gets to open.
    """
    if settings.is_production:
        return True

    return settings.webhook_require_signature


def derive_external_id(business_id: str, timestamp: str | None, raw_body: bytes) -> str:
    """Fallback delivery id for senders that do not provide one.

    Hashing the canonical message means byte-identical replays collapse to the
    same id and are caught by the unique index, while a genuinely new delivery
    (different body, or the same body at a later timestamp) gets a different one.

    The hash is over the *message*, not the signature, so no secret-derived value
    is ever written to the database.
    """
    digest = hashlib.sha256(
        signature.canonical_message(str(business_id), str(timestamp or ""), raw_body or b"")
    ).hexdigest()
    return f"sha256:{digest}"


def evaluate_delivery(
    *,
    source: str,
    business_id: str | None,
    raw_body: bytes,
    provided_signature: str | None = None,
    timestamp: str | None = None,
    event_type_header: str | None = None,
    event_id_header: str | None = None,
    secret: str | None = None,
    tolerance_seconds: int | None = None,
    require_signature: bool | None = None,
    now: datetime | None = None,
) -> WebhookDecision:
    """Decide whether a delivery is acceptable. No database, no side effects."""
    normalised_source = mapping.normalise_source(source)

    if not mapping.is_supported_source(normalised_source):
        return WebhookDecision(
            accepted=False,
            status_code=404,
            reason=REASON_UNSUPPORTED_SOURCE,
            detail=f"No webhook mapping is configured for source '{normalised_source or source}'.",
            source=normalised_source,
            details={"supported_sources": list(mapping.SUPPORTED_SOURCES)},
        )

    tenant = str(business_id or "").strip()

    if not tenant:
        return WebhookDecision(
            accepted=False,
            status_code=401,
            reason=REASON_MISSING_BUSINESS,
            detail=(
                f"The {settings.webhook_business_header} header is required and must match "
                "the business the signature was computed for."
            ),
            source=normalised_source,
        )

    effective_secret = settings.webhook_secret if secret is None else secret
    tolerance = (
        settings.webhook_tolerance_seconds if tolerance_seconds is None else tolerance_seconds
    )
    must_verify = signature_required() if require_signature is None else require_signature

    verified = False

    if must_verify or provided_signature:
        check = signature.verify(
            effective_secret,
            business_id=tenant,
            timestamp=timestamp,
            body=raw_body or b"",
            provided_signature=provided_signature,
            tolerance_seconds=tolerance,
            now=now,
        )

        if not check.verified:
            # An unsigned request that was *allowed* to be unsigned is different
            # from one that failed verification; only the latter is rejected.
            if must_verify or provided_signature:
                return WebhookDecision(
                    accepted=False,
                    status_code=401,
                    reason=check.reason,
                    detail=check.detail,
                    business_id=tenant,
                    source=normalised_source,
                )

        verified = check.verified

    # Parsing happens only after verification: unverified bytes get no JSON parser.
    if raw_body:
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return WebhookDecision(
                accepted=False,
                status_code=400,
                reason=REASON_INVALID_PAYLOAD,
                detail=f"Request body must be valid UTF-8 JSON: {exc.__class__.__name__}.",
                business_id=tenant,
                source=normalised_source,
                signature_verified=verified,
            )
    else:
        parsed = {}

    if not isinstance(parsed, dict):
        return WebhookDecision(
            accepted=False,
            status_code=400,
            reason=REASON_INVALID_PAYLOAD,
            detail="Request body must be a JSON object.",
            business_id=tenant,
            source=normalised_source,
            signature_verified=verified,
        )

    inbound_event_type = mapping.extract_event_type(parsed, event_type_header)
    route = mapping.resolve_route(normalised_source, inbound_event_type)

    if route is None:
        return WebhookDecision(
            accepted=False,
            status_code=400,
            reason=REASON_UNSUPPORTED_EVENT,
            detail=(
                f"Event type '{inbound_event_type or 'unknown'}' is not mapped for source "
                f"'{normalised_source}'."
            ),
            business_id=tenant,
            source=normalised_source,
            inbound_event_type=inbound_event_type or "",
            signature_verified=verified,
            details={"supported_event_types": mapping.supported_event_types(normalised_source)},
        )

    external_id = mapping.extract_external_id(parsed, event_id_header)
    derived = external_id is None

    if derived:
        external_id = derive_external_id(tenant, timestamp, raw_body or b"")

    return WebhookDecision(
        accepted=True,
        status_code=202,
        business_id=tenant,
        source=normalised_source,
        inbound_event_type=inbound_event_type or "",
        event_type=route.event_type,
        topic=route.topic,
        external_event_id=str(external_id)[:255],
        external_id_derived=derived,
        signature_verified=verified,
        payload=mapping.event_payload(parsed),
    )


def _raise_for(decision: WebhookDecision) -> None:
    """Translate a rejection into the matching application error."""
    if decision.reason == REASON_UNSUPPORTED_SOURCE:
        raise NotFoundError(
            decision.detail or "Unknown webhook source",
            code=decision.reason,
            details=decision.details,
        )

    if decision.reason in (REASON_UNSUPPORTED_EVENT, REASON_INVALID_PAYLOAD):
        raise ValidationError(
            decision.detail or "Webhook payload could not be accepted",
            code=decision.reason,
            status_code=400,
            details=decision.details,
        )

    if decision.reason == signature.REASON_MISSING_SECRET:
        raise ConfigurationError(
            "Webhook ingestion is not configured on this server.",
            code=decision.reason,
        )

    raise WebhookVerificationError(
        decision.detail or "Webhook signature verification failed",
        code=decision.reason,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _resolve_customer_id(session, *, business_id: str, external_customer_id: str) -> str:
    """Find or create the customer an order belongs to.

    Creating a shell customer is the honest option: the order genuinely happened,
    and dropping it would understate that customer's frequency and monetary value
    forever. The shell carries only the external id — no invented name or email.
    """
    from app.repositories.customer_repository import CustomerRepository

    repository = CustomerRepository(session)
    existing = repository.get_by_external_id(
        business_id=business_id, external_customer_id=external_customer_id
    )

    if existing is not None:
        return str(existing.id)

    created = repository.create(
        business_id=business_id, external_customer_id=external_customer_id
    )
    return str(created.id)


def _persist_customer(session, *, business_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    from app.repositories.customer_repository import CustomerRepository

    repository = CustomerRepository(session)
    external_id = str(fields["external_customer_id"])
    existing = repository.get_by_external_id(
        business_id=business_id, external_customer_id=external_id
    )

    if existing is None:
        created = repository.create(
            business_id=business_id,
            external_customer_id=external_id,
            **{key: value for key, value in fields.items() if key != "external_customer_id"},
        )
        return {"entity": "customer", "id": str(created.id), "created": True}

    for key, value in fields.items():
        if key == "external_customer_id":
            continue
        # A partial update must not blank a field the sender simply omitted.
        if value is not None:
            setattr(existing, key, value)

    session.flush()
    return {"entity": "customer", "id": str(existing.id), "created": False}


def _persist_order(session, *, business_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    from app.repositories.order_repository import OrderRepository

    payload = dict(fields)
    external_order_id = str(payload.pop("external_order_id"))
    external_customer_id = str(payload.pop("external_customer_id"))

    customer_id = _resolve_customer_id(
        session, business_id=business_id, external_customer_id=external_customer_id
    )

    repository = OrderRepository(session)
    existing = repository.get_by_external_id(
        business_id=business_id, external_order_id=external_order_id
    )

    if existing is None:
        created = repository.create(
            business_id=business_id,
            external_order_id=external_order_id,
            customer_id=customer_id,
            **payload,
        )
        return {"entity": "order", "id": str(created.id), "created": True}

    existing.customer_id = customer_id

    for key, value in payload.items():
        if value is not None:
            setattr(existing, key, value)

    session.flush()
    return {"entity": "order", "id": str(existing.id), "created": False}


def _persist_inventory(session, *, business_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    from app.db.models.inventory import Inventory
    from app.repositories.product_repository import ProductRepository
    from sqlalchemy import select

    payload = dict(fields)
    external_product_id = str(payload.pop("external_product_id"))
    product_name = payload.pop("product_name", None)
    sku = payload.pop("sku", None)

    products = ProductRepository(session)
    product = products.get_by_external_id(
        business_id=business_id, external_product_id=external_product_id
    )

    if product is None:
        product_fields: dict[str, Any] = {}
        if product_name:
            product_fields["name"] = product_name
        if sku:
            product_fields["sku"] = sku

        product = products.create(
            business_id=business_id,
            external_product_id=external_product_id,
            **product_fields,
        )
    else:
        if product_name and not product.name:
            product.name = product_name
        if sku and not product.sku:
            product.sku = sku

    stmt = select(Inventory).where(
        Inventory.business_id == str(business_id),
        Inventory.product_id == str(product.id),
    )
    row = session.execute(stmt).scalars().first()

    quantity = int(payload.get("quantity", 0))
    reorder_level = int(payload.get("reorder_level", row.reorder_level if row else 0) or 0)
    status = payload.get("status") or records.derive_stock_status(quantity, reorder_level)

    if row is None:
        row = Inventory(
            business_id=str(business_id),
            product_id=str(product.id),
            quantity=quantity,
            reserved_quantity=int(payload.get("reserved_quantity", 0) or 0),
            reorder_level=reorder_level,
            supplier=payload.get("supplier"),
            status=status,
        )
        session.add(row)
        session.flush()
        created = True
    else:
        row.quantity = quantity
        row.reorder_level = reorder_level
        row.status = status
        if payload.get("reserved_quantity") is not None:
            row.reserved_quantity = int(payload["reserved_quantity"])
        if payload.get("supplier"):
            row.supplier = payload["supplier"]
        session.flush()
        created = False

    return {
        "entity": "inventory",
        "id": str(row.id),
        "product_id": str(product.id),
        "created": created,
        "quantity": quantity,
        "reorder_level": reorder_level,
        "low_stock": reorder_level > 0 and quantity <= reorder_level,
    }


_PERSISTERS = {
    records.KIND_CUSTOMER: _persist_customer,
    records.KIND_ORDER: _persist_order,
    records.KIND_INVENTORY: _persist_inventory,
}


def _persist(session, *, business_id: str, record: records.NormalisedRecord) -> dict[str, Any]:
    persister = _PERSISTERS.get(record.kind)

    if persister is None:  # pragma: no cover - kinds and persisters are declared together
        return {"entity": record.kind, "skipped": True}

    return persister(session, business_id=business_id, fields=record.fields)


def _cache_prefixes(event_type: str, business_id: str) -> list[str]:
    if event_type in (EventType.CUSTOMER_CREATED, EventType.CUSTOMER_UPDATED):
        return cache_keys.customer_write_prefixes(business_id)

    if event_type in (EventType.ORDER_CREATED, EventType.ORDER_UPDATED):
        return cache_keys.order_write_prefixes(business_id)

    if event_type in (EventType.INVENTORY_UPDATED, EventType.INVENTORY_LOW_STOCK):
        return cache_keys.inventory_write_prefixes(business_id) + [
            cache_keys.namespace_prefix(business_id, cache_keys.PRODUCTS_NS)
        ]

    return [cache_keys.tenant_prefix(business_id)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _business_exists(session, business_id: str) -> bool:
    from app.repositories.business_repository import BusinessRepository

    return BusinessRepository(session).get_business(business_id) is not None


async def process_delivery(
    session,
    *,
    source: str,
    business_id: str | None,
    raw_body: bytes,
    provided_signature: str | None = None,
    timestamp: str | None = None,
    event_type_header: str | None = None,
    event_id_header: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify, record and publish one delivery.

    Raises an :class:`app.core.exceptions.AppError` subclass on rejection so the
    route layer does not have to map reason codes to status codes.

    Structure worth noting: signature verification stays on the event loop (it is
    an HMAC over a small body — microseconds, and it must reject bad callers
    without spending a worker slot on them), then every database interaction runs
    in **one** worker thread via :func:`run_blocking`, and only the event publish
    comes back to the loop. Previously the whole chain — existence check, claim,
    persist, mark, commit — ran on the loop, so a burst of webhook deliveries
    could stall unrelated dashboard requests for as long as Supabase took to
    answer.

    The DB work is one closure rather than several offloads because it is one
    transaction. Splitting it would let another task interleave on the shared
    session between the claim and the commit.
    """
    decision = evaluate_delivery(
        source=source,
        business_id=business_id,
        raw_body=raw_body,
        provided_signature=provided_signature,
        timestamp=timestamp,
        event_type_header=event_type_header,
        event_id_header=event_id_header,
        now=now,
    )

    if not decision.accepted:
        logger.warning("Webhook delivery rejected", extra=decision.log_fields())
        _raise_for(decision)

    def _record() -> dict[str, Any]:
        """All of the synchronous database work for one delivery."""
        if not _business_exists(session, decision.business_id):
            # Reached only with a valid signature, so this is a misconfigured
            # sender rather than an attack. Saying so plainly is safe and saves an
            # hour of debugging; it reveals nothing an attacker could not already
            # sign for.
            logger.warning("Webhook for unknown business", extra=decision.log_fields())
            raise NotFoundError(
                "The business this webhook is signed for does not exist.",
                code="business_not_found",
            )

        from app.repositories.webhook_repository import WebhookRepository

        webhooks = WebhookRepository(session)
        record_row, accepted = webhooks.claim_delivery(
            business_id=decision.business_id,
            source=decision.source,
            external_event_id=decision.external_event_id,
            event_type=decision.event_type,
            signature_verified=decision.signature_verified,
            payload=decision.payload or None,
        )

        if not accepted:
            session.commit()
            logger.info(
                "Webhook delivery is a duplicate; nothing republished",
                extra={**decision.log_fields(), "webhook_event_id": str(record_row.id)},
            )
            return {
                "duplicate": True,
                "response": {
                    "accepted": True,
                    "duplicate": True,
                    "webhook_event_id": str(record_row.id),
                    "event_type": record_row.event_type,
                    "status": record_row.status,
                    "status_code": 200,
                },
            }

        webhook_event_id = str(record_row.id)
        normalised = records.normalise_for_event(decision.event_type, decision.payload)
        persisted: dict[str, Any] | None = None

        if normalised is not None and not normalised.valid:
            # The delivery was authentic but unusable. Keep the audit row so the
            # sender's retry is still deduplicated, and report why.
            webhooks.mark_status(
                webhook_event_id=webhook_event_id,
                status=STATUS_FAILED,
                error_message="; ".join(normalised.errors)[:1000],
            )
            session.commit()
            logger.warning(
                "Webhook payload failed validation",
                extra={**decision.log_fields(), "webhook_event_id": webhook_event_id},
            )
            raise ValidationError(
                "Webhook payload is missing required fields.",
                code="webhook_payload_invalid",
                status_code=400,
                details={"errors": list(normalised.errors), "webhook_event_id": webhook_event_id},
            )

        try:
            if normalised is not None:
                persisted = _persist(
                    session, business_id=decision.business_id, record=normalised
                )

            webhooks.mark_status(webhook_event_id=webhook_event_id, status=STATUS_PROCESSED)
            session.commit()
        except Exception as exc:
            session.rollback()
            # The audit row went back with the rollback, so re-record the failure
            # in a fresh transaction. Without this the sender's retry would look
            # brand new.
            try:
                failed_row, _ = webhooks.claim_delivery(
                    business_id=decision.business_id,
                    source=decision.source,
                    external_event_id=decision.external_event_id,
                    event_type=decision.event_type,
                    signature_verified=decision.signature_verified,
                    payload=decision.payload or None,
                )
                webhooks.mark_status(
                    webhook_event_id=str(failed_row.id),
                    status=STATUS_FAILED,
                    error_message=f"{exc.__class__.__name__}: {exc}"[:1000],
                )
                session.commit()
            except Exception:  # pragma: no cover - the database is in real trouble
                session.rollback()
                logger.error("Could not record webhook failure", exc_info=True)

            logger.error(
                "Webhook persistence failed",
                exc_info=exc,
                extra={**decision.log_fields(), "webhook_event_id": webhook_event_id},
            )
            raise

        return {
            "duplicate": False,
            "webhook_event_id": webhook_event_id,
            "persisted": persisted,
        }

    # Bounded so a webhook sender cannot hold a request open indefinitely against
    # a slow database. The +5s is headroom over the server-side statement timeout,
    # which is what actually stops the query.
    outcome = await run_blocking(
        _record,
        operation="webhook.record",
        timeout_seconds=float(settings.db_statement_timeout_seconds) + 5.0,
    )

    if outcome["duplicate"]:
        return outcome["response"]

    webhook_event_id = outcome["webhook_event_id"]
    persisted = outcome["persisted"]

    publish = await _publish(decision, webhook_event_id=webhook_event_id, persisted=persisted)

    logger.info(
        "Webhook delivery accepted",
        extra={
            **decision.log_fields(),
            "webhook_event_id": webhook_event_id,
            "published": publish["published"],
            "transport": publish["transport"],
        },
    )

    return {
        "accepted": True,
        "duplicate": False,
        "webhook_event_id": webhook_event_id,
        "event_type": decision.event_type,
        "status": STATUS_PROCESSED,
        "status_code": 202,
        "signature_verified": decision.signature_verified,
        "external_event_id": decision.external_event_id,
        "external_event_id_derived": decision.external_id_derived,
        "persisted": persisted,
        **publish,
    }


async def _publish(
    decision: WebhookDecision,
    *,
    webhook_event_id: str,
    persisted: dict[str, Any] | None,
) -> dict[str, Any]:
    """Announce the change after the commit. Never raises."""
    from app.cache import invalidate_prefixes
    from app.events.producer import publish_event

    payload: dict[str, Any] = {
        "webhook_event_id": webhook_event_id,
        "source": decision.source,
        "external_event_id": decision.external_event_id,
    }

    if persisted:
        payload.update(
            {key: value for key, value in persisted.items() if key in ("entity", "id", "created")}
        )

    if decision.event_type in (EventType.CUSTOMER_CREATED, EventType.CUSTOMER_UPDATED):
        payload["customer_id"] = (persisted or {}).get("id")
    elif decision.event_type in (EventType.ORDER_CREATED, EventType.ORDER_UPDATED):
        payload["order_id"] = (persisted or {}).get("id")
    elif decision.event_type == EventType.INVENTORY_UPDATED:
        payload["product_id"] = (persisted or {}).get("product_id")
        payload["quantity_available"] = (persisted or {}).get("quantity")

    event = make_event(
        decision.event_type,
        decision.business_id,
        payload,
        source="webhook",
    )

    result = await publish_event(decision.topic, event)

    # Handlers clear caches when they run, but the null transport has no handlers
    # and a broker outage means nobody consumed this. Invalidating here keeps the
    # dashboard correct in both cases; a redundant delete is harmless.
    if not result.delivered:
        await invalidate_prefixes(_cache_prefixes(decision.event_type, decision.business_id))

    published = {
        "published": result.delivered,
        "transport": result.transport,
        "event_id": event.event_id,
    }

    if (persisted or {}).get("low_stock"):
        await _announce_low_stock(decision, persisted or {})

    return published


async def _announce_low_stock(decision: WebhookDecision, persisted: dict[str, Any]) -> None:
    """Emit a low-stock event when a real quantity crossed a real threshold."""
    from app.events.producer import publish_event

    event = make_event(
        EventType.INVENTORY_LOW_STOCK,
        decision.business_id,
        {
            "product_id": persisted.get("product_id"),
            "quantity_available": persisted.get("quantity"),
            "threshold": persisted.get("reorder_level"),
            "source": decision.source,
        },
        source="webhook",
    )
    await publish_event(decision.topic, event)


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


def serialise_delivery(row) -> dict[str, Any]:
    """Shape a delivery for the API.

    ``payload`` is omitted: it is tenant business data that the caller can already
    see through the customers and orders endpoints, and echoing it back through a
    list view is needless exposure.
    """
    return {
        "id": str(row.id),
        "source": row.source,
        "event_type": row.event_type,
        "external_event_id": row.external_event_id,
        "signature_verified": bool(row.signature_verified),
        "status": row.status,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
        "error_message": row.error_message,
    }


def list_deliveries(
    session,
    *,
    business_id: str,
    source: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from app.repositories.webhook_repository import WebhookRepository

    rows = WebhookRepository(session).list_recent(
        business_id=business_id, source=source, status=status, limit=limit
    )
    return [serialise_delivery(row) for row in rows]


def delivery_stats(session, *, business_id: str) -> dict[str, Any]:
    from app.repositories.webhook_repository import WebhookRepository

    counts = WebhookRepository(session).counts_by_status(business_id=business_id)

    return {
        "total": sum(counts.values()),
        "by_status": counts,
        "configured": bool(settings.webhook_secret),
        "signature_required": signature_required(),
        "supported_sources": list(mapping.SUPPORTED_SOURCES),
        "supported_event_types": mapping.supported_event_types(),
        "persisted_event_types": records.persistable_event_types(),
        "signature_header": settings.webhook_signature_header,
        "timestamp_header": settings.webhook_timestamp_header,
        "business_header": settings.webhook_business_header,
        "event_id_header": settings.webhook_event_id_header,
        "tolerance_seconds": settings.webhook_tolerance_seconds,
    }


__all__ = [
    "REASON_INVALID_PAYLOAD",
    "REASON_MISSING_BUSINESS",
    "REASON_UNSUPPORTED_EVENT",
    "REASON_UNSUPPORTED_SOURCE",
    "STATUS_DUPLICATE",
    "STATUS_FAILED",
    "STATUS_PROCESSED",
    "STATUS_RECEIVED",
    "WebhookDecision",
    "delivery_stats",
    "derive_external_id",
    "evaluate_delivery",
    "list_deliveries",
    "process_delivery",
    "serialise_delivery",
    "signature_required",
]
