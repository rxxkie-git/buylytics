/**
 * Static verification of the frontend module graph, run with plain Node.
 *
 *   node src/__checks__/wiring.check.mjs
 *
 * Why this exists: `vite build` cannot run in this environment — `frontend/node_modules`
 * is absent and the npm registry is unreachable, so there is no esbuild, no rollup and
 * no way to compile the JSX. Shipping ~4,000 lines of new JSX with nothing but a
 * careful read behind it is not good enough, so this file does the checks that do not
 * need a compiler:
 *
 * 1. **Every relative import resolves to a file that exists.** A typo in a lazy import
 *    path in `App.jsx` is invisible until someone clicks that nav item, because
 *    `React.lazy` defers the request to first render.
 * 2. **Every named import is actually exported by the target module.** The single most
 *    common way a refactor breaks a page — the module resolves, the binding is
 *    `undefined`, and React fails at render with "type is invalid".
 * 3. **Every barrel namespace member exists.** `catalogApi.fetchOrders` has to be a real
 *    export of `api/catalog.js`; a misspelling here throws only when the call is made.
 * 4. **Brackets balance and JSX tags close**, checked with a scanner that strips
 *    comments and string bodies first. This is the cheap substitute for a parser: it
 *    catches truncated files and forgotten closing tags, which is most of what a
 *    hand-written render tree gets wrong.
 * 5. **Every nav destination has a route, and every route element file exists.** A
 *    sidebar item pointing at a path with no `<Route>` renders an empty shell, which
 *    looks like a data problem rather than a routing one.
 * 6. **No hardcoded hosts and no secret-shaped strings** anywhere under `src/`.
 *
 * What it cannot check, stated plainly: whether `@mui/material`, `@mui/icons-material`,
 * `recharts` and `@clerk/clerk-react` export the names imported from them. Those are
 * bare specifiers with no package installed to inspect. `vite build` remains the real
 * verification for those, and has to be run by a human with `node_modules` present.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(here, "..");

let passed = 0;
const failures = [];

const check = (label, condition, detail = "") => {
  if (condition) {
    passed += 1;
    return;
  }
  failures.push(detail ? `${label}\n      → ${detail}` : label);
};

// ---------------------------------------------------------------------------
// Source collection
// ---------------------------------------------------------------------------

const walk = (dir) => {
  const found = [];

  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);

    if (statSync(full).isDirectory()) {
      found.push(...walk(full));
    } else if (/\.(jsx?|mjs)$/.test(entry)) {
      found.push(full);
    }
  }

  return found;
};

const files = walk(SRC).filter((file) => !file.includes("__checks__"));
const sourceOf = new Map(files.map((file) => [file, readFileSync(file, "utf8")]));
const relative = (file) => file.slice(SRC.length + 1);

check("the source tree was found", files.length > 20, `only ${files.length} files under ${SRC}`);

// ---------------------------------------------------------------------------
// Comment and string stripping
//
// Bodies are replaced with spaces rather than removed so that every offset in the
// stripped text still lines up with the original — which is what makes a reported
// line number worth anything.
// ---------------------------------------------------------------------------

const blank = (text) => text.replace(/[^\n]/g, " ");

/**
 * Blank out comments, and optionally string bodies, preserving length exactly.
 *
 * Two modes, because the checks need different things. Bracket and tag balance must not
 * see `"</div>"` inside a string literal, so those run with `strings: true`. Import
 * specifiers and hardcoded-host detection live *inside* string literals, so those run
 * with `strings: false` — comments gone, quotes intact. Running the import scan against
 * the fully blanked text was the first version of this file, and every specifier came
 * back as a run of spaces.
 */
const strip = (source, { strings = true } = {}) => {
  let out = "";
  let i = 0;

  // A `/` is a regex only in expression position. Tracking the previous significant
  // character is the standard way to tell `a / b` from `/ab/`.
  let prevSignificant = "";

  // Deliberately conservative, and `<` `>` `}` are deliberately absent. With `}` in the
  // set, the `/` of `size={14} />` read as a regex opening and swallowed everything up
  // to the next slash — which was the `/` of the *following* self-closing tag. With `<`
  // in the set, `</Box></Grid>` did the same thing. Both silently deleted JSX and then
  // reported the deletion as an unclosed element.
  const isRegexPosition = () => prevSignificant === "" || "(,=:[!&|?;{".includes(prevSignificant);

  while (i < source.length) {
    const char = source[i];
    const next = source[i + 1];

    if (char === "/" && next === "/") {
      const end = source.indexOf("\n", i);
      const stop = end === -1 ? source.length : end;
      out += blank(source.slice(i, stop));
      i = stop;
      continue;
    }

    if (char === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      const stop = end === -1 ? source.length : end + 2;
      out += blank(source.slice(i, stop));
      i = stop;
      continue;
    }

    if (char === '"' || char === "'") {
      let j = i + 1;
      while (j < source.length && source[j] !== char) {
        if (source[j] === "\\") j += 1;
        if (source[j] === "\n") break;
        j += 1;
      }
      const body = source.slice(i + 1, j);
      out += char + (strings ? blank(body) : body) + (source[j] === char ? char : "");
      i = j + 1;
      prevSignificant = char;
      continue;
    }

    if (char === "`") {
      // Template literals can nest `${...}` containing anything, including JSX. The
      // body is blanked wholesale in `strings` mode, so a template holding markup is
      // not scanned — none in this codebase do.
      let j = i + 1;
      let depth = 0;
      while (j < source.length) {
        if (source[j] === "\\") {
          j += 2;
          continue;
        }
        if (source[j] === "$" && source[j + 1] === "{") depth += 1;
        if (source[j] === "}" && depth > 0) depth -= 1;
        if (source[j] === "`" && depth === 0) break;
        j += 1;
      }
      const body = source.slice(i + 1, j);
      out += "`" + (strings ? blank(body) : body) + (source[j] === "`" ? "`" : "");
      i = j + 1;
      prevSignificant = "`";
      continue;
    }

    // `/>` closes a JSX element and `</` opens a closing tag. Neither is ever a regex,
    // and treating them as one destroys the markup this file exists to check.
    if (char === "/" && next !== ">" && prevSignificant !== "<" && isRegexPosition()) {
      let j = i + 1;
      let inClass = false;
      let closed = false;

      while (j < source.length && source[j] !== "\n") {
        if (source[j] === "\\") {
          j += 2;
          continue;
        }
        if (source[j] === "[") inClass = true;
        else if (source[j] === "]") inClass = false;
        else if (source[j] === "/" && !inClass) {
          closed = true;
          break;
        }
        j += 1;
      }

      if (closed) {
        // Flags follow the closing slash and are part of the literal.
        let k = j + 1;
        while (k < source.length && /[a-z]/.test(source[k])) k += 1;
        out += blank(source.slice(i, k));
        i = k;
        prevSignificant = "x";
        continue;
      }
    }

    out += char;
    if (!/\s/.test(char)) prevSignificant = char;
    i += 1;
  }

  return out;
};

// `clean` — comments and string bodies blanked. For structural checks.
// `bare`  — comments blanked, strings intact. For anything that reads a literal.
const clean = new Map(files.map((file) => [file, strip(sourceOf.get(file))]));
const bare = new Map(files.map((file) => [file, strip(sourceOf.get(file), { strings: false })]));

check(
  "the stripper preserves length exactly, so offsets stay usable",
  files.every(
    (file) =>
      clean.get(file).length === sourceOf.get(file).length &&
      bare.get(file).length === sourceOf.get(file).length
  ),
  files
    .filter((file) => clean.get(file).length !== sourceOf.get(file).length)
    .map(relative)
    .join(", ")
);

// A self-test on a string built to contain every hazard the stripper handles. The JSX
// cases are here because the first version of this file got them wrong: it reported
// eleven files as having unclosed elements when the fault was in the tokenizer.
{
  const hazards = [
    'const a = "text with // not a comment";',
    "const b = 'and /* not a block */';",
    "const c = /\\/regex\\//g;",
    "const d = total / count; // real division then a real comment",
    "const e = `template ${with_ + `nested`} body`;",
    "const f = <Box sx={{ p: 1 }} />;",
    "const g = <A size={14} /> ; const h = <B />;",
    "const i = <><Box>text</Box></>;",
  ].join("\n");

  const result = strip(hazards);
  const kept = strip(hazards, { strings: false });

  check("stripper: a // inside a string is not treated as a comment", result.includes("const a ="));
  check("stripper: division is not mistaken for a regex", result.includes("const d = total / count;"));
  check("stripper: the trailing comment after division is removed", !result.includes("real division"));
  check("stripper: comment text is gone", !result.includes("not a comment"));
  check("stripper: template bodies are blanked in strings mode", !result.includes("nested"));
  check("stripper: string bodies survive when strings:false", kept.includes("not a comment"));
  check("stripper: comments still go when strings:false", !kept.includes("real division"));
  check("stripper: line count is preserved", result.split("\n").length === hazards.split("\n").length);

  // The regressions that motivated the guards.
  check("stripper: '/>' after a brace is not a regex opening", result.includes("<A size={14} />"));
  check("stripper: the tag after a self-closing tag survives", result.includes("<B />"));
  check("stripper: '</' is not a regex opening", result.includes("</Box></>"));
  check(
    "stripper: two self-closing tags on one line both survive intact",
    result.includes("<A size={14} /> ; const h = <B />;")
  );
}

// ---------------------------------------------------------------------------
// Bracket balance
// ---------------------------------------------------------------------------

const PAIRS = { ")": "(", "]": "[", "}": "{" };

for (const file of files) {
  const text = clean.get(file);
  const stack = [];
  let broke = null;

  for (let i = 0; i < text.length && !broke; i += 1) {
    const char = text[i];

    if (char === "(" || char === "[" || char === "{") {
      stack.push({ char, index: i });
    } else if (char in PAIRS) {
      const top = stack.pop();
      if (!top || top.char !== PAIRS[char]) {
        broke = `unexpected '${char}' at line ${text.slice(0, i).split("\n").length}`;
      }
    }
  }

  const unclosed = stack.length
    ? `${stack.length} unclosed, first '${stack[0].char}' at line ${
        text.slice(0, stack[0].index).split("\n").length
      }`
    : null;

  check(`${relative(file)}: brackets balance`, !broke && !unclosed, broke ?? unclosed ?? "");
}

// ---------------------------------------------------------------------------
// JSX tag balance
//
// Per tag name rather than a full nesting check: an unclosed element is the realistic
// mistake in hand-written markup, and it shows up as a count mismatch. Mis-nesting
// would need a real parser and is a far less likely typo.
// ---------------------------------------------------------------------------

/** Walk an opening tag from `<` to its terminating `>`, honouring attribute braces. */
const endOfTag = (text, start) => {
  let i = start + 1;
  let depth = 0;

  while (i < text.length) {
    const char = text[i];

    if (char === "{") depth += 1;
    else if (char === "}") depth -= 1;
    else if (char === ">" && depth === 0) return i;

    i += 1;
  }

  return -1;
};

const TAG = /<\/?([A-Za-z][A-Za-z0-9_.]*)/g;

for (const file of files.filter((f) => f.endsWith(".jsx"))) {
  const text = clean.get(file);
  const opens = new Map();
  const closes = new Map();

  TAG.lastIndex = 0;
  let match;

  while ((match = TAG.exec(text)) !== null) {
    const at = match.index;
    const before = at > 0 ? text[at - 1] : "";
    const name = match[1];
    const isClosing = text[at + 1] === "/";

    // The identifier guard applies only to *opening* tags: in JSX a `<` follows
    // whitespace, `(`, `{`, `>` or `,`, and anything that could be the left side of a
    // comparison — an identifier, `)`, `]` — rules it out, which is what keeps
    // `count < limit` from being read as a tag. A closing tag needs no such guard,
    // because `</` is not valid as anything else. Applying the guard to both was the
    // first version's mistake: `<AlertTitle>Some text</AlertTitle>` has a letter
    // immediately before the `</`, so every closing tag that hugged its own text
    // content was thrown away, and eleven healthy files were reported as broken.
    if (!isClosing && /[A-Za-z0-9_$)\]]/.test(before)) continue;

    const end = endOfTag(text, at);

    if (end === -1) continue;

    if (isClosing) {
      closes.set(name, (closes.get(name) ?? 0) + 1);
    } else if (text[end - 1] !== "/") {
      opens.set(name, (opens.get(name) ?? 0) + 1);
    }
  }

  const names = new Set([...opens.keys(), ...closes.keys()]);
  const mismatched = [...names]
    .filter((name) => (opens.get(name) ?? 0) !== (closes.get(name) ?? 0))
    .map((name) => `${name}: ${opens.get(name) ?? 0} open, ${closes.get(name) ?? 0} close`);

  check(`${relative(file)}: every JSX element closes`, mismatched.length === 0, mismatched.join("; "));

  const fragmentOpens = (text.match(/<>/g) ?? []).length;
  const fragmentCloses = (text.match(/<\/>/g) ?? []).length;

  check(
    `${relative(file)}: fragments balance`,
    fragmentOpens === fragmentCloses,
    `${fragmentOpens} <> against ${fragmentCloses} </>`
  );
}

// ---------------------------------------------------------------------------
// Import resolution
// ---------------------------------------------------------------------------

const IMPORT = /import\s+([^;'"]*?)\s*from\s*["']([^"']+)["']/g;
const SIDE_EFFECT_IMPORT = /import\s+["']([^"']+)["']/g;
const LAZY = /lazy\(\s*\(\)\s*=>\s*import\(\s*["']([^"']+)["']\s*\)\s*\)/g;

const resolveLocal = (fromFile, specifier) => {
  const base = resolve(dirname(fromFile), specifier);
  const candidates = [base, `${base}.js`, `${base}.jsx`, join(base, "index.js"), join(base, "index.jsx")];

  return candidates.find((candidate) => {
    try {
      return statSync(candidate).isFile();
    } catch {
      return false;
    }
  }) ?? null;
};

/** Names a module exports, plus `default` when it has a default export. */
const exportsOf = (file) => {
  const text = bare.get(file) ?? strip(readFileSync(file, "utf8"), { strings: false });
  const names = new Set();

  for (const match of text.matchAll(/export\s+(?:async\s+)?(?:const|let|var|function|class)\s+([A-Za-z0-9_$]+)/g)) {
    names.add(match[1]);
  }

  // `export { a, b as c }` and `export { x } from "./y"` both contribute the names
  // visible to an importer, which for `as` is the alias.
  for (const match of text.matchAll(/export\s*\{([^}]*)\}/g)) {
    for (const part of match[1].split(",")) {
      const piece = part.trim();
      if (!piece) continue;
      const alias = piece.split(/\s+as\s+/).pop().trim();
      if (alias) names.add(alias);
    }
  }

  for (const match of text.matchAll(/export\s*\*\s*as\s+([A-Za-z0-9_$]+)\s+from/g)) {
    names.add(match[1]);
  }

  if (/export\s+default/.test(text)) names.add("default");

  return names;
};

const exportCache = new Map();
const cachedExports = (file) => {
  if (!exportCache.has(file)) exportCache.set(file, exportsOf(file));
  return exportCache.get(file);
};

/** `{ default: [...], named: [...], namespace: string|null }` from an import clause. */
const parseClause = (clause) => {
  const result = { defaults: [], named: [], namespace: null };
  let rest = clause.trim();

  const namespaceMatch = rest.match(/^\*\s*as\s+([A-Za-z0-9_$]+)$/);
  if (namespaceMatch) {
    result.namespace = namespaceMatch[1];
    return result;
  }

  const braceStart = rest.indexOf("{");

  if (braceStart !== -1) {
    const braceEnd = rest.indexOf("}", braceStart);
    const inner = rest.slice(braceStart + 1, braceEnd);

    for (const part of inner.split(",")) {
      const piece = part.trim();
      if (!piece) continue;
      result.named.push(piece.split(/\s+as\s+/)[0].trim());
    }

    rest = rest.slice(0, braceStart).replace(/,\s*$/, "").trim();
  }

  if (rest) result.defaults.push(rest);

  return result;
};

let localImportCount = 0;

for (const file of files) {
  // Strings intact: the specifier *is* a string literal.
  const text = bare.get(file);

  {
    IMPORT.lastIndex = 0;
    let match;

    while ((match = IMPORT.exec(text)) !== null) {
      const [, clause, specifier] = match;
      if (!specifier.startsWith(".")) continue;

      localImportCount += 1;
      const target = resolveLocal(file, specifier);
      const line = text.slice(0, match.index).split("\n").length;

      check(
        `${relative(file)}:${line} imports '${specifier}' — file exists`,
        target !== null,
        target === null ? `nothing resolves for ${specifier}` : ""
      );

      if (!target) continue;

      // Vite turns an asset import into a URL string, so the file has no JS exports to
      // inspect. Existence is still worth asserting — a missing asset fails the build.
      if (/\.(svg|png|jpe?g|gif|webp|avif|css|ico|woff2?|json)$/i.test(specifier)) {
        check(`${relative(file)}:${line} asset '${specifier}' exists on disk`, true);
        continue;
      }

      const { defaults, named, namespace } = parseClause(clause);
      const available = cachedExports(target);

      for (const name of defaults) {
        check(
          `${relative(file)}:${line} default import of ${name} from '${specifier}'`,
          available.has("default"),
          `${relative(target)} has no default export`
        );
      }

      for (const name of named) {
        check(
          `${relative(file)}:${line} named import { ${name} } from '${specifier}'`,
          available.has(name),
          `${relative(target)} exports: ${[...available].sort().join(", ") || "nothing"}`
        );
      }

      if (namespace) {
        check(`${relative(file)}:${line} namespace import from '${specifier}'`, available.size > 0);
      }
    }
  }

  SIDE_EFFECT_IMPORT.lastIndex = 0;
  let sideEffect;
  while ((sideEffect = SIDE_EFFECT_IMPORT.exec(text)) !== null) {
    const specifier = sideEffect[1];
    if (!specifier.startsWith(".")) continue;
    check(
      `${relative(file)} side-effect import '${specifier}' resolves`,
      resolveLocal(file, specifier) !== null
    );
  }

  LAZY.lastIndex = 0;
  let lazyMatch;
  while ((lazyMatch = LAZY.exec(text)) !== null) {
    const specifier = lazyMatch[1];
    const target = resolveLocal(file, specifier);

    check(
      `${relative(file)} lazy import '${specifier}' resolves`,
      target !== null,
      "React.lazy defers this to first render, so a bad path only breaks when the page is opened"
    );

    if (target) {
      check(
        `${relative(file)} lazy target ${specifier} has a default export`,
        cachedExports(target).has("default"),
        "lazy() requires a module whose default export is the component"
      );
    }
  }
}

check("there are local imports to verify", localImportCount > 40, `found ${localImportCount}`);

// ---------------------------------------------------------------------------
// Barrel namespace members
//
// `import { catalogApi } from "../../api/index.js"` then `catalogApi.fetchOrders(...)`.
// The barrel re-exports a namespace, so a misspelled member is `undefined` and throws
// only when called — which on a dashboard means one card failing in production.
// ---------------------------------------------------------------------------

const barrel = resolve(SRC, "api/index.js");
const barrelText = bare.get(barrel) ?? "";
const namespaceTargets = new Map();

for (const match of barrelText.matchAll(/export\s*\*\s*as\s+([A-Za-z0-9_$]+)\s+from\s*["']([^"']+)["']/g)) {
  const target = resolveLocal(barrel, match[2]);
  if (target) namespaceTargets.set(match[1], target);
}

check(
  "the api barrel re-exports namespaces",
  namespaceTargets.size >= 5,
  `found ${[...namespaceTargets.keys()].join(", ")}`
);

let memberChecks = 0;

for (const file of files) {
  const text = clean.get(file);

  for (const [namespace, target] of namespaceTargets) {
    if (!new RegExp(`\\b${namespace}\\b`).test(text)) continue;

    const available = cachedExports(target);

    for (const match of text.matchAll(new RegExp(`\\b${namespace}\\.([A-Za-z0-9_$]+)`, "g"))) {
      memberChecks += 1;
      const member = match[1];
      const line = text.slice(0, match.index).split("\n").length;

      check(
        `${relative(file)}:${line} ${namespace}.${member} exists`,
        available.has(member),
        `${relative(target)} exports: ${[...available].sort().join(", ")}`
      );
    }
  }
}

check("barrel members were checked", memberChecks > 20, `only ${memberChecks} member references found`);

// ---------------------------------------------------------------------------
// Routes against navigation
// ---------------------------------------------------------------------------

const navigationSource = readFileSync(resolve(SRC, "utils/navigation.js"), "utf8");
const appSource = readFileSync(resolve(SRC, "App.jsx"), "utf8");

const navPaths = [...navigationSource.matchAll(/path:\s*"([^"]+)"/g)].map((match) => match[1]);

check("nav items parsed from utils/navigation.js", navPaths.length >= 10, `found ${navPaths.length}`);

const routePaths = new Set(
  [...appSource.matchAll(/<Route\s+path="([^"]+)"/g)].map((match) => match[1])
);

for (const path of navPaths) {
  check(
    `nav destination ${path} has a <Route>`,
    routePaths.has(path),
    `App.jsx declares: ${[...routePaths].join(", ")}`
  );
}

const EXPECTED_PAGES = [
  "pages/Landing.jsx",
  "pages/Dashboard.jsx",
  "pages/DataHub.jsx",
  "pages/Orders.jsx",
  "pages/ProductAnalysis.jsx",
  "pages/Preview.jsx",
  "pages/Results.jsx",
  "pages/Segments.jsx",
  "pages/Insights.jsx",
  "pages/Inventory.jsx",
  "pages/About.jsx",
];

for (const page of EXPECTED_PAGES) {
  const full = resolve(SRC, page);
  let exists = false;
  try {
    exists = statSync(full).isFile();
  } catch {
    exists = false;
  }

  check(`${page} exists`, exists);

  if (exists) {
    check(`${page} has a default export`, cachedExports(full).has("default"));
  }
}

// ---------------------------------------------------------------------------
// Hardcoded hosts and secret-shaped strings
// ---------------------------------------------------------------------------

const HOST_PATTERN = /https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0)/;

for (const file of files) {
  const source = sourceOf.get(file);

  // Comments blanked, string bodies intact. A URL in a docstring is documentation; a
  // URL in a string literal is configuration that has escaped into the bundle. Running
  // this against the string-blanked text is a check that can never fail, which is what
  // the first version of this file did — the mutation test caught it.
  const withoutComments = bare.get(file);

  check(
    `${relative(file)} has no hardcoded localhost in code`,
    !HOST_PATTERN.test(withoutComments),
    (withoutComments.match(HOST_PATTERN) ?? []).join("")
  );

  for (const [label, pattern] of [
    ["a Clerk secret key", /\bsk_(test|live)_[A-Za-z0-9]{8,}/],
    ["a webhook secret", /\bwhsec_[A-Za-z0-9]{8,}/],
    ["a database URL with credentials", /postgres(ql)?:\/\/[^\s"']*:[^\s"']*@/],
    ["a bearer token literal", /Bearer\s+eyJ[A-Za-z0-9._-]{10,}/],
  ]) {
    check(`${relative(file)} contains no ${label}`, !pattern.test(source));
  }
}

// The publishable key is the only Clerk key allowed in the bundle, and it must come
// from the environment rather than being written into a source file.
for (const file of files) {
  check(
    `${relative(file)} does not inline a Clerk publishable key`,
    !/\bpk_(test|live)_[A-Za-z0-9]{8,}/.test(sourceOf.get(file))
  );
}

// ---------------------------------------------------------------------------
// Page-level conventions
// ---------------------------------------------------------------------------

const pageFiles = files.filter((file) => file.includes(`pages${"/"}app${"/"}`));

check("all ten app pages were collected", pageFiles.length === 10, `found ${pageFiles.length}`);

for (const file of pageFiles) {
  const text = clean.get(file);

  check(
    `${relative(file)} fetches through the api layer, not axios directly`,
    !/from\s+["']axios["']/.test(text),
    "pages must go through api/client so token handling and the error envelope apply"
  );

  check(
    `${relative(file)} never sends a business id`,
    !/business_id\s*:/.test(text),
    "the tenant is derived server-side from the bearer token; a client-supplied id would be untrustworthy"
  );

  check(
    `${relative(file)} uses useApiResource rather than raw useEffect fetching`,
    /useApiResource/.test(text) || /useJobPolling/.test(text) || !/fetch|axios/.test(text)
  );

  check(`${relative(file)} has a default export`, cachedExports(file).has("default"));

  // Every page that loads data must render one of the states, or a failure shows as
  // an empty page.
  if (/useApiResource/.test(text)) {
    check(
      `${relative(file)} renders loading/error states through AsyncBoundary`,
      /AsyncBoundary/.test(text),
      "a page that fetches without one shows a blank card when the request fails"
    );
  }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

const total = passed + failures.length;

if (failures.length) {
  console.error(`\n  ${failures.length} of ${total} FRONTEND WIRING CHECKS FAILED\n`);
  for (const failure of failures) console.error(`  ✗ ${failure}`);
  console.error("");
  process.exit(1);
}

console.log(`\n  ALL ${total} FRONTEND WIRING CHECKS PASSED\n`);
