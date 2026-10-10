/**
 * Barrel for the v1 API layer.
 *
 * Pages import from here rather than reaching for axios, so the token handling,
 * error envelope and timeout policy in `client.js` apply to every call without a
 * page having to remember them.
 */

export {
  ApiError,
  apiClient,
  getApiBaseUrl,
  setTokenProvider,
  toApiError,
  UPLOAD_TIMEOUT_MS,
} from "./client";

export * as dashboardApi from "./dashboard";
export * as analyticsApi from "./analytics";
export * as importsApi from "./imports";
export * as catalogApi from "./catalog";
export * as systemApi from "./system";
