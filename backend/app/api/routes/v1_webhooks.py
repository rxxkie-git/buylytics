"""Webhook ingestion routes.

``POST /api/v1/webhooks/{source}`` is the only unauthenticated write path in the
application. It has no Clerk session, so its tenant comes from the HMAC signature
instead: the business id is part of the signed material, which makes it as
trustworthy as the shared secret and no more forgeable than one.

The raw request body is read with ``await request.body()`` and passed through
untouched. Signing is byte-exact — re-serialising a parsed model would reorder
keys and change whitespace, and every signature would fail.

The management endpoints below it *are* authenticated and tenant-scoped through
:func:`app.core.multi_tenancy.get_business_context`, so one business can never
read another's delivery history.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core.multi_tenancy import BusinessContext, get_business_context
from app.db.session import get_db
from app.webhooks import service

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.get("/config")
def webhook_config(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """How to send this business a webhook, plus delivery counts.

    Returns header names and the replay window so an integrator can build a
    correct sender. It never returns the secret — that is shared out of band.
    """
    stats = service.delivery_stats(
        business_context.session, business_id=business_context.business_id
    )
    return {
        "business_id": business_context.business_id,
        "endpoint": f"/api/v1/webhooks/{{source}}",
        "signature_scheme": (
            "hex HMAC-SHA256 over \"{business_id}.{timestamp}.{raw_body}\", "
            "sent as 'sha256=<digest>'"
        ),
        **stats,
    }


@router.get("/deliveries")
def list_deliveries(
    source: str | None = Query(default=None, max_length=50),
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Recent deliveries for the authenticated business."""
    deliveries = service.list_deliveries(
        business_context.session,
        business_id=business_context.business_id,
        source=source,
        status=status,
        limit=limit,
    )
    return {"count": len(deliveries), "deliveries": deliveries}


@router.post("/{source}")
async def receive_webhook(
    source: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    """Receive one webhook delivery.

    Unauthenticated by design; the signature is the credential. Returns 202 for a
    newly accepted delivery and 200 for a recognised duplicate, so a retrying
    sender sees success and stops retrying instead of hammering the endpoint.
    """
    raw_body = await request.body()

    result = await service.process_delivery(
        session,
        source=source,
        business_id=request.headers.get(settings.webhook_business_header),
        raw_body=raw_body,
        provided_signature=request.headers.get(settings.webhook_signature_header),
        timestamp=request.headers.get(settings.webhook_timestamp_header),
        event_type_header=request.headers.get("X-Webhook-Event-Type"),
        event_id_header=request.headers.get(settings.webhook_event_id_header),
    )

    response.status_code = result.pop("status_code", 202)
    return result
