from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.product import Product
from app.repositories.bulk import bulk_upsert_by_key, fetch_ids_by_external_ids


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, business_id: str | UUID, external_product_id: str, **fields: object) -> Product:
        product = Product(business_id=str(business_id), external_product_id=external_product_id, **fields)
        self.session.add(product)
        self.session.flush()
        return product

    def get_by_id(self, product_id: str | UUID) -> Product | None:
        return self.session.get(Product, str(product_id))

    def get_by_external_id(self, *, business_id: str | UUID, external_product_id: str) -> Product | None:
        stmt = select(Product).where(
            Product.business_id == str(business_id),
            Product.external_product_id == external_product_id,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_by_business(self, business_id: str | UUID) -> list[Product]:
        stmt = select(Product).where(Product.business_id == str(business_id)).order_by(Product.created_at.desc())
        return list(self.session.execute(stmt).scalars().all())

    def bulk_upsert(self, business_id: str | UUID, products: Iterable[dict[str, object]]) -> list[Product]:
        """Upsert products with one SELECT per chunk instead of one per row.

        See ``app.repositories.bulk``.
        """
        return bulk_upsert_by_key(
            self.session,
            Product,
            business_id=business_id,
            rows=products,
            key_field="external_product_id",
        )

    def ids_by_external_ids(
        self,
        *,
        business_id: str | UUID,
        external_product_ids: Iterable[str],
    ) -> dict[str, str]:
        """Resolve many external product ids to internal ids in chunked queries.

        The inventory import needs this: it used to call ``get_by_external_id``
        once per row while resolving ``product_id``.
        """
        return fetch_ids_by_external_ids(
            self.session,
            Product,
            business_id=business_id,
            external_field="external_product_id",
            external_ids=external_product_ids,
        )
