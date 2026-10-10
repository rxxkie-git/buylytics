"""Repository-wide scan for committed credentials.

    python3 tests/test_no_committed_secrets.py

This exists because it would have caught a real incident. ``backend/.env.example``
was committed containing a live Clerk secret key, a live Clerk issuer and a Supabase
connection string with its password inline. Nothing in the project noticed. A file
named ``.example`` is exactly where this happens: it looks like documentation, so it
gets filled in with whatever was on hand and then never looked at again.

The scan distinguishes a real credential from a placeholder by inspecting the matched
text, so ``sk_test_your_clerk_secret_key_here`` and ``USER:PASSWORD@HOST`` pass while
anything with the shape of a live value does not.

Files that are *supposed* to hold secrets — ``.env`` and friends — are not scanned.
Instead there is a separate assertion that they are gitignored, which is the property
that actually matters.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("CBPA_REPO_ROOT") or Path(__file__).resolve().parents[2])

#: Directories with nothing hand-written in them. Scanning `node_modules` finds
#: hundreds of test fixtures from unrelated packages and teaches you to ignore the
#: output, which defeats the purpose.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".vscode",
    "dataset",
    "models",
    "preprocessing_outputs",
}

#: Binary and bulk-data extensions. A 23 MB spreadsheet is not where a credential
#: hides, and reading it as text is slow and noisy.
SKIP_SUFFIXES = {
    ".xlsx",
    ".xls",
    ".pkl",
    ".joblib",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
    ".zip",
    ".gz",
    ".lock",
    ".pyc",
}

#: Files whose entire job is to hold real secrets locally. Excluded from the content
#: scan; covered by :func:`test_secret_files_are_gitignored` instead.
SECRET_FILES = {".env", ".env.local", ".env.development", ".env.production"}

#: Anything in the matched text that marks it as an obvious stand-in. Kept
#: deliberately short — a long allowlist is how a real key eventually slips through.
#: "here" was in an earlier draft and removed: a random base64 secret can contain it
#: by chance, and `your_` already covers the `your_key_here` idiom.
PLACEHOLDER_MARKERS = (
    "your_",
    "your-",
    "changeme",
    "change_me",
    "placeholder",
    "example",
    "replace",
    "xxxx",
    "<",
    "USER:PASSWORD",
    "PASSWORD@",
    "username:password",
    "user:pass",
)

PATTERNS: tuple[tuple[str, str], ...] = (
    ("Clerk secret key", r"sk_(?:test|live)_[A-Za-z0-9]{16,}"),
    ("Stripe-style secret key", r"rk_(?:test|live)_[A-Za-z0-9]{16,}"),
    ("webhook signing secret", r"whsec_[A-Za-z0-9+/=]{16,}"),
    ("database URL with an inline password", r"(?:postgres|postgresql|mysql|mongodb(?:\+srv)?)://[^\s:@/\"']+:[^\s@\"']+@"),
    ("Redis URL with an inline password", r"redis://[^\s:@/\"']+:[^\s@\"']+@"),
    ("AWS access key id", r"AKIA[0-9A-Z]{16}"),
    ("private key block", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("Slack token", r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("Google API key", r"AIza[0-9A-Za-z_-]{30,}"),
    ("hardcoded JWT", r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def is_placeholder(matched: str) -> bool:
    lowered = matched.lower()
    return any(marker.lower() in lowered for marker in PLACEHOLDER_MARKERS)


def scannable_files() -> list[Path]:
    found: list[Path] = []

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name in SECRET_FILES:
            continue
        found.append(path)

    return found


def findings() -> list[str]:
    hits: list[str] = []

    for path in scannable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; nothing hand-written to review

        for label, pattern in PATTERNS:
            for match in re.finditer(pattern, text):
                if is_placeholder(match.group(0)):
                    continue

                line = text[: match.start()].count("\n") + 1
                shown = match.group(0)
                # Report enough to locate it, not enough to leak it further into a
                # CI log that a wider audience can read.
                redacted = f"{shown[:12]}…" if len(shown) > 12 else shown
                hits.append(f"{path.relative_to(REPO_ROOT)}:{line} {label} ({redacted})")

    return hits


def test_no_credentials_are_committed() -> None:
    hits = findings()
    assert not hits, "committed credentials found:\n  " + "\n  ".join(hits)


def test_the_scanner_actually_detects_a_credential() -> None:
    """A scanner that has never fired proves nothing.

    Each pattern is checked against a synthetic value of the right shape. Without
    this, a typo in a regex turns the whole file into an expensive no-op that reports
    success forever.

    Every sample is assembled by concatenation so that no matching literal appears in
    this file's source text. Written the obvious way, these eleven samples were the
    only findings of the repository scan — the scanner reported itself. Splitting them
    keeps this file inside the scan, which is where it belongs: it is as capable of
    accumulating a pasted-in credential as any other.
    """
    samples = {
        "Clerk secret key": "sk_test" + "_A1b2C3d4E5f6G7h8I9j0K1l2",
        "Stripe-style secret key": "rk_live" + "_A1b2C3d4E5f6G7h8I9j0K1l2",
        "webhook signing secret": "whsec" + "_A1b2C3d4E5f6G7h8I9j0K1l2",
        "database URL with an inline password": "postgresql://admin:" + "s3cr3tp4ss@db.internal:5432/app",
        "Redis URL with an inline password": "redis://default:" + "s3cr3tp4ss@cache.internal:6379/0",
        "AWS access key id": "AKIA" + "IOSFODNN7SELCOPY",
        "private key block": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "Slack token": "xoxb" + "-123456789012-abcdefghijkl",
        "GitHub token": "ghp" + "_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
        "Google API key": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
        "hardcoded JWT": "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    }

    assert set(samples) == {label for label, _ in PATTERNS}, "a pattern has no sample to prove it works"

    for label, pattern in PATTERNS:
        sample = samples[label]
        assert re.search(pattern, sample), f"the {label} pattern does not match its own sample"
        assert not is_placeholder(sample), f"the {label} sample is being dismissed as a placeholder"

    # And the assembled samples must not appear literally in this file, or the
    # repository scan starts reporting its own test data forever.
    own_source = Path(__file__).read_text(encoding="utf-8")
    for label, sample in samples.items():
        assert sample not in own_source, f"the {label} sample is written as a contiguous literal"


def test_placeholders_are_not_reported() -> None:
    """The values actually written in `.env.example` must not trip the scan."""
    for placeholder in (
        "sk_test_your_clerk_secret_key_here",
        "postgresql://USER:PASSWORD@HOST:5432/postgres",
        "https://your-instance.clerk.accounts.dev",
        "redis://localhost:6379/0",
    ):
        matched = [
            match.group(0)
            for _, pattern in PATTERNS
            for match in re.finditer(pattern, placeholder)
            if not is_placeholder(match.group(0))
        ]
        assert not matched, f"{placeholder} is reported as a real credential: {matched}"


def test_env_example_exists_and_is_not_a_real_env() -> None:
    example = REPO_ROOT / "backend" / ".env.example"
    assert example.is_file(), "backend/.env.example is missing; nobody will guess the variable names"

    text = example.read_text(encoding="utf-8")

    # The variables an operator must set have to at least be named, or the file is
    # not doing its job.
    for key in ("DATABASE_URL", "CLERK_SECRET_KEY", "WEBHOOK_SECRET", "KAFKA_ENABLED", "REDIS_ENABLED"):
        assert key in text, f"{key} is not documented in .env.example"

    # And it must tell whoever reads it that the previously committed values need
    # rotating — deleting the lines does not un-publish them from git history.
    assert "rotate" in text.lower(), ".env.example should say the leaked credentials must be rotated"


def test_secret_files_are_gitignored() -> None:
    ignore_files = [REPO_ROOT / ".gitignore", REPO_ROOT / "backend" / ".gitignore"]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in ignore_files if path.is_file())

    assert combined.strip(), "no .gitignore found at all"

    for required in (".env", ".venv"):
        assert re.search(rf"^\**/?{re.escape(required)}", combined, re.MULTILINE), (
            f"{required} is not gitignored, so a real credential file can be committed"
        )

    # `.env.example` must NOT be ignored — it is the documentation.
    assert not re.search(r"^\.env\.example", combined, re.MULTILINE)


def main() -> int:
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0

    print(f"scanning {len(scannable_files())} files under {REPO_ROOT}")

    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failed += 1
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok   {test.__name__}")

    print()
    if failed:
        print(f"{failed} of {len(tests)} SECRET-SCAN CHECKS FAILED")
        return 1

    print(f"ALL {len(tests)} SECRET-SCAN CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
