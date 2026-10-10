/**
 * File imports.
 *
 * Four datasets, one shape each. The orders import is the one that matters most
 * for analytics: recency, frequency and monetary are measured from those rows,
 * whereas a customers file can only ever pass RFM columns through on trust.
 */

import { upload } from "./client";

const BASE = "/api/v1/imports";

export const DATASETS = {
  CUSTOMERS: "customers",
  PRODUCTS: "products",
  INVENTORY: "inventory",
  ORDERS: "orders",
};

/** Mirrors `ALLOWED_SUFFIXES` in the backend route, so the UI can reject early. */
export const ACCEPTED_EXTENSIONS = [".csv", ".tsv", ".xls", ".xlsx"];

/** Mirrors `MAX_UPLOAD_BYTES`. Checking here saves the user a 50 MB round trip. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(",");

/**
 * Validate before sending. Returns an error string, or null when the file is
 * acceptable. The server checks the same things — this only avoids the wait.
 */
export const validateFile = (file) => {
  if (!file) return "Choose a file first.";

  const name = file.name ?? "";
  const dot = name.lastIndexOf(".");
  const extension = dot === -1 ? "" : name.slice(dot).toLowerCase();

  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    return `${extension || "That file"} is not supported. Use ${ACCEPTED_EXTENSIONS.join(", ")}.`;
  }

  if (file.size === 0) return "That file is empty.";

  if (file.size > MAX_UPLOAD_BYTES) {
    const limit = MAX_UPLOAD_BYTES / (1024 * 1024);
    return `That file is ${(file.size / (1024 * 1024)).toFixed(1)} MB. The limit is ${limit} MB.`;
  }

  return null;
};

/**
 * Ask the server how this file's columns map onto the canonical fields.
 *
 * Writes nothing: no import record, no rows, so it is safe to call on every file
 * selection and safe to call repeatedly. Returns either
 * `{status: "ready", column_mapping, unmapped_columns, sample_rows, row_count}`
 * or `{status: "unmappable", missing_fields, ambiguous_fields, available_columns}`.
 *
 * This exists so a user finds out that their `order_id` column was understood —
 * or that no column supplies the customer id — before uploading 40 MB, rather
 * than from a row count of zero afterwards.
 */
export const previewDataset = (dataset, file, options) =>
  upload(`${BASE}/${dataset}/preview`, file, options);

export const uploadDataset = (dataset, file, options) =>
  upload(`${BASE}/${dataset}`, file, options);

export const uploadCustomers = (file, options) => uploadDataset(DATASETS.CUSTOMERS, file, options);
export const uploadProducts = (file, options) => uploadDataset(DATASETS.PRODUCTS, file, options);
export const uploadInventory = (file, options) => uploadDataset(DATASETS.INVENTORY, file, options);
export const uploadOrders = (file, options) => uploadDataset(DATASETS.ORDERS, file, options);
