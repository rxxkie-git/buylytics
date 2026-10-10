from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile, Depends
from pydantic import BaseModel, Field

from ..config import settings
from ..core.concurrency import (
    BlockingWorkBusy,
    BlockingWorkTimeout,
    run_import_blocking,
)
from ..core.logging import get_logger
from ..database import InMemoryRepository, _item_status
from ..auth import get_current_user, ClerkUser

logger = get_logger(__name__)

router = APIRouter(prefix="/inventory", tags=["inventory"])


def get_item_status(quantity: int) -> str:
    return _item_status(quantity)


class ItemCreate(BaseModel):
    id: str = Field(..., description="Unique serial code or SKU")
    itemName: str = Field(..., description="Name of the inventory item")
    category: str = Field("General", description="Product category")
    quantity: int = Field(0, ge=0, description="Quantity in stock")
    price: float = Field(0.0, ge=0.0, description="Unit price of the item")
    supplier: str = Field("Unknown", description="Supplier or manufacturer")


class ItemUpdate(BaseModel):
    itemName: str | None = None
    category: str | None = None
    quantity: int | None = None
    price: float | None = None
    supplier: str | None = None


def _get_repo(request: Request) -> InMemoryRepository:
    repository = request.app.state.dataset_repository
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Storage backend is not configured. Use an implementation-specific repository or keep this in memory for local development.",
        )
    return repository


@router.get("")
def get_inventory(request: Request, current_user: ClerkUser = Depends(get_current_user)) -> dict[str, Any]:
    repo = _get_repo(request)
    return {"items": repo.get_inventory_items()}


@router.post("")
def add_item(request: Request, item: ItemCreate, current_user: ClerkUser = Depends(get_current_user)) -> dict[str, Any]:
    repo = _get_repo(request)
    new_item = item.dict()
    new_item["status"] = get_item_status(new_item["quantity"])
    try:
        return repo.add_inventory_item(new_item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{item_id}")
def update_item(request: Request, item_id: str, payload: ItemUpdate, current_user: ClerkUser = Depends(get_current_user)) -> dict[str, Any]:
    repo = _get_repo(request)
    updates = payload.dict(exclude_unset=True)
    # Recalculate status if quantity is changing
    if "quantity" in updates:
        updates["status"] = get_item_status(updates["quantity"])
    result = repo.update_inventory_item(item_id, updates)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Item with Serial Code/SKU '{item_id}' not found.",
        )
    return result


@router.delete("/{item_id}")
def delete_item(request: Request, item_id: str, current_user: ClerkUser = Depends(get_current_user)) -> dict[str, Any]:
    repo = _get_repo(request)
    deleted = repo.delete_inventory_item(item_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Item with Serial Code/SKU '{item_id}' not found.",
        )
    return {"success": True, "message": f"Item '{item_id}' removed from inventory."}


@router.post("/upload")
async def upload_inventory(request: Request, file: UploadFile = File(...), current_user: ClerkUser = Depends(get_current_user)) -> dict[str, Any]:
    """Replace the inventory sheet from an uploaded file.

    ``async def`` only because it awaits the upload stream. Everything after that
    — the pandas parse, the coercions, the per-row status ``apply``, and the
    repository write — is synchronous and runs in a worker thread. Left on the
    loop it would freeze every other request for the duration of the parse, which
    is exactly the defect this application was reported for.
    """
    repo = _get_repo(request)

    file_name = file.filename or ""
    extension = Path(file_name).suffix.lower()

    if extension not in {".csv", ".xls", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Only CSV and Excel sheets are allowed.",
        )

    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    logger.info("inventory_upload_started", extra={"file_bytes": len(file_bytes)})

    def _process() -> list[dict[str, Any]]:
        try:
            if extension == ".csv":
                # on_bad_lines="skip" so one ragged row cannot fail the upload,
                # matching the hardened parser used by the v1 import path.
                df = pd.read_csv(io.BytesIO(file_bytes), on_bad_lines="skip")
            else:
                df = pd.read_excel(io.BytesIO(file_bytes))
        except Exception as exc:
            # Re-raised as ValueError so the async wrapper can map it to 422
            # without HTTPException travelling out of a worker thread.
            raise ValueError(f"Failed to parse CSV/Excel file: {exc}") from exc

        if df.columns.empty:
            raise ValueError("The file has no readable column headers.")

        # Dynamic column mapping to robustly map incoming files
        col_mapping = {}
        for col in df.columns:
            col_lower = str(col).strip().lower()
            if col_lower in {"id", "serial", "serialcode", "serial_code", "sku", "code"}:
                col_mapping[col] = "id"
            elif col_lower in {"itemname", "item_name", "name", "product", "title"}:
                col_mapping[col] = "itemName"
            elif col_lower in {"category", "type", "group"}:
                col_mapping[col] = "category"
            elif col_lower in {"quantity", "qty", "stock", "inventory", "count"}:
                col_mapping[col] = "quantity"
            elif col_lower in {"price", "cost", "rate", "unitprice", "unit_price"}:
                col_mapping[col] = "price"
            elif col_lower in {"supplier", "vendor", "manufacturer", "brand"}:
                col_mapping[col] = "supplier"

        df = df.rename(columns=col_mapping)

        if "id" not in df.columns:
            df["id"] = [f"SKU-{1000 + i}" for i in range(len(df))]
        if "itemName" not in df.columns:
            df["itemName"] = "Unnamed Item"
        if "category" not in df.columns:
            df["category"] = "General"
        if "quantity" not in df.columns:
            df["quantity"] = 0
        if "price" not in df.columns:
            df["price"] = 0.0
        if "supplier" not in df.columns:
            df["supplier"] = "Unknown"

        df["id"] = df["id"].astype(str).str.strip()
        df["itemName"] = df["itemName"].astype(str).str.strip()
        df["category"] = df["category"].astype(str).str.strip()
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).astype(int)
        df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0.0).astype(float)
        df["supplier"] = df["supplier"].astype(str).str.strip()
        df["status"] = df["quantity"].apply(get_item_status)

        df = df.dropna(subset=["id"])
        # astype(str) turns a missing cell into the literal "nan", which would
        # otherwise become an inventory item with the SKU "nan".
        df = df[~df["id"].isin({"", "nan", "None", "<NA>"})]

        target_columns = ["id", "itemName", "category", "quantity", "price", "supplier", "status"]
        items_list = df[target_columns].to_dict(orient="records")

        repo.replace_inventory(items_list)
        return items_list

    try:
        items_list = await run_import_blocking(
            _process,
            operation="inventory_upload",
            timeout_seconds=settings.import_timeout_seconds,
        )
    except BlockingWorkBusy:
        logger.warning("inventory_upload_rejected", extra={"reason": "at_capacity"})
        raise HTTPException(
            status_code=503,
            detail=(
                "The server is processing other uploads and could not start this "
                "one. Retry in a few seconds."
            ),
            headers={"Retry-After": "10"},
        ) from None
    except BlockingWorkTimeout as exc:
        logger.error(
            "inventory_upload_failed",
            extra={"reason": "timeout", "timeout_seconds": exc.timeout_seconds},
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Processing this file took longer than {exc.timeout_seconds:.0f}s "
                "and the request was released. Try a smaller file."
            ),
        ) from None
    except ValueError as exc:
        logger.warning("inventory_upload_failed", extra={"reason": "unreadable"})
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info("inventory_upload_completed", extra={"rows": len(items_list)})

    return {
        "success": True,
        "message": f"Successfully imported {len(items_list)} inventory items.",
        "items": items_list,
    }


@router.post("/sync")
def sync_inventory_datasets(request: Request, current_user: ClerkUser = Depends(get_current_user)) -> dict[str, Any]:
    repo = _get_repo(request)

    # Get the latest product dataset
    product_dataset = repo.get_dataset(dataset_type="product")
    if not product_dataset:
        raise HTTPException(
            status_code=404,
            detail="No product dataset found to sync. Please upload a product dataset first.",
        )

    # Extract the cleaned file path
    product_data = product_dataset.get("productData", {})
    artifact_paths = product_data.get("artifactPaths", {})
    cleaned_file_path = artifact_paths.get("cleanedFilePath")

    if not cleaned_file_path or not Path(cleaned_file_path).exists():
        raise HTTPException(
            status_code=404,
            detail="Product dataset artifact not found. Cannot sync.",
        )

    try:
        if cleaned_file_path.endswith(".csv"):
            df = pd.read_csv(cleaned_file_path)
        else:
            df = pd.read_excel(cleaned_file_path)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to read product dataset: {str(e)}",
        )

    # Map columns heuristically just like upload
    col_mapping = {}
    for col in df.columns:
        col_lower = str(col).strip().lower()
        if col_lower in {"id", "serial", "serialcode", "serial_code", "sku", "code", "product_id", "item_id"}:
            col_mapping[col] = "id"
        elif col_lower in {"itemname", "item_name", "name", "product", "title", "product_name"}:
            col_mapping[col] = "itemName"
        elif col_lower in {"category", "type", "group"}:
            col_mapping[col] = "category"
        elif col_lower in {"price", "cost", "rate", "unitprice", "unit_price"}:
            col_mapping[col] = "price"
        elif col_lower in {"supplier", "vendor", "manufacturer", "brand"}:
            col_mapping[col] = "supplier"

    df = df.rename(columns=col_mapping)

    if "id" not in df.columns:
        if "itemName" in df.columns:
            df["id"] = "SYNC-" + df["itemName"].astype(str).str.upper().str.replace(r"[^A-Z0-9]+", "-", regex=True)
        else:
            df["id"] = [f"SYNC-{1000 + i}" for i in range(len(df))]

    if "itemName" not in df.columns:
        df["itemName"] = "Synced Product"
    if "category" not in df.columns:
        df["category"] = "General"
    if "price" not in df.columns:
        df["price"] = 0.0
    if "supplier" not in df.columns:
        df["supplier"] = "Unknown"
    if "quantity" not in df.columns:
        df["quantity"] = 0
    else:
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).astype(int)

    df["id"] = df["id"].astype(str).str.strip()
    df["itemName"] = df["itemName"].astype(str).str.strip()
    df["category"] = df["category"].astype(str).str.strip()
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0.0).astype(float)
    df["supplier"] = df["supplier"].astype(str).str.strip()

    df = df.dropna(subset=["id"])
    df = df[df["id"] != ""]
    df = df.drop_duplicates(subset=["id"], keep="first")

    target_columns = ["id", "itemName", "category", "quantity", "price", "supplier"]
    synced_items = df[target_columns].to_dict(orient="records")

    added, updated = repo.upsert_inventory_items(synced_items)

    return {
        "success": True,
        "message": f"Sync complete. Added {added} items and updated {updated} existing items.",
        "added": added,
        "updated": updated,
    }
