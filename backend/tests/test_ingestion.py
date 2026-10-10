"""File parsing and validation tests."""

import pytest
import pandas as pd
from pathlib import Path
import tempfile

from app.ingestion.file_parser import FileParser, DataValidator


def test_csv_parsing() -> None:
    """Test parsing a CSV file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("external_customer_id,name,email\n")
        f.write("cust_1,John Doe,john@example.com\n")
        f.write("cust_2,Jane Smith,jane@example.com\n")
        f.flush()
        tmp_path = Path(f.name)

    try:
        df = FileParser.parse_file(tmp_path)
        assert len(df) == 2
        assert "external_customer_id" in df.columns
        assert df.iloc[0]["name"] == "John Doe"
    finally:
        tmp_path.unlink()


def test_validate_customer_data() -> None:
    """Test customer data validation."""
    data = {
        "external_customer_id": ["cust_1", "cust_2"],
        "name": ["John Doe", "Jane Smith"],
        "email": ["john@example.com", "jane@example.com"],
    }
    df = pd.DataFrame(data)

    valid_records, errors = DataValidator.validate_customer_data(df)

    assert len(valid_records) == 2
    assert len(errors) == 0
    assert valid_records[0]["external_customer_id"] == "cust_1"
    assert valid_records[0]["name"] == "John Doe"


def test_validate_customer_data_with_errors() -> None:
    """Test customer validation with missing required fields."""
    data = {
        "name": ["John Doe", "Jane Smith"],
        "email": ["john@example.com", "jane@example.com"],
    }
    df = pd.DataFrame(data)

    valid_records, errors = DataValidator.validate_customer_data(df)

    assert len(valid_records) == 0
    assert len(errors) == 2  # Two rows with missing external_customer_id


def test_validate_product_data() -> None:
    """Test product data validation."""
    data = {
        "external_product_id": ["prod_1", "prod_2"],
        "name": ["Widget", "Gadget"],
        "sku": ["WID-001", "GAD-001"],
        "category": ["Widgets", "Gadgets"],
        "price": [9.99, 19.99],
    }
    df = pd.DataFrame(data)

    valid_records, errors = DataValidator.validate_product_data(df)

    assert len(valid_records) == 2
    assert len(errors) == 0
    assert valid_records[0]["external_product_id"] == "prod_1"
    assert valid_records[0]["price"] == 9.99


def test_validate_order_data() -> None:
    """Test order data validation."""
    data = {
        "external_order_id": ["order_1", "order_2"],
        "external_customer_id": ["cust_1", "cust_2"],
        "total_amount": [100.00, 200.00],
    }
    df = pd.DataFrame(data)

    valid_records, errors = DataValidator.validate_order_data(df)

    assert len(valid_records) == 2
    assert len(errors) == 0
    assert valid_records[0]["external_order_id"] == "order_1"


def test_validate_inventory_data() -> None:
    """Test inventory data validation."""
    data = {
        "external_product_id": ["prod_1", "prod_2"],
        "quantity": [100, 200],
        "reorder_level": [10, 20],
    }
    df = pd.DataFrame(data)

    valid_records, errors = DataValidator.validate_inventory_data(df)

    assert len(valid_records) == 2
    assert len(errors) == 0
    assert valid_records[0]["quantity"] == 100
