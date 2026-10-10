from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.business import Business, BusinessMembership


class BusinessRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_business(self, name: str) -> Business:
        business = Business(name=name)
        self.session.add(business)
        self.session.flush()
        return business

    def get_business(self, business_id: str | UUID) -> Business | None:
        business_uuid = str(business_id)
        return self.session.get(Business, business_uuid)

    def get_business_for_user(self, user_id: str) -> list[Business]:
        stmt = select(Business).join(BusinessMembership).where(BusinessMembership.user_id == user_id)
        return list(self.session.execute(stmt).scalars().all())

    def create_membership(self, business_id: str | UUID, user_id: str, role: str = "member") -> BusinessMembership:
        membership = BusinessMembership(business_id=str(business_id), user_id=user_id, role=role)
        self.session.add(membership)
        self.session.flush()
        return membership
