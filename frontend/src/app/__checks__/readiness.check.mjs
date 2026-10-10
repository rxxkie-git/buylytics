/**
 * Executable checks for `src/app/readiness.js`.
 *
 * Run with `node src/app/__checks__/readiness.check.mjs` from `frontend/`.
 *
 * The rule under test is the one the module's own docstring claims and the one that
 * matters operationally: **a cache or event-bus outage is not an application
 * outage.** Phase 2 keeps Redis and Kafka optional — `REDIS_ENABLED=false` and
 * `KAFKA_ENABLED=false` are a supported configuration in which every read falls
 * back to PostgreSQL. If this logic ever inverts, the UI shouts "down" at an
 * operator whose system is working exactly as they configured it, and the shouting
 * is indistinguishable from a real database outage.
 *
 * The fixtures below are shaped from the real `GET /health/ready` body in
 * `backend/app/api/routes/health.py`, and group 6 reads that file to confirm the
 * four status strings still match. Guessing this shape is what went wrong the first
 * time these summaries were written.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const HEALTH_PY = join(HERE, "..", "..", "..", "..", "backend", "app", "api", "routes", "health.py");

let failures = 0;
let count = 0;

const check = (label, condition, detail = "") => {
  count += 1;
  if (condition) {
    console.log(`  ok   ${label}`);
  } else {
    console.log(`  FAIL ${label}${detail ? `\n       ${detail}` : ""}`);
    failures += 1;
  }
};

const group = (title) => console.log(`\n[${title}]`);

const readiness = await import("../readiness.js");
const { summariseReadiness, dependencyRows, DEPENDENCY_STATUS, dependencyLabel } = readiness;

// --- fixtures ---------------------------------------------------------------

const dep = (status, extra = {}) => ({ status, detail: null, latency_ms: 4.2, ...extra });

/** Everything up: the full Phase 2 stack running. */
const allUp = {
  status: "ok",
  ready: true,
  service: "cbpa-api",
  version: "0.2.0",
  required: ["database"],
  degraded: [],
  dependencies: {
    database: dep("ok"),
    redis: dep("ok", { backend: "redis" }),
    kafka: dep("ok", { transport: "kafka" }),
  },
};

/** The documented development configuration: optional infrastructure switched off. */
const optionalOff = {
  status: "ok",
  ready: true,
  required: ["database"],
  degraded: [],
  dependencies: {
    database: dep("ok"),
    redis: dep("disabled", { backend: "none" }),
    kafka: dep("disabled", { transport: "inline" }),
  },
};

/** Redis configured but unreachable — reads fall back to Postgres. */
const cacheDown = {
  status: "degraded",
  ready: true,
  required: ["database"],
  degraded: ["redis"],
  dependencies: {
    database: dep("ok"),
    redis: dep("unavailable", { backend: "redis", detail: "unavailable: ConnectionError" }),
    kafka: dep("ok", { transport: "kafka" }),
  },
};

/**
 * The case the top-level `degraded` array misses: the Kafka producer reports
 * `degraded` on itself, but `degraded` only lists dependencies at `unavailable`.
 */
const busDegradedButNotListed = {
  status: "ok",
  ready: true,
  required: ["database"],
  degraded: [],
  dependencies: {
    database: dep("ok"),
    redis: dep("ok", { backend: "redis" }),
    kafka: dep("degraded", { transport: "kafka", detail: "producer disconnected" }),
  },
};

/** The only genuine outage: PostgreSQL is the source of truth. */
const databaseDown = {
  status: "unavailable",
  ready: false,
  required: ["database"],
  degraded: ["database"],
  dependencies: {
    database: dep("unavailable", { detail: "unavailable: OperationalError" }),
    redis: dep("ok", { backend: "redis" }),
    kafka: dep("ok", { transport: "kafka" }),
  },
};

console.log("=".repeat(74));
console.log("FRONTEND READINESS CHECKS");
console.log("=".repeat(74));

// ---------------------------------------------------------------------------
group("0 the module is importable with no React runtime present");

check("importing readiness.js does not throw", typeof summariseReadiness === "function");
check("dependencyRows is exported", typeof dependencyRows === "function");

// Matched at the start of a line rather than as a substring: the module's own
// docstring contains the phrase "no React import", and a bare `.includes("import")`
// fails on the prose that documents the very property being asserted.
const readinessSource = readFileSync(join(HERE, "..", "readiness.js"), "utf8");
check(
  "the module has no import statement at all",
  !/^\s*import\s/m.test(readinessSource),
  "a React import would make this file untestable outside a bundler"
);
check("nor a require call", !/\brequire\s*\(/.test(readinessSource));

// ---------------------------------------------------------------------------
group("1 optional infrastructure being off is not a fault");

const off = summariseReadiness(optionalOff, null);
check("a disabled cache and event bus do not produce an error tone", off.tone !== "error", `got ${off.tone}`);
check("nor a warning tone", off.tone !== "warning", `got ${off.tone}`);
check('the tone is informational', off.tone === "info", `got ${off.tone}`);
check("the label does not claim anything is down", !/down|fail|error/i.test(off.label), `got ${off.label}`);
check("the detail says the operator turned them off", /disabled by configuration/.test(off.detail), `got ${off.detail}`);
check("the detail names the cache in plain language", off.detail.includes("cache"), `got ${off.detail}`);
check("the detail names the event bus in plain language", off.detail.includes("event bus"), `got ${off.detail}`);
check('the raw key "redis" is not shown to the operator', !off.detail.includes("redis"), `got ${off.detail}`);
check('the raw key "kafka" is not shown to the operator', !off.detail.includes("kafka"), `got ${off.detail}`);

// ---------------------------------------------------------------------------
group("2 an optional dependency failing is a warning, never an error");

const cache = summariseReadiness(cacheDown, null);
check("an unreachable cache is not an error tone", cache.tone !== "error", `got ${cache.tone}`);
check("an unreachable cache is a warning", cache.tone === "warning", `got ${cache.tone}`);
check("the label names the cache, singular", cache.label === "cache degraded", `got ${cache.label}`);
check("the detail explains the fallback", /PostgreSQL/.test(cache.detail), `got ${cache.detail}`);

const bus = summariseReadiness(busDegradedButNotListed, null);
check(
  'a "degraded" event bus is caught even though the top-level degraded array is empty',
  bus.tone === "warning",
  `got ${bus.tone} — this is the exact case that reading readiness.degraded would miss`
);
check("the label names the event bus", bus.label === "event bus degraded", `got ${bus.label}`);

const both = summariseReadiness(
  {
    ...cacheDown,
    dependencies: { ...cacheDown.dependencies, kafka: dep("unavailable", { transport: "kafka" }) },
  },
  null
);
check("two failing optional dependencies collapse to one label", both.label === "Degraded", `got ${both.label}`);
check("both are still named in the detail", /cache and event bus/.test(both.detail), `got ${both.detail}`);
check("two failing optional dependencies are still not an error", both.tone === "warning", `got ${both.tone}`);

// ---------------------------------------------------------------------------
group("3 a non-ok database is always an error");

for (const status of ["unavailable", "degraded"]) {
  const summary = summariseReadiness(
    { ...databaseDown, ready: status === "unavailable" ? false : true, dependencies: { ...databaseDown.dependencies, database: dep(status) } },
    null
  );
  check(`database "${status}" produces an error tone`, summary.tone === "error", `got ${summary.tone}`);
  check(`database "${status}" is labelled as a database problem`, /database/i.test(summary.label), `got ${summary.label}`);
}

const down = summariseReadiness(databaseDown, null);
check("ready:false produces an error tone", down.tone === "error", `got ${down.tone}`);
check(
  "the database verdict wins over healthy optional dependencies",
  down.tone === "error" && !/cache|event bus/.test(down.label),
  `got ${down.label}`
);
check(
  "a ready:false body with no dependencies block still errors",
  summariseReadiness({ ready: false }, null).tone === "error"
);

// ---------------------------------------------------------------------------
group("4 everything up, and the shapes in between");

const up = summariseReadiness(allUp, null);
check("a fully healthy stack is a success tone", up.tone === "success", `got ${up.tone}`);
check("the label is affirmative", up.label === "All systems", `got ${up.label}`);

const checking = summariseReadiness(null, null);
check("no snapshot yet is neutral, not an error", checking.tone === "default", `got ${checking.tone}`);
check("no snapshot yet says it is checking", /Checking/.test(checking.label), `got ${checking.label}`);

check("an empty dependencies block does not throw", summariseReadiness({ ready: true, dependencies: {} }, null).tone === "success");
check("a missing dependencies block does not throw", summariseReadiness({ ready: true }, null).tone === "success");
check(
  "an unknown dependency key is tolerated",
  summariseReadiness({ ready: true, dependencies: { database: dep("ok"), search: dep("unavailable") } }, null).tone === "warning"
);
check(
  "an unknown dependency key falls back to its own name in the detail",
  summariseReadiness({ ready: true, dependencies: { database: dep("ok"), search: dep("unavailable") } }, null).detail.includes("search")
);
check(
  "a null dependency value does not throw",
  summariseReadiness({ ready: true, dependencies: { database: dep("ok"), redis: null } }, null).tone === "success"
);

// ---------------------------------------------------------------------------
group("5 a transport failure reads differently from a dependency failure");

const unreachable = summariseReadiness(null, { status: 0, message: "Could not reach the API." });
check("a status-0 error is an error tone", unreachable.tone === "error");
check("a status-0 error says the API is unreachable", /unreachable/i.test(unreachable.label), `got ${unreachable.label}`);
check(
  "a status-0 error blames the connection, not the database",
  /cannot reach the backend/i.test(unreachable.detail),
  `got ${unreachable.detail}`
);

const serverError = summariseReadiness(null, { status: 500, message: "The server hit an unexpected error." });
check("a 500 is still an error tone", serverError.tone === "error");
check("a 500 surfaces the message it was given", serverError.detail === "The server hit an unexpected error.");
check("an error with no message still yields a detail", typeof summariseReadiness(null, { status: 500 }).detail === "string");
check(
  "an error takes precedence over a stale healthy snapshot",
  summariseReadiness(allUp, { status: 0 }).tone === "error",
  "the caller keeps the last snapshot, so this branch must be checked first"
);

// ---------------------------------------------------------------------------
group("6 the status vocabulary matches the backend");

const healthSource = readFileSync(HEALTH_PY, "utf8");

for (const [name, value] of Object.entries(DEPENDENCY_STATUS)) {
  check(
    `status "${value}" (${name}) appears in health.py`,
    healthSource.includes(`"${value}"`),
    "the frontend is comparing against a string the backend no longer emits"
  );
}

check(
  "health.py reports its dependencies under a nested key",
  /"dependencies"|dependencies\s*[:=]/.test(healthSource),
  "the summary reads readiness.dependencies, so this nesting must exist"
);

for (const key of ["database", "redis", "kafka"]) {
  check(`health.py reports a "${key}" dependency`, healthSource.includes(`"${key}"`));
}

check(
  "health.py exposes the boolean the error branch reads",
  /"ready"/.test(healthSource),
  "summariseReadiness short-circuits on readiness.ready === false"
);

// ---------------------------------------------------------------------------
group("7 dependency rows for the settings table");

const rows = dependencyRows(allUp);
check("three rows for three dependencies", rows.length === 3, `got ${rows.length}`);
check("rows are in a fixed order, database first", rows[0].key === "database", `got ${rows[0].key}`);
check("cache second", rows[1].key === "redis");
check("event bus third", rows[2].key === "kafka");
check("each row carries a friendly label", rows[1].label === "cache", `got ${rows[1].label}`);
check("each row carries the raw status for colouring", rows[0].status === "ok");
check("latency is mapped from the snake_case field", rows[0].latencyMs === 4.2, `got ${rows[0].latencyMs}`);
check("transport is read for kafka", rows[2].transport === "kafka", `got ${rows[2].transport}`);
check("backend is read for redis under the same column", rows[1].transport === "redis", `got ${rows[1].transport}`);
check(
  "target is null when EXPOSE_HEALTH_TARGETS is off",
  rows.every((row) => row.target === null)
);
check(
  "a host:port target is passed through when the backend chose to expose it",
  dependencyRows({
    dependencies: { database: dep("ok"), redis: dep("ok", { target: "cache.internal:6379" }) },
  })[1].target === "cache.internal:6379"
);
check("no row exposes a password", !JSON.stringify(rows).includes("@"));
check("dependencyRows(null) returns an empty array rather than throwing", dependencyRows(null).length === 0);
check("dependencyRows({}) returns an empty array", dependencyRows({}).length === 0);
check(
  "a dependency the backend omitted produces no row",
  dependencyRows({ dependencies: { database: dep("ok") } }).length === 1
);

check('dependencyLabel("redis") reads as "cache"', dependencyLabel("redis") === "cache");
check("dependencyLabel passes an unknown key through unchanged", dependencyLabel("search") === "search");

console.log(`\n${"=".repeat(74)}`);
if (failures > 0) {
  console.log(`${failures} of ${count} CHECKS FAILED`);
  process.exit(1);
}
console.log(`ALL ${count} READINESS CHECKS PASSED`);
console.log("=".repeat(74));
