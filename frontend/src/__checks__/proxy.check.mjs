/**
 * Dev-server proxy agreement.
 *
 * Run with plain Node, no bundler:
 *
 *     node src/__checks__/proxy.check.mjs
 *
 * The bug this exists to prevent has a nasty signature. When the API client requests a
 * path the Vite proxy does not forward, the dev server answers with `index.html` —
 * its SPA fallback. The request succeeds with status 200, and the failure surfaces
 * much later as "Unexpected token '<'" from a JSON parse, or as a component rendering
 * an empty state forever. Nothing says "that path is not proxied".
 *
 * It was a real defect here, not a hypothetical: the proxy forwarded `/analytics` and
 * `/jobs`, which no backend route serves, while omitting `/product-analysis`, which the
 * ported Product Analysis page calls on mount. So this file asserts both directions.
 *
 * Only paths reachable from the real entry point count. `services/api.js` still exports
 * helpers for `/dashboard`, `/results`, `/insights`, `/preview` and the legacy inventory
 * routes, but they are imported exclusively by Phase 1 pages that `App.jsx` no longer
 * mounts. Requiring proxy entries for unreachable code would mean configuring the dev
 * server for pages nobody can open — and for `/upload` and `/inventory`, whose URLs the
 * SPA also owns, it would break the redirect living there. Reachability is computed from
 * `src/index.jsx` through static and `lazy(() => import(...))` edges, so re-routing one
 * of those pages turns its missing proxy entry into a failure here, which is exactly
 * when someone needs to be told.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(here, "..");
const FRONTEND = resolve(SRC, "..");

let passed = 0;
const failures = [];

const check = (label, condition, detail = "") => {
  if (condition) {
    passed += 1;
    return true;
  }
  failures.push(detail ? `${label}: ${detail}` : label);
  return false;
};

// ---------------------------------------------------------------------------
// Source loading
// ---------------------------------------------------------------------------

const walk = (dir) => {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...walk(full));
    } else if (/\.(jsx?|mjs)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
};

const files = walk(SRC).filter((file) => !file.includes("__checks__"));
const sourceOf = new Map(files.map((file) => [file, readFileSync(file, "utf8")]));
const relative = (file) => file.slice(SRC.length + 1);

/**
 * Blank out comments so a commented-out call or an example path in a docstring is not
 * read as real code. String contents are preserved — they are the subject here.
 */
const stripComments = (text) => {
  let out = "";
  let index = 0;
  let quote = null;

  while (index < text.length) {
    const char = text[index];
    const next = text[index + 1];

    if (quote) {
      out += char;
      if (char === "\\") {
        out += next ?? "";
        index += 2;
        continue;
      }
      if (char === quote) quote = null;
      index += 1;
      continue;
    }

    if (char === '"' || char === "'" || char === "`") {
      quote = char;
      out += char;
      index += 1;
      continue;
    }

    if (char === "/" && next === "/") {
      while (index < text.length && text[index] !== "\n") index += 1;
      continue;
    }

    if (char === "/" && next === "*") {
      index += 2;
      while (index < text.length && !(text[index] === "*" && text[index + 1] === "/")) {
        if (text[index] === "\n") out += "\n";
        index += 1;
      }
      index += 2;
      continue;
    }

    out += char;
    index += 1;
  }

  return out;
};

const clean = new Map(files.map((file) => [file, stripComments(sourceOf.get(file))]));

// ---------------------------------------------------------------------------
// Reachability from the real entry point
// ---------------------------------------------------------------------------

const IMPORT = /import\s+(?:[^;'"]*?\s*from\s*)?["']([^"']+)["']/g;
const LAZY = /import\(\s*["']([^"']+)["']\s*\)/g;

/**
 * `export * as x from "./y"`, `export * from "./y"`, `export { a } from "./y"`.
 *
 * These are import edges that the word `import` never appears in, and missing them made
 * the first version of this file blind to the entire v1 API layer: `api/index.js` reaches
 * `api/dashboard.js` and its siblings only through `export * as`. The check found three
 * request paths instead of thirty and said so, which is the only reason it was noticed.
 */
const RE_EXPORT = /export\s+(?:\*(?:\s+as\s+[A-Za-z_$][\w$]*)?|\{[^}]*\})\s*from\s*["']([^"']+)["']/g;

const resolveLocal = (fromFile, specifier) => {
  if (!specifier.startsWith(".")) return null;

  const base = resolve(dirname(fromFile), specifier);
  const candidates = [
    base,
    `${base}.js`,
    `${base}.jsx`,
    `${base}.mjs`,
    join(base, "index.js"),
    join(base, "index.jsx"),
  ];

  return candidates.find((candidate) => sourceOf.has(candidate)) ?? null;
};

const ENTRY = resolve(SRC, "index.jsx");

check("the entry point src/index.jsx exists", sourceOf.has(ENTRY), "nothing to walk from");

const reachable = new Set();

if (sourceOf.has(ENTRY)) {
  const queue = [ENTRY];

  while (queue.length) {
    const file = queue.pop();
    if (reachable.has(file)) continue;
    reachable.add(file);

    const text = clean.get(file) ?? "";

    for (const pattern of [IMPORT, LAZY, RE_EXPORT]) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(text)) !== null) {
        const target = resolveLocal(file, match[1]);
        if (target && !reachable.has(target)) queue.push(target);
      }
    }
  }
}

check(
  "reachability found more than just the entry file",
  reachable.size > 5,
  `only ${reachable.size} modules reachable, so the import walk is not working`,
);

// ---------------------------------------------------------------------------
// Which exports of a reachable module are actually used
// ---------------------------------------------------------------------------

/**
 * Module-level reachability is too coarse here, and pretending otherwise produced ten
 * false failures on the first run of this file.
 *
 * `services/api.js` is one large Phase 1 module holding fifteen helpers. Exactly one of
 * them, `getProductAnalysisData`, is imported by a routed page; the rest are imported
 * only by Phase 1 pages `App.jsx` no longer mounts. The module is therefore reachable
 * while most of its request paths are not, and requiring proxy entries for all of them
 * would mean configuring the dev server for URLs nobody can reach — including `/upload`
 * and `/inventory`, where a proxy entry would break the SPA redirect living there.
 *
 * `ALL` marks a module imported by namespace or default, where every export must be
 * assumed live. The `api/index.js` barrel does exactly that.
 */
const ALL = Symbol("all exports");
const usedExports = new Map(); // file -> Set<string> | ALL

const noteUse = (file, names) => {
  if (usedExports.get(file) === ALL) return;

  if (names === ALL) {
    usedExports.set(file, ALL);
    return;
  }

  if (!usedExports.has(file)) usedExports.set(file, new Set());
  for (const name of names) usedExports.get(file).add(name);
};

const IMPORT_CLAUSE = /import\s+([^;]*?)\s*from\s*["']([^"']+)["']/g;

for (const file of reachable) {
  const text = clean.get(file) ?? "";

  IMPORT_CLAUSE.lastIndex = 0;
  let match;

  while ((match = IMPORT_CLAUSE.exec(text)) !== null) {
    const target = resolveLocal(file, match[2]);
    if (!target) continue;

    const clause = match[1].trim();

    // `import * as x` and a default import both expose the whole module surface.
    if (/^\*\s+as\s/.test(clause) || /^[A-Za-z_$][\w$]*\s*(,|$)/.test(clause)) {
      noteUse(target, ALL);
    }

    const braced = clause.match(/\{([^}]*)\}/);
    if (braced) {
      const names = braced[1]
        .split(",")
        .map((part) => part.trim().split(/\s+as\s+/)[0].trim())
        .filter(Boolean);
      noteUse(target, names);
    }
  }

  // Bare `import("./x")` gives no names, so treat the target as fully live.
  LAZY.lastIndex = 0;
  while ((match = LAZY.exec(text)) !== null) {
    const target = resolveLocal(file, match[1]);
    if (target) noteUse(target, ALL);
  }

  // Re-exports. `export * as x` and `export *` expose the whole surface; a braced
  // re-export names exactly what it forwards.
  RE_EXPORT.lastIndex = 0;
  while ((match = RE_EXPORT.exec(text)) !== null) {
    const target = resolveLocal(file, match[1]);
    if (!target) continue;

    const braced = match[0].match(/\{([^}]*)\}/);

    if (!braced) {
      noteUse(target, ALL);
      continue;
    }

    noteUse(
      target,
      braced[1]
        .split(",")
        .map((part) => part.trim().split(/\s+as\s+/)[0].trim())
        .filter(Boolean),
    );
  }
}

// The entry module's own top-level code runs unconditionally.
noteUse(ENTRY, ALL);

/**
 * Top-level declarations in a module, in source order, with whether they are exported.
 *
 * A request call is attributed to the nearest declaration above it. An internal helper
 * is treated as live regardless of name, because it may be called from a live export and
 * position alone cannot tell — erring toward "live" risks a redundant proxy entry, while
 * erring the other way would let a genuinely unreachable path pass unnoticed.
 */
const declarationsOf = (text) => {
  const found = [];
  const pattern =
    /^(export\s+)?(?:async\s+)?(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*))/gm;
  let match;

  while ((match = pattern.exec(text)) !== null) {
    found.push({
      index: match.index,
      exported: Boolean(match[1]),
      name: match[2] ?? match[3],
    });
  }

  return found;
};

const isLive = (file, callIndex, declarations) => {
  const used = usedExports.get(file);
  if (used === ALL) return true;

  let enclosing = null;
  for (const declaration of declarations) {
    if (declaration.index <= callIndex) enclosing = declaration;
    else break;
  }

  if (!enclosing) return true; // top-level module code
  if (!enclosing.exported) return true; // internal helper, conservatively live

  return Boolean(used && used.has(enclosing.name));
};

// ---------------------------------------------------------------------------
// Request paths issued by reachable modules
// ---------------------------------------------------------------------------

/** `const BASE = "/api/v1/x"` style declarations, per file. */
const literalConstants = (text) => {
  const table = new Map();
  const pattern = /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*["'](\/[^"']*)["']/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    table.set(match[1], match[2]);
  }
  return table;
};

/**
 * First argument of a call to a request helper, when it is a literal.
 *
 * Requiring a quote immediately after the parenthesis is what keeps `map.get(key)` and
 * `params.get("limit")` out: the former has no literal, and the latter yields a string
 * that does not start with `/` and is dropped below.
 */
const REQUEST_CALL =
  /\b(?:get|post|put|patch|del|remove|upload|download|request)\s*\(\s*(`[^`]*`|"[^"]*"|'[^']*')/g;

const requestPaths = new Map(); // path -> Set of files

for (const file of reachable) {
  const text = clean.get(file) ?? "";
  const constants = literalConstants(text);
  const declarations = declarationsOf(text);

  REQUEST_CALL.lastIndex = 0;
  let match;

  while ((match = REQUEST_CALL.exec(text)) !== null) {
    if (!isLive(file, match.index, declarations)) continue;

    const raw = match[1];
    let literal = raw.slice(1, -1);

    // `${BASE}/jobs` -> resolve BASE from the same file's own declarations.
    const interpolated = literal.match(/^\$\{\s*([A-Za-z_$][\w$]*)\s*\}(.*)$/);
    if (interpolated) {
      const base = constants.get(interpolated[1]);
      if (!base) continue;
      literal = `${base}${interpolated[2]}`;
    }

    if (!literal.startsWith("/")) continue;

    // Trailing `${...}` segments are path parameters; the prefix is what matters.
    const path = literal.split("${")[0].replace(/\/+$/, "") || "/";

    if (!requestPaths.has(path)) requestPaths.set(path, new Set());
    requestPaths.get(path).add(relative(file));
  }
}

check(
  "request paths were found in reachable modules",
  requestPaths.size >= 5,
  `found ${requestPaths.size}; the call-site scan is probably broken`,
);

// ---------------------------------------------------------------------------
// The proxy table in vite.config.js
// ---------------------------------------------------------------------------

const configText = stripComments(readFileSync(resolve(FRONTEND, "vite.config.js"), "utf8"));

/** Text of the balanced `{...}` block that starts at or after `from`. */
const blockAt = (text, from) => {
  const start = text.indexOf("{", from);
  if (start === -1) return "";

  let depth = 0;

  for (let index = start; index < text.length; index += 1) {
    if (text[index] === "{") depth += 1;
    else if (text[index] === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(start, index + 1);
    }
  }

  return "";
};

const proxyBlock = blockAt(configText, configText.indexOf("proxy: {"));

check("vite.config.js declares a server proxy", proxyBlock.length > 0, "no `proxy: {` block found");

/** proxy key -> identifier it is assigned to. */
const proxyEntries = new Map();
{
  const pattern = /["']([^"']+)["']\s*:\s*([A-Za-z_$][\w$]*)/g;
  let match;
  while ((match = pattern.exec(proxyBlock)) !== null) {
    proxyEntries.set(match[1], match[2]);
  }
}

check("the proxy table has entries", proxyEntries.size > 0, "parsed zero entries");

/** Identifiers whose object literal configures a `bypass`. */
const identifiersWithBypass = new Set();
for (const identifier of new Set(proxyEntries.values())) {
  const declaration = configText.indexOf(`const ${identifier} =`);
  if (declaration === -1) continue;
  if (blockAt(configText, declaration).includes("bypass")) {
    identifiersWithBypass.add(identifier);
  }
}

const covers = (prefix, path) => path === prefix || path.startsWith(`${prefix}/`);

// ---------------------------------------------------------------------------
// Direction 1: everything the routed app requests must be forwarded
// ---------------------------------------------------------------------------

for (const [path, callers] of [...requestPaths].sort()) {
  const prefix = [...proxyEntries.keys()].find((key) => covers(key, path));

  check(
    `${path} is forwarded by the dev proxy`,
    Boolean(prefix),
    `requested by ${[...callers].join(", ")} but no proxy entry matches; the dev server ` +
      "would answer with index.html and the JSON parse would fail",
  );
}

// ---------------------------------------------------------------------------
// Direction 2: no entry forwards a path nothing asks for
// ---------------------------------------------------------------------------

for (const prefix of [...proxyEntries.keys()].sort()) {
  const used = [...requestPaths.keys()].some((path) => covers(prefix, path));

  check(
    `the ${prefix} proxy entry is still needed`,
    used,
    "no reachable module requests this path; a stale entry shadows any SPA route at " +
      "the same URL for no reason",
  );
}

// ---------------------------------------------------------------------------
// Direction 3: a path the SPA also owns needs a bypass, or its route dies
// ---------------------------------------------------------------------------

const appSource = stripComments(readFileSync(resolve(SRC, "App.jsx"), "utf8"));
const spaRoutes = new Set(
  [...appSource.matchAll(/<Route\s+path="(\/[^"]*)"/g)].map((match) => match[1]).filter((path) => path !== "/"),
);

check("App.jsx route paths were parsed", spaRoutes.size > 0, "found none");

for (const [prefix, identifier] of [...proxyEntries].sort()) {
  if (!spaRoutes.has(prefix)) continue;

  check(
    `${prefix} is claimed by both the API and the SPA, so its proxy entry bypasses navigations`,
    identifiersWithBypass.has(identifier),
    `proxied via \`${identifier}\`, which has no bypass; a browser opening ${prefix} would ` +
      "receive proxied JSON instead of the SPA route defined in App.jsx",
  );
}

// A bypass on a path the SPA does not own is harmless but misleading, so it is worth
// knowing about: it suggests a route was removed and the config not revisited.
for (const [prefix, identifier] of [...proxyEntries].sort()) {
  if (!identifiersWithBypass.has(identifier)) continue;

  check(
    `the bypass on ${prefix} is justified by a matching SPA route`,
    spaRoutes.has(prefix),
    `\`${identifier}\` bypasses HTML navigations, but App.jsx has no ${prefix} route to bypass to`,
  );
}

// ---------------------------------------------------------------------------
// The target must stay configurable rather than pinned to one machine
// ---------------------------------------------------------------------------

check(
  "the proxy target is overridable by environment",
  /VITE_DEV_API_TARGET/.test(configText),
  "the dev target is hardcoded, so anyone running the backend elsewhere must edit the config",
);

check(
  "the proxy target default is a loopback address, not a deployed host",
  /target\s*=\s*env\.VITE_DEV_API_TARGET\s*\|\|\s*["']http:\/\/(127\.0\.0\.1|localhost)/.test(configText),
  "the fallback target should be local; a remote default would send dev traffic off-machine",
);

check(
  "no VITE_ variable carries the dev proxy target into the bundle",
  !/VITE_DEV_API_TARGET/.test([...reachable].map((file) => clean.get(file) ?? "").join("\n")),
  "src/ reads VITE_DEV_API_TARGET, which is a dev-server concern and would be compiled in",
);

// ---------------------------------------------------------------------------

const total = passed + failures.length;

for (const failure of failures) {
  console.log(`  FAIL ${failure}`);
}

console.log();

if (failures.length) {
  console.log(`${failures.length} of ${total} PROXY CHECKS FAILED`);
  process.exit(1);
}

console.log(`ALL ${total} PROXY CHECKS PASSED`);
