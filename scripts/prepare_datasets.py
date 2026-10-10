#!/usr/bin/env python3
"""Prepare production-ready CBPA datasets from raw retail transaction records (e.g. Online Retail.xlsx).

Generates 4 fully matched CSVs ready for immediate upload via the UI:
  1. customers.csv  -> Customers tab
  2. products.csv   -> Products tab
  3. orders.csv     -> Orders tab
  4. inventory.csv  -> Inventory tab
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd


def assign_category(description: str) -> str:
    """Categorize item based on descriptive keywords."""
    d = str(description or "").upper()
    if any(k in d for k in ["HEART", "LIGHT", "CANDLE", "LANTERN", "HOLDER", "LAMP"]):
        return "Lighting & Candles"
    if any(k in d for k in ["MUG", "CUP", "BOWL", "PLATE", "TEA", "KITCHEN", "BAKING", "JAR", "BOTTLE"]):
        return "Kitchen & Dining"
    if any(k in d for k in ["BAG", "PURSE", "WALLET", "TOTE", "LUNCH BAG"]):
        return "Bags & Accessories"
    if any(k in d for k in ["CHRISTMAS", "XMAS", "TREE", "SANTA", "DECORATION", "ORNAMENT"]):
        return "Seasonal & Decor"
    if any(k in d for k in ["CARD", "PAPER", "PEN", "PENCIL", "NOTEBOOK", "STICKER", "TAPE"]):
        return "Stationery"
    if any(k in d for k in ["CLOCK", "MIRROR", "BOX", "DRAWER", "FRAME", "CUSHION", "MAT"]):
        return "Home Decor & Living"
    if any(k in d for k in ["TOY", "GAME", "DOLL", "PUZZLE", "CHILDREN", "KIDS"]):
        return "Toys & Games"
    return "Gifts & Novelties"


def prepare_datasets(source_file: str | Path = "Online Retail.xlsx", output_dir: str | Path = "dataset/online_retail_ready"):
    source_path = Path(source_file)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if not source_path.exists():
        print(f"Error: Source file '{source_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading '{source_path}'...")
    if source_path.suffix.lower() in [".xls", ".xlsx"]:
        df = pd.read_excel(source_path)
    else:
        df = pd.read_csv(source_path)

    print(f"Raw shape: {df.shape}")

    # 1. Clean transactions
    df = df.dropna(subset=["CustomerID", "StockCode", "InvoiceNo", "InvoiceDate", "UnitPrice"])
    df["CustomerID"] = df["CustomerID"].astype(int).astype(str)
    df["StockCode"] = df["StockCode"].astype(str).str.strip()
    df["Description"] = df["Description"].fillna("Standard Retail Item").astype(str).str.strip()

    # Exclude non-merchandise fees / adjustments
    excluded_codes = {"POST", "D", "M", "PADS", "DOT", "CRUK", "BANK CHARGES", "AMAZONFEE"}
    df = df[~df["StockCode"].isin(excluded_codes)]
    df_valid = df[(df["Quantity"] > 0) & (df["UnitPrice"] > 0)].copy()
    df_valid["LineTotal"] = (df_valid["Quantity"] * df_valid["UnitPrice"]).round(2)

    print(f"Cleaned valid transactions: {len(df_valid)}")

    # 2. Products
    products = (
        df_valid.groupby("StockCode")
        .agg({
            "Description": lambda x: x.mode()[0] if not x.empty else "Retail Item",
            "UnitPrice": "median",
        })
        .reset_index()
    )
    products.columns = ["product_id", "name", "price"]
    products["sku"] = "SKU-" + products["product_id"]
    products["category"] = products["name"].apply(assign_category)
    products["price"] = products["price"].round(2)
    products = products[["product_id", "name", "sku", "category", "price"]]
    products.to_csv(out_path / "products.csv", index=False)
    print(f"✓ Created '{out_path / 'products.csv'}' with {len(products):,} records")

    # 3. Customers
    cities_uk = ["London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Liverpool", "Bristol", "Edinburgh", "Sheffield", "Newcastle"]
    first_names = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda", "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen"]
    last_names = ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson", "Wright", "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke"]

    customers_raw = df_valid.groupby("CustomerID").agg({"Country": "first"}).reset_index()
    np.random.seed(42)
    n_cust = len(customers_raw)
    cust_names = [f"{np.random.choice(first_names)} {np.random.choice(last_names)}" for _ in range(n_cust)]
    cust_emails = [f"{name.lower().replace(' ', '.')}.{cid}@retailmail.co.uk" for name, cid in zip(cust_names, customers_raw["CustomerID"])]
    cust_cities = [c if c != "United Kingdom" else np.random.choice(cities_uk) for c in customers_raw["Country"]]

    customers = pd.DataFrame({
        "customer_id": customers_raw["CustomerID"],
        "name": cust_names,
        "email": cust_emails,
        "city": cust_cities,
    })
    customers.to_csv(out_path / "customers.csv", index=False)
    print(f"✓ Created '{out_path / 'customers.csv'}' with {len(customers):,} records")

    # 4. Orders
    orders = (
        df_valid.groupby(["InvoiceNo", "CustomerID", "InvoiceDate"])
        .agg({"LineTotal": "sum"})
        .reset_index()
    )
    orders.columns = ["order_id", "customer_id", "order_date", "total_amount"]
    orders["order_date"] = pd.to_datetime(orders["order_date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    orders["total_amount"] = orders["total_amount"].round(2)
    orders["status"] = "completed"
    orders = orders[["order_id", "customer_id", "order_date", "total_amount", "status"]]
    orders.to_csv(out_path / "orders.csv", index=False)
    print(f"✓ Created '{out_path / 'orders.csv'}' with {len(orders):,} records")

    # 5. Inventory
    np.random.seed(42)
    qty = np.random.randint(5, 500, size=len(products))
    reorder = np.random.choice([15, 20, 25, 30, 50], size=len(products))
    suppliers = np.random.choice(
        ["Apex Retail Logistics", "Crown Direct Supply", "Metro Wholesale UK", "Beacon Distribution", "Global Goods Hub"],
        size=len(products),
    )
    status = ["Low Stock" if q <= r else ("Out of Stock" if q == 0 else "In Stock") for q, r in zip(qty, reorder)]

    inventory = pd.DataFrame({
        "product_id": products["product_id"],
        "quantity": qty,
        "reorder_level": reorder,
        "supplier": suppliers,
        "status": status,
    })
    inventory.to_csv(out_path / "inventory.csv", index=False)
    print(f"✓ Created '{out_path / 'inventory.csv'}' with {len(inventory):,} records")

    print("\nDataset preparation completed successfully!")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "Online Retail.xlsx"
    dest = sys.argv[2] if len(sys.argv) > 2 else "dataset/online_retail_ready"
    prepare_datasets(src, dest)
