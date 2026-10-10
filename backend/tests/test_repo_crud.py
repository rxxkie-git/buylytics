from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.analytics import AnalyticsRun, CustomerRFM, CustomerSegment
from app.db.models.business import Business, BusinessMembership
from app.db.models.customer import Customer
from app.db.models.dataset import DatasetImport
from app.db.models.inventory import Inventory
from app.db.models.order import Order, OrderItem
from app.db.models.product import Product
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.business_repository import BusinessRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as session:
        yield session


def test_business_creation(session: Session) -> None:
    repo = BusinessRepository(session)
    business = repo.create_business("Acme Retail")
    assert isinstance(business, Business)
    assert business.name == "Acme Retail"


def test_business_membership(session: Session) -> None:
    repo = BusinessRepository(session)
    business = repo.create_business("Northwind")
    membership = repo.create_membership(business.id, "user_123", role="owner")
    assert isinstance(membership, BusinessMembership)
    assert membership.role == "owner"
    assert repo.get_business_for_user("user_123")[0].id == business.id


def test_customer_crud(session: Session) -> None:
    repo = CustomerRepository(session)
    customer = repo.create(business_id="business-1", external_customer_id="C-100", name="Alice", email="alice@example.com")
    assert isinstance(customer, Customer)
    assert repo.get_by_id(customer.id).id == customer.id
    assert repo.get_by_external_id(business_id="business-1", external_customer_id="C-100").id == customer.id


def test_product_crud(session: Session) -> None:
    repo = ProductRepository(session)
    product = repo.create(business_id="business-1", external_product_id="P-900", name="Widget", sku="W-1", price=12.50)
    assert isinstance(product, Product)
    assert repo.get_by_external_id(business_id="business-1", external_product_id="P-900").id == product.id


def test_order_crud(session: Session) -> None:
    repo = OrderRepository(session)
    customer_id = str(uuid.uuid4())
    order = repo.create(business_id="business-1", external_order_id="O-500", customer_id=customer_id, total_amount=110.0, status="paid")
    assert isinstance(order, Order)
    assert repo.get_by_external_id(business_id="business-1", external_order_id="O-500").id == order.id


def test_inventory_crud(session: Session) -> None:
    repo = InventoryRepository(session)
    inventory = repo.create(business_id="business-1", product_id="product-1", quantity=25, status="In Stock")
    assert isinstance(inventory, Inventory)
    assert repo.update(inventory.id, quantity=12).quantity == 12
    assert repo.delete(inventory.id) is True


def test_dataset_import_crud(session: Session) -> None:
    repo = DatasetRepository(session)
    dataset_import = repo.create_import(business_id="business-1", dataset_type="customer", source_type="csv", file_name="customers.csv")
    assert isinstance(dataset_import, DatasetImport)
    assert repo.get_import(dataset_import.id).id == dataset_import.id
    updated = repo.update_status(dataset_import.id, status="completed")
    assert updated.status == "completed"


def test_analytics_persistence(session: Session) -> None:
    repo = AnalyticsRepository(session)
    run = repo.create_analytics_run(business_id="business-1", dataset_import_id="import-1", job_type="rfm", status="running")
    assert isinstance(run, AnalyticsRun)
    rfm = repo.save_rfm(business_id="business-1", customer_id="customer-1", dataset_import_id="import-1", recency=10.0, frequency=5.0, monetary=100.0)
    segment = repo.save_segments(business_id="business-1", customer_id="customer-1", dataset_import_id="import-1", segment="vip", cluster=1, score=0.9)
    assert isinstance(rfm, CustomerRFM)
    assert isinstance(segment, CustomerSegment)
    assert repo.get_rfm(business_id="business-1", customer_id="customer-1")[0].customer_id == "customer-1"


def test_tenant_isolation(session: Session) -> None:
    customer_repo = CustomerRepository(session)
    business_a = "business-a"
    business_b = "business-b"
    customer_repo.create(business_id=business_a, external_customer_id="c-1", name="A Customer")
    customer_repo.create(business_id=business_b, external_customer_id="c-1", name="B Customer")
    inventory_repo = InventoryRepository(session)
    inventory_repo.create(business_id=business_a, product_id="p-a", quantity=5, status="In Stock")
    inventory_repo.create(business_id=business_b, product_id="p-b", quantity=9, status="Low Stock")
    assert len(customer_repo.list_by_business(business_a)) == 1
    assert len(customer_repo.list_by_business(business_b)) == 1
    assert len(inventory_repo.list_by_business(business_a)) == 1
    assert len(inventory_repo.list_by_business(business_b)) == 1
