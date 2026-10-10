"""Mutation harness: does each new checker actually fail when the code is wrong?

Not part of the suite. Run by hand:

    python3 tests/mutation_probe.py

Every mutation below is a single realistic defect applied to a copy of the source
tree. If a mutation survives — the code is broken and the checker still reports
success — then that check is decoration, and the harness says so.

The copy is made under /tmp so nothing here can touch the real tree.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

Mutation = tuple[str, str, str, str, str]

#: (label, checker, target file relative to backend, find, replace)
MUTATIONS: list[Mutation] = [
    # ---------------- scoring ----------------
    (
        "recency is no longer inverted, so stale customers score as recent",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        "row.r_score = SCORE_BUCKETS + 1 - bucket_of(float(row.recency), recency_cuts)",
        "row.r_score = bucket_of(float(row.recency), recency_cuts)",
    ),
    (
        "customers with no recency are scored anyway (invented data)",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        "return self.recency is not None and self.frequency > 0 and self.monetary is not None",
        "return True",
    ),
    (
        "segment rules reordered so 'New Customers' becomes unreachable",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        '    if r_score >= 4 and f_score <= 1:\n        return "New Customers"\n\n    if r_score >= 4:\n        return "Potential Loyalist"',
        '    if r_score >= 4:\n        return "Potential Loyalist"\n\n    if r_score >= 4 and f_score <= 1:\n        return "New Customers"',
    ),
    (
        "bucket comparison flipped, so higher values score lower",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        "        if value > threshold:",
        "        if value < threshold:",
    ),
    (
        "value-score weights no longer total 1.0",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        "monetary[index] * 0.50 + frequency[index] * 0.35",
        "monetary[index] * 0.40 + frequency[index] * 0.35",
    ),
    (
        "a flat series normalises to 0.0 instead of the midpoint",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        "        return [0.5] * len(numeric)",
        "        return [0.0] * len(numeric)",
    ),
    (
        "one cut point too many, so the top bucket is unreachable",
        "tests/test_scoring.py",
        "app/analytics/scoring.py",
        "    for index in range(1, buckets):",
        "    for index in range(1, buckets + 1):",
    ),
    # ---------------- cache keys ----------------
    (
        "the tenant guard is dropped from the in-memory backend",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        "    async def get(self, key: str) -> Any | None:\n        assert_tenant_scoped(key)\n\n        async with self._lock:",
        "    async def get(self, key: str) -> Any | None:\n        async with self._lock:",
    ),
    (
        "an empty business_id is accepted, producing an unscoped key",
        "tests/test_cache_isolation.py",
        "app/cache/keys.py",
        '    if not candidate:\n        raise CacheKeyError("business_id is required to build a cache key")\n\n    if SEPARATOR in candidate:',
        '    if not candidate:\n        return "unknown"\n\n    if SEPARATOR in candidate:',
    ),
    (
        "a colon in business_id is allowed, so a tenant can forge another's namespace",
        "tests/test_cache_isolation.py",
        "app/cache/keys.py",
        '        raise CacheKeyError("business_id must not contain a \':\' separator")',
        "        pass",
    ),
    (
        "key shape puts the namespace before the tenant",
        "tests/test_cache_isolation.py",
        "app/cache/keys.py",
        '    suffix = SEPARATOR.join(_clean_part(part) for part in parts)\n    return f"{tenant_prefix(business_id)}{suffix}"',
        '    suffix = SEPARATOR.join(_clean_part(part) for part in parts)\n    return f"{_KEY_PREFIX}{suffix}{SEPARATOR}{_clean_business_id(business_id)}"',
    ),
    (
        "prefix invalidation ignores the prefix and clears every tenant",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        "            matched = [key for key in self._store if key.startswith(prefix)]",
        "            matched = list(self._store)",
    ),
    (
        "an order write no longer invalidates analytics, leaving stale RFM",
        "tests/test_cache_isolation.py",
        "app/cache/keys.py",
        "        namespace_prefix(business_id, ORDERS_NS),\n        namespace_prefix(business_id, CUSTOMERS_NS),\n        namespace_prefix(business_id, ANALYTICS_NS),\n        namespace_prefix(business_id, DASHBOARD_NS),",
        "        namespace_prefix(business_id, ORDERS_NS),\n        namespace_prefix(business_id, CUSTOMERS_NS),\n        namespace_prefix(business_id, DASHBOARD_NS),",
    ),
    # ---------------- cache behaviour ----------------
    (
        "delete_prefix uses KEYS, blocking the server across the keyspace",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        "            async for key in client.scan_iter(match=f\"{prefix}*\", count=500):\n                batch.append(key)",
        "            for key in await client.keys(f\"{prefix}*\"):\n                batch.append(key)",
    ),
    (
        "the Redis URL is logged on connect, leaking its password",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        '            logger.info("Redis cache connected")',
        '            logger.info("Redis cache connected", extra={"url": self._url})',
    ),
    (
        "None is cached, leaving a 'null' entry in a bounded store",
        "tests/test_cache_isolation.py",
        "app/cache/service.py",
        "    if cache.enabled and value is not None:",
        "    if cache.enabled:",
    ),
    (
        "the write guard uses truthiness, so an empty result is never cached",
        "tests/test_cache_isolation.py",
        "app/cache/service.py",
        "    if cache.enabled and value is not None:",
        "    if cache.enabled and value:",
    ),
    (
        "the read guard uses truthiness, so a cached empty result never hits",
        "tests/test_cache_isolation.py",
        "app/cache/service.py",
        "        if cached_value is not None:\n            return cached_value",
        "        if cached_value:\n            return cached_value",
    ),
    (
        "a cache error propagates and fails the request",
        "tests/test_cache_isolation.py",
        "app/cache/service.py",
        "        try:\n            cached_value = await cache.get(key)\n        except Exception as exc:  # pragma: no cover - backends already guard\n            cached_value = None\n            logger.warning(\"Cache lookup failed; using database\", extra={\"reason\": str(exc)})",
        "        cached_value = await cache.get(key)",
    ),
    (
        "a non-positive TTL means 'cache forever' again (the original bug)",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        "        if ttl <= 0:\n            return False\n\n        expires_at = time.monotonic() + ttl\n\n        async with self._lock:",
        "        expires_at = time.monotonic() + ttl if ttl and ttl > 0 else None\n\n        async with self._lock:",
    ),
    (
        "a cache hit is ignored, so the loader runs on every read",
        "tests/test_cache_isolation.py",
        "app/cache/service.py",
        "        if cached_value is not None:\n            return cached_value",
        "        if False:\n            return cached_value",
    ),
    # --- dependency declaration -------------------------------------------
    # Every entry below reproduces a defect that was actually present before
    # check_dependencies.py existed, or the obvious way of reintroducing one.
    (
        "redis is imported but declared nowhere (the original bug)",
        "tests/check_dependencies.py",
        "requirements.txt",
        "redis>=5.0.0,<7.0.0\n",
        "",
    ),
    (
        "aiokafka is imported but declared nowhere (the original bug)",
        "tests/check_dependencies.py",
        "requirements.txt",
        "aiokafka>=0.10.0,<1.0.0\n",
        "",
    ),
    (
        "statsmodels is imported but declared nowhere (the original bug)",
        "tests/check_dependencies.py",
        "requirements.txt",
        "statsmodels>=0.14.0,<1.0.0\n",
        "",
    ),
    (
        "pydantic is imported directly but left to FastAPI to supply",
        "tests/check_dependencies.py",
        "requirements.txt",
        "pydantic>=2.7.0,<3.0.0\n",
        "",
    ),
    (
        "an upper bound is dropped, so the next major release ships silently",
        "tests/check_dependencies.py",
        "requirements.txt",
        "pandas>=2.3.3,<3.0.0",
        "pandas>=2.3.3",
    ),
    (
        "a lower bound is dropped, so a fresh install can resolve anything older",
        "tests/check_dependencies.py",
        "requirements.txt",
        "httpx>=0.27.0,<1.0.0",
        "httpx<1.0.0",
    ),
    (
        "a dependency is added that nothing imports and nothing justifies",
        "tests/check_dependencies.py",
        "requirements.txt",
        "pandas>=2.3.3,<3.0.0",
        "pandas>=2.3.3,<3.0.0\nboto3>=1.34.0,<2.0.0",
    ),
    (
        "a feature-gated import moves to module scope, so absence breaks startup",
        "tests/check_dependencies.py",
        "app/cache/client.py",
        "import asyncio\nimport json\nimport time",
        "import asyncio\nimport json\nimport time\n\nfrom redis.asyncio import Redis",
    ),
    (
        "app/ starts importing a dev-only package, breaking a production image",
        "tests/check_dependencies.py",
        "app/cache/client.py",
        "from app.cache.keys import assert_tenant_prefix, assert_tenant_scoped",
        "import pytest\n\nfrom app.cache.keys import assert_tenant_prefix, assert_tenant_scoped",
    ),
    (
        "a justification goes stale because the package is imported now",
        "tests/check_dependencies.py",
        "app/cache/client.py",
        "from app.cache.keys import assert_tenant_prefix, assert_tenant_scoped",
        "import uvicorn\n\nfrom app.cache.keys import assert_tenant_prefix, assert_tenant_scoped",
    ),
    (
        "requirements-dev.txt stops including the runtime set",
        "tests/check_dependencies.py",
        "requirements-dev.txt",
        "-r requirements.txt\n",
        "",
    ),
    (
        "one distribution is declared in both files with two version ranges",
        "tests/check_dependencies.py",
        "requirements-dev.txt",
        "pytest>=8.0.0,<9.0.0",
        "pytest>=8.0.0,<9.0.0\nhttpx>=0.26.0,<1.0.0",
    ),
    # --- cache delete guards ----------------------------------------------
    # These three cover the operation with the largest blast radius. The guard
    # was genuinely missing here until an audit of the documentation went
    # looking for the claim that backed it up and found none.
    (
        "delete_prefix loses its tenant guard, so an empty prefix clears every tenant",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        "    async def delete_prefix(self, prefix: str) -> int:\n        assert_tenant_prefix(prefix)\n\n        async with self._lock:",
        "    async def delete_prefix(self, prefix: str) -> int:\n        async with self._lock:",
    ),
    (
        "delete loses its tenant guard, so a hand-built key reaches another tenant",
        "tests/test_cache_isolation.py",
        "app/cache/client.py",
        "    async def delete(self, *keys: str) -> int:\n        for key in keys:\n            assert_tenant_scoped(key)\n",
        "    async def delete(self, *keys: str) -> int:\n",
    ),
    (
        "a prefix may stop mid-tenant, so invalidating biz also hits biz-2",
        "tests/test_cache_isolation.py",
        "app/cache/keys.py",
        '        raise CacheKeyError("cache prefix must terminate the business_id with \':\'")',
        "        pass",
    ),
    # --- .env.example agreement -------------------------------------------
    (
        "the CORS default in .env.example drifts from config.py (the original bug)",
        "tests/check_env_example.py",
        ".env.example",
        "CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
        "CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
    ),
    (
        "a setting the code reads is dropped from .env.example",
        "tests/check_env_example.py",
        ".env.example",
        "WEBHOOK_TOLERANCE_SECONDS=300",
        "",
    ),
    (
        "a renamed setting leaves a stale entry in .env.example",
        "tests/check_env_example.py",
        ".env.example",
        # The leading newline pins this to the assignment on its own line. Without
        # it the first match is the prose "With REDIS_ENABLED=false the app runs"
        # further up the file, and renaming a word in a sentence is not a defect —
        # so the mutation survived, correctly, and said nothing about the check.
        "\nREDIS_ENABLED=false",
        "\nREDIS_ENABLE=false",
    ),
    # --- analytics job claim ----------------------------------------------
    # The read-then-write these replace was real: two workers handed the same
    # redelivered message could both pass the old Python-side status test and both
    # execute one job, interleaving two sets of writes over one snapshot.
    (
        "the job claim reverts to a read-then-write, so two workers can both win it",
        "tests/check_job_claim.py",
        "app/services/analytics_job_service.py",
        "    claimed = repository.claim_run(",
        "    run = repository.get_run(business_id=business_id, run_id=run_id)\n"
        "    claimed = run is not None and run.status in PENDING_STATUSES\n"
        "    if claimed:\n"
        "        run.status = STATUS_RUNNING\n"
        "    _unused = repository.claim_run(",
    ),
    (
        "'running' becomes claimable, so a redelivery re-runs a job mid-flight",
        "tests/check_job_claim.py",
        "app/services/analytics_job_service.py",
        'PENDING_STATUSES = (STATUS_QUEUED, "pending")',
        'PENDING_STATUSES = (STATUS_QUEUED, "pending", STATUS_RUNNING)',
    ),
    (
        "the legacy 'pending' status is dropped, stranding every Phase 1 job",
        "tests/check_job_claim.py",
        "app/services/analytics_job_service.py",
        'PENDING_STATUSES = (STATUS_QUEUED, "pending")',
        "PENDING_STATUSES = (STATUS_QUEUED,)",
    ),
    (
        "the claim UPDATE loses its status predicate and overwrites finished work",
        "tests/check_job_claim.py",
        "app/repositories/analytics_repository.py",
        "                AnalyticsRun.status.in_(list(claimable_statuses)),\n",
        "",
    ),
    (
        "the claim reports success on a multi-row update",
        "tests/check_job_claim.py",
        "app/repositories/analytics_repository.py",
        "        return self.session.execute(stmt).rowcount == 1",
        "        return self.session.execute(stmt).rowcount >= 1",
    ),
    (
        "the claim is never committed, so the other worker cannot see it",
        "tests/check_job_claim.py",
        "app/services/analytics_job_service.py",
        "        started_at=datetime.now(timezone.utc),\n    )\n    session.commit()",
        "        started_at=datetime.now(timezone.utc),\n    )",
    ),
    # --- tenant never comes from the client -------------------------------
    # Both shapes matter. The parameter form is the obvious one; the raw-request
    # form is the one the codebase actually uses, and a check that only looked at
    # parameters would have passed while never examining it.
    (
        "a route accepts the tenant as a query parameter",
        "tests/check_api_wiring.py",
        "app/api/routes/v1_customers.py",
        "async def list_customers(\n    business_context: BusinessContext = Depends(get_business_context),",
        "async def list_customers(\n    business_id: str = Query(...),\n"
        "    business_context: BusinessContext = Depends(get_business_context),",
    ),
    (
        "a non-exempt route reads the tenant from an unsigned header",
        "tests/check_api_wiring.py",
        "app/api/routes/v1_customers.py",
        '    service = CustomerService(business_context.session)\n    customers = service.list_customers(business_context.business_id)',
        '    service = CustomerService(business_context.session)\n'
        '    customers = service.list_customers(business_id=request.headers.get("X-Business-Id"))',
    ),
    (
        "the webhook exemption stops being justified: the HMAC no longer covers the tenant",
        "tests/check_api_wiring.py",
        "app/webhooks/service.py",
        "signature.canonical_message(str(business_id)",
        "signature.canonical_message(str",
    ),
    (
        "a router is written and imported but never mounted, so every path 404s",
        "tests/check_api_wiring.py",
        "app/main.py",
        "app.include_router(v1_orders_router)\n",
        "",
    ),
]


def build_tree(root: Path) -> Path:
    """A copy faithful enough that every checker sees what it sees for real.

    "Minimal" was the original intent and it has been wrong three times.
    ``check_dependencies.py`` reads the requirements files, so a tree without
    them made every dependency mutation a no-op; it attributes ``requests`` to
    the by-hand upload scripts at the backend root, so a tree without those
    reported ``requests`` as an unjustified dependency and broke the baseline;
    and ``check_env_example.py`` reads ``.env.example``, which a tree of only
    Python files does not contain.

    Every one of the three surfaced as BAD MUTATION or a broken baseline rather
    than as a silent pass, which is the reason the baseline run exists. The
    lesson each time was the same: copy what the checkers read, not what the
    mutations happen to touch.
    """
    backend = root / "backend"
    backend.mkdir(parents=True)

    for name in ("app", "tests"):
        shutil.copytree(
            BACKEND / name,
            backend / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    for source in (
        *BACKEND.glob("requirements*.txt"),
        *BACKEND.glob("*.py"),
        BACKEND / ".env.example",
    ):
        if source.is_file():
            shutil.copy2(source, backend / source.name)

    return backend


def run_checker(backend: Path, checker: str) -> tuple[int, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(backend)

    completed = subprocess.run(
        [sys.executable, checker],
        cwd=str(backend),
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
    )

    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def failing_check_names(output: str) -> list[str]:
    return re.findall(r"^\s*(?:FAIL|ERROR)\s+(\S+)", output, re.MULTILINE)


def main() -> int:
    print("=" * 78)
    print("Mutation probe - each defect must be caught by the matching checker")
    print("=" * 78)
    print()

    # Baseline: an unmutated copy must pass, or every result below is meaningless.
    with tempfile.TemporaryDirectory() as temporary:
        backend = build_tree(Path(temporary))

        for checker in sorted({mutation[1] for mutation in MUTATIONS}):
            code, output = run_checker(backend, checker)
            if code != 0:
                print(f"BASELINE BROKEN: {checker} fails on an unmutated copy")
                print(output[-2000:])
                return 1
            print(f"  baseline ok   {checker}")

    print()

    survived: list[str] = []
    caught = 0

    for label, checker, target, find, replace in MUTATIONS:
        with tempfile.TemporaryDirectory() as temporary:
            backend = build_tree(Path(temporary))
            path = backend / target
            source = path.read_text(encoding="utf-8")

            if find not in source:
                print(f"  BAD MUTATION   {label}")
                print(f"                 anchor not found in {target}")
                survived.append(f"{label} (anchor missing)")
                continue

            path.write_text(source.replace(find, replace, 1), encoding="utf-8")

            code, output = run_checker(backend, checker)

            if code == 0:
                survived.append(label)
                print(f"  SURVIVED       {label}")
                print(f"                 {checker} still reports success")
            else:
                caught += 1
                names = failing_check_names(output)
                detail = ", ".join(names[:3]) if names else "non-zero exit"
                print(f"  caught         {label}")
                print(f"                 -> {detail}")

    print()
    print("=" * 78)
    print(f"{caught} of {len(MUTATIONS)} mutations caught")

    if survived:
        print()
        print("SURVIVED - these checks cannot detect a real defect:")
        for label in survived:
            print(f"  - {label}")
        print("=" * 78)
        return 1

    print("Every mutation was caught.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
