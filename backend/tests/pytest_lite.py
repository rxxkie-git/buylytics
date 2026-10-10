"""Run a pytest-style module without pytest installed.

    cd backend && python3 tests/pytest_lite.py tests/test_column_mapping.py

``run_all.py`` calls this automatically for any test file whose *only* missing
dependency is pytest itself, so a machine with SQLAlchemy but no dev requirements
still executes those files instead of skipping them. Reporting them as skipped was
honest but meant the cases inside them shipped unverified.

A pytest module is mostly plain functions and asserts. The small part that is not
— ``mark.parametrize``, ``raises``, ``fixture``, ``importorskip`` — is shimmed
here, and every ``test_`` function is called directly.

This is a substitute for pytest, not a replacement: it implements only what the
files in this directory use and raises ``NotImplementedError`` on anything else,
so an unsupported feature is a loud failure rather than a silent skip. When pytest
*is* available, prefer it:

    cd backend && pip install -r requirements-dev.txt && pytest
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import sys
import traceback
import types
from pathlib import Path


class _Fixture:
    """``@pytest.fixture`` — a factory, re-run per test, torn down after it.

    Per-test is pytest's own default scope and it matters here rather than being a
    detail: every fixture in these files builds a fresh in-memory SQLite database,
    so sharing one across tests would let rows written by an earlier test satisfy a
    later one's assertions. A generator fixture yields once; whatever follows the
    ``yield`` is the teardown and runs even when the test fails.
    """

    def __init__(self, function) -> None:
        self.function = function
        self.name = function.__name__

    def open(self, resolve):
        """Enter the fixture, returning ``(value, close)``."""
        kwargs = {name: resolve(name) for name in _parameters(self.function)}

        if inspect.isgeneratorfunction(self.function):
            generator = self.function(**kwargs)
            value = next(generator)

            def close() -> None:
                # Exhausting the generator is what runs the teardown. A fixture that
                # yields twice is a bug in the fixture, and is reported as one.
                with contextlib.suppress(StopIteration):
                    next(generator)
                    raise AssertionError(f"fixture {self.name} yielded more than once")

            return value, close

        return self.function(**kwargs), lambda: None


def _parameters(function) -> list[str]:
    return [
        name
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]


class _Marker:
    """``@pytest.mark.parametrize(...)`` — records cases on the function."""

    def parametrize(self, argnames: str, argvalues, **kwargs):
        names = [name.strip() for name in argnames.split(",")]

        def decorate(function):
            cases = getattr(function, "_cases", [])

            for values in argvalues:
                row = values if isinstance(values, (tuple, list)) else (values,)
                if len(row) != len(names):
                    raise AssertionError(f"{function.__name__}: {row!r} does not fit {names}")
                cases.append(dict(zip(names, row)))

            function._cases = cases
            return function

        return decorate

    def __getattr__(self, name):
        raise NotImplementedError(f"pytest.mark.{name} is not shimmed")


class _Raises:
    """``with pytest.raises(X) as caught:`` — exposes ``caught.value``."""

    def __init__(self, expected, match=None):
        self.expected = expected
        self.match = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type is None:
            raise AssertionError(f"expected {self.expected.__name__}, nothing was raised")
        if not issubclass(exc_type, self.expected):
            return False

        self.value = exc

        if self.match is not None:
            import re

            if not re.search(self.match, str(exc)):
                raise AssertionError(f"{str(exc)!r} does not match {self.match!r}")

        return True


class _Skipped(Exception):
    """Raised by the ``skip``/``importorskip`` shims. Reported, never hidden."""


def install_shim() -> None:
    module = types.ModuleType("pytest")
    module.mark = _Marker()
    module.raises = lambda expected, match=None: _Raises(expected, match)
    module.fail = lambda reason="": (_ for _ in ()).throw(AssertionError(reason))

    def fixture(function=None, **_kwargs):
        """``@pytest.fixture`` and ``@pytest.fixture(scope=...)`` both land here."""
        if function is None:
            return fixture
        return _Fixture(function)

    def skip(reason: str = "", **_kwargs):
        raise _Skipped(reason or "no reason given")

    def importorskip(name: str, *_args, **_kwargs):
        """Import ``name`` or skip. Skipping at module scope skips the whole file."""
        try:
            return importlib.import_module(name)
        except ImportError as exc:
            raise _Skipped(f"needs {name} ({exc})") from exc

    module.fixture = fixture
    module.skip = skip
    module.importorskip = importorskip
    module.main = lambda _args=None: 0
    sys.modules["pytest"] = module


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so a module importing itself by name works.
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def run(path: Path) -> tuple[int, int, list[str], str | None]:
    try:
        module = load(path)
    except _Skipped as exc:
        # A module-scope importorskip. The whole file is unrunnable here, which is
        # reported as a skip rather than counted as a pass.
        return 0, 0, [], str(exc)

    fixtures = {
        name: value for name, value in vars(module).items() if isinstance(value, _Fixture)
    }
    names = [
        name
        for name, value in vars(module).items()
        if name.startswith("test_") and not isinstance(value, _Fixture) and callable(value)
    ]

    passed = 0
    skipped = 0
    failed: list[str] = []

    for name in sorted(names):
        function = getattr(module, name)
        cases = getattr(function, "_cases", None) or [{}]

        for kwargs in cases:
            label = name + (f"[{', '.join(f'{k}={v!r}' for k, v in kwargs.items())}]" if kwargs else "")

            # Fixtures are opened per test and closed in reverse order, so a session
            # fixture is closed before the engine fixture it was built from.
            opened: list = []
            cache: dict = {}

            def resolve(fixture_name: str):
                if fixture_name in cache:
                    return cache[fixture_name]
                if fixture_name not in fixtures:
                    raise AssertionError(f"no fixture named {fixture_name!r} in {path.name}")
                value, close = fixtures[fixture_name].open(resolve)
                cache[fixture_name] = value
                opened.append(close)
                return value

            try:
                arguments = dict(kwargs)
                for parameter in _parameters(function):
                    if parameter not in arguments:
                        arguments[parameter] = resolve(parameter)

                function(**arguments)
            except _Skipped as exc:
                skipped += 1
                print(f"  skip {label}  {exc}")
            except Exception as exc:  # noqa: BLE001 - reporting, not handling
                failed.append(label)
                print(f"  FAIL {label}")
                print("       " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
            else:
                passed += 1
            finally:
                for close in reversed(opened):
                    # A teardown that raises must not be able to turn a real failure
                    # into a different one, nor a pass into a fail silently.
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001
                        failed.append(f"{label} (teardown)")
                        print(f"  FAIL {label} (teardown): {exc!r}")

    return passed, skipped, failed, None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    install_shim()

    total_passed = 0
    total_skipped = 0
    total_failed: list[str] = []
    files_skipped: list[str] = []

    for name in argv:
        path = Path(name).resolve()
        print(f"\n{path.name}")
        passed, skipped, failed, file_skip = run(path)

        if file_skip:
            files_skipped.append(f"{path.name}: {file_skip}")
            print(f"  skip whole file — {file_skip}")
            continue

        total_passed += passed
        total_skipped += skipped
        total_failed += [f"{path.name}::{entry}" for entry in failed]
        print(f"  {passed} passed, {len(failed)} failed, {skipped} skipped")

    print("\n" + "=" * 74)

    if total_failed:
        print(f"{len(total_failed)} of {total_passed + len(total_failed)} CASES FAILED")
        for entry in total_failed:
            print(f"  - {entry}")
        print("=" * 74)
        return 1

    print(f"ALL {total_passed} CASES PASSED" + (f", {total_skipped} skipped" if total_skipped else ""))

    # "Nothing ran" must never read the same as "everything passed".
    for entry in files_skipped:
        print(f"  skipped file — {entry}")

    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
