"""Multi-tenancy context and helpers."""

from __future__ import annotations

from fastapi import Depends, HTTPException

from app.auth import ClerkUser, get_current_user
from app.db.session import get_db
from app.services.business_service import BusinessService
from sqlalchemy.orm import Session


class BusinessContext:
    """Encapsulates the authenticated user and their business context."""

    def __init__(self, user: ClerkUser, business_id: str, session: Session) -> None:
        self.user = user
        self.business_id = business_id
        self.session = session

    @property
    def user_id(self) -> str:
        """Get the current user's ID."""
        return self.user.user_id


def get_business_context(
    user: ClerkUser = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> BusinessContext:
    """Dependency to get authenticated user's business context.

    This ensures:
    1. User is authenticated (via Clerk)
    2. User is resolved to a business
    3. All database queries will be tenant-scoped

    Declared ``def``, not ``async def``, and that single keyword is load-bearing.

    Every authenticated route depends on this, and the body is two synchronous
    SQLAlchemy queries against a database across the internet. As ``async def``,
    FastAPI awaits it *on the event loop*, so those queries blocked the one loop
    serving every connection in the process — each authenticated request stalling
    every other request, including ``/health``.

    As a plain ``def``, FastAPI runs it in the anyio threadpool instead. The
    queries still take exactly as long; they just cost a worker thread rather than
    the whole server. Nothing else about the signature or return value changes,
    so callers are unaffected.

    Args:
        user: Authenticated user from Clerk
        session: Database session

    Returns:
        BusinessContext with user and business_id

    Raises:
        HTTPException: If user has no business or business resolution fails
    """
    business_service = BusinessService(session)

    # Try to resolve user's business
    business = business_service.resolve_business_for_user(user.user_id)

    # If no business found, create a default one (for development)
    if business is None:
        business = business_service.get_or_create_business_for_user(
            user_id=user.user_id,
            business_name=f"Business for {user.email or user.user_id}",
        )

    if business is None:
        raise HTTPException(
            status_code=500,
            detail="Could not resolve or create business for user",
        )

    return BusinessContext(user=user, business_id=business.id, session=session)
