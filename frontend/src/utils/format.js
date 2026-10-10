/**
 * Formatting helpers for the Phase 2 pages.
 *
 * The Phase 1 `formatters.js` had a bug worth not repeating: it formatted currency
 * with `Intl.NumberFormat("en-IN", { currency: "USD" })` — Indian digit grouping
 * with a dollar sign, so ₹12,34,567 worth of revenue rendered as "$12,34,567".
 * Locale and currency are now consistent and both come from one place, and the
 * legacy module is left untouched so the ported product-analysis page keeps
 * working.
 */

/**
 * Change these two together; they are read from `src/config/env.js`.
 *
 * The `.js` extension is explicit so this module resolves under plain Node as well
 * as Vite — the checks in `__checks__/` import it directly.
 *
 * Two lines, and they are not redundant. `export { X } from "..."` is a *pure
 * re-export*: it forwards the binding to importers of this module and creates no
 * local binding here, so referencing bare `LOCALE` below is a ReferenceError, not
 * a lookup of the re-exported name. That is exactly what crashed the dashboard —
 * `formatDate` and `formatDateTime` used `LOCALE`, the error only fires when the
 * function is *called*, and the dashboard is the first page that calls them.
 * Everything inside this module must therefore use the aliases from line 26.
 */
export { CURRENCY, LOCALE } from "../config/env.js";

import { CURRENCY as CURRENCY_CODE, LOCALE as LOCALE_TAG } from "../config/env.js";

const currencyFormatter = new Intl.NumberFormat(LOCALE_TAG, {
  style: "currency",
  currency: CURRENCY_CODE,
  maximumFractionDigits: 0,
});

const currencyPreciseFormatter = new Intl.NumberFormat(LOCALE_TAG, {
  style: "currency",
  currency: CURRENCY_CODE,
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const numberFormatter = new Intl.NumberFormat(LOCALE_TAG);

const decimalFormatter = new Intl.NumberFormat(LOCALE_TAG, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** A genuine absence renders as an em dash. Zero renders as zero. */
const isBlank = (value) => value === null || value === undefined || value === "";

export const formatCurrency = (value) => (isBlank(value) ? "—" : currencyFormatter.format(Number(value)));

export const formatCurrencyPrecise = (value) =>
  isBlank(value) ? "—" : currencyPreciseFormatter.format(Number(value));

export const formatCount = (value) => (isBlank(value) ? "—" : numberFormatter.format(Number(value)));

export const formatDecimal = (value) => (isBlank(value) ? "—" : decimalFormatter.format(Number(value)));

/** `share` arrives from the API as a fraction in 0..1, not a percentage. */
export const formatShare = (fraction, digits = 1) =>
  isBlank(fraction) ? "—" : `${(Number(fraction) * 100).toFixed(digits)}%`;

export const formatPercent = (value, digits = 1) =>
  isBlank(value) ? "—" : `${Number(value).toFixed(digits)}%`;

/** Compact axis labels: 1.2M rather than 1,200,000, which never fits. */
export const formatCompact = (value) => {
  if (isBlank(value)) return "—";

  const number = Number(value);
  const abs = Math.abs(number);

  if (abs >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(number / 1_000).toFixed(1)}K`;

  return numberFormatter.format(number);
};

export const formatDate = (value) => {
  if (isBlank(value)) return "—";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  return date.toLocaleDateString(LOCALE_TAG, { day: "numeric", month: "short", year: "numeric" });
};

export const formatDateTime = (value) => {
  if (isBlank(value)) return "—";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  return date.toLocaleString(LOCALE_TAG, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

/** "4 minutes ago" for job timestamps, where elapsed time is what you want. */
export const formatRelative = (value) => {
  if (isBlank(value)) return "—";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  const seconds = Math.round((Date.now() - date.getTime()) / 1000);

  if (seconds < 0) return "just now";
  if (seconds < 45) return "just now";
  if (seconds < 90) return "a minute ago";

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minutes ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;

  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;

  return formatDate(value);
};

export const formatDuration = (milliseconds) => {
  if (isBlank(milliseconds)) return "—";

  const ms = Number(milliseconds);
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;

  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
};

/**
 * The nine RFM segments, in the order the backend reports them.
 *
 * Copied verbatim from `RFM_SEGMENT_ORDER` in `backend/app/analytics/scoring.py`.
 * Note the exact spellings: "Potential Loyalist" is singular and "About To Sleep"
 * capitalises the "To". Getting either wrong means the lookup silently misses and
 * the segment renders in the fallback grey, which looks like a styling quirk
 * rather than a bug — so `frontend/src/utils/__checks__/format.check.mjs` asserts
 * these strings against the Python source.
 */
export const RFM_SEGMENT_ORDER = [
  "Champions",
  "Loyal Customers",
  "Potential Loyalist",
  "New Customers",
  "Needs Attention",
  "About To Sleep",
  "Cannot Lose Them",
  "At Risk",
  "Hibernating",
];

/** Warm-to-cool by value, so a chart reads high-value on the left. */
export const RFM_SEGMENT_COLORS = {
  Champions: "#0F766E",
  "Loyal Customers": "#2563EB",
  "Potential Loyalist": "#0891B2",
  "New Customers": "#7C3AED",
  "Needs Attention": "#CA8A04",
  "About To Sleep": "#EA580C",
  "Cannot Lose Them": "#BE123C",
  "At Risk": "#DC2626",
  Hibernating: "#64748B",
};

export const segmentColor = (segment) => RFM_SEGMENT_COLORS[segment] ?? "#94A3B8";

/** Sort helper. Anything unrecognised sorts last rather than first. */
export const segmentRank = (segment) => {
  const index = RFM_SEGMENT_ORDER.indexOf(segment);
  return index === -1 ? RFM_SEGMENT_ORDER.length : index;
};

export const CLUSTER_PALETTE = [
  "#0F766E",
  "#2563EB",
  "#EA580C",
  "#A855F7",
  "#0891B2",
  "#CA8A04",
  "#DC2626",
  "#65A30D",
];

export const clusterColor = (index) => CLUSTER_PALETTE[Math.abs(Number(index) || 0) % CLUSTER_PALETTE.length];

/** Job status to MUI severity/colour, used by chips in several places. */
export const JOB_STATUS_COLOR = {
  queued: "default",
  running: "info",
  completed: "success",
  failed: "error",
};

export const DEPENDENCY_COLOR = {
  ok: "success",
  degraded: "warning",
  disabled: "default",
  unavailable: "error",
};

export const truncate = (value, max = 40) => {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
};

/** Short form of a UUID for a table cell, with the full value in a tooltip. */
export const shortId = (value) => {
  const text = String(value ?? "");
  return text.length > 12 ? `${text.slice(0, 8)}…` : text;
};
