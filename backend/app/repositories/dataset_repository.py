from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.dataset import DatasetImport


class DatasetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_import(
        self,
        *,
        business_id: str | UUID,
        dataset_type: str,
        source_type: str,
        file_name: str | None = None,
        status: str = "pending",
        row_count: int | None = None,
    ) -> DatasetImport:
        dataset_import = DatasetImport(
            business_id=str(business_id),
            dataset_type=dataset_type,
            source_type=source_type,
            file_name=file_name,
            status=status,
            row_count=row_count,
        )
        self.session.add(dataset_import)
        self.session.flush()
        return dataset_import

    def get_import(self, import_id: str | UUID) -> DatasetImport | None:
        return self.session.get(DatasetImport, str(import_id))

    def update_status(self, import_id: str | UUID, *, status: str, error_message: str | None = None) -> DatasetImport | None:
        import_row = self.get_import(import_id)
        if not import_row:
            return None
        import_row.status = status
        if error_message is not None:
            import_row.error_message = error_message
        self.session.flush()
        return import_row

    def list_by_business(self, business_id: str | UUID) -> list[DatasetImport]:
        stmt = select(DatasetImport).where(DatasetImport.business_id == str(business_id)).order_by(DatasetImport.created_at.desc())
        return list(self.session.execute(stmt).scalars().all())
