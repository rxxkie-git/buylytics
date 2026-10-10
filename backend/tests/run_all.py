"""Run every check that works without infrastructure.

    python3 tests/run_all.py
    python3 tests/run_all.py --python-only

Why this exists in a project that ships pytest in requirements-dev.txt: the checks
here must be runnable in an environment where nothing can be installed. Each file
is a plain script with a ``main()`` and an exit code, so this runner is a
subprocess loop rather than a test framework. Where pytest *is* available it
collects the ``test_*.py`` files as normal — they are written to work both ways.

Two naming conventions, and the distinction is load-bearing:

``test_*.py``
    Ordinary pytest-collectable modules. Assertions live inside ``test_`` functions
    and nothing runs at import.
``check_*.py``
    Script-style checkers that assert at import time and call ``sys.exit(1)`` on
    failure. Deliberately *not* named ``test_*`` — pytest would import them during
    collection, and a ``SystemExit`` there aborts the run for every other file too.

The frontend checks are Node scripts. No bundler or package registry is reachable
in the sandbox this was written in, so they verify the module graph, route table and
formatting helpers by parsing the real source rather than by building it. They are
skipped, not failed, when ``node`` is absent.

What this runner cannot do is stated plainly rather than papered over:
``alembic upgrade head``, ``uvicorn app.main:app``, ``vite build``, ``docker compose
up -d`` and the SQLAlchemy/FastAPI unit tests all need dependencies that are not
installable here. Those are the user's to run.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TESTS = BACKEND / "tests"
FRONTEND = BACKEND.parent / "frontend"
PYTEST_LITE = TESTS / "pytest_lite.py"

#: First-party and local names, never treated as a missing dependency.
LOCAL_MODULES = {"app", "tests", "alembic_utils"}


def unavailable_imports(path: Path) -> list[str]:
    """Third-party modules a file imports that this interpreter cannot load.

    Determined by parsing the file rather than from a hand-maintained table. The
    first version of this runner used a table and it was wrong on its very first
    run — it claimed the Phase 1 ingestion test needed pandas when it actually
    needs pytest — so the table reported a failure where a skip was correct.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []

    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    missing = []

    for root in sorted(roots):
        if root in LOCAL_MODULES or root in sys.stdlib_module_names or root == "__future__":
            continue

        try:
            found = importlib.util.find_spec(root) is not None
        except (ImportError, ValueError):
            found = False

        if not found:
            missing.append(root)

    return missing


def discover() -> tuple[list[Path], list[Path]]:
    python_checks = sorted(TESTS.glob("check_*.py")) + sorted(
        path for path in TESTS.glob("test_*.py") if path.name != "run_all.py"
    )
    node_checks = sorted(FRONTEND.glob("src/**/__checks__/*.check.mjs")) if FRONTEND.is_dir() else []
    return python_checks, node_checks


def run(command: list[str], cwd: Path) -> tuple[int, str, str]:
    started = time.monotonic()

    # `python3 tests/foo.py` puts *tests/* on sys.path, not the working directory,
    # so `import app` fails for the Phase 1 tests that pytest resolves via rootdir.
    # Supplying it here beats editing four working test files.
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = f"{BACKEND}{os.pathsep}{existing}" if existing else str(BACKEND)

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=300,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out after 300s", ""

    elapsed = time.monotonic() - started
    output = (completed.stdout or "") + (completed.stderr or "")

    # The last summary line is what a reader wants; the per-check detail is only
    # useful on failure, where the full output is printed instead.
    summary = ""
    for line in reversed(output.strip().splitlines()):
        if "PASSED" in line or "FAILED" in line or "SKIPPED" in line:
            summary = line.strip()
            break

    return completed.returncode, f"{summary or 'no summary line'}  ({elapsed:.1f}s)", output


#: Markers a checker prints when it ran and judged something wrong. Their presence
#: means the process reached its own reporting code, so the exit status is a real
#: verdict and not an import failure.
_VERDICT_MARKERS = ("CHECKS FAILED", "FAILED", "  FAIL ", "  ERROR ")


def missing_module_from_traceback(output: str) -> str | None:
    """The third-party module whose absence killed the process, or ``None``.

    A static scan of a file's own imports cannot see that ``app.db.session`` needs
    SQLAlchemy. Rather than importing application modules in this process to find
    out — which would run their side effects — the failure is read back from the
    traceback, so a skip is only ever attributed to a module that a real import
    actually asked for.

    Two conditions guard the reclassification, both added after a probe caught this
    function laundering a genuine failure into a skip. A checker asserting on error
    handling can legitimately *print* the words ``ModuleNotFoundError: No module
    named 'kafka'`` while failing for an unrelated reason; the earlier version
    matched that text anywhere in the output and reported ``skip needs kafka``,
    which would have let a real regression exit 0.

    So the error must be the *terminal* line of the output — where Python leaves an
    unhandled exception — and the output must carry none of the markers a checker
    prints when it ran far enough to reach its own verdict.
    """
    if any(marker in output for marker in _VERDICT_MARKERS):
        return None

    lines = [line for line in output.strip().splitlines() if line.strip()]

    if not lines:
        return None

    match = re.match(r"ModuleNotFoundError: No module named '([\w.]+)'", lines[-1].strip())

    if not match:
        return None

    root = match.group(1).split(".")[0]
    return None if root in LOCAL_MODULES else root


def missing_requirement(path: Path) -> str | None:
    """Why a check cannot run here, or ``None`` if it can.

    A skip is reported, never silently dropped: "nothing ran" must not read the
    same as "everything passed".
    """
    missing = unavailable_imports(path)
    return ", ".join(missing) if missing else None


def main() -> int:
    python_only = "--python-only" in sys.argv
    python_checks, node_checks = discover()

    failures: list[str] = []
    skipped: list[str] = []
    passed = 0

    print("=" * 78)
    print("CBPA Phase 2 - checks runnable without Kafka, Redis, or a database")
    print("=" * 78)
    print()
    print(f"backend  {BACKEND}")
    print(f"frontend {FRONTEND if FRONTEND.is_dir() else '(not found)'}")
    print()

    print("-" * 78)
    print(f"Python  ({len(python_checks)} files)")
    print("-" * 78)

    for path in python_checks:
        reason = missing_requirement(path)

        # A file whose *only* missing dependency is pytest is still runnable: the
        # local shim calls its `test_` functions directly. Worth the detour because
        # "skip test_column_mapping.py — needs pytest" hid 74 real assertions about
        # column resolution, the repositories and the ORM models on any machine
        # without requirements-dev.txt installed.
        if reason == "pytest" and PYTEST_LITE.is_file():
            code, summary, output = run(
                [sys.executable, str(PYTEST_LITE.relative_to(BACKEND)), str(path.relative_to(BACKEND))],
                BACKEND,
            )

            if code == 0:
                passed += 1
                print(f"  ok   {path.name:<34} {summary}  (no pytest; via pytest_lite)")
                continue

            blocked_by = missing_module_from_traceback(output)

            if blocked_by:
                skipped.append(f"{path.name} (needs {blocked_by})")
                print(f"  skip {path.name:<34} needs {blocked_by}")
            else:
                failures.append(path.name)
                print(f"  FAIL {path.name:<34} exit {code}  {summary}")

            continue

        if reason:
            skipped.append(f"{path.name} (needs {reason})")
            print(f"  skip {path.name:<34} needs {reason}")
            continue

        code, summary, output = run([sys.executable, str(path.relative_to(BACKEND))], BACKEND)

        if code == 0:
            passed += 1
            print(f"  ok   {path.name:<34} {summary}")
            continue

        # A missing dependency is a skip, not a failure. Anything else is a failure.
        blocked_by = missing_module_from_traceback(output)

        if blocked_by:
            skipped.append(f"{path.name} (needs {blocked_by})")
            print(f"  skip {path.name:<34} needs {blocked_by}")
        else:
            failures.append(path.name)
            print(f"  FAIL {path.name:<34} exit {code}  {summary}")

    if not python_only:
        print()
        print("-" * 78)
        print(f"Frontend  ({len(node_checks)} files)")
        print("-" * 78)

        if not node_checks:
            print("  none found")
        elif shutil.which("node") is None:
            for path in node_checks:
                skipped.append(f"{path.name} (needs node)")
                print(f"  skip {path.name:<34} node is not installed")
        else:
            for path in node_checks:
                code, summary, _output = run(["node", str(path)], FRONTEND)

                if code == 0:
                    passed += 1
                    print(f"  ok   {path.name:<34} {summary}")
                else:
                    failures.append(path.name)
                    print(f"  FAIL {path.name:<34} exit {code}  {summary}")

    print()
    print("=" * 78)
    print(f"{passed} passed, {len(failures)} failed, {len(skipped)} skipped")

    if skipped:
        print()
        print("Skipped - these need packages that are not installed:")
        for entry in skipped:
            print(f"  - {entry}")

    if failures:
        print()
        print("Failed:")
        for name in failures:
            print(f"  - {name}")
        print()
        print("Re-run one for its full output:  python3 tests/<name>")
        print("=" * 78)
        return 1

    print()
    print("Still requiring a real environment, and not covered above:")
    print("  cd backend && alembic upgrade head")
    print("  cd backend && uvicorn app.main:app --reload")
    print("  cd backend && pip install -r requirements-dev.txt && pytest")
    print("  cd frontend && npm install && npm run build")
    # kafka-init used to sit behind a profile and needed the second command. It now
    # runs as part of `up`, and the backend waits for it to exit 0.
    print("  docker compose up --build          # frontend, backend, redis, kafka")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
