/**
 * Mutation harness for the frontend checks.
 *
 * Not part of the suite — the filename deliberately does not end in `.check.mjs`, so
 * `tests/run_all.py` will not collect it. Run by hand:
 *
 *     node src/__checks__/mutation_probe.mjs
 *
 * Each entry breaks one thing on a throwaway copy of the frontend and asserts the check
 * notices. A check that has never been observed to fail is not evidence that anything
 * works; it is only evidence that it runs.
 */

import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(here, "../..");

/** @type {{label: string, check: string, file: string, find: string, replace: string}[]} */
const MUTATIONS = [
  {
    label: "the proxy entry the ported page depends on is removed",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: '        "/product-analysis": sharedWithSpa,\n',
    replace: "",
  },
  {
    label: "a proxy entry is added that nothing requests",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: '        "/api": proxy,',
    replace: '        "/api": proxy,\n        "/metrics": proxy,',
  },
  {
    label: "a path the SPA also owns is proxied without a bypass",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: '        "/product-analysis": sharedWithSpa,',
    replace: '        "/product-analysis": proxy,',
  },
  {
    label: "the bypass is dropped from the shared-path proxy",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: "    bypass: (req) => ((req.headers.accept || \"\").includes(\"text/html\") ? \"/index.html\" : undefined),",
    replace: "    secure: false,",
  },
  {
    label: "a bypass is attached to a path the SPA does not own",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: '        "/health": proxy,',
    replace: '        "/health": sharedWithSpa,',
  },
  {
    label: "the dev target is hardcoded instead of read from the environment",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: 'const target = env.VITE_DEV_API_TARGET || "http://127.0.0.1:8000";',
    replace: 'const target = "http://127.0.0.1:8000";',
  },
  {
    label: "the dev target defaults to a remote host",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: 'env.VITE_DEV_API_TARGET || "http://127.0.0.1:8000"',
    replace: 'env.VITE_DEV_API_TARGET || "https://api.staging.example.com"',
  },
  {
    label: "a live module starts requesting an unproxied path",
    check: "src/__checks__/proxy.check.mjs",
    file: "src/api/system.js",
    find: "export const",
    replace: "export const fetchMetrics = (options) => get(\"/metrics\", options);\n\nexport const",
  },
  {
    label: "the v1 dashboard prefix loses its proxy coverage",
    check: "src/__checks__/proxy.check.mjs",
    file: "vite.config.js",
    find: '        "/api": proxy,\n',
    replace: "",
  },

  // ---------------------------------------------------------------------------
  // resolve.check.mjs - the two failure classes a text-only check cannot see
  // ---------------------------------------------------------------------------

  {
    label: "a module is left with a syntax error",
    check: "src/__checks__/resolve.check.mjs",
    file: "src/App.jsx",
    find: "import { lazy } from \"react\";",
    replace: "import { lazy } from \"react\";\nconst broken = (() => {;",
  },
  {
    label: "a static relative import names a module that does not exist",
    check: "src/__checks__/resolve.check.mjs",
    file: "src/App.jsx",
    find: 'import AppShell from "./components/shell/AppShell.jsx";',
    replace: 'import AppShell from "./components/shell/AppShellRenamed.jsx";',
  },
  {
    label: "a lazy route's dynamic import names a module that does not exist",
    check: "src/__checks__/resolve.check.mjs",
    file: "src/App.jsx",
    find: 'const RfmPage = lazy(() => import("./pages/app/RfmPage.jsx"));',
    replace: 'const RfmPage = lazy(() => import("./pages/app/RFMPage.jsx"));',
  },
  {
    label: "a relative import differs from the on-disk name only by case",
    check: "src/__checks__/resolve.check.mjs",
    file: "src/App.jsx",
    find: 'import { ROUTES } from "./app/navigation.js";',
    replace: 'import { ROUTES } from "./app/Navigation.js";',
  },
  {
    label: "a package is imported but never declared in package.json",
    check: "src/__checks__/resolve.check.mjs",
    file: "src/App.jsx",
    find: 'import { lazy } from "react";',
    replace: 'import { lazy } from "react";\nimport dayjs from "dayjs";',
  },
  {
    label: "a declared dependency is dropped from package.json while still imported",
    check: "src/__checks__/resolve.check.mjs",
    file: "package.json",
    find: '"recharts"',
    replace: '"recharts-unused"',
  },
  {
    label: "a Node builtin is imported into browser code",
    check: "src/__checks__/resolve.check.mjs",
    file: "src/App.jsx",
    find: 'import { lazy } from "react";',
    replace: 'import { lazy } from "react";\nimport { readFileSync } from "node:fs";',
  },
  {
    label: "the HTML entry point names a module that does not exist",
    check: "src/__checks__/resolve.check.mjs",
    file: "index.html",
    find: '<script type="module" src="/src/index.jsx"></script>',
    replace: '<script type="module" src="/src/main.jsx"></script>',
  },
];

const buildTree = () => {
  const root = mkdtempSync(join(tmpdir(), "cbpa-frontend-mutation-"));
  const target = join(root, "frontend");

  cpSync(join(FRONTEND, "src"), join(target, "src"), { recursive: true });
  cpSync(join(FRONTEND, "vite.config.js"), join(target, "vite.config.js"));
  cpSync(join(FRONTEND, "index.html"), join(target, "index.html"));

  // `resolve.check.mjs` reads the manifest and resolves against node_modules, so a
  // tree without them fails its baseline for the wrong reason. The manifest is copied
  // because a mutation edits it; node_modules is symlinked because copying 182
  // packages per mutation would dominate the runtime and nothing here writes to it.
  cpSync(join(FRONTEND, "package.json"), join(target, "package.json"));
  symlinkSync(join(FRONTEND, "node_modules"), join(target, "node_modules"), "dir");

  return target;
};

const runCheck = (tree, check) => {
  const result = spawnSync("node", [check], { cwd: tree, encoding: "utf8" });
  return { code: result.status, output: `${result.stdout ?? ""}${result.stderr ?? ""}` };
};

console.log("=".repeat(78));
console.log("Frontend mutation probe - each defect must be caught");
console.log("=".repeat(78));
console.log();

// A baseline run, or every result below means nothing.
{
  const tree = buildTree();
  try {
    for (const check of new Set(MUTATIONS.map((mutation) => mutation.check))) {
      const { code, output } = runCheck(tree, check);
      if (code !== 0) {
        console.log(`BASELINE BROKEN: ${check} fails on an unmutated copy`);
        console.log(output.slice(-2000));
        process.exit(1);
      }
      console.log(`  baseline ok   ${check}`);
    }
  } finally {
    rmSync(tree, { recursive: true, force: true });
  }
}

console.log();

const survived = [];
let caught = 0;

for (const mutation of MUTATIONS) {
  const tree = buildTree();

  try {
    const path = join(tree, mutation.file);
    const source = readFileSync(path, "utf8");

    if (!source.includes(mutation.find)) {
      console.log(`  BAD MUTATION   ${mutation.label}`);
      console.log(`                 anchor not found in ${mutation.file}`);
      survived.push(`${mutation.label} (anchor missing)`);
      continue;
    }

    writeFileSync(path, source.replace(mutation.find, mutation.replace), "utf8");

    const { code, output } = runCheck(tree, mutation.check);

    if (code === 0) {
      survived.push(mutation.label);
      console.log(`  SURVIVED       ${mutation.label}`);
      console.log(`                 ${mutation.check} still reports success`);
    } else {
      caught += 1;
      const first = output
        .split("\n")
        .find((line) => line.includes("FAIL"))
        ?.trim()
        .slice(0, 96);
      console.log(`  caught         ${mutation.label}`);
      console.log(`                 -> ${first ?? "non-zero exit"}`);
    }
  } finally {
    rmSync(tree, { recursive: true, force: true });
  }
}

console.log();
console.log("=".repeat(78));
console.log(`${caught} of ${MUTATIONS.length} mutations caught`);

if (survived.length) {
  console.log();
  console.log("SURVIVED - these checks cannot detect a real defect:");
  for (const label of survived) console.log(`  - ${label}`);
  console.log("=".repeat(78));
  process.exit(1);
}

console.log("Every mutation was caught.");
console.log("=".repeat(78));
