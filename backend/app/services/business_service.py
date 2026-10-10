"""Business management service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.business import Business, BusinessMembership
from app.repositories.business_repository import BusinessRepository


class BusinessService:
    """Service for business management and multi-tenancy resolution."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = BusinessRepository(session)

    def create_business(self, name: str) -> Business:
        """Create a new business."""
        return self.repository.create_business(name)

    def get_business(self, business_id: str) -> Business | None:
        """Get business by ID."""
        return self.repository.get_business(business_id)

    def get_or_create_business_for_user(self, user_id: str, business_name: str | None = None) -> Business:
        """Get or create a default business for a user.
        
        This is a development/bootstrap helper. In production, businesses are typically
        created explicitly and users are invited to join them.
        """
        businesses = self.repository.get_business_for_user(user_id)
        if businesses:
            return businesses[0]

        # Create a default business for this user
        default_name = business_name or f"{user_id}'s Business"
        business = self.create_business(default_name)
        self.repository.create_membership(business.id, user_id, role="owner")
        self.session.commit()
        return business

    def resolve_business_for_user(self, user_id: str) -> Business | None:
        """Resolve a business for a user. Returns first business if user is a member of multiple."""
        businesses = self.repository.get_business_for_user(user_id)
        return businesses[0] if businesses else None

    def get_user_businesses(self, user_id: str) -> list[Business]:
        """Get all businesses a user is a member of."""
        return self.repository.get_business_for_user(user_id)

    def create_membership(self, business_id: str, user_id: str, role: str = "member") -> BusinessMembership:
        """Add a user to a business."""
        return self.repository.create_membership(business_id, user_id, role)
