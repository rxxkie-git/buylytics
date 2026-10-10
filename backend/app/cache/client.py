"""Cache backends.

Three implementations behind one interface, chosen from configuration:

``RedisCache``
    Real Redis. Used when ``REDIS_ENABLED=true``.
``InMemoryCache``
    Process-local dict with TTLs. Used when Redis is disabled but
    ``CACHE_LOCAL_FALLBACK`` is on. Correct for a single-process dev server;
    not shared between workers, which is why it is not a production option.
``NullCache``
    Every read misses. Used when caching is fully off.

Nothing in the application depends on the cache being available: every read
path treats a miss as "go to PostgreSQL". A Redis outage costs latency, not
correctness — Postgres remains the source of truth.

``redis`` is imported lazily inside :meth:`RedisCache.connect` so this module
imports cleanly without the library installed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.cache.keys import assert_tenant_prefix, assert_tenant_scoped
from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Upper bound on the dev-only in-memory cache so a long-running process cannot
#: grow without limit.
_MAX_LOCAL_ENTRIES = 2000


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _loads(raw: str | bytes) -> Any:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


class CacheBackend:
    """Interface implemented by every cache backend."""

    backend: str = "unknown"
    enabled: bool = False

    async def connect(self) -> None:  # pragma: no cover - trivial
        return None

    async def close(self) -> None:  # pragma: no cover - trivial
        return None

    async def get(self, key: str) -> Any | None:
        raise NotImplementedError

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        raise NotImplementedError

    async def delete(self, *keys: str) -> int:
        raise NotImplementedError

    async def delete_prefix(self, prefix: str) -> int:
        raise NotImplementedError

    async def health(self) -> dict[str, Any]:
        return {"backend": self.backend, "status": "disabled" if not self.enabled else "ok"}


class NullCache(CacheBackend):
    backend = "disabled"
    enabled = False

    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        return False

    async def delete(self, *keys: str) -> int:
        return 0

    async def delete_prefix(self, prefix: str) -> int:
        return 0


class InMemoryCache(CacheBackend):
    """Process-local TTL cache for development and tests."""

    backend = "memory"
    enabled = True

    def __init__(self, max_entries: int = _MAX_LOCAL_ENTRIES) -> None:
        self._store: dict[str, tuple[float | None, str]] = {}
        self._max_entries = max_entries
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    def _is_expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and expires_at <= time.monotonic()

    async def get(self, key: str) -> Any | None:
        assert_tenant_scoped(key)

        async with self._lock:
            entry = self._store.get(key)

            if entry is None:
                self._misses += 1
                return None

            expires_at, payload = entry

            if self._is_expired(expires_at):
                self._store.pop(key, None)
                self._misses += 1
                return None

            self._hits += 1

        try:
            return _loads(payload)
        except (ValueError, TypeError):  # pragma: no cover - defensive
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        assert_tenant_scoped(key)

        try:
            payload = _dumps(value)
        except (TypeError, ValueError) as exc:
            logger.warning("Value is not cacheable", extra={"reason": str(exc)})
            return False

        ttl = settings.cache_default_ttl_seconds if ttl_seconds is None else ttl_seconds

        # A non-positive TTL means "do not cache", not "cache forever".
        #
        # Every TTL reaching this method comes from a CACHE_*_TTL_SECONDS setting, so
        # an operator who sets one to 0 to stop caching a slow dashboard would
        # otherwise pin that value in the process permanently — the figures would
        # never move again and nothing would log a reason. `None` still means "use
        # the configured default"; only an explicit non-positive value refuses.
        if ttl <= 0:
            return False

        expires_at = time.monotonic() + ttl

        async with self._lock:
            if len(self._store) >= self._max_entries and key not in self._store:
                self._evict_locked()

            self._store[key] = (expires_at, payload)

        return True

    def _evict_locked(self) -> None:
        """Drop expired entries first, then the oldest inserted ones."""
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._store.items() if expires_at and expires_at <= now]

        for key in expired:
            self._store.pop(key, None)

        overflow = len(self._store) - self._max_entries + 1

        if overflow > 0:
            for key in list(self._store.keys())[:overflow]:
                self._store.pop(key, None)

    async def delete(self, *keys: str) -> int:
        for key in keys:
            assert_tenant_scoped(key)

        removed = 0

        async with self._lock:
            for key in keys:
                if self._store.pop(key, None) is not None:
                    removed += 1

        return removed

    async def delete_prefix(self, prefix: str) -> int:
        assert_tenant_prefix(prefix)

        async with self._lock:
            matched = [key for key in self._store if key.startswith(prefix)]

            for key in matched:
                self._store.pop(key, None)

        return len(matched)

    async def health(self) -> dict[str, Any]:
        async with self._lock:
            size = len(self._store)

        return {
            "backend": self.backend,
            "status": "ok",
            "entries": size,
            "hits": self._hits,
            "misses": self._misses,
            "detail": "process-local cache; not shared between workers",
        }


class RedisCache(CacheBackend):
    """Redis-backed cache.

    Failures are swallowed and logged: a cache error degrades the request to a
    database read rather than surfacing a 500.
    """

    backend = "redis"
    enabled = True

    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.redis_url
        self._client: Any | None = None
        self._connect_lock = asyncio.Lock()
        self._last_error: str | None = None
        self._hits = 0
        self._misses = 0

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._client is not None:
                return

            try:
                from redis.asyncio import Redis
            except ImportError as exc:
                self._last_error = "redis is not installed"
                logger.error(
                    "Redis is enabled but the redis package is not installed; "
                    "install requirements or set REDIS_ENABLED=false",
                    extra={"reason": str(exc)},
                )
                raise

            client = Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
            )

            await client.ping()

            self._client = client
            self._last_error = None

            # The URL can embed a password, so it is never logged.
            logger.info("Redis cache connected")

    async def close(self) -> None:
        client = self._client
        self._client = None

        if client is None:
            return

        try:
            await client.aclose()
        except AttributeError:  # pragma: no cover - older redis clients
            await client.close()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("Redis failed to close cleanly", extra={"reason": str(exc)})
        else:
            logger.info("Redis cache closed")

    async def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client

        try:
            await self.connect()
        except Exception as exc:
            self._last_error = str(exc)
            return None

        return self._client

    # -- operations ---------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        assert_tenant_scoped(key)

        client = await self._ensure_client()

        if client is None:
            return None

        try:
            raw = await client.get(key)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Cache read failed; falling back to database", extra={"reason": str(exc)})
            return None

        if raw is None:
            self._misses += 1
            return None

        self._hits += 1

        try:
            return _loads(raw)
        except (ValueError, TypeError):
            # Corrupt or stale-format entry: drop it and treat as a miss.
            logger.warning("Discarding undecodable cache entry")
            await self.delete(key)
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        assert_tenant_scoped(key)

        ttl = settings.cache_default_ttl_seconds if ttl_seconds is None else ttl_seconds

        # Refused before a connection is opened. See InMemoryCache.set for why a
        # non-positive TTL is a refusal rather than an unbounded write.
        if ttl <= 0:
            return False

        client = await self._ensure_client()

        if client is None:
            return False

        try:
            payload = _dumps(value)
        except (TypeError, ValueError) as exc:
            logger.warning("Value is not cacheable", extra={"reason": str(exc)})
            return False

        try:
            await client.set(key, payload, ex=ttl)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Cache write failed", extra={"reason": str(exc)})
            return False

        return True

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0

        for key in keys:
            assert_tenant_scoped(key)

        client = await self._ensure_client()

        if client is None:
            return 0

        try:
            return int(await client.delete(*keys))
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Cache delete failed", extra={"reason": str(exc)})
            return 0

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every key under ``prefix``.

        Uses ``SCAN`` rather than ``KEYS``: ``KEYS`` blocks the single-threaded
        server for the whole keyspace, which is unacceptable with real tenants
        on the instance. Deletes are batched to limit round trips.
        """
        assert_tenant_prefix(prefix)

        client = await self._ensure_client()

        if client is None:
            return 0

        deleted = 0
        batch: list[str] = []

        try:
            async for key in client.scan_iter(match=f"{prefix}*", count=500):
                batch.append(key)

                if len(batch) >= 200:
                    deleted += int(await client.delete(*batch))
                    batch.clear()

            if batch:
                deleted += int(await client.delete(*batch))
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Cache prefix invalidation failed", extra={"reason": str(exc)})
            return deleted

        return deleted

    async def health(self) -> dict[str, Any]:
        client = self._client

        if client is None:
            return {
                "backend": self.backend,
                "status": "disconnected",
                "detail": self._last_error or "not connected",
            }

        try:
            await client.ping()
        except Exception as exc:
            return {"backend": self.backend, "status": "error", "detail": str(exc)}

        return {
            "backend": self.backend,
            "status": "ok",
            "hits": self._hits,
            "misses": self._misses,
        }


# ---------------------------------------------------------------------------
# Module-level backend registry
# ---------------------------------------------------------------------------

_cache: CacheBackend | None = None


def build_cache() -> CacheBackend:
    """Choose a backend from configuration."""
    if settings.redis_enabled:
        return RedisCache()

    if settings.cache_local_fallback:
        return InMemoryCache()

    return NullCache()


async def init_cache() -> CacheBackend:
    """Create and connect the process-wide cache.

    An unreachable Redis degrades to the local fallback instead of blocking
    startup.
    """
    global _cache

    if _cache is not None:
        return _cache

    candidate = build_cache()

    try:
        await candidate.connect()
    except Exception as exc:
        logger.error(
            "Cache backend failed to start; falling back",
            extra={"backend": candidate.backend, "reason": str(exc)},
        )
        candidate = InMemoryCache() if settings.cache_local_fallback else NullCache()

    _cache = candidate
    logger.info("Cache ready", extra={"backend": _cache.backend})
    return _cache


def get_cache() -> CacheBackend:
    """Return the active backend, building a lazy one if startup was skipped."""
    global _cache

    if _cache is None:
        _cache = build_cache()

    return _cache


def set_cache(backend: CacheBackend | None) -> None:
    """Override the active backend. Intended for tests."""
    global _cache
    _cache = backend


async def shutdown_cache() -> None:
    global _cache

    if _cache is None:
        return

    await _cache.close()
    _cache = None
