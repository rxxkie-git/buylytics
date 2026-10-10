"""Customer management service."""

from __future__ import annotations

from typing import Any
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.db.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository


class CustomerService:
    """Service for customer data management."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = CustomerRepository(session)

    def create_customer(
        self,
        business_id: str,
        external_customer_id: str,
        **fields: Any,
    ) -> Customer:
        """Create a new customer."""
        return self.repository.create(business_id=business_id, external_customer_id=external_customer_id, **fields)

    def get_customer(self, customer_id: str) -> Customer | None:
        """Get customer by ID."""
        return self.repository.get_by_id(customer_id)

    def get_customer_by_external_id(
        self,
        business_id: str,
        external_customer_id: str,
    ) -> Customer | None:
        """Get customer by external ID within a business."""
        return self.repository.get_by_external_id(business_id=business_id, external_customer_id=external_customer_id)

    def customer_ids_by_external_ids(
        self,
        business_id: str,
        external_customer_ids: Iterable[str],
    ) -> dict[str, str]:
        """Resolve many external ids to internal ids at once.

        Exists so the orders import can resolve every referenced customer in a
        handful of chunked queries instead of one query per order row.
        """
        return self.repository.ids_by_external_ids(
            business_id=business_id,
            external_customer_ids=external_customer_ids,
        )

    def list_customers(
        self,
        business_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Customer]:
        """List all customers for a business."""
        return self.repository.list_by_business(business_id, limit=limit, offset=offset)

    def list_customers_with_rfm(
        self,
        business_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List paginated customers with their RFM metrics."""
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        total = self.repository.count_by_business(business_id)
        rows = self.repository.list_with_rfm(business_id, limit=limit, offset=offset)
        return {
            "count": len(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
            "customers": [
                {
                    "id": c.id,
                    "external_customer_id": c.external_customer_id,
                    "name": c.name,
                    "email": c.email,
                    "phone": c.phone,
                    "city": c.city,
                    "recency": rfm.recency if rfm else None,
                    "frequency": rfm.frequency if rfm else None,
                    "monetary": float(rfm.monetary) if rfm and rfm.monetary is not None else None,
                    "average_order_value": (
                        float(rfm.average_order_value) if rfm and rfm.average_order_value is not None else None
                    ),
                    "segment": rfm.rfm_segment if rfm else None,
                    "source": rfm.source if rfm else None,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                }
                for c, rfm in rows
            ],
        }

    def bulk_upsert_customers(self, business_id: str, customers: list[dict[str, Any]]) -> list[Customer]:
        """Upsert multiple customers at once."""
        return self.repository.bulk_upsert(business_id, customers)
