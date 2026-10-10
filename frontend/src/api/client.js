/**
 * HTTP client for the v1 API.
 *
 * Three things here are deliberate.
 *
 * **The base URL has no fallback host.** An empty `VITE_API_BASE_URL` resolves to
 * same-origin relative paths, which the Vite dev proxy and any production reverse
 * proxy both handle. The previous version fell back to `http://localhost:8000`,
 * which meant a deployed build with a missing env var would silently try to reach
 * the user's own machine and fail in a way that looks like a network problem
 * rather than a configuration one.
 *
 * **The auth interceptor is installed exactly once**, at module load, and reads
 * the token provider from a module-level slot. Installing it from a React effect
 * — as the Phase 1 client did — adds a new interceptor every time Clerk's
 * `getToken` changes identity, and axios never ejects them, so each request ends
 * up awaiting a token N times.
 *
 * **Errors are normalised into `ApiError`.** The backend returns a single
 * envelope, `{ error: { code, message, request_id, details } }`, and pages should
 * not each re-implement digging through `err.response.data.error.message`.
 */

import axios from "axios";
import { API_BASE_URL } from "../config/env.js";

/** Resolved and normalised in `src/config/env.js`; empty means same origin. */
const BASE_URL = API_BASE_URL;

/** Reads comfortably on a slow connection without hanging a page forever. */
const DEFAULT_TIMEOUT_MS = 20_000;

/** Uploads are parsed and written to Postgres synchronously, so they need room. */
export const UPLOAD_TIMEOUT_MS = 300_000;

export const apiClient = axios.create({
  baseURL: BASE_URL,
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { Accept: "application/json" },
});

/**
 * A request failure with the backend's error envelope already unpacked.
 *
 * `status` is 0 for a failure that never reached the server — a DNS error, a
 * refused connection, CORS — which is worth distinguishing in the UI because the
 * user's action is different: check the backend is running, not check the input.
 */
export class ApiError extends Error {
  constructor({ message, status = 0, code = "unknown", requestId = null, exceptionType = null, fields = [], cause }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    /**
     * The backend exception class name, present only on a 500.
     *
     * The 500 body is deliberately generic — "An unexpected error occurred" — so
     * that a traceback, a SQL statement or a connection string can never reach a
     * browser. That left the UI with nothing to show and no way to tell a
     * serialization failure from a dead database. The class name is source code
     * rather than data, so the backend now includes it, and a page that shows it
     * next to the request id gives an operator a one-line lead instead of a
     * shrug.
     */
    this.exceptionType = exceptionType;
    this.fields = fields;
    this.cause = cause;
  }

  /** True when retrying the identical request could plausibly succeed. */
  get isRetryable() {
    return this.status === 0 || this.status === 429 || this.status >= 500;
  }

  get isAuthError() {
    return this.status === 401 || this.status === 403;
  }

  get isNotFound() {
    return this.status === 404;
  }

  /**
   * A dependency the API needs is down. Phase 2 keeps Redis and Kafka optional,
   * so this generally means PostgreSQL, which is worth saying differently.
   */
  get isDependencyDown() {
    return this.status === 503;
  }
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/**
 * Set by `<ApiAuthBridge />` once Clerk has loaded. Held in a module slot rather
 * than closed over by the interceptor so the interceptor itself is registered
 * only once.
 */
let tokenProvider = null;

export const setTokenProvider = (provider) => {
  tokenProvider = typeof provider === "function" ? provider : null;
};

/** Endpoints that must work before sign-in. Health is public by design. */
const isPublicPath = (url = "") => url.startsWith("/health");

apiClient.interceptors.request.use(async (config) => {
  if (!tokenProvider || isPublicPath(config.url ?? "")) {
    return config;
  }

  try {
    const token = await tokenProvider();

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  } catch {
    // A token that cannot be minted is not a reason to abandon the request: the
    // server will answer 401 and the UI can prompt a sign-in. Swallowing the
    // reason is intentional — a Clerk error object can carry the raw JWT, and
    // logging it would put a bearer token in the browser console.
  }

  return config;
});

// ---------------------------------------------------------------------------
// Error normalisation
// ---------------------------------------------------------------------------

const NETWORK_MESSAGE =
  "Could not reach the API. Check that the backend is running and that " +
  "VITE_API_BASE_URL points at it.";

const STATUS_MESSAGES = {
  401: "Your session has expired. Sign in again to continue.",
  403: "You do not have access to this resource.",
  404: "That resource does not exist.",
  409: "That conflicts with something that already exists.",
  413: "That file is too large.",
  429: "Too many requests. Wait a moment and try again.",
  500: "The server hit an unexpected error.",
  503: "A service the API depends on is unavailable.",
};

/** Pull a human-readable string out of whatever shape the body arrived in. */
const extractMessage = (body, status) => {
  if (typeof body === "string" && body.trim()) return body.trim();

  const error = body?.error;

  if (typeof error === "string" && error.trim()) return error.trim();
  if (typeof error?.message === "string" && error.message.trim()) return error.message.trim();

  // FastAPI's own HTTPException shape, for any route not yet migrated to the
  // envelope. `detail` can be a dict, so only take it when it is a string.
  if (typeof body?.detail === "string" && body.detail.trim()) return body.detail.trim();
  if (typeof body?.detail?.message === "string") return body.detail.message;

  return STATUS_MESSAGES[status] ?? `Request failed with status ${status}.`;
};

const extractFields = (body) => {
  const fields = body?.error?.details?.fields;
  if (!Array.isArray(fields)) return [];

  return fields.map((entry) => ({
    field: entry?.field ?? "",
    reason: entry?.reason ?? "invalid",
  }));
};

export const toApiError = (error) => {
  if (error instanceof ApiError) return error;

  if (axios.isCancel?.(error) || error?.code === "ERR_CANCELED") {
    const cancelled = new ApiError({ message: "Request cancelled", code: "cancelled" });
    cancelled.isCancelled = true;
    return cancelled;
  }

  const response = error?.response;

  if (!response) {
    const timedOut = error?.code === "ECONNABORTED";
    return new ApiError({
      message: timedOut ? "The request timed out." : NETWORK_MESSAGE,
      status: 0,
      code: timedOut ? "timeout" : "network_error",
      cause: error,
    });
  }

  const body = response.data;

  return new ApiError({
    message: extractMessage(body, response.status),
    status: response.status,
    code: body?.error?.code ?? "http_error",
    // The correlation id ties a message on screen to a specific server log line.
    // Surfacing it turns "it broke" into something diagnosable.
    requestId: body?.error?.request_id ?? null,
    exceptionType: body?.error?.type ?? null,
    fields: extractFields(body),
    cause: error,
  });
};

apiClient.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(toApiError(error))
);

// ---------------------------------------------------------------------------
// Verbs
// ---------------------------------------------------------------------------

/**
 * `params` entries that are `null`, `undefined` or `""` are dropped rather than
 * serialised. `?status=` reaches FastAPI as an empty string, which is a value,
 * not an absence — and would filter every row out.
 */
const clean = (params = {}) =>
  Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== "")
  );

export const get = async (url, { params, signal, timeout, validateStatus } = {}) => {
  const { data } = await apiClient.get(url, {
    params: clean(params),
    signal,
    timeout,
    validateStatus,
  });
  return data;
};

export const post = async (url, body, { params, signal, timeout } = {}) => {
  const { data } = await apiClient.post(url, body, { params: clean(params), signal, timeout });
  return data;
};

/**
 * Multipart upload. The `Content-Type` header is left unset on purpose so the
 * browser writes it with the correct multipart boundary; setting it by hand
 * produces a body the server cannot parse.
 */
export const upload = async (url, file, { signal, onProgress, fieldName = "file" } = {}) => {
  const form = new FormData();
  form.append(fieldName, file);

  const { data } = await apiClient.post(url, form, {
    signal,
    timeout: UPLOAD_TIMEOUT_MS,
    onUploadProgress: onProgress
      ? (event) => onProgress(event.total ? Math.round((event.loaded / event.total) * 100) : null)
      : undefined,
  });

  return data;
};

/** Exposed for the settings page, which shows the operator where it is pointed. */
export const getApiBaseUrl = () => BASE_URL || `${window.location.origin} (same origin)`;
