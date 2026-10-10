"""Repository layer tests."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.business import Business
from app.db.models.customer import Customer
from app.db.models.product import Product
from app.repositories.business_repository import BusinessRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.product_repository import ProductRepository


@pytest.fixture
def test_db():
    """Create an in-memory test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


def test_business_repository_create(test_db: Session) -> None:
    """Test creating a business via repository."""
    repo = BusinessRepository(test_db)
    business = repo.create_business("Test Business")
    test_db.commit()

    assert business.id is not None
    assert business.name == "Test Business"


def test_business_repository_get(test_db: Session) -> None:
    """Test getting a business via repository."""
    repo = BusinessRepository(test_db)
    business = repo.create_business("Test Business")
    test_db.commit()

    retrieved = repo.get_business(business.id)
    assert retrieved is not None
    assert retrieved.name == "Test Business"


def test_business_repository_get_for_user(test_db: Session) -> None:
    """Test getting businesses for a user."""
    repo = BusinessRepository(test_db)
    business = repo.create_business("Test Business")
    test_db.commit()

    membership = repo.create_membership(business.id, "user_123", "owner")
    test_db.commit()

    businesses = repo.get_business_for_user("user_123")
    assert len(businesses) == 1
    assert businesses[0].id == business.id


def test_customer_repository_create(test_db: Session) -> None:
    """Test creating a customer via repository."""
    # Create business first
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    repo = CustomerRepository(test_db)
    customer = repo.create(
        business_id=business.id,
        external_customer_id="cust_123",
        name="John Doe",
    )
    test_db.commit()

    assert customer.id is not None
    assert customer.external_customer_id == "cust_123"


def test_customer_repository_bulk_upsert(test_db: Session) -> None:
    """Test bulk upserting customers."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    repo = CustomerRepository(test_db)
    customers_data = [
        {"external_customer_id": "cust_1", "name": "Customer 1"},
        {"external_customer_id": "cust_2", "name": "Customer 2"},
    ]

    customers = repo.bulk_upsert(business.id, customers_data)
    test_db.commit()

    assert len(customers) == 2
    assert customers[0].name == "Customer 1"
    assert customers[1].name == "Customer 2"


def test_customer_repository_bulk_upsert_update(test_db: Session) -> None:
    """Test bulk upsert with updates."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    repo = CustomerRepository(test_db)

    # Create initial customer
    customers1 = repo.bulk_upsert(
        business.id,
        [{"external_customer_id": "cust_1", "name": "Original Name"}],
    )
    test_db.commit()

    # Upsert with updated name
    customers2 = repo.bulk_upsert(
        business.id,
        [{"external_customer_id": "cust_1", "name": "Updated Name"}],
    )
    test_db.commit()

    assert customers2[0].id == customers1[0].id
    assert customers2[0].name == "Updated Name"


def test_product_repository_create(test_db: Session) -> None:
    """Test creating a product via repository."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    repo = ProductRepository(test_db)
    product = repo.create(
        business_id=business.id,
        external_product_id="prod_123",
        name="Widget",
    )
    test_db.commit()

    assert product.id is not None
    assert product.external_product_id == "prod_123"


def test_product_repository_list_by_business(test_db: Session) -> None:
    """Test listing products for a business."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    repo = ProductRepository(test_db)
    repo.create(business_id=business.id, external_product_id="prod_1", name="Product 1")
    repo.create(business_id=business.id, external_product_id="prod_2", name="Product 2")
    test_db.commit()

    products = repo.list_by_business(business.id)
    assert len(products) == 2
