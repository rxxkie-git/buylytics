/**
 * Health and webhook configuration.
 *
 * The health endpoints are the only unauthenticated ones the app calls, and they
 * return fixed status vocabulary rather than driver messages — so a status chip
 * can render whatever comes back without risking a credential appearing in the
 * UI.
 */

import { get } from "./client";

/** Matches the backend's status constants. */
export const DEPENDENCY_STATUS = {
  OK: "ok",
  DEGRADED: "degraded",
  DISABLED: "disabled",
  UNAVAILABLE: "unavailable",
};

/**
 * Readiness across all three dependencies in one call.
 *
 * Returns 200 or 503 depending on the database alone. Redis and Kafka being down
 * is reported but does not make the API unready, because reads fall back to
 * Postgres and publishes fall back to a no-op — so `validateStatus` accepts 503
 * and lets the caller read the body instead of treating it as a thrown error.
 */
export const fetchReadiness = ({ signal } = {}) =>
  get("/health/ready", {
    signal,
    timeout: 8_000,
    // 503 is a meaningful body, not a transport failure: it says which dependency
    // is down. Rejecting it would throw away the only information worth having.
    validateStatus: (status) => status === 200 || status === 503,
  });

export const fetchLiveness = ({ signal } = {}) => get("/health", { signal, timeout: 5_000 });

/**
 * Integration details for sending this business a webhook. Never includes the
 * signing secret — that is shared out of band — so it is safe to render.
 */
export const fetchWebhookConfig = (options) => get("/api/v1/webhooks/config", options);

export const fetchWebhookDeliveries = ({ source, status, limit = 50, ...options } = {}) =>
  get("/api/v1/webhooks/deliveries", { params: { source, status, limit }, ...options });
