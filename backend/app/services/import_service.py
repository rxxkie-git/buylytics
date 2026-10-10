"""Data import service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.dataset import DatasetImport
from app.repositories.dataset_repository import DatasetRepository


class ImportService:
    """Service for managing data imports."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = DatasetRepository(session)

    def create_import(
        self,
        business_id: str,
        dataset_type: str,
        source_type: str,
        file_name: str | None = None,
    ) -> DatasetImport:
        """Create a new import record, and commit it before any data is touched.

        The commit is the point, not an oversight. ``create_import`` used to
        ``add`` + ``flush`` only, so the row existed solely inside the
        orchestrator's transaction — and the orchestrator's failure path begins
        with ``session.rollback()``. Rollback expunges an object that was only
        flushed, so the history row vanished, ``session.get(DatasetImport, id)``
        inside ``mark_failed`` returned ``None``, the status update was a silent
        no-op, and the ``import_id`` handed back to the caller pointed at nothing.
        Every failed import was therefore invisible in the imports list — the one
        place a user would go to find out what went wrong.

        An audit row is not part of the data transaction it describes: it has to
        outlive the failure it records. Committing here is what makes that true.
        Nothing else is pending at this point in a request, so the commit cannot
        publish a half-written import.
        """
        import_record = self.repository.create_import(
            business_id=business_id,
            dataset_type=dataset_type,
            source_type=source_type,
            file_name=file_name,
            status="pending",
        )
        self.session.commit()
        return import_record

    def get_import(self, import_id: str) -> DatasetImport | None:
        """Get an import by ID."""
        return self.repository.get_import(import_id)

    def mark_processing(self, import_id: str) -> DatasetImport | None:
        """Mark an import as processing."""
        return self.repository.update_status(import_id, status="processing")

    def mark_completed(
        self,
        import_id: str,
        row_count: int | None = None,
    ) -> DatasetImport | None:
        """Mark an import as completed."""
        import_obj = self.repository.update_status(import_id, status="completed")
        if import_obj and row_count is not None:
            import_obj.row_count = row_count
            import_obj.completed_at = datetime.now(timezone.utc)
            self.session.flush()
        return import_obj

    def mark_failed(self, import_id: str, error_message: str) -> DatasetImport | None:
        """Mark an import as failed."""
        return self.repository.update_status(import_id, status="failed", error_message=error_message)

    def list_imports(self, business_id: str) -> list[DatasetImport]:
        """List all imports for a business."""
        return self.repository.list_by_business(business_id)

    def get_latest_import(self, business_id: str, dataset_type: str) -> DatasetImport | None:
        """Get the latest completed import of a specific type."""
        imports = self.list_imports(business_id)
        # Filter by dataset_type and status, sort by created_at descending
        completed_imports = [
            imp for imp in imports
            if imp.dataset_type == dataset_type and imp.status == "completed"
        ]
        return completed_imports[0] if completed_imports else None
