/**
 * Reduce a `/health/ready` body to a single status for display.
 *
 * Kept in a plain `.js` module with no React import so it can be exercised
 * directly by `__checks__/readiness.check.mjs`. The rule it encodes — that a cache
 * or event-bus outage is not an application outage — is the kind of thing that gets
 * quietly inverted during a refactor, and it is much easier to assert here than
 * through a rendered component.
 */

/** Matches the backend's status constants in `app/api/routes/health.py`. */
export const DEPENDENCY_STATUS = {
  OK: "ok",
  DEGRADED: "degraded",
  DISABLED: "disabled",
  UNAVAILABLE: "unavailable",
};

/**
 * Friendly names for the three dependency keys the backend reports.
 *
 * A status message should read "event bus", not "kafka" — someone reading this UI
 * configured a feature, not a broker.
 */
export const DEPENDENCY_LABELS = {
  database: "database",
  redis: "cache",
  kafka: "event bus",
};

export const dependencyLabel = (key) => DEPENDENCY_LABELS[key] ?? key;

/**
 * `{ tone, label, detail }` for the status chip.
 *
 * Mirrors `GET /health/ready`, which returns
 * `{ status, ready, required, degraded, dependencies: { database, redis, kafka } }`
 * with each dependency shaped `{ status, detail, latency_ms, ... }`.
 *
 * Per-dependency statuses are read directly rather than trusting the top-level
 * `degraded` array, because that array only lists dependencies at `unavailable` —
 * a Kafka producer that is merely `disconnected` reports `degraded` on itself and
 * would otherwise be missed entirely.
 */
export const summariseReadiness = (readiness, error) => {
  if (error) {
    return {
      tone: "error",
      label: "API unreachable",
      detail:
        error.status === 0
          ? "The frontend cannot reach the backend."
          : error.message ?? "The readiness check failed.",
    };
  }

  if (!readiness) {
    return { tone: "default", label: "Checking…", detail: null };
  }

  const dependencies = readiness.dependencies ?? {};
  const database = dependencies.database?.status;

  if (readiness.ready === false || (database && database !== DEPENDENCY_STATUS.OK)) {
    return {
      tone: "error",
      label: "Database down",
      // Safe to display: the backend classifies this string through a fixed table
      // and never forwards a driver message, so it cannot contain a connection URL.
      detail: dependencies.database?.detail ?? "PostgreSQL is not reachable.",
    };
  }

  const faulty = Object.entries(dependencies)
    .filter(
      ([key, value]) =>
        key !== "database" &&
        (value?.status === DEPENDENCY_STATUS.UNAVAILABLE ||
          value?.status === DEPENDENCY_STATUS.DEGRADED)
    )
    .map(([key]) => dependencyLabel(key));

  if (faulty.length) {
    return {
      tone: "warning",
      label: faulty.length === 1 ? `${faulty[0]} degraded` : "Degraded",
      detail: `Running without ${faulty.join(" and ")}. Reads fall back to PostgreSQL.`,
    };
  }

  // A dependency that was switched off is a configuration choice, not a fault, and
  // must not raise a warning about a service the operator told the app not to use.
  const disabled = Object.entries(dependencies)
    .filter(([, value]) => value?.status === DEPENDENCY_STATUS.DISABLED)
    .map(([key]) => dependencyLabel(key));

  if (disabled.length) {
    return {
      tone: "info",
      label: "Core only",
      detail: `${disabled.join(" and ")} disabled by configuration.`,
    };
  }

  return {
    tone: "success",
    label: "All systems",
    detail: "Database, cache and event bus are up.",
  };
};

/** Rows for the settings page's dependency table, in a stable order. */
export const dependencyRows = (readiness) => {
  const dependencies = readiness?.dependencies ?? {};

  return ["database", "redis", "kafka"]
    .filter((key) => key in dependencies)
    .map((key) => ({
      key,
      label: dependencyLabel(key),
      status: dependencies[key]?.status ?? "unknown",
      detail: dependencies[key]?.detail ?? null,
      // Present only when EXPOSE_HEALTH_TARGETS is on, and reduced to host:port
      // with any credentials stripped server-side.
      target: dependencies[key]?.target ?? null,
      latencyMs: dependencies[key]?.latency_ms ?? null,
      transport: dependencies[key]?.transport ?? dependencies[key]?.backend ?? null,
    }));
};
