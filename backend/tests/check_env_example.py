"""Agreement between app/config.py and .env.example.

    python3 tests/check_env_example.py

This check exists because its absence cost something specific. ``.env.example``
set ``CORS_ORIGINS`` to ``http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173``
while ``config.py`` defaulted to the 3000 and 3001 pair. Port 5173 is Vite's
default, but this project's dev server listens on 3000, so the example file
allowed an origin nothing serves and omitted two that the code deliberately
allows.

The failure mode is the interesting part. A developer who runs
``cp .env.example .env`` gets different CORS behaviour from one who runs no
``.env`` at all, and only the second matches the documentation. Neither sees an
error; the first just finds that a browser request from :3001 is refused, with
nothing in the logs to connect that to a file they copied once on their first
day. Nothing in the test suite could notice, because both values are valid
configuration — they simply disagree.

So this file asserts the correspondence three ways: every variable the code
reads is documented, every variable documented is still read, and where both
sides state a default they state the same one.

Standard library only, so it runs anywhere the interpreter does.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

CONFIG = BACKEND / "app" / "config.py"
EXAMPLE = BACKEND / ".env.example"

#: The helpers in config.py that read an environment variable. Each takes the
#: variable name as its first argument and the default as its second, which is
#: what makes a single AST pass enough to extract both.
READERS = {"getenv", "_env_bool", "_env_int", "_env_float"}

#: Variables whose value in .env.example is a placeholder for the developer to
#: replace, where config.py has no default because there is no safe one. The
#: two cannot agree by construction, and requiring them to would mean either
#: inventing a fake default in the code or shipping an example file that looks
#: pre-configured. Anything not listed here must match.
#:
#: Group 5 asserts every entry here is still read by config.py, which is not
#: ceremony: this list was first written with entries for CLERK_JWKS_URL,
#: CLERK_AUDIENCE and CLERK_PUBLISHABLE_KEY, none of which this backend reads —
#: the first two do not exist here and the third is the frontend's
#: VITE_CLERK_PUBLISHABLE_KEY. An exemption for a variable nobody reads is a
#: silenced check waiting for someone to add that variable back.
PLACEHOLDER_BY_DESIGN = {
    "DATABASE_URL": "no default is safe; an unset database must fail loudly, not point somewhere",
    "CLERK_ISSUER": "instance-specific; the example shows the shape of the URL",
    "CLERK_SECRET_KEY": "a secret; the example shows the prefix so the right key is recognisable",
    "WEBHOOK_SECRET": "a secret with no safe default; blank means signature checks cannot pass",
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

group("1 both files exist and parse")

check("app/config.py exists", CONFIG.is_file(), f"looked in {CONFIG}")
check("backend/.env.example exists", EXAMPLE.is_file(), f"looked in {EXAMPLE}")

if not CONFIG.is_file() or not EXAMPLE.is_file():
    print("\nCannot continue without both files.")
    sys.exit(1)


def config_variables() -> dict[str, str | None]:
    """``{env var name: literal default}``, ``None`` where the default is computed.

    Parsed with ``ast`` rather than a regex because the names and the defaults
    are both string literals in the same call, and a regex that matches the
    first also matches the second: an earlier attempt at this reported ``INFO``
    and ``PLAINTEXT`` as environment variables, which are the *defaults* for
    ``LOG_LEVEL`` and ``KAFKA_SECURITY_PROTOCOL``.
    """
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"), filename=str(CONFIG))
    found: dict[str, str | None] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue

        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")

        if name not in READERS:
            continue

        variable = node.args[0]
        if not isinstance(variable, ast.Constant) or not isinstance(variable.value, str):
            continue

        default: str | None = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            literal = node.args[1].value
            if isinstance(literal, bool):
                default = "true" if literal else "false"
            elif isinstance(literal, (str, int, float)):
                default = str(literal)

        found[variable.value] = default

    return found


def example_variables() -> tuple[dict[str, str], set[str]]:
    """``{name: value}`` for uncommented lines, plus names present only as comments.

    A commented variable still counts as documented: it is how an optional
    setting is shown without switching it on.
    """
    assigned: dict[str, str] = {}
    commented: set[str] = set()

    for raw in EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if match := re.match(r"^([A-Z][A-Z_0-9]*)=(.*)$", line):
            assigned[match.group(1)] = match.group(2).strip()
        elif match := re.match(r"^#\s*([A-Z][A-Z_0-9]*)=(.*)$", line):
            commented.add(match.group(1))

    return assigned, commented


config_vars = config_variables()
assigned, commented = example_variables()
documented = set(assigned) | commented

check(
    "config.py was parsed and reads environment variables",
    len(config_vars) > 20,
    f"found only {len(config_vars)}; the AST scan looks broken rather than the config",
)
check(
    ".env.example documents variables",
    len(documented) > 20,
    f"found only {len(documented)}",
)


# ---------------------------------------------------------------------------

group("2 every variable the code reads is documented")

for name in sorted(config_vars):
    check(
        f"{name} appears in .env.example",
        name in documented,
        "config.py reads it but the example file does not mention it, so an operator "
        "copying .env.example has no way to discover the setting exists",
    )


# ---------------------------------------------------------------------------

group("3 every documented variable is still read")

for name in sorted(documented):
    check(
        f"{name} is read by config.py",
        name in config_vars,
        "it is in .env.example but nothing reads it; either it was renamed in the code "
        "and the example file kept the old spelling, or the setting is gone. Both leave "
        "an operator setting a value that does nothing",
    )


# ---------------------------------------------------------------------------

group("4 stated defaults agree")

compared = 0

for name in sorted(set(config_vars) & set(assigned)):
    code_default = config_vars[name]
    file_value = assigned[name]

    if name in PLACEHOLDER_BY_DESIGN:
        check(
            f"{name} is a documented placeholder, and the code has no default for it",
            not code_default,
            f"config.py now defaults it to {code_default!r}, so the exemption in "
            "PLACEHOLDER_BY_DESIGN is stale and the two values should be compared",
        )
        continue

    if code_default is None or code_default == "":
        # Computed at import (a path, a derived URL) or deliberately blank.
        continue

    compared += 1
    check(
        f"{name} matches the default in config.py",
        file_value == code_default,
        f"config.py defaults to {code_default!r} but .env.example sets {file_value!r}. "
        "Whichever is wrong, `cp .env.example .env` changes behaviour versus no .env "
        "at all, and only one of the two matches the README",
    )

check(
    "the default comparison actually compared something",
    compared > 10,
    f"only {compared} variables were compared; if the extraction breaks, every "
    "mismatch above becomes an ok by omission",
)


# ---------------------------------------------------------------------------

group("5 the placeholder list is honest")

for name in sorted(PLACEHOLDER_BY_DESIGN):
    check(
        f"the placeholder exemption for {name} is still needed",
        name in config_vars,
        "nothing reads it any more, so the exemption is dead weight",
    )
    check(
        f"the reason recorded for {name} is not a placeholder itself",
        len(PLACEHOLDER_BY_DESIGN.get(name, "")) > 20,
        "a one-word reason silences the check without telling the next reader anything",
    )


# ---------------------------------------------------------------------------

print("\n" + "=" * 74)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED")
    for name in failures:
        print(f"  - {name}")
    print("=" * 74)
    sys.exit(1)
print(f"ALL {checks} ENV EXAMPLE CHECKS PASSED")
print("=" * 74)
