/**
 * Executable checks for `src/utils/format.js`.
 *
 * Run with `node src/utils/__checks__/format.check.mjs` from `frontend/`. Plain
 * Node with no test runner, because the repo has no frontend test dependency
 * installed and adding one to prove nine strings match would be a poor trade.
 *
 * The important assertion is the last group: it reads
 * `backend/app/analytics/scoring.py` and checks the segment names in the colour
 * map against `RFM_SEGMENT_ORDER` in the Python source. A rename on either side
 * fails here instead of silently rendering a segment in the fallback grey.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCORING_PY = join(HERE, "..", "..", "..", "..", "backend", "app", "analytics", "scoring.py");

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

// `format.js` reads its currency and locale from `src/config/env.js`, which
// guards the `import.meta.env` access — so this module imports cleanly under plain
// Node and falls back to the documented defaults.
const format = await import("../format.js");
const env = await import("../../config/env.js");

console.log("=".repeat(74));
console.log("FRONTEND FORMATTER CHECKS");
console.log("=".repeat(74));

group("0 the env module is importable outside Vite");
check("importing config/env.js does not throw", typeof env.CURRENCY === "string");
check("currency falls back to the documented default", env.CURRENCY === "INR", `got ${env.CURRENCY}`);
check("locale falls back to the documented default", env.LOCALE === "en-IN", `got ${env.LOCALE}`);
check("there is no localhost fallback baked into the base URL",
  !env.API_BASE_URL.includes("localhost"), `got ${env.API_BASE_URL}`);
check("an unset base URL is empty, meaning same origin", env.API_BASE_URL === "");
check("environmentSummary exposes no secret",
  !JSON.stringify(env.environmentSummary()).match(/sk_|secret|password/i),
  JSON.stringify(env.environmentSummary()));

// ---------------------------------------------------------------------------
group("1 zero is a value, absence is a dash");

check('formatCount(0) === "0"', format.formatCount(0) === "0", `got ${format.formatCount(0)}`);
check("formatCurrency(0) is not a dash", format.formatCurrency(0) !== "—", `got ${format.formatCurrency(0)}`);
check("formatDecimal(0) is not a dash", format.formatDecimal(0) !== "—");
check('formatShare(0) === "0.0%"', format.formatShare(0) === "0.0%", `got ${format.formatShare(0)}`);
check("formatCompact(0) is not a dash", format.formatCompact(0) !== "—");

for (const [name, fn] of [
  ["formatCount", format.formatCount],
  ["formatCurrency", format.formatCurrency],
  ["formatDecimal", format.formatDecimal],
  ["formatShare", format.formatShare],
  ["formatDate", format.formatDate],
  ["formatDateTime", format.formatDateTime],
  ["formatRelative", format.formatRelative],
  ["formatCompact", format.formatCompact],
]) {
  check(`${name}(null) is a dash`, fn(null) === "—", `got ${fn(null)}`);
  check(`${name}(undefined) is a dash`, fn(undefined) === "—", `got ${fn(undefined)}`);
}

// ---------------------------------------------------------------------------
group("2 currency and locale agree");

const money = format.formatCurrency(1234567);
check("currency output carries no stray dollar sign for INR", format.CURRENCY !== "INR" || !money.includes("$"), `got ${money}`);
check("currency output is non-empty", money.length > 0);
check(
  "the same locale drives numbers and currency",
  format.formatCount(1234567).replace(/[^\d]/g, "") === "1234567",
  `got ${format.formatCount(1234567)}`
);
check(
  "precise currency keeps two decimals",
  /[.,]\d\d(\D|$)/.test(format.formatCurrencyPrecise(1234.5)),
  `got ${format.formatCurrencyPrecise(1234.5)}`
);

// ---------------------------------------------------------------------------
group("3 share is a fraction, percent is already scaled");

check('formatShare(0.25) === "25.0%"', format.formatShare(0.25) === "25.0%", `got ${format.formatShare(0.25)}`);
check('formatShare(1) === "100.0%"', format.formatShare(1) === "100.0%", `got ${format.formatShare(1)}`);
check('formatPercent(25) === "25.0%"', format.formatPercent(25) === "25.0%", `got ${format.formatPercent(25)}`);
check(
  "the two are not interchangeable, which is why they have different names",
  format.formatShare(25) !== format.formatPercent(25)
);

// ---------------------------------------------------------------------------
group("4 compact numbers and durations");

check('formatCompact(1500) === "1.5K"', format.formatCompact(1500) === "1.5K", `got ${format.formatCompact(1500)}`);
check('formatCompact(2_400_000) === "2.4M"', format.formatCompact(2_400_000) === "2.4M");
check('formatCompact(-1500) === "-1.5K"', format.formatCompact(-1500) === "-1.5K", `got ${format.formatCompact(-1500)}`);
check("formatCompact(999) is not abbreviated", !format.formatCompact(999).includes("K"));
check('formatDuration(450) === "450 ms"', format.formatDuration(450) === "450 ms", `got ${format.formatDuration(450)}`);
check('formatDuration(4500) === "4.5 s"', format.formatDuration(4500) === "4.5 s");
check('formatDuration(125000) === "2m 5s"', format.formatDuration(125_000) === "2m 5s", `got ${format.formatDuration(125_000)}`);

// ---------------------------------------------------------------------------
group("5 invalid dates do not render as Invalid Date");

for (const bad of ["not-a-date", "", "2026-13-45T99:99:99"]) {
  check(`formatDate(${JSON.stringify(bad)}) is a dash`, format.formatDate(bad) === "—", `got ${format.formatDate(bad)}`);
  check(`formatRelative(${JSON.stringify(bad)}) is a dash`, format.formatRelative(bad) === "—");
}

const oneHourAgo = new Date(Date.now() - 3_600_000).toISOString();
check("an hour ago reads as hours", format.formatRelative(oneHourAgo).includes("hour"), `got ${format.formatRelative(oneHourAgo)}`);
check('a future timestamp reads as "just now", not "-1 minutes ago"',
  format.formatRelative(new Date(Date.now() + 60_000).toISOString()) === "just now");

// ---------------------------------------------------------------------------
group("5b a VALID date is actually formatted");

// This group exists because the previous one did not catch a real crash. Every
// date assertion above passes a blank or an unparseable value, and both return the
// em dash from a guard clause at the top of the function — so the body, which is
// where `LOCALE` was referenced without a local binding, was never executed. The
// dashboard called these with real timestamps and died with
// "LOCALE is not defined".
//
// `attempt` turns a throw into a reported failure instead of letting it abort the
// run, so the error text is named in the output and the groups after this one
// still execute.
const VALID = "2026-03-05T14:30:00Z";

const attempt = (label, fn, predicate) => {
  let value;
  try {
    value = fn();
  } catch (error) {
    check(label, false, `threw ${error.name}: ${error.message}`);
    return;
  }
  check(label, predicate(value), `got ${JSON.stringify(value)}`);
};

attempt(
  "formatDate on a real timestamp returns a rendered date",
  () => format.formatDate(VALID),
  (v) => v !== "—" && String(v).includes("2026")
);
attempt(
  "formatDateTime on a real timestamp returns a rendered date and time",
  () => format.formatDateTime(VALID),
  (v) => v !== "—" && /\d/.test(String(v))
);
attempt(
  "formatDate accepts a Date instance as well as a string",
  () => format.formatDate(new Date(VALID)),
  (v) => String(v).includes("2026")
);
attempt(
  "formatRelative falls through to formatDate for an old timestamp",
  () => format.formatRelative("2020-01-01T00:00:00Z"),
  (v) => v !== "—" && String(v).includes("2020")
);

// Every exported function, called with a plausible real argument rather than a
// null. A ReferenceError to a name that only exists as a re-export cannot be seen
// any other way: the module imports fine and only the call site throws.
const REAL_ARGUMENTS = {
  formatCurrency: [1234.56],
  formatCurrencyPrecise: [1234.56],
  formatCount: [1234],
  formatDecimal: [12.34],
  formatShare: [0.42],
  formatPercent: [42],
  formatCompact: [1_234_567],
  formatDuration: [1234],
  formatDate: [VALID],
  formatDateTime: [VALID],
  formatRelative: [VALID],
  segmentColor: ["Champions"],
  segmentRank: ["Champions"],
  clusterColor: [3],
  truncate: ["a long enough string to truncate", 8],
  shortId: ["11111111-1111-1111-1111-111111111111"],
};

for (const [name, value] of Object.entries(format)) {
  if (typeof value !== "function") continue;

  const args = REAL_ARGUMENTS[name];
  check(`${name} has a real-argument case in this check file`, Array.isArray(args),
    "add one, or a crash in its body stays invisible");

  if (!args) continue;

  let threw = null;
  try {
    value(...args);
  } catch (error) {
    threw = error;
  }
  check(`${name}(${args.map((a) => JSON.stringify(a)).join(", ")}) does not throw`,
    threw === null, threw ? `${threw.name}: ${threw.message}` : "");
}

// ---------------------------------------------------------------------------
group("5c no module references a name it only re-exports");

// The static half of the same bug. `export { X } from "./m.js"` forwards the
// binding to importers and creates no local `X`, so a bare `X` anywhere else in
// that file is a ReferenceError waiting for the function to be called. Cheap to
// check across the whole tree, and it generalises beyond LOCALE.
const SRC = join(HERE, "..", "..");

const sourceFiles = (function walk(directory) {
  return readdirSync(directory).flatMap((entry) => {
    if (entry === "node_modules" || entry === "__checks__") return [];
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.(js|jsx|mjs)$/.test(entry) ? [full] : [];
  });
})(SRC);

check("the re-export scan found source files to scan", sourceFiles.length > 20,
  `only ${sourceFiles.length} files under ${SRC}`);

const reexportHazards = [];

for (const file of sourceFiles) {
  const source = readFileSync(file, "utf8");
  const reexported = new Set();

  for (const match of source.matchAll(/export\s*\{([^}]*)\}\s*from\s*["'][^"']+["']/g)) {
    for (const part of match[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/)[0].trim();
      if (name) reexported.add(name);
    }
  }

  if (reexported.size === 0) continue;

  // Strip the re-export lines themselves, and any line that imports the name
  // locally (`import { LOCALE as LOCALE_TAG }`), before looking for bare uses.
  const body = source
    .replace(/export\s*\{[^}]*\}\s*from\s*["'][^"']+["'];?/g, "")
    .replace(/import\s+[^;]*?from\s*["'][^"']+["'];?/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");

  for (const name of reexported) {
    if (new RegExp(`(?<![\\w.$])${name}(?![\\w$])`).test(body)) {
      reexportHazards.push(`${file.slice(SRC.length + 1)} references bare ${name}`);
    }
  }
}

check("no file uses a bare identifier that it only re-exports", reexportHazards.length === 0,
  reexportHazards.join("\n       "));


// ---------------------------------------------------------------------------
group("6 segment names match the Python source");

const scoringSource = readFileSync(SCORING_PY, "utf8");
const orderMatch = scoringSource.match(/RFM_SEGMENT_ORDER[^=]*=\s*\(([\s\S]*?)\)/);

check("RFM_SEGMENT_ORDER was located in scoring.py", Boolean(orderMatch));

const pythonSegments = [...(orderMatch?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);

check("the Python tuple parsed to nine segments", pythonSegments.length === 9, `got ${pythonSegments.length}: ${pythonSegments}`);

check(
  "the JS order array matches the Python tuple exactly, element for element",
  JSON.stringify(format.RFM_SEGMENT_ORDER) === JSON.stringify(pythonSegments),
  `js:     ${JSON.stringify(format.RFM_SEGMENT_ORDER)}\n       python: ${JSON.stringify(pythonSegments)}`
);

for (const segment of pythonSegments) {
  check(
    `"${segment}" has a colour of its own`,
    Object.prototype.hasOwnProperty.call(format.RFM_SEGMENT_COLORS, segment),
    "falls through to the fallback grey"
  );
  check(`"${segment}" is not the fallback colour`, format.segmentColor(segment) !== "#94A3B8");
}

const phantom = Object.keys(format.RFM_SEGMENT_COLORS).filter((key) => !pythonSegments.includes(key));
check("no colour is defined for a segment the backend never emits", phantom.length === 0, `phantom: ${phantom}`);

const colours = Object.values(format.RFM_SEGMENT_COLORS);
check("every segment colour is distinct", new Set(colours).size === colours.length);

check("an unknown segment gets the fallback rather than throwing", format.segmentColor("Nonexistent") === "#94A3B8");
check("segmentColor(null) does not throw", format.segmentColor(null) === "#94A3B8");
check("segmentRank puts Champions first", format.segmentRank("Champions") === 0);
check("segmentRank sorts an unknown segment last", format.segmentRank("Nonexistent") === 9);

// ---------------------------------------------------------------------------
group("7 status colour maps cover the backend vocabulary");

for (const status of ["queued", "running", "completed", "failed"]) {
  check(`job status "${status}" has a colour`, Boolean(format.JOB_STATUS_COLOR[status]));
}

for (const status of ["ok", "degraded", "disabled", "unavailable"]) {
  check(`dependency status "${status}" has a colour`, Boolean(format.DEPENDENCY_COLOR[status]));
}

check("cluster colours cycle rather than going undefined", Boolean(format.clusterColor(97)));
check("clusterColor tolerates a negative index", Boolean(format.clusterColor(-3)));
check("clusterColor tolerates a non-number", Boolean(format.clusterColor(null)));

// ---------------------------------------------------------------------------
group("8 truncation");

check("truncate leaves a short string alone", format.truncate("abc", 10) === "abc");
check("truncate shortens a long string to the limit", format.truncate("a".repeat(50), 10).length === 10);
check("truncate marks that it truncated", format.truncate("a".repeat(50), 10).endsWith("…"));
check("truncate(null) does not throw", format.truncate(null) === "");
check("shortId shortens a uuid", format.shortId("11111111-1111-1111-1111-111111111111") === "11111111…");
check("shortId leaves a short id alone", format.shortId("abc") === "abc");

console.log(`\n${"=".repeat(74)}`);
if (failures > 0) {
  console.log(`${failures} of ${count} CHECKS FAILED`);
  process.exit(1);
}
console.log(`ALL ${count} FORMATTER CHECKS PASSED`);
console.log("=".repeat(74));
