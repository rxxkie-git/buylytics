/**
 * Every module parses, and every import resolves to something that exists.
 *
 * Run with plain Node, no bundler:
 *
 *     node src/__checks__/resolve.check.mjs
 *
 * This exists because `npm run build` cannot run everywhere. The installed
 * `node_modules` carries platform-specific binaries — `@rollup/rollup-darwin-arm64`
 * and `@esbuild/darwin-arm64` here — so a Linux container cannot invoke Vite at all,
 * and with a blocked registry it cannot install the binaries it would need. That left
 * the frontend verified only by checks that read source as text.
 *
 * Reading source as text cannot see the two things that break a build most often:
 *
 *   1. A syntax error. Every check here other than this one uses regular expressions,
 *      which are perfectly happy with a file that has an unbalanced brace.
 *   2. An import that names a module which is not there — a renamed file, a deleted
 *      component, a package used but never installed.
 *
 * Both are caught by parsing with `@babel/parser` (already present as a transitive
 * dependency of `@vitejs/plugin-react`, and pure JavaScript, so it runs anywhere) and
 * then resolving each specifier the way a bundler would.
 *
 * The case check is not hypothetical padding. This project is developed on macOS, whose
 * filesystem is case-insensitive: `import Card from "./card"` finds `Card.jsx` locally
 * and fails on a case-sensitive Linux filesystem, which is where it would be deployed.
 * That class of defect is invisible on the machine that introduces it.
 *
 * What this deliberately does not claim: it is not a build. It does not run plugins, so
 * it cannot see a JSX transform failure, and it resolves a subpath import
 * (`react-dom/client`) only as far as the package root, because honouring an `exports`
 * map means reimplementing a resolver. It proves parseability and reachability, and the
 * summary at the end says so.
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { builtinModules } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(here, "../..");
const SRC = join(FRONTEND, "src");

const failures = [];
let passed = 0;

const check = (label, condition, detail = "") => {
  if (condition) {
    passed += 1;
    return;
  }
  failures.push(detail ? `${label}\n       ${detail}` : label);
};

// ---------------------------------------------------------------------------
// The parser is a transitive dependency, so say so plainly if it is absent
// rather than reporting a pass that never ran.
// ---------------------------------------------------------------------------

let parse;

try {
  ({ parse } = await import("@babel/parser"));
} catch {
  console.log("SKIP - @babel/parser is not installed; run `npm install` first");
  process.exit(0);
}

// ---------------------------------------------------------------------------
// Inventory
// ---------------------------------------------------------------------------

const SOURCE_EXTENSIONS = [".js", ".jsx"];
const RESOLVE_ORDER = ["", ".js", ".jsx", "/index.js", "/index.jsx"];

const walk = (directory) => {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) {
      found.push(...walk(full));
    } else if (SOURCE_EXTENSIONS.some((extension) => entry.endsWith(extension))) {
      found.push(full);
    }
  }
  return found;
};

const sources = walk(SRC).sort();
const shown = (file) => relative(FRONTEND, file);

check("src contains source modules", sources.length > 0, `walked ${SRC} and found none`);

const manifest = JSON.parse(readFileSync(join(FRONTEND, "package.json"), "utf8"));
const declared = new Set([
  ...Object.keys(manifest.dependencies ?? {}),
  ...Object.keys(manifest.devDependencies ?? {}),
]);

// ---------------------------------------------------------------------------
// 1. Every module parses
// ---------------------------------------------------------------------------

/** @type {Map<string, import("@babel/parser").ParseResult>} */
const trees = new Map();

for (const file of sources) {
  try {
    trees.set(
      file,
      parse(readFileSync(file, "utf8"), {
        sourceType: "module",
        plugins: ["jsx"],
        errorRecovery: false,
      }),
    );
    passed += 1;
  } catch (error) {
    const at = error.loc ? ` at line ${error.loc.line}, column ${error.loc.column}` : "";
    failures.push(`${shown(file)} does not parse\n       ${error.message}${at}`);
  }
}

// ---------------------------------------------------------------------------
// 2. Collect every specifier, from static imports, re-exports and dynamic import()
// ---------------------------------------------------------------------------

/** @type {{file: string, specifier: string, line: number}[]} */
const specifiers = [];

const visit = (node, file, seen = new Set()) => {
  if (!node || typeof node !== "object" || seen.has(node)) return;
  seen.add(node);

  if (Array.isArray(node)) {
    for (const child of node) visit(child, file, seen);
    return;
  }

  const isStatic =
    node.type === "ImportDeclaration" ||
    node.type === "ExportNamedDeclaration" ||
    node.type === "ExportAllDeclaration";

  if (isStatic && node.source?.value) {
    specifiers.push({ file, specifier: node.source.value, line: node.source.loc?.start.line ?? 0 });
  }

  // `lazy(() => import("../pages/Foo"))` is how routes are code-split here, so a
  // broken path in one of those is a runtime 404 for a whole page rather than a
  // build error. Only a literal argument can be checked.
  if (node.type === "ImportExpression" || (node.type === "CallExpression" && node.callee?.type === "Import")) {
    const argument = node.source ?? node.arguments?.[0];
    if (argument?.type === "StringLiteral") {
      specifiers.push({ file, specifier: argument.value, line: argument.loc?.start.line ?? 0 });
    }
  }

  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "leadingComments" || key === "trailingComments") continue;
    visit(node[key], file, seen);
  }
};

for (const [file, tree] of trees) visit(tree.program, file);

check("static imports were found", specifiers.length > 0, "the collector walked the trees and found nothing");

// ---------------------------------------------------------------------------
// 3. Relative specifiers resolve, with the on-disk case matching exactly
// ---------------------------------------------------------------------------

/**
 * Resolve like a bundler, but case-insensitively, the way macOS does.
 *
 * Resolving case-insensitively here is deliberate and is what makes the exactness
 * check below meaningful. If this used `existsSync` alone, then on a case-sensitive
 * filesystem a wrong-case import would fail *resolution* and the exactness check
 * could never fail — it would be unfalsifiable on Linux while being the only thing
 * that catches the defect on macOS, where it is introduced. Emulating the permissive
 * filesystem and then asserting exactness separately means either machine reports the
 * same failure for the same reason.
 *
 * Returns `{ path, exact }`, or null when nothing matches under any casing.
 */
const resolveRelative = (fromFile, specifier) => {
  const base = resolve(dirname(fromFile), specifier);

  for (const suffix of RESOLVE_ORDER) {
    const candidate = `${base}${suffix}`;
    if (existsSync(candidate) && statSync(candidate).isFile()) return { path: candidate, exact: true };
  }

  // Nothing matched exactly. Walk the path segment by segment, comparing without
  // regard to case, to find what macOS would have handed the bundler.
  for (const suffix of RESOLVE_ORDER) {
    const segments = `${base}${suffix}`.split("/").filter(Boolean);
    let current = "/";
    let ok = true;

    for (const segment of segments) {
      if (!existsSync(current)) {
        ok = false;
        break;
      }
      const match = readdirSync(current).find(
        (entry) => entry.toLowerCase() === segment.toLowerCase(),
      );
      if (!match) {
        ok = false;
        break;
      }
      current = join(current, match);
    }

    if (ok && existsSync(current) && statSync(current).isFile()) {
      return { path: current, exact: false };
    }
  }

  return null;
};

const relativeSpecifiers = specifiers.filter((entry) => entry.specifier.startsWith("."));

for (const { file, specifier, line } of relativeSpecifiers) {
  const resolved = resolveRelative(file, specifier);

  check(
    `${shown(file)}:${line} imports ${specifier}`,
    resolved !== null,
    `nothing at that path with any of ${RESOLVE_ORDER.filter(Boolean).join(", ")} - a renamed or deleted module`,
  );

  if (resolved) {
    check(
      `${shown(file)}:${line} imports ${specifier} with the exact on-disk case`,
      resolved.exact,
      `on disk it is ${shown(resolved.path)} - resolves on macOS, fails on Linux`,
    );
  }
}

// ---------------------------------------------------------------------------
// 4. Bare specifiers are installed, declared, and not Node builtins
// ---------------------------------------------------------------------------

/** `@scope/name/sub` -> `@scope/name`, `name/sub` -> `name`. */
const packageRoot = (specifier) => {
  const parts = specifier.split("/");
  return specifier.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
};

const bare = specifiers.filter(
  (entry) => !entry.specifier.startsWith(".") && !entry.specifier.startsWith("/"),
);

const usedPackages = new Map();

for (const entry of bare) {
  const root = packageRoot(entry.specifier);
  if (!usedPackages.has(root)) usedPackages.set(root, entry);
}

for (const [root, entry] of usedPackages) {
  const builtin = builtinModules.includes(root) || root.startsWith("node:");

  check(
    `${root} is not a Node builtin`,
    !builtin,
    `${shown(entry.file)}:${entry.line} imports it; a browser bundle has no such module`,
  );

  if (builtin) continue;

  check(
    `${root} is declared in package.json`,
    declared.has(root),
    `${shown(entry.file)}:${entry.line} imports it, so a clean install would not have it`,
  );

  check(
    `${root} is installed in node_modules`,
    existsSync(join(FRONTEND, "node_modules", root)),
    "declared but absent - the install is stale or partial",
  );
}

// ---------------------------------------------------------------------------
// 5. Every declared runtime dependency is actually installed
// ---------------------------------------------------------------------------

for (const name of Object.keys(manifest.dependencies ?? {})) {
  check(
    `declared dependency ${name} is installed`,
    existsSync(join(FRONTEND, "node_modules", name)),
    "in package.json but not in node_modules - `npm install` has not been run since it was added",
  );
}

// ---------------------------------------------------------------------------
// 6. The HTML entry point reaches a real module
// ---------------------------------------------------------------------------

const html = readFileSync(join(FRONTEND, "index.html"), "utf8");
const entryMatch = html.match(/<script[^>]+src="([^"]+)"[^>]*>/);

check("index.html declares a module script", entryMatch !== null, "Vite needs a script src to start from");

if (entryMatch) {
  const entrySpecifier = entryMatch[1].replace(/^\//, "./");
  const entry = resolveRelative(join(FRONTEND, "index.html"), entrySpecifier);

  check(
    `index.html entry ${entryMatch[1]} resolves`,
    entry !== null,
    "the entry named in the HTML does not exist, so the build has no input",
  );

  if (entry) {
    check(
      "the entry module was parsed",
      trees.has(entry.path),
      `${shown(entry.path)} is the entry but was not among the parsed sources`,
    );
  }
}

// ---------------------------------------------------------------------------

const total = passed + failures.length;

for (const failure of failures) {
  console.log(`  FAIL ${failure}`);
}

console.log();

if (failures.length) {
  console.log(`${failures.length} of ${total} RESOLVE CHECKS FAILED`);
  process.exit(1);
}

console.log(
  `ALL ${total} RESOLVE CHECKS PASSED  (${trees.size} modules parsed, ` +
    `${relativeSpecifiers.length} relative and ${usedPackages.size} package imports resolved)`,
);
