"""Database and model tests."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.business import Business, BusinessMembership
from app.db.models.customer import Customer
from app.db.models.product import Product
from app.db.models.order import Order, OrderItem
from app.db.models.inventory import Inventory
from app.db.models.dataset import DatasetImport


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


def test_create_business(test_db: Session) -> None:
    """Test creating a business."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    assert business.id is not None
    assert business.name == "Test Business"
    assert business.created_at is not None


def test_create_business_membership(test_db: Session) -> None:
    """Test creating a business membership."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    membership = BusinessMembership(
        business_id=business.id,
        user_id="clerk_user_123",
        role="owner",
    )
    test_db.add(membership)
    test_db.commit()

    assert membership.business_id == business.id
    assert membership.user_id == "clerk_user_123"


def test_create_customer(test_db: Session) -> None:
    """Test creating a customer."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    customer = Customer(
        business_id=business.id,
        external_customer_id="cust_123",
        name="John Doe",
        email="john@example.com",
    )
    test_db.add(customer)
    test_db.commit()

    assert customer.id is not None
    assert customer.business_id == business.id
    assert customer.external_customer_id == "cust_123"


def test_create_product(test_db: Session) -> None:
    """Test creating a product."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    product = Product(
        business_id=business.id,
        external_product_id="prod_123",
        name="Widget",
        sku="WID-001",
        category="Widgets",
    )
    test_db.add(product)
    test_db.commit()

    assert product.id is not None
    assert product.business_id == business.id
    assert product.external_product_id == "prod_123"


def test_create_order_with_items(test_db: Session) -> None:
    """Test creating an order with items."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    customer = Customer(
        business_id=business.id,
        external_customer_id="cust_123",
    )
    test_db.add(customer)
    test_db.commit()

    product = Product(
        business_id=business.id,
        external_product_id="prod_123",
    )
    test_db.add(product)
    test_db.commit()

    order = Order(
        business_id=business.id,
        external_order_id="order_123",
        customer_id=customer.id,
        status="completed",
    )
    test_db.add(order)
    test_db.commit()

    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        quantity=5,
        unit_price=9.99,
        total_amount=49.95,
    )
    test_db.add(item)
    test_db.commit()

    assert order.id is not None
    assert len(order.items) == 1
    assert order.items[0].quantity == 5


def test_tenant_isolation(test_db: Session) -> None:
    """Test that tenant data is isolated."""
    # Create two businesses
    business1 = Business(name="Business 1")
    business2 = Business(name="Business 2")
    test_db.add_all([business1, business2])
    test_db.commit()

    # Create customers for each business
    customer1 = Customer(
        business_id=business1.id,
        external_customer_id="cust_1",
    )
    customer2 = Customer(
        business_id=business2.id,
        external_customer_id="cust_2",
    )
    test_db.add_all([customer1, customer2])
    test_db.commit()

    # Verify customers are isolated
    from sqlalchemy import select

    stmt = select(Customer).filter(Customer.business_id == business1.id)
    customers_b1 = test_db.execute(stmt).scalars().all()
    assert len(customers_b1) == 1
    assert customers_b1[0].external_customer_id == "cust_1"

    stmt = select(Customer).filter(Customer.business_id == business2.id)
    customers_b2 = test_db.execute(stmt).scalars().all()
    assert len(customers_b2) == 1
    assert customers_b2[0].external_customer_id == "cust_2"


def test_create_dataset_import(test_db: Session) -> None:
    """Test creating a dataset import record."""
    business = Business(name="Test Business")
    test_db.add(business)
    test_db.commit()

    import_record = DatasetImport(
        business_id=business.id,
        dataset_type="customer",
        source_type="csv",
        file_name="customers.csv",
        status="completed",
        row_count=100,
    )
    test_db.add(import_record)
    test_db.commit()

    assert import_record.id is not None
    assert import_record.business_id == business.id
    assert import_record.status == "completed"
    assert import_record.row_count == 100
