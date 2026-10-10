/**
 * Dashboard read endpoints.
 *
 * None of these takes a business id. The server derives the tenant from the
 * bearer token, so there is no parameter a caller here could get wrong in a way
 * that crosses tenants.
 */

import { get } from "./client";

const BASE = "/api/v1/dashboard";

export const fetchSummary = (options) => get(`${BASE}/summary`, options);

/** `days` is clamped server-side to 1..365; anything outside that is a 422. */
export const fetchRevenueTrend = ({ days = 30, ...options } = {}) =>
  get(`${BASE}/revenue-trend`, { params: { days }, ...options });

export const fetchRfmDistribution = (options) => get(`${BASE}/rfm-distribution`, options);

export const fetchSegmentDistribution = (options) => get(`${BASE}/segment-distribution`, options);

export const fetchTopCustomers = ({ limit = 10, ...options } = {}) =>
  get(`${BASE}/top-customers`, { params: { limit }, ...options });

export const fetchInventorySummary = (options) => get(`${BASE}/inventory-summary`, options);
