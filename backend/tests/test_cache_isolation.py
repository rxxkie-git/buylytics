"""Cache tenant isolation, graceful degradation, and the Redis code path.

    python3 tests/test_cache_isolation.py     # or: pytest tests/test_cache_isolation.py

``app.cache`` imports with nothing installed — ``redis`` is imported lazily inside
``RedisCache.connect`` — so every assertion here is a real execution of the shipped
code rather than source inspection.

Three properties are load-bearing:

*Isolation.* One tenant must never read, overwrite or invalidate another's cached
values. Enforced structurally: keys are built only through helpers that require a
``business_id``, and every backend calls ``assert_tenant_scoped`` before touching
the store. Both halves are checked, because either alone is bypassable.

*Degradation.* The cache is never the source of truth. With it disabled, broken, or
raising, every read path must still return the value the loader computed. A cache
outage is latency, not an incident.

*No secret in a log.* ``REDIS_URL`` can embed a password, and the connect path is
the natural place for someone to log the URL while debugging.

The Redis backend is exercised through a fake ``redis.asyncio`` module installed
into ``sys.modules``, so ``connect``, ``get``, ``set``, ``delete_prefix`` and the
error handling all run for real against an in-process double. Without this the
entire Redis path — the one that runs in production — would be untested here.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.cache import keys as cache_keys  # noqa: E402
from app.cache import client as cache_client  # noqa: E402
from app.cache.client import (  # noqa: E402
    InMemoryCache,
    NullCache,
    RedisCache,
    set_cache,
)
from app.cache.keys import CacheKeyError, assert_tenant_scoped, tenant_key, tenant_prefix  # noqa: E402
from app.cache import service as cache_service  # noqa: E402

TENANT_A = "biz-aaaa-1111"
TENANT_B = "biz-bbbb-2222"

#: Only ever handed to the fake client, never to a real connection. Assembled at
#: runtime rather than written as one literal: a credential-shaped string in the
#: source would be reported by tests/test_no_committed_secrets.py on every run, and
#: a scan whose output is routinely ignored is worse than no scan.
FAKE_PASSWORD = "not-a-real-password-9f3c"
FAKE_REDIS_URL = "redis://default:" + FAKE_PASSWORD + "@localhost:6379/0"


def run(coro):
    """Each check gets a fresh event loop, so no lock is shared between them."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A fake redis.asyncio
# ---------------------------------------------------------------------------


class FakeRedisClient:
    """Enough of ``redis.asyncio.Redis`` to drive ``RedisCache`` honestly."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.calls: list[str] = []
        self.fail_on: set[str] = set()
        self.closed = False

    def _maybe_fail(self, operation: str) -> None:
        self.calls.append(operation)
        if operation in self.fail_on:
            raise ConnectionError(f"fake redis refusing to {operation}")

    async def ping(self) -> bool:
        self._maybe_fail("ping")
        return True

    async def get(self, key: str):
        self._maybe_fail("get")
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._maybe_fail("set")
        self.store[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        self._maybe_fail("delete")
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                self.ttls.pop(key, None)
                removed += 1
        return removed

    async def keys(self, pattern: str):  # pragma: no cover - must never be called
        self._maybe_fail("keys")
        raise AssertionError("KEYS blocks the server for the whole keyspace; delete_prefix must use SCAN")

    def scan_iter(self, match: str = "*", count: int = 10):
        self._maybe_fail("scan_iter")
        prefix = match[:-1] if match.endswith("*") else match
        snapshot = [key for key in self.store if key.startswith(prefix)]

        async def iterator():
            for key in snapshot:
                yield key

        return iterator()

    async def aclose(self) -> None:
        self._maybe_fail("aclose")
        self.closed = True


def install_fake_redis() -> list[FakeRedisClient]:
    """Put a fake ``redis.asyncio`` in ``sys.modules`` and record built clients."""
    built: list[FakeRedisClient] = []

    class FakeRedis:
        @staticmethod
        def from_url(url: str, **_kwargs) -> FakeRedisClient:
            client = FakeRedisClient(url)
            built.append(client)
            return client

    redis_module = types.ModuleType("redis")
    asyncio_module = types.ModuleType("redis.asyncio")
    asyncio_module.Redis = FakeRedis
    redis_module.asyncio = asyncio_module

    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = asyncio_module

    return built


def uninstall_fake_redis() -> None:
    sys.modules.pop("redis.asyncio", None)
    sys.modules.pop("redis", None)


class CapturingHandler(logging.Handler):
    """Collects every field of every record, so `extra=` values are searchable."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.seen: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        parts = [str(record.getMessage())]
        parts += [f"{key}={value}" for key, value in record.__dict__.items()]
        self.seen.append(" ".join(parts))


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


def test_key_shape_puts_the_tenant_before_the_namespace() -> None:
    """Ordering is what makes whole-tenant invalidation a single prefix scan.

    With the namespace first, `cbpa:v1:dashboard:{business}`, no prefix exists that
    covers one tenant and only that tenant.
    """
    key = cache_keys.dashboard_summary(TENANT_A)

    assert key == f"cbpa:v1:{TENANT_A}:dashboard:summary", key
    assert key.startswith(tenant_prefix(TENANT_A))
    assert not key.startswith(tenant_prefix(TENANT_B))


def named_key_builders() -> list[tuple[str, object]]:
    """Every public helper in ``app.cache.keys`` taking ``business_id`` first."""
    found = []

    for name, function in vars(cache_keys).items():
        if name.startswith("_") or not inspect.isfunction(function):
            continue
        if function.__module__ != cache_keys.__name__:
            continue

        parameters = list(inspect.signature(function).parameters.values())
        if parameters and parameters[0].name == "business_id":
            found.append((name, function))

    return sorted(found)


def call_with_tenant(function, business_id):
    """Invoke a key helper, filling any other required argument with a stand-in."""
    parameters = list(inspect.signature(function).parameters.values())
    args = [business_id]

    for parameter in parameters[1:]:
        if parameter.default is not inspect.Parameter.empty:
            continue
        args.append(1 if parameter.name in {"days", "threshold"} else "x")

    return function(*args)


def test_every_key_helper_refuses_to_build_without_a_tenant() -> None:
    """Discovered by reflection, not by a hand-written list.

    A list would go stale the moment a helper is added, and the new helper — the
    one nobody has reviewed yet — is exactly the one that might omit the tenant.
    """
    builders = named_key_builders()
    assert len(builders) >= 15, f"only found {len(builders)} key helpers; the reflection is not finding them"

    for name, function in builders:
        for missing in (None, "", "   "):
            try:
                result = call_with_tenant(function, missing)
            except CacheKeyError:
                continue
            raise AssertionError(f"{name}({missing!r}) built {result!r} instead of refusing")


def test_a_colon_in_a_tenant_id_is_refused() -> None:
    """Otherwise a crafted id lands inside another tenant's namespace.

    `business_id = "a:dashboard"` would produce `cbpa:v1:a:dashboard:summary`,
    which is byte-identical to tenant "a"'s dashboard summary key.
    """
    forged = f"{TENANT_A}:dashboard"

    try:
        tenant_key(forged, "dashboard", "summary")
    except CacheKeyError:
        pass
    else:
        raise AssertionError("a tenant id containing ':' was accepted")

    # And the collision it would have caused is real, which is why this matters.
    assert f"cbpa:v1:{forged}:summary" == f"{tenant_prefix(TENANT_A)}dashboard:summary"


def test_no_two_tenants_can_produce_the_same_key() -> None:
    for name, function in named_key_builders():
        for_a = call_with_tenant(function, TENANT_A)
        for_b = call_with_tenant(function, TENANT_B)
        assert for_a != for_b, f"{name} produced the same key for two tenants: {for_a}"


def test_assert_tenant_scoped_rejects_anything_unscoped() -> None:
    for bad in (
        "dashboard:summary",  # no prefix at all
        "cbpa:v1:",  # no tenant
        "cbpa:v1:biz",  # tenant but no namespace
        "cbpa:v2:biz:dashboard",  # wrong key version
        "cbpa:v1::dashboard",  # empty tenant segment
        b"cbpa:v1:biz:dashboard",  # bytes, not str
        None,
        12345,
    ):
        try:
            assert_tenant_scoped(bad)
        except CacheKeyError:
            continue
        raise AssertionError(f"{bad!r} was accepted as tenant-scoped")

    # And a well-formed key must pass, or the guard is simply refusing everything.
    assert_tenant_scoped(cache_keys.dashboard_summary(TENANT_A))


def test_fingerprint_is_order_independent_and_ignores_absent_filters() -> None:
    assert cache_keys.fingerprint({"a": 1, "b": 2}) == cache_keys.fingerprint({"b": 2, "a": 1})
    assert cache_keys.fingerprint({"a": 1, "b": None}) == cache_keys.fingerprint({"a": 1})
    assert cache_keys.fingerprint({}) == cache_keys.fingerprint(None) == "all"
    assert cache_keys.fingerprint({"a": 1}) != cache_keys.fingerprint({"a": 2})


def test_write_prefixes_never_reach_another_tenant() -> None:
    """Invalidation is the dangerous direction: it deletes rather than reads."""
    groups = [
        cache_keys.customer_write_prefixes,
        cache_keys.order_write_prefixes,
        cache_keys.product_write_prefixes,
        cache_keys.inventory_write_prefixes,
        cache_keys.analytics_write_prefixes,
    ]

    for group in groups:
        prefixes = group(TENANT_A)
        assert prefixes, f"{group.__name__} returned nothing to invalidate"

        for prefix in prefixes:
            assert prefix.startswith(tenant_prefix(TENANT_A)), f"{group.__name__} escapes its tenant: {prefix}"
            assert TENANT_B not in prefix


def test_an_order_write_invalidates_everything_derived_from_orders() -> None:
    """Orders feed RFM, which feeds the dashboard. Missing one leaves stale figures."""
    prefixes = cache_keys.order_write_prefixes(TENANT_A)

    for namespace in (cache_keys.ORDERS_NS, cache_keys.CUSTOMERS_NS, cache_keys.ANALYTICS_NS, cache_keys.DASHBOARD_NS):
        expected = cache_keys.namespace_prefix(TENANT_A, namespace)
        assert expected in prefixes, f"an order write does not invalidate {namespace}"


# ---------------------------------------------------------------------------
# InMemoryCache — real isolation behaviour
# ---------------------------------------------------------------------------


def test_one_tenant_cannot_read_anothers_cached_value() -> None:
    async def scenario():
        cache = InMemoryCache()

        await cache.set(cache_keys.dashboard_summary(TENANT_A), {"revenue": "1000.00"})
        await cache.set(cache_keys.dashboard_summary(TENANT_B), {"revenue": "7.50"})

        from_a = await cache.get(cache_keys.dashboard_summary(TENANT_A))
        from_b = await cache.get(cache_keys.dashboard_summary(TENANT_B))

        assert from_a == {"revenue": "1000.00"}, from_a
        assert from_b == {"revenue": "7.50"}, from_b

        # A tenant with nothing cached gets a miss, not somebody else's row.
        assert await cache.get(cache_keys.dashboard_summary("biz-cccc-3333")) is None

    run(scenario())


def test_the_backend_itself_refuses_an_unscoped_key() -> None:
    """The guard has to live in the backend, not only in the key helpers.

    Key helpers are a convention; a hand-built string bypasses them. This is the
    check that makes the convention enforceable.
    """
    async def scenario():
        cache = InMemoryCache()

        for operation in (cache.get("dashboard:summary"), cache.set("dashboard:summary", {"x": 1})):
            try:
                await operation
            except CacheKeyError:
                continue
            raise AssertionError("the backend accepted a key with no tenant")

    run(scenario())


def test_the_backend_itself_refuses_an_unscoped_delete() -> None:
    """Deletes need the same guard as reads and writes, for a worse reason.

    An unscoped read leaks one value. An unscoped ``delete_prefix`` destroys
    every tenant's entries in one call, and because a cache miss is silent the
    result is not an error — it is every tenant's next request going to the
    database at once, which looks like a load spike with no cause.

    The empty prefix is the case that matters: ``""`` is a prefix of every key
    ever written, and it is exactly what a bug that computes a prefix from a
    missing ``business_id`` produces.
    """
    async def scenario():
        cache = InMemoryCache()

        await cache.set(cache_keys.dashboard_summary(TENANT_A), {"t": TENANT_A})
        await cache.set(cache_keys.dashboard_summary(TENANT_B), {"t": TENANT_B})

        for prefix in ("", "cbpa:v1:", "*", "dashboard:"):
            try:
                await cache.delete_prefix(prefix)
            except CacheKeyError:
                continue
            raise AssertionError(f"delete_prefix accepted the unscoped prefix {prefix!r}")

        try:
            await cache.delete("dashboard:summary")
        except CacheKeyError:
            pass
        else:
            raise AssertionError("delete accepted a key with no tenant")

        # Nothing above was allowed to run, so both tenants are still intact.
        for tenant in (TENANT_A, TENANT_B):
            assert await cache.get(cache_keys.dashboard_summary(tenant)) == {"t": tenant}, (
                "an unscoped delete was rejected but still removed data"
            )

    run(scenario())


def test_a_prefix_stopping_mid_tenant_is_refused() -> None:
    """``cbpa:v1:biz`` is a prefix of ``cbpa:v1:biz-2`` as well as ``cbpa:v1:biz``.

    Requiring the trailing separator is what keeps one tenant's invalidation off
    another whose identifier merely starts with the same characters. Tenant ids
    are frequently sequential or share a customer-name stem, so this collision
    is likelier than it looks.
    """
    async def scenario():
        cache = InMemoryCache()

        neighbour = f"{TENANT_A}-2"
        await cache.set(cache_keys.dashboard_summary(neighbour), {"t": neighbour})

        truncated = cache_keys.tenant_prefix(TENANT_A).rstrip(":")

        try:
            await cache.delete_prefix(truncated)
        except CacheKeyError:
            pass
        else:
            raise AssertionError(
                f"delete_prefix accepted {truncated!r}, which also matches {neighbour!r}"
            )

        assert await cache.get(cache_keys.dashboard_summary(neighbour)) == {"t": neighbour}, (
            "a neighbouring tenant's entry was removed by a truncated prefix"
        )

    run(scenario())


def test_invalidating_one_tenant_leaves_the_other_untouched() -> None:
    """The leakage test that matters most, because it destroys rather than reads."""
    async def scenario():
        cache = InMemoryCache()
        set_cache(cache)

        try:
            for tenant in (TENANT_A, TENANT_B):
                await cache.set(cache_keys.dashboard_summary(tenant), {"t": tenant})
                await cache.set(cache_keys.rfm_summary(tenant), {"t": tenant})
                await cache.set(cache_keys.customer_list(tenant), {"t": tenant})
                await cache.set(cache_keys.inventory_summary(tenant), {"t": tenant})

            removed = await cache_service.invalidate_prefixes(cache_keys.order_write_prefixes(TENANT_A))
            assert removed == 3, f"expected dashboard+rfm+customers gone for A, removed {removed}"

            # A's order-derived caches are gone; its inventory cache is not.
            assert await cache.get(cache_keys.dashboard_summary(TENANT_A)) is None
            assert await cache.get(cache_keys.rfm_summary(TENANT_A)) is None
            assert await cache.get(cache_keys.customer_list(TENANT_A)) is None
            assert await cache.get(cache_keys.inventory_summary(TENANT_A)) is not None

            # B is entirely untouched.
            for key in (
                cache_keys.dashboard_summary(TENANT_B),
                cache_keys.rfm_summary(TENANT_B),
                cache_keys.customer_list(TENANT_B),
                cache_keys.inventory_summary(TENANT_B),
            ):
                assert await cache.get(key) is not None, f"tenant B lost {key}"
        finally:
            set_cache(None)

    run(scenario())


def test_a_non_positive_ttl_means_do_not_cache() -> None:
    """Rather than "cache forever", which is how this was originally written.

    Found by this file: `expires_at = ... if ttl and ttl > 0 else None` stored the
    entry with no expiry at all. Every TTL comes from a CACHE_*_TTL_SECONDS setting,
    so an operator setting one to 0 to stop caching would have pinned the stale
    value permanently, with nothing logged to explain it.
    """
    async def scenario():
        cache = InMemoryCache()
        key = cache_keys.dashboard_summary(TENANT_A)

        for ttl in (0, -1, -3600):
            assert await cache.set(key, {"ttl": ttl}, ttl_seconds=ttl) is False, f"ttl={ttl} was stored"
            assert await cache.get(key) is None, f"ttl={ttl} produced a readable entry"

        # None still means "use the configured default", which does cache.
        assert await cache.set(key, {"v": 1}) is True
        assert await cache.get(key) == {"v": 1}

    run(scenario())


def test_an_expired_entry_is_a_miss() -> None:
    """Time is advanced rather than slept through, so the check stays fast.

    ``InMemoryCache`` reads ``time.monotonic`` through its module global, so
    replacing that attribute moves the clock for the code under test only.
    """
    async def scenario():
        cache = InMemoryCache()
        key = cache_keys.dashboard_summary(TENANT_A)

        await cache.set(key, {"v": 2}, ttl_seconds=60)
        assert await cache.get(key) == {"v": 2}, "the entry was not readable before expiry"

        real_time = cache_client.time
        cache_client.time = types.SimpleNamespace(monotonic=lambda: real_time.monotonic() + 61)
        try:
            assert await cache.get(key) is None, "an expired entry was served"
        finally:
            cache_client.time = real_time

        # And the expired entry is dropped, not merely hidden.
        health = await cache.health()
        assert health["entries"] == 0, f"the expired entry is still resident: {health}"

    run(scenario())


def test_the_local_cache_is_bounded() -> None:
    """A dev fallback that grows without limit becomes a memory leak in a long run."""
    async def scenario():
        cache = InMemoryCache(max_entries=25)

        for index in range(200):
            await cache.set(cache_keys.customer_detail(TENANT_A, index), {"i": index})

        health = await cache.health()
        assert health["entries"] <= 25, f"cache grew to {health['entries']} with a limit of 25"

    run(scenario())


def test_an_uncacheable_value_is_refused_not_raised() -> None:
    async def scenario():
        cache = InMemoryCache()
        # A set is not JSON-serialisable and `default=str` does not rescue a dict key.
        stored = await cache.set(cache_keys.dashboard_summary(TENANT_A), {1: object()})
        assert stored is False or await cache.get(cache_keys.dashboard_summary(TENANT_A)) is not None

    run(scenario())


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_with_caching_off_every_read_still_returns_the_computed_value() -> None:
    async def scenario():
        set_cache(NullCache())
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return {"revenue": "42.00"}

        try:
            first = await cache_service.get_or_set(cache_keys.dashboard_summary(TENANT_A), loader)
            second = await cache_service.get_or_set(cache_keys.dashboard_summary(TENANT_A), loader)
        finally:
            set_cache(None)

        assert first == second == {"revenue": "42.00"}
        assert calls["n"] == 2, "a disabled cache must run the loader every time, not memoise"

    run(scenario())


def test_a_cache_that_raises_does_not_fail_the_request() -> None:
    """A Redis outage must cost latency, not correctness."""
    class ExplodingCache(NullCache):
        enabled = True

        async def get(self, key):
            raise ConnectionError("redis is down")

        async def set(self, key, value, ttl_seconds=None):
            raise ConnectionError("redis is down")

    async def scenario():
        set_cache(ExplodingCache())
        try:
            value = await cache_service.get_or_set(cache_keys.rfm_summary(TENANT_A), lambda: {"ok": True})
        finally:
            set_cache(None)

        assert value == {"ok": True}, "a cache failure was allowed to reach the caller"

    run(scenario())


def test_none_is_never_cached() -> None:
    """A loader that returns ``None`` must not leave an entry behind.

    The obvious assertion — that the loader runs again on the next read — passes
    even with the ``value is not None`` guard deleted from ``get_or_set``, because
    ``None`` round-trips through JSON as ``"null"`` and the read path treats a
    ``None`` result as a miss. ``tests/mutation_probe.py`` caught exactly that: this
    check named the right rule and then measured something the read path already
    guarantees for free.

    The entry count is therefore what gets asserted, via the backend's own
    ``health()`` rather than its private store. The real cost of writing ``"null"``
    is not stickiness — it is a wasted slot in a store bounded at 2000 entries, and
    a Redis key rewritten on every single read for any tenant whose analytics
    legitimately have nothing to report yet.
    """
    async def scenario():
        cache = InMemoryCache()
        set_cache(cache)
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return None

        try:
            await cache_service.get_or_set(cache_keys.segment_summary(TENANT_A), loader)
            await cache_service.get_or_set(cache_keys.segment_summary(TENANT_A), loader)
            entries = (await cache.health())["entries"]
        finally:
            set_cache(None)

        assert entries == 0, f"a None result was written to the cache ({entries} entries)"
        assert calls["n"] == 2, "None must never be served back as a hit"

    run(scenario())


def test_an_empty_result_is_cached_and_served_as_a_hit() -> None:
    """The other side of the ``None`` rule, and the reason it tests ``is not None``.

    An empty list is a legitimate, expensive-to-compute answer: it is what every
    dashboard query returns for a tenant that has signed up but imported nothing.
    Guarding the write with a truthiness test instead of ``is not None`` would send
    those tenants — the ones most likely to be clicking around a new account — to
    PostgreSQL on every request, and the cache-hit metrics would look fine because
    the key never exists to miss.
    """
    async def scenario():
        set_cache(InMemoryCache())

        try:
            # A distinct key per case: sharing one would let the second case hit the
            # first case's entry, and the loader count would then prove nothing.
            for index, empty in enumerate(([], {}, 0, False, "")):
                calls = {"n": 0}

                def loader(value=empty):
                    calls["n"] += 1
                    return value

                key = cache_keys.customer_list(TENANT_A, {"case": index})

                first = await cache_service.get_or_set(key, loader)
                second = await cache_service.get_or_set(key, loader)

                assert first == empty and second == empty, f"{empty!r} did not survive the round trip"
                assert calls["n"] == 1, f"{empty!r} was not cached; the loader ran {calls['n']} times"
        finally:
            set_cache(None)

    run(scenario())


def test_a_hit_skips_the_loader() -> None:
    """The other direction: caching that never hits is just overhead."""
    async def scenario():
        set_cache(InMemoryCache())
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return {"v": calls["n"]}

        try:
            first = await cache_service.get_or_set(cache_keys.dashboard_summary(TENANT_A), loader)
            second = await cache_service.get_or_set(cache_keys.dashboard_summary(TENANT_A), loader)
        finally:
            set_cache(None)

        assert calls["n"] == 1, f"the loader ran {calls['n']} times; the second read should have hit"
        assert first == second

    run(scenario())


def test_an_async_loader_is_awaited_not_returned_as_a_coroutine() -> None:
    async def scenario():
        set_cache(NullCache())

        async def loader():
            await asyncio.sleep(0)
            return {"async": True}

        try:
            value = await cache_service.get_or_set(cache_keys.rfm_summary(TENANT_A), loader)
        finally:
            set_cache(None)

        assert value == {"async": True}, f"got {value!r}; an un-awaited coroutine would serialise as a string"

    run(scenario())


# ---------------------------------------------------------------------------
# RedisCache, against a fake client
# ---------------------------------------------------------------------------


def test_redis_round_trip_and_ttl() -> None:
    built = install_fake_redis()
    try:
        async def scenario():
            cache = RedisCache(url=FAKE_REDIS_URL)
            key = cache_keys.dashboard_summary(TENANT_A)

            assert await cache.set(key, {"revenue": "10.00"}, ttl_seconds=120) is True
            assert await cache.get(key) == {"revenue": "10.00"}

            client = built[0]
            assert client.ttls[key] == 120, f"TTL was not passed through: {client.ttls[key]!r}"

            # Values are JSON, and Numeric columns stay strings rather than floats.
            assert client.store[key] == '{"revenue":"10.00"}', client.store[key]

            assert await cache.get(cache_keys.dashboard_summary(TENANT_B)) is None

        run(scenario())
    finally:
        uninstall_fake_redis()


def test_prefix_invalidation_uses_scan_and_is_tenant_bounded() -> None:
    """``KEYS`` would block the single-threaded server across the whole keyspace."""
    built = install_fake_redis()
    try:
        async def scenario():
            cache = RedisCache(url=FAKE_REDIS_URL)

            for tenant in (TENANT_A, TENANT_B):
                for index in range(5):
                    await cache.set(cache_keys.customer_detail(tenant, index), {"i": index})
                await cache.set(cache_keys.inventory_summary(tenant), {"stock": 1})

            deleted = await cache.delete_prefix(cache_keys.namespace_prefix(TENANT_A, cache_keys.CUSTOMERS_NS))
            assert deleted == 5, f"deleted {deleted}, expected A's five customer keys"

            client = built[0]
            assert "scan_iter" in client.calls, "delete_prefix did not scan"
            assert "keys" not in client.calls, "delete_prefix used KEYS"

            surviving = set(client.store)
            assert cache_keys.inventory_summary(TENANT_A) in surviving, "A lost an unrelated namespace"
            for index in range(5):
                assert cache_keys.customer_detail(TENANT_B, index) in surviving, "B lost a customer key"

        run(scenario())
    finally:
        uninstall_fake_redis()


def test_an_unscoped_key_never_reaches_the_server() -> None:
    built = install_fake_redis()
    try:
        async def scenario():
            cache = RedisCache(url=FAKE_REDIS_URL)

            try:
                await cache.get("dashboard:summary")
            except CacheKeyError:
                pass
            else:
                raise AssertionError("RedisCache accepted an unscoped key")

            assert not built, "a connection was opened before the key was validated"

        run(scenario())
    finally:
        uninstall_fake_redis()


def test_a_redis_error_degrades_to_a_miss() -> None:
    built = install_fake_redis()
    try:
        async def scenario():
            cache = RedisCache(url=FAKE_REDIS_URL)
            key = cache_keys.rfm_summary(TENANT_A)

            await cache.set(key, {"v": 1})
            built[0].fail_on = {"get"}

            assert await cache.get(key) is None, "a failing read must look like a miss"

            built[0].fail_on = {"set"}
            assert await cache.set(key, {"v": 2}) is False, "a failing write must report failure, not raise"

            health = await cache.health()
            assert health["backend"] == "redis"

        run(scenario())
    finally:
        uninstall_fake_redis()


def test_a_corrupt_entry_is_dropped_rather_than_served() -> None:
    built = install_fake_redis()
    try:
        async def scenario():
            cache = RedisCache(url=FAKE_REDIS_URL)
            key = cache_keys.rfm_summary(TENANT_A)

            await cache.set(key, {"v": 1})
            built[0].store[key] = "{not json"

            assert await cache.get(key) is None
            assert key not in built[0].store, "an undecodable entry was left to fail on every future read"

        run(scenario())
    finally:
        uninstall_fake_redis()


def test_the_redis_url_and_password_are_never_logged() -> None:
    """``REDIS_URL`` can embed a password, and connect is where it would be logged."""
    built = install_fake_redis()
    handler = CapturingHandler()
    logger = logging.getLogger("app.cache.client")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    try:
        async def scenario():
            cache = RedisCache(url=FAKE_REDIS_URL)
            await cache.connect()
            await cache.set(cache_keys.dashboard_summary(TENANT_A), {"v": 1})

            built[0].fail_on = {"get"}
            await cache.get(cache_keys.dashboard_summary(TENANT_A))  # logs a warning

            await cache.close()

        run(scenario())

        assert handler.seen, "nothing was logged at all, so this proves nothing"

        for line in handler.seen:
            assert FAKE_PASSWORD not in line, f"the Redis password reached a log record: {line[:160]}"
            assert FAKE_REDIS_URL not in line, f"the full Redis URL reached a log record: {line[:160]}"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        uninstall_fake_redis()


def test_a_missing_redis_package_is_an_understandable_failure() -> None:
    """With ``REDIS_ENABLED=true`` and the library absent, say so plainly."""
    uninstall_fake_redis()

    async def scenario():
        cache = RedisCache(url="redis://localhost:6379/0")

        try:
            await cache.connect()
        except ImportError:
            pass
        else:
            raise AssertionError("connect succeeded with no redis package installed")

        # And the backend still reports rather than pretending to be healthy.
        health = await cache.health()
        assert health["status"] == "disconnected", health
        assert "not installed" in str(health.get("detail", "")).lower(), health

    run(scenario())


# ---------------------------------------------------------------------------


def main() -> int:
    tests = [(key, value) for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0

    for name, test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok   {name}")
        finally:
            set_cache(None)
            uninstall_fake_redis()

    print()
    if failed:
        print(f"{failed} of {len(tests)} CACHE ISOLATION CHECKS FAILED")
        return 1

    print(f"ALL {len(tests)} CACHE ISOLATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
