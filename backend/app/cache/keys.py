"""Cache key construction.

Every key in this application is tenant-scoped. That is enforced structurally
rather than by convention: keys are only ever built through the helpers here,
each of which requires a ``business_id`` and refuses to build a key without one.

Key shape::

    cbpa:v1:{business_id}:{namespace}[:{discriminator}]

Putting the tenant ahead of the namespace means a whole tenant can be
invalidated with a single prefix scan, and it makes an accidental cross-tenant
read impossible to express — there is no key form that omits the business.

:func:`assert_tenant_scoped` is called by the cache backends on every read and
write, and :func:`assert_tenant_prefix` on every bulk delete, so even a
hand-built string cannot slip through.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

NAMESPACE = "cbpa"

#: Bump to invalidate every cached value at once, e.g. when a payload shape
#: changes in a deploy. Cheaper and safer than a mass delete.
KEY_VERSION = "v1"

SEPARATOR = ":"

_KEY_PREFIX = f"{NAMESPACE}{SEPARATOR}{KEY_VERSION}{SEPARATOR}"


class CacheKeyError(ValueError):
    """Raised when a cache key cannot be built safely."""


def _clean_business_id(business_id: Any) -> str:
    if business_id is None:
        raise CacheKeyError("business_id is required to build a cache key")

    candidate = str(business_id).strip()

    if not candidate:
        raise CacheKeyError("business_id is required to build a cache key")

    if SEPARATOR in candidate:
        # A colon in the tenant segment would let a crafted ID impersonate
        # another tenant's namespace.
        raise CacheKeyError("business_id must not contain a ':' separator")

    return candidate


def _clean_part(part: Any) -> str:
    candidate = str(part).strip()

    if not candidate:
        raise CacheKeyError("cache key parts must not be empty")

    return candidate.replace(SEPARATOR, "_").replace(" ", "_")


def tenant_prefix(business_id: Any) -> str:
    """Prefix covering every key owned by one tenant."""
    return f"{_KEY_PREFIX}{_clean_business_id(business_id)}{SEPARATOR}"


def tenant_key(business_id: Any, *parts: Any) -> str:
    """Build a tenant-scoped key from ``parts``."""
    if not parts:
        raise CacheKeyError("at least one key part is required")

    suffix = SEPARATOR.join(_clean_part(part) for part in parts)
    return f"{tenant_prefix(business_id)}{suffix}"


def fingerprint(values: dict[str, Any] | None) -> str:
    """Stable short hash of query parameters, for keys that vary by filter.

    Sorted keys make the hash order-independent, so ``?a=1&b=2`` and
    ``?b=2&a=1`` share a cache entry. ``None`` values are dropped so an absent
    filter and an explicitly-null filter agree.
    """
    if not values:
        return "all"

    filtered = {key: value for key, value in sorted(values.items()) if value is not None}

    if not filtered:
        return "all"

    encoded = json.dumps(filtered, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def assert_tenant_scoped(key: str) -> None:
    """Reject any key that is not tenant-scoped.

    Called by every cache backend before a read or write. This is the last line
    of defence against cross-tenant cache leakage.
    """
    if not isinstance(key, str) or not key.startswith(_KEY_PREFIX):
        raise CacheKeyError(f"cache key must start with '{_KEY_PREFIX}'")

    remainder = key[len(_KEY_PREFIX) :]
    business_segment, _, rest = remainder.partition(SEPARATOR)

    if not business_segment or not rest:
        raise CacheKeyError("cache key must contain a business_id and a namespace")


def assert_tenant_prefix(prefix: str) -> None:
    """Reject any invalidation prefix that is not confined to a single tenant.

    A prefix is not a key, so :func:`assert_tenant_scoped` is the wrong check
    here: :func:`tenant_prefix` legitimately stops at the tenant segment, and
    that function requires a namespace after it. The property that matters for a
    bulk delete is narrower — the prefix must name exactly one tenant.

    This exists because ``delete_prefix`` is the one cache operation whose blast
    radius is unbounded. ``""``, ``"cbpa:v1:"`` and ``"*"`` are all prefixes that
    would clear every tenant's entries at once, and none of them is rejected by
    a check written for whole keys.
    """
    if not isinstance(prefix, str) or not prefix.startswith(_KEY_PREFIX):
        raise CacheKeyError(f"cache prefix must start with '{_KEY_PREFIX}'")

    business_segment, separator, _ = prefix[len(_KEY_PREFIX) :].partition(SEPARATOR)

    if not business_segment:
        raise CacheKeyError("cache prefix must name a business_id")

    if not separator:
        # `cbpa:v1:biz` without the trailing colon also matches `biz-2`, so a
        # delete would reach a tenant whose id merely starts with this one's.
        raise CacheKeyError("cache prefix must terminate the business_id with ':'")


# ---------------------------------------------------------------------------
# Named keys
#
# Grouped by the read path they serve. Namespaces are also used as invalidation
# prefixes, so keep them stable.
# ---------------------------------------------------------------------------

DASHBOARD_NS = "dashboard"
CUSTOMERS_NS = "customers"
ANALYTICS_NS = "analytics"
INVENTORY_NS = "inventory"
PRODUCTS_NS = "products"
ORDERS_NS = "orders"
JOBS_NS = "jobs"


def dashboard_summary(business_id: Any) -> str:
    return tenant_key(business_id, DASHBOARD_NS, "summary")


def dashboard_revenue_trend(business_id: Any, days: int) -> str:
    return tenant_key(business_id, DASHBOARD_NS, "revenue_trend", days)


def customer_list(business_id: Any, filters: dict[str, Any] | None = None) -> str:
    return tenant_key(business_id, CUSTOMERS_NS, "list", fingerprint(filters))


def customer_detail(business_id: Any, customer_id: Any) -> str:
    return tenant_key(business_id, CUSTOMERS_NS, "detail", customer_id)


def rfm_summary(business_id: Any) -> str:
    return tenant_key(business_id, ANALYTICS_NS, "rfm")


def rfm_distribution(business_id: Any) -> str:
    return tenant_key(business_id, ANALYTICS_NS, "rfm_distribution")


def segment_summary(business_id: Any) -> str:
    return tenant_key(business_id, ANALYTICS_NS, "segments")


def product_analytics(business_id: Any, variant: str = "default") -> str:
    return tenant_key(business_id, ANALYTICS_NS, "product_analytics", variant)


def inventory_summary(business_id: Any) -> str:
    return tenant_key(business_id, INVENTORY_NS, "summary")


def inventory_low_stock(business_id: Any, threshold: Any) -> str:
    return tenant_key(business_id, INVENTORY_NS, "low_stock", threshold)


def product_list(business_id: Any, filters: dict[str, Any] | None = None) -> str:
    return tenant_key(business_id, PRODUCTS_NS, "list", fingerprint(filters))


def order_list(business_id: Any, filters: dict[str, Any] | None = None) -> str:
    return tenant_key(business_id, ORDERS_NS, "list", fingerprint(filters))


def job_status(business_id: Any, job_id: Any) -> str:
    return tenant_key(business_id, JOBS_NS, "status", job_id)


def job_list(business_id: Any) -> str:
    return tenant_key(business_id, JOBS_NS, "list")


# ---------------------------------------------------------------------------
# Invalidation groups
#
# Written as prefixes so one domain change clears everything derived from it.
# ---------------------------------------------------------------------------


def namespace_prefix(business_id: Any, namespace: str) -> str:
    return f"{tenant_prefix(business_id)}{_clean_part(namespace)}{SEPARATOR}"


def customer_write_prefixes(business_id: Any) -> list[str]:
    """Prefixes invalidated when customer data changes."""
    return [
        namespace_prefix(business_id, CUSTOMERS_NS),
        namespace_prefix(business_id, ANALYTICS_NS),
        namespace_prefix(business_id, DASHBOARD_NS),
    ]


def order_write_prefixes(business_id: Any) -> list[str]:
    """Prefixes invalidated when orders change.

    Orders feed RFM, so analytics and the dashboard both go stale.
    """
    return [
        namespace_prefix(business_id, ORDERS_NS),
        namespace_prefix(business_id, CUSTOMERS_NS),
        namespace_prefix(business_id, ANALYTICS_NS),
        namespace_prefix(business_id, DASHBOARD_NS),
    ]


def product_write_prefixes(business_id: Any) -> list[str]:
    return [
        namespace_prefix(business_id, PRODUCTS_NS),
        namespace_prefix(business_id, ANALYTICS_NS),
        namespace_prefix(business_id, DASHBOARD_NS),
    ]


def inventory_write_prefixes(business_id: Any) -> list[str]:
    return [
        namespace_prefix(business_id, INVENTORY_NS),
        namespace_prefix(business_id, DASHBOARD_NS),
    ]


def analytics_write_prefixes(business_id: Any) -> list[str]:
    return [
        namespace_prefix(business_id, ANALYTICS_NS),
        namespace_prefix(business_id, CUSTOMERS_NS),
        namespace_prefix(business_id, DASHBOARD_NS),
    ]
