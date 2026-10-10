/** Customers, products, orders and inventory reads. All tenant-scoped server-side. */

import { get } from "./client";

export const fetchCustomers = ({ limit = 100, offset = 0, ...options } = {}) =>
  get("/api/v1/customers", { params: { limit, offset }, ...options });

export const fetchCustomer = (id, options) =>
  get(`/api/v1/customers/${encodeURIComponent(id)}`, options);

export const fetchProducts = (options) => get("/api/v1/products", options);

export const fetchProduct = (id, options) =>
  get(`/api/v1/products/${encodeURIComponent(id)}`, options);

export const fetchInventory = (options) => get("/api/v1/inventory", options);

export const fetchLowStock = (options) => get("/api/v1/inventory/low-stock", options);

export const fetchInventoryItem = (id, options) =>
  get(`/api/v1/inventory/${encodeURIComponent(id)}`, options);

/**
 * Orders are paged server-side because a tenant with a year of transactions has far
 * more of them than customers, and the list endpoint caps `limit` at 500 regardless
 * of what is asked for. The response carries `total` alongside `count` so the page
 * can say how much it is not showing.
 *
 * An empty `status` is stripped by the client's `clean` helper rather than sent as
 * `?status=`, which the backend would treat as a filter matching nothing.
 */
export const fetchOrders = ({ limit = 100, offset = 0, status, ...options } = {}) =>
  get("/api/v1/orders", { params: { limit, offset, status }, ...options });

export const fetchOrder = (id, options) =>
  get(`/api/v1/orders/${encodeURIComponent(id)}`, options);
