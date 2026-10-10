"""Analytics API routes for v1.

Two generations of endpoint live here on purpose.

The Phase 2 endpoints under ``/jobs`` are the real interface: a POST queues work
and returns immediately with a job id, and a GET reports the state. The API
process never runs an analysis on the request thread, so a tenant with a large
order history cannot make a request time out.

``POST /jobs/rfm`` is the Phase 1 spelling. It is kept because Phase 1 clients
call it, but it now queues through the same lifecycle rather than creating a row
nothing ever picks up.

Every route reads its tenant from the authenticated business context. The one
identifier a client may pass — a job id — is looked up with the business id in
the ``WHERE`` clause, so another tenant's id reads as not found.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app.analytics import job_types
from app.core.multi_tenancy import BusinessContext, get_business_context
from app.services import analytics_job_service
from app.services.analytics_service import AnalyticsService
from app.services.import_service import ImportService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def submit_analytics_job(
    response: Response,
    job_type: str = Body(
        ...,
        embed=True,
        description=f"One of: {', '.join(sorted(job_types.SUPPORTED_JOB_TYPES))}",
    ),
    import_id: str | None = Body(None, embed=True),
    reference_date: datetime | None = Body(
        None,
        embed=True,
        description="Treat this instant as 'now' when measuring recency. Defaults to now.",
    ),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Queue an analytics job.

    Returns ``202`` with a job id to poll. ``200`` would claim the work is done.

    A supplied ``import_id`` is verified to belong to this business before it is
    stored — it is a client-supplied identifier, and attaching another tenant's
    import to this tenant's run would leak the fact that the import exists.
    """
    if not job_types.is_supported(job_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Unknown analytics job type '{job_type}'.",
                "supported": sorted(job_types.SUPPORTED_JOB_TYPES),
            },
        )

    if import_id is not None:
        import_record = ImportService(business_context.session).get_import(import_id)

        if import_record is None or import_record.business_id != business_context.business_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")

    payload = await analytics_job_service.submit_job(
        business_context.session,
        business_id=business_context.business_id,
        job_type=job_type,
        requested_by=business_context.user_id,
        dataset_import_id=import_id,
        reference_date=reference_date,
    )

    # A queued-but-undispatched job is a real state, not a success. Saying so in
    # the status code stops a client from waiting forever on a job no worker will
    # ever see.
    if not payload.get("dispatched"):
        response.status_code = status.HTTP_200_OK

    return payload


@router.get("/jobs")
def list_analytics_jobs(
    job_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Recent jobs for this business, newest first."""
    jobs = analytics_job_service.list_jobs(
        business_context.session,
        business_id=business_context.business_id,
        job_type=job_type,
        status=status_filter,
        limit=limit,
    )
    return {"count": len(jobs), "results": jobs, "supported_job_types": sorted(job_types.SUPPORTED_JOB_TYPES)}


@router.get("/jobs/{job_id}")
def get_analytics_job(
    job_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Current state of one job.

    ``analytics_job_service.get_job`` raises ``NotFoundError`` for both an unknown
    id and another tenant's id, which the app-wide handler renders as 404. The two
    cases are indistinguishable to the caller by design.
    """
    return analytics_job_service.get_job(
        business_context.session,
        business_id=business_context.business_id,
        job_id=job_id,
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_analytics_job(
    job_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Cancel a running or queued job for this business."""
    return analytics_job_service.cancel_job(
        business_context.session,
        business_id=business_context.business_id,
        job_id=job_id,
    )


@router.post("/jobs/reap-stale")
def reap_stale_analytics_jobs(
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Reap stuck jobs for this business."""
    reaped = analytics_job_service.reap_stale_jobs(
        business_context.session,
        business_id=business_context.business_id,
    )
    return {"reaped": reaped}



@router.post("/jobs/rfm", status_code=status.HTTP_202_ACCEPTED)
async def create_rfm_job(
    response: Response,
    import_id: str,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Queue an RFM job for a completed import.

    The Phase 1 signature, preserved for existing clients. It used to create a
    row with no worker behind it; now it queues through the same lifecycle as
    ``POST /jobs``.
    """
    import_service = ImportService(business_context.session)
    import_record = import_service.get_import(import_id)

    if import_record is None or import_record.business_id != business_context.business_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")

    if import_record.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Import must be completed before creating an analysis job",
        )

    payload = await analytics_job_service.submit_job(
        business_context.session,
        business_id=business_context.business_id,
        job_type=job_types.JOB_RFM,
        requested_by=business_context.user_id,
        dataset_import_id=import_id,
    )

    if not payload.get("dispatched"):
        response.status_code = status.HTTP_200_OK

    return payload


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@router.get("/rfm")
def get_rfm_results(
    customer_id: str | None = None,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Stored RFM rows, including the Phase 2 scores and provenance."""
    analytics_service = AnalyticsService(business_context.session)
    rfm_results = analytics_service.get_rfm(
        business_id=business_context.business_id,
        customer_id=customer_id,
    )

    return {
        "count": len(rfm_results),
        "results": [
            {
                "customer_id": r.customer_id,
                "recency": r.recency,
                "frequency": r.frequency,
                "monetary": float(r.monetary) if r.monetary is not None else None,
                "total_orders": r.total_orders,
                "average_order_value": (
                    float(r.average_order_value) if r.average_order_value is not None else None
                ),
                "r_score": r.r_score,
                "f_score": r.f_score,
                "m_score": r.m_score,
                "rfm_segment": r.rfm_segment,
                # Where the numbers came from. A consumer that wants to treat
                # measured and uploaded values differently needs to be able to
                # tell them apart.
                "source": r.source,
                "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
            }
            for r in rfm_results
        ],
    }


@router.get("/segments")
def get_segment_results(
    customer_id: str | None = None,
    business_context: BusinessContext = Depends(get_business_context),
) -> dict:
    """Stored segmentation rows."""
    analytics_service = AnalyticsService(business_context.session)
    segment_results = analytics_service.get_segments(
        business_id=business_context.business_id,
        customer_id=customer_id,
    )

    return {
        "count": len(segment_results),
        "results": [
            {
                "customer_id": r.customer_id,
                "segment": r.segment,
                "cluster": r.cluster,
                "score": float(r.score) if r.score is not None else None,
                "churn_risk": r.churn_risk,
                "model_version": r.model_version,
                "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
            }
            for r in segment_results
        ],
    }
