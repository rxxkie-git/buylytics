"""Caching layer.

PostgreSQL is the source of truth; Redis is only ever an accelerator. Every read
path here degrades to a database query on a miss, an outage, or a disabled
backend, so the application is correct with or without Redis running.

Modules
-------
``keys``
    Tenant-scoped key construction. There is no key form without a
    ``business_id``, which is what makes cross-tenant leakage unrepresentable.
``client``
    Backends (``RedisCache``, ``InMemoryCache``, ``NullCache``) and the
    process-wide registry.
``service``
    Cache-aside read helper and prefix invalidation used by services.
"""

from __future__ import annotations

from app.cache.client import (
    CacheBackend,
    InMemoryCache,
    NullCache,
    RedisCache,
    build_cache,
    get_cache,
    init_cache,
    set_cache,
    shutdown_cache,
)
from app.cache.keys import (
    CacheKeyError,
    analytics_write_prefixes,
    assert_tenant_scoped,
    customer_write_prefixes,
    fingerprint,
    inventory_write_prefixes,
    namespace_prefix,
    order_write_prefixes,
    product_write_prefixes,
    tenant_key,
    tenant_prefix,
)
from app.cache.service import (
    get_or_set,
    invalidate_keys,
    invalidate_prefixes,
    set_value,
)

__all__ = [
    "CacheBackend",
    "CacheKeyError",
    "InMemoryCache",
    "NullCache",
    "RedisCache",
    "analytics_write_prefixes",
    "assert_tenant_scoped",
    "build_cache",
    "customer_write_prefixes",
    "fingerprint",
    "get_cache",
    "get_or_set",
    "init_cache",
    "invalidate_keys",
    "invalidate_prefixes",
    "inventory_write_prefixes",
    "namespace_prefix",
    "order_write_prefixes",
    "product_write_prefixes",
    "set_cache",
    "set_value",
    "shutdown_cache",
    "tenant_key",
    "tenant_prefix",
]
