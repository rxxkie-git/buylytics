export const SEGMENT_COLORS = {
  "High Value Customers": "#0F9D8A",
  "Loyal Customers": "#2563EB",
  "At Risk Customers": "#F97316",
  "Low Value Customers": "#A855F7",
};

export const CLUSTER_COLORS = ["#0F9D8A", "#2563EB", "#F97316", "#A855F7"];

// Currency formatting is shared across KPI cards, tables, and insights.
export const formatCurrency = (value) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value || 0);

export const formatNumber = (value) =>
  new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 1,
  }).format(value || 0);

export const formatPercent = (value, fractionDigits = 0) =>
  `${Number(value || 0).toFixed(fractionDigits)}%`;

export const getSegmentColor = (segment) =>
  SEGMENT_COLORS[segment] || "#64748B";

export const isMissingValue = (value) =>
  value === null || value === undefined || value === "";

export const getInitials = (value = "") =>
  value
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();

export const getDateLabel = () =>
  new Date().toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
