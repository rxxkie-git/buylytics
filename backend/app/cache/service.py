"""Cache-aside helpers.

Read paths use :func:`get_or_set`; write paths use :func:`invalidate_prefixes`.
Both are safe to call unconditionally — with caching disabled the loader simply
runs every time.

Design rules enforced here:

* A cache failure never fails a request. Errors are logged and the loader result
  is returned.
* ``None`` is not cached. Distinguishing "cached null" from "not cached" adds a
  sentinel value for no real benefit at this scale, and it means a failed
  computation is never memoised.
* Every key passes through :func:`app.cache.keys.assert_tenant_scoped` inside the
  backend, so no read or write can escape its tenant.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Iterable, TypeVar

from app.cache.client import get_cache
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

Loader = Callable[[], "T | Awaitable[T]"]


async def _call_loader(loader: Loader) -> Any:
    """Run a loader, moving blocking work off the event loop.

    Loaders are usually synchronous SQLAlchemy or pandas calls, which would stall
    every other request if awaited directly on the loop.
    """
    if inspect.iscoroutinefunction(loader):
        return await loader()

    result = loader()

    if inspect.isawaitable(result):
        return await result

    return result


async def _call_loader_threaded(loader: Loader) -> Any:
    if inspect.iscoroutinefunction(loader):
        return await loader()

    result = await asyncio.to_thread(loader)

    if inspect.isawaitable(result):
        return await result

    return result


async def get_or_set(
    key: str,
    loader: Loader,
    ttl_seconds: int | None = None,
    *,
    offload: bool = True,
) -> Any:
    """Return the cached value for ``key``, computing and storing it on a miss.

    ``offload`` runs a synchronous loader in a worker thread. Pass ``False`` when
    the loader is cheap and known not to block.
    """
    cache = get_cache()

    if cache.enabled:
        try:
            cached_value = await cache.get(key)
        except Exception as exc:  # pragma: no cover - backends already guard
            cached_value = None
            logger.warning("Cache lookup failed; using database", extra={"reason": str(exc)})

        if cached_value is not None:
            return cached_value

    value = await (_call_loader_threaded(loader) if offload else _call_loader(loader))

    if cache.enabled and value is not None:
        try:
            await cache.set(key, value, ttl_seconds)
        except Exception as exc:  # pragma: no cover - backends already guard
            logger.warning("Cache store failed", extra={"reason": str(exc)})

    return value


async def get(key: str) -> Any | None:
    """Read a single key, returning ``None`` on miss or on any cache error."""
    cache = get_cache()

    if not cache.enabled:
        return None

    try:
        return await cache.get(key)
    except Exception as exc:  # pragma: no cover - backends already guard
        logger.warning("Cache lookup failed", extra={"reason": str(exc)})
        return None


async def set_value(key: str, value: Any, ttl_seconds: int | None = None) -> bool:
    cache = get_cache()

    if not cache.enabled or value is None:
        return False

    try:
        return await cache.set(key, value, ttl_seconds)
    except Exception as exc:  # pragma: no cover - backends already guard
        logger.warning("Cache store failed", extra={"reason": str(exc)})
        return False


async def invalidate_keys(*keys: str) -> int:
    cache = get_cache()

    if not cache.enabled or not keys:
        return 0

    try:
        return await cache.delete(*keys)
    except Exception as exc:  # pragma: no cover - backends already guard
        logger.warning("Cache invalidation failed", extra={"reason": str(exc)})
        return 0


async def invalidate_prefixes(prefixes: Iterable[str]) -> int:
    """Delete every key under each prefix.

    Prefixes come from the ``*_write_prefixes`` helpers in
    :mod:`app.cache.keys`, which always start with the tenant segment, so an
    invalidation can never reach another tenant's keys.
    """
    cache = get_cache()
    prefix_list = [prefix for prefix in prefixes if prefix]

    if not cache.enabled or not prefix_list:
        return 0

    deleted = 0

    for prefix in prefix_list:
        try:
            deleted += await cache.delete_prefix(prefix)
        except Exception as exc:  # pragma: no cover - backends already guard
            logger.warning("Cache invalidation failed", extra={"reason": str(exc)})

    if deleted:
        logger.debug("Cache invalidated", extra={"keys_removed": deleted, "prefixes": len(prefix_list)})

    return deleted
