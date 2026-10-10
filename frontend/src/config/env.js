/**
 * The one place the frontend reads its environment.
 *
 * `import.meta.env` only exists when a bundler defines it. It is `undefined` in
 * plain Node, which means any module reading it directly cannot be imported by a
 * script, a check, or a server-side render — the property access throws before the
 * first line of real work. Guarding once here keeps every other module importable
 * anywhere.
 *
 * Nothing secret belongs in here. Everything Vite exposes under `VITE_` is
 * compiled into the JavaScript bundle and readable by anyone who opens dev tools,
 * so a publishable key is fine and a secret key never is.
 */

const source =
  typeof import.meta !== "undefined" && import.meta.env ? import.meta.env : {};

const read = (key, fallback = "") => {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
};

/**
 * Base URL of the API.
 *
 * Empty means same-origin: requests go to `/api/v1/...` and are resolved by the
 * Vite dev proxy in development or by whatever serves the built assets in
 * production. There is deliberately no `http://localhost:8000` fallback — a
 * deployed bundle missing this variable should fail visibly against its own
 * origin, not quietly try to reach the visitor's own machine.
 */
export const API_BASE_URL = read("VITE_API_BASE_URL").replace(/\/+$/, "");

/** Clerk's publishable key. Safe to ship; the secret key stays server-side. */
export const CLERK_PUBLISHABLE_KEY = read("VITE_CLERK_PUBLISHABLE_KEY");

/** Display currency and locale. These two want to agree with each other. */
export const CURRENCY = read("VITE_CURRENCY", "INR");
export const LOCALE = read("VITE_LOCALE", "en-IN");

export const IS_PRODUCTION_BUILD = source.PROD === true;
export const MODE = read("MODE", source.MODE ?? "development");

/**
 * Non-sensitive summary for the settings page, so an operator can confirm what
 * the bundle was built against without reading the network tab.
 */
export const environmentSummary = () => ({
  apiBaseUrl: API_BASE_URL || "same origin",
  currency: CURRENCY,
  locale: LOCALE,
  mode: MODE,
  clerkConfigured: Boolean(CLERK_PUBLISHABLE_KEY),
});
