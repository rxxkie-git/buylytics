"""Analytics service for RFM, segmentation, and analytics jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.analytics import AnalyticsRun, CustomerRFM, CustomerSegment
from app.repositories.analytics_repository import AnalyticsRepository


class AnalyticsService:
    """Service for analytics calculation and persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AnalyticsRepository(session)

    def create_analytics_job(
        self,
        business_id: str,
        dataset_import_id: str,
        job_type: str,
    ) -> AnalyticsRun:
        """Create a new analytics job."""
        return self.repository.create_analytics_run(
            business_id=business_id,
            dataset_import_id=dataset_import_id,
            job_type=job_type,
            status="pending",
        )

    def mark_job_running(self, job_id: str) -> AnalyticsRun | None:
        """Mark a job as running."""
        return self.repository.update_analytics_run(job_id, status="running")

    def mark_job_completed(self, job_id: str) -> AnalyticsRun | None:
        """Mark a job as completed."""
        return self.repository.update_analytics_run(
            job_id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )

    def mark_job_failed(self, job_id: str, error_message: str) -> AnalyticsRun | None:
        """Mark a job as failed."""
        return self.repository.update_analytics_run(
            job_id,
            status="failed",
            error_message=error_message,
            completed_at=datetime.now(timezone.utc),
        )

    def save_rfm(
        self,
        business_id: str,
        customer_id: str,
        dataset_import_id: str,
        **fields: Any,
    ) -> CustomerRFM:
        """Save RFM calculation for a customer."""
        return self.repository.save_rfm(
            business_id=business_id,
            customer_id=customer_id,
            dataset_import_id=dataset_import_id,
            **fields,
        )

    def save_segment(
        self,
        business_id: str,
        customer_id: str,
        dataset_import_id: str,
        **fields: Any,
    ) -> CustomerSegment:
        """Save segmentation result for a customer."""
        return self.repository.save_segments(
            business_id=business_id,
            customer_id=customer_id,
            dataset_import_id=dataset_import_id,
            **fields,
        )

    def get_rfm(
        self,
        business_id: str,
        customer_id: str | None = None,
    ) -> list[CustomerRFM]:
        """Get RFM data for customers."""
        return self.repository.get_rfm(business_id=business_id, customer_id=customer_id)

    def get_segments(
        self,
        business_id: str,
        customer_id: str | None = None,
    ) -> list[CustomerSegment]:
        """Get segmentation data for customers."""
        return self.repository.get_segments(business_id=business_id, customer_id=customer_id)
