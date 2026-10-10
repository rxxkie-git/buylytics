"""Agreement between the requirements files and what the code actually imports.

    python3 tests/check_dependencies.py

This check exists because its absence cost something. Eight distributions were
imported by code in this repository and declared in no requirements file: ``redis``
and ``aiokafka``, which the entire Phase 2 infrastructure layer rests on;
``statsmodels``, which the ARIMA forecast needs; ``pydantic`` and ``starlette``,
imported directly while only being declared transitively through FastAPI; and
``pytest``, ``PyYAML`` and ``requests`` on the test side. Nothing failed locally,
because a working machine already had most of them installed as transitive
dependencies of something else. A clean ``pip install -r requirements.txt``
followed by ``REDIS_ENABLED=true`` would have failed, and the error message the
operator got told them to "install requirements" — which is exactly what they had
just done.

That is the shape of this bug class: it is invisible in the environment where the
code was written and only appears on a fresh install, which is to say in
deployment. A static check is the only cheap defence, so this file asserts the
correspondence in both directions.

Both directions matter, and the reverse one is subtler than it looks. Five
declared distributions are imported by no Python file here, and every one of them
is legitimate — a console entry point, a database driver loaded by URL, a form
parser FastAPI reaches for at runtime, a JWT backend. Rather than skip the reverse
direction, each is listed in :data:`NOT_IMPORTED_BY_DESIGN` with the reason. A
dependency nobody can justify is then a failure, which is how an unused install
gets noticed instead of being inherited forever.

Standard library only, so it runs anywhere the interpreter does.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

RUNTIME = BACKEND / "requirements.txt"
DEV = BACKEND / "requirements-dev.txt"

#: Names that resolve inside this repository, never a missing dependency.
LOCAL = {"app", "tests", "alembic_utils"}

#: Distribution name -> module name, for the cases where they differ. Listed
#: explicitly rather than derived, because there is no rule to derive: the mapping
#: is a choice each project makes and only its own metadata records it.
IMPORT_NAME = {
    "scikit-learn": "sklearn",
    "pyjwt": "jwt",
    "python-multipart": "multipart",
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
    "uvicorn": "uvicorn",
    "psycopg": "psycopg",
}

#: Declared but imported by no file here, with the reason it is still required.
#: Anything not on this list must appear in an import statement somewhere.
NOT_IMPORTED_BY_DESIGN = {
    "uvicorn": "console entry point; `uvicorn app.main:app` is how the server starts",
    "alembic": "console entry point; `alembic upgrade head` runs the migrations",
    "psycopg": "DBAPI driver, loaded by SQLAlchemy from the postgresql+psycopg:// URL",
    "python-multipart": "FastAPI imports it at runtime to parse multipart uploads",
    "cryptography": "PyJWT's backend for the RS256 verification in app/auth.py",
}

#: Imported lazily on purpose, so that absence degrades one feature rather than
#: breaking startup. The value is what stops working. Asserted below to be
#: genuinely lazy — moving one of these to module scope would turn a missing
#: optional package into a crash on import, which is the failure mode the whole
#: KAFKA_ENABLED/REDIS_ENABLED design exists to avoid.
FEATURE_GATED = {
    "redis": "Redis caching; reads fall back to PostgreSQL",
    "aiokafka": "Kafka publish and consume",
    "statsmodels": "the ARIMA demand forecast, which returns None without it",
}

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
        failures.append(label)


def group(title: str) -> None:
    print(f"\n[{title}]")


# ---------------------------------------------------------------------------
# Reading the requirements files
# ---------------------------------------------------------------------------


def parse_requirements(path: Path) -> tuple[dict[str, str], list[str]]:
    """``{normalised name: full line}`` plus the paths pulled in by ``-r``.

    Normalisation follows PEP 503 — lowercase, runs of ``-_.`` collapsed to a
    single ``-`` — so ``PyYAML`` and ``pyyaml`` are one distribution rather than
    two, and a duplicate declared under a different spelling is still a duplicate.
    """
    entries: dict[str, str] = {}
    includes: list[str] = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue

        if line.startswith(("-r ", "--requirement ")):
            includes.append(line.split(None, 1)[1].strip())
            continue

        if line.startswith("-"):
            continue

        name = re.split(r"[<>=!~;\s]", line, maxsplit=1)[0].strip()
        bare = name.split("[")[0]
        entries[re.sub(r"[-_.]+", "-", bare).lower()] = line

    return entries, includes


group("1 both requirements files exist and parse")

check("backend/requirements.txt exists", RUNTIME.is_file(), f"looked in {RUNTIME}")
check("backend/requirements-dev.txt exists", DEV.is_file(), f"looked in {DEV}")

if not RUNTIME.is_file() or not DEV.is_file():
    print("\nCannot continue without both requirements files.")
    sys.exit(1)

runtime_reqs, runtime_includes = parse_requirements(RUNTIME)
dev_reqs, dev_includes = parse_requirements(DEV)

check("requirements.txt declares something", len(runtime_reqs) > 5, f"parsed {len(runtime_reqs)}")
check("requirements-dev.txt declares something", len(dev_reqs) > 0, "parsed none")

check(
    "requirements-dev.txt includes the runtime set",
    "requirements.txt" in dev_includes,
    "without `-r requirements.txt` a contributor installing only the dev file gets a "
    "broken environment, and the two files drift apart",
)

check(
    "requirements.txt includes nothing",
    not runtime_includes,
    f"the runtime set should stand alone, but it pulls in {runtime_includes}",
)

overlap = sorted(set(runtime_reqs) & set(dev_reqs))
check(
    "no distribution is declared in both files",
    not overlap,
    f"{', '.join(overlap)} appear twice; the dev file already inherits the runtime set, "
    "and two version ranges for one package is how a resolver conflict starts",
)


# ---------------------------------------------------------------------------
# 2. Version bounds
# ---------------------------------------------------------------------------

group("2 every declaration is bounded")

#: Documented exceptions to the upper-bound rule, with the reason.
NO_UPPER_BOUND = {
    "starlette": "FastAPI pins it to a narrow window already; a second ceiling here "
    "would cause resolver conflicts on FastAPI upgrades without adding protection",
}

for name, line in sorted({**runtime_reqs, **dev_reqs}.items()):
    check(
        f"{name} has a lower bound",
        bool(re.search(r"(>=|==|~=)\s*\d", line)),
        f"`{line}` accepts any version, so a fresh install can differ from a working one",
    )

    has_upper = bool(re.search(r"<\s*\d", line)) or "==" in line

    if name in NO_UPPER_BOUND:
        check(
            f"{name} is a documented exception to the upper-bound rule",
            not has_upper,
            "it now has an upper bound, so the exception in NO_UPPER_BOUND is stale",
        )
    else:
        check(
            f"{name} has an upper bound",
            has_upper,
            f"`{line}` allows the next major release, which is a build that breaks "
            "without anything in this repository changing",
        )


# ---------------------------------------------------------------------------
# Reading the imports
# ---------------------------------------------------------------------------


def third_party_imports(path: Path) -> dict[str, list[ast.stmt]]:
    """Top-level module name -> the import nodes that ask for it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return {}

    found: dict[str, list[ast.stmt]] = {}

    for node in ast.walk(tree):
        names: list[str] = []

        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]

        for dotted in names:
            root = dotted.split(".")[0]
            if root in LOCAL or root in sys.stdlib_module_names or root == "__future__":
                continue
            found.setdefault(root, []).append(node)

    return found


def python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and ".venv" not in path.parts
    )


def collect(paths: list[Path]) -> dict[str, set[str]]:
    """module name -> the files importing it, relative to the backend directory."""
    out: dict[str, set[str]] = {}
    for path in paths:
        for module in third_party_imports(path):
            out.setdefault(module, set()).add(str(path.relative_to(BACKEND)))
    return out


app_files = python_files(BACKEND / "app")
test_files = python_files(BACKEND / "tests")
# Root-level scripts are by-hand tools, not shipped code, so they count as dev.
script_files = sorted(BACKEND.glob("*.py"))

app_imports = collect(app_files)
dev_imports = collect(test_files + script_files)

declared_runtime = {IMPORT_NAME.get(name, name) for name in runtime_reqs}
declared_dev = {IMPORT_NAME.get(name, name) for name in dev_reqs} | declared_runtime


# ---------------------------------------------------------------------------
# 3. Forward direction: everything imported is declared
# ---------------------------------------------------------------------------

group("3 every third-party import is declared")

check(
    "the app package was found and parsed",
    len(app_files) > 20 and len(app_imports) > 5,
    f"{len(app_files)} files, {len(app_imports)} third-party modules; the scan looks broken",
)

for module, users in sorted(app_imports.items()):
    where = ", ".join(sorted(users)[:3])
    check(
        f"{module} (imported by app/) is in requirements.txt",
        module in declared_runtime,
        f"imported by {where} but declared nowhere in the runtime set, so a clean "
        "install produces an ImportError at the point of use",
    )

for module, users in sorted(dev_imports.items()):
    where = ", ".join(sorted(users)[:3])
    check(
        f"{module} (imported by tests/ or a script) is declared",
        module in declared_dev,
        f"imported by {where} but in neither requirements file",
    )


# ---------------------------------------------------------------------------
# 4. Runtime code must not depend on dev-only packages
# ---------------------------------------------------------------------------

group("4 the runtime/dev split holds")

dev_only = {IMPORT_NAME.get(name, name) for name in dev_reqs} - declared_runtime

for module in sorted(dev_only & set(app_imports)):
    check(
        f"app/ does not import the dev-only package {module}",
        False,
        f"imported by {', '.join(sorted(app_imports[module])[:3])}; either it belongs in "
        "requirements.txt or app/ should not be using it. A production image built from "
        "requirements.txt alone would crash here",
    )

if not dev_only & set(app_imports):
    check("no dev-only package is imported by app/", True)


# ---------------------------------------------------------------------------
# 5. Reverse direction: everything declared is used, or justified
# ---------------------------------------------------------------------------

group("5 every declaration is used or justified")

all_imported = set(app_imports) | set(dev_imports)

for name in sorted({**runtime_reqs, **dev_reqs}):
    module = IMPORT_NAME.get(name, name)

    if module in all_imported:
        check(f"{name} is imported by something", True)
        continue

    check(
        f"{name} is not imported, and the reason is recorded",
        name in NOT_IMPORTED_BY_DESIGN,
        "no file imports it and NOT_IMPORTED_BY_DESIGN does not say why it is needed; "
        "either drop it or record the reason",
    )

for name in sorted(NOT_IMPORTED_BY_DESIGN):
    module = IMPORT_NAME.get(name, name)
    importers = app_imports.get(module, set()) | dev_imports.get(module, set())
    check(
        f"the justification for {name} is still needed",
        module not in all_imported,
        f"it is imported now (by {', '.join(sorted(importers)[:2])}), so the entry in "
        "NOT_IMPORTED_BY_DESIGN is stale and hides a real dependency",
    )
    check(
        f"the justification for {name} is not empty",
        len(NOT_IMPORTED_BY_DESIGN.get(name, "")) > 20,
        "a placeholder reason is worse than none: it silences the check without "
        "telling the next reader anything",
    )


# ---------------------------------------------------------------------------
# 6. Feature-gated packages must stay lazily imported
# ---------------------------------------------------------------------------

group("6 feature-gated imports cannot break startup")


def module_level_imports(path: Path) -> set[str]:
    """Third-party modules imported at module scope *without* an ImportError guard.

    Being inside a function is safe: the import runs when the feature is used. A
    module-scope ``try/except ImportError`` is also safe, since the failure is
    handled. A bare module-scope import is not — it turns a missing optional
    package into a crash the moment anything imports the module.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()

    unguarded: set[str] = set()

    def visit(nodes: list[ast.stmt], guarded: bool) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # deferred to call time

            if isinstance(node, ast.Try):
                catches_import = any(
                    handler.type is not None
                    and "ImportError" in ast.dump(handler.type)
                    or handler.type is None
                    for handler in node.handlers
                )
                visit(node.body, guarded or catches_import)
                for handler in node.handlers:
                    visit(handler.body, guarded)
                visit(node.orelse, guarded)
                visit(node.finalbody, guarded)
                continue

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if guarded:
                    continue
                if isinstance(node, ast.ImportFrom):
                    roots = [node.module.split(".")[0]] if node.module and node.level == 0 else []
                else:
                    roots = [alias.name.split(".")[0] for alias in node.names]
                for root in roots:
                    if root not in LOCAL and root not in sys.stdlib_module_names:
                        unguarded.add(root)
                continue

            for field in ("body", "orelse", "finalbody"):
                child = getattr(node, field, None)
                if isinstance(child, list):
                    visit([item for item in child if isinstance(item, ast.stmt)], guarded)

    visit(tree.body, False)
    return unguarded


eager = {module: set() for module in FEATURE_GATED}

for path in app_files:
    for module in module_level_imports(path) & set(FEATURE_GATED):
        eager[module].add(str(path.relative_to(BACKEND)))

for module, lost in sorted(FEATURE_GATED.items()):
    check(
        f"{module} is declared, since the code imports it",
        module in declared_runtime,
        f"without it, {lost} is unavailable and the log tells the operator to install "
        "requirements — which will not help if requirements does not list it",
    )
    check(
        f"{module} is imported lazily, so its absence does not break startup",
        not eager[module],
        f"imported at module scope by {', '.join(sorted(eager[module]))} with no "
        f"ImportError guard, so an install without {module} now fails at import time "
        f"instead of degrading {lost}",
    )
    check(
        f"the trade-off of dropping {module} is documented in requirements.txt",
        module in RUNTIME.read_text(encoding="utf-8").lower(),
        "an operator trimming dependencies has no way to know what they lose",
    )


# ---------------------------------------------------------------------------
# 7. No credentials in either file
# ---------------------------------------------------------------------------

group("7 the requirements files carry no credentials")

for path in (RUNTIME, DEV):
    lowered = path.read_text(encoding="utf-8").lower()
    for marker in ("://", "password", "token", "sk_live", "sk_test", "@github.com"):
        check(
            f"no {marker!r} in {path.name}",
            marker not in lowered,
            "a VCS or index URL in a requirements file is a common place for a token "
            "to get committed",
        )


# ---------------------------------------------------------------------------

print("\n" + "=" * 74)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED")
    for name in failures:
        print(f"  - {name}")
    print("=" * 74)
    sys.exit(1)
print(f"ALL {checks} DEPENDENCY CHECKS PASSED")
print("=" * 74)
