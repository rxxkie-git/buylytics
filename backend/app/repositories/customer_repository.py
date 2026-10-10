from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.analytics import CustomerRFM
from app.db.models.customer import Customer
from app.repositories.bulk import bulk_upsert_by_key, fetch_ids_by_external_ids


class CustomerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, business_id: str | UUID, external_customer_id: str, **fields: object) -> Customer:
        customer = Customer(business_id=str(business_id), external_customer_id=external_customer_id, **fields)
        self.session.add(customer)
        self.session.flush()
        return customer

    def get_by_id(self, customer_id: str | UUID) -> Customer | None:
        return self.session.get(Customer, str(customer_id))

    def get_by_external_id(self, *, business_id: str | UUID, external_customer_id: str) -> Customer | None:
        stmt = select(Customer).where(
            Customer.business_id == str(business_id),
            Customer.external_customer_id == external_customer_id,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def ids_by_external_ids(
        self,
        *,
        business_id: str | UUID,
        external_customer_ids: Iterable[str],
    ) -> dict[str, str]:
        """Resolve many external ids to internal ids in chunked queries.

        The orders import needs this: it used to call ``get_by_external_id`` once
        per order row, so a 38,000-row file meant 38,000 sequential round-trips
        before a single order was written.
        """
        return fetch_ids_by_external_ids(
            self.session,
            Customer,
            business_id=business_id,
            external_field="external_customer_id",
            external_ids=external_customer_ids,
        )

    def count_by_business(self, business_id: str | UUID) -> int:
        stmt = select(func.count()).select_from(Customer).where(Customer.business_id == str(business_id))
        return int(self.session.execute(stmt).scalar() or 0)

    def list_by_business(
        self,
        business_id: str | UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Customer]:
        stmt = select(Customer).where(Customer.business_id == str(business_id)).order_by(Customer.created_at.desc())
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def list_with_rfm(
        self,
        business_id: str | UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[tuple[Customer, CustomerRFM | None]]:
        stmt = (
            select(Customer, CustomerRFM)
            .outerjoin(
                CustomerRFM,
                (CustomerRFM.business_id == Customer.business_id) & (CustomerRFM.customer_id == Customer.id),
            )
            .where(Customer.business_id == str(business_id))
            .order_by(Customer.created_at.desc())
        )
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).all())

    def bulk_upsert(self, business_id: str | UUID, customers: Iterable[dict[str, object]]) -> list[Customer]:
        """Upsert customers with one SELECT per chunk instead of one per row.

        Behaviour is unchanged — existing rows are updated, new ones inserted, and
        the result is returned in input order. What changed is the number of
        round-trips it takes. See ``app.repositories.bulk``.
        """
        return bulk_upsert_by_key(
            self.session,
            Customer,
            business_id=business_id,
            rows=customers,
            key_field="external_customer_id",
        )
