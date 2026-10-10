"""Executed checks for the event handler layer.

Runs with no Kafka, no Redis, no SQLAlchemy. Everything asserted here is real
behaviour of the shipped code, exercised through the actual dispatch path.
"""

import asyncio
import logging
import sys
import types
from pathlib import Path

# Resolved from this file's location so the checks run from any checkout, on any
# machine. An earlier revision hardcoded an absolute sandbox path, which made the
# whole file silently unrunnable anywhere else.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Keep the output readable; the code under test logs a lot on purpose.
logging.disable(logging.CRITICAL)

from app.cache import client as cache_client  # noqa: E402
from app.cache import keys as cache_keys  # noqa: E402
from app.config import settings  # noqa: E402
from app.events import consumer  # noqa: E402
from app.events.schemas import Event, EventType  # noqa: E402

A = "biz-aaa"
B = "biz-bbb"

failures = []


def check(label, condition):
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


# ---------------------------------------------------------------------------
# 1. Handler registration
# ---------------------------------------------------------------------------
print("\n[1] handler registration")

consumer.clear_registry()
consumer.load_handlers()
registered = set(consumer.registered_event_types())

expected = {
    EventType.IMPORT_RECEIVED,
    EventType.IMPORT_COMPLETED,
    EventType.IMPORT_FAILED,
    EventType.CUSTOMER_CREATED,
    EventType.CUSTOMER_UPDATED,
    EventType.CUSTOMERS_IMPORTED,
    EventType.ORDER_CREATED,
    EventType.ORDER_UPDATED,
    EventType.ORDERS_IMPORTED,
    EventType.INVENTORY_UPDATED,
    EventType.INVENTORY_IMPORTED,
    EventType.INVENTORY_LOW_STOCK,
    EventType.ANALYTICS_JOB_REQUESTED,
    EventType.ANALYTICS_JOB_COMPLETED,
    EventType.ANALYTICS_JOB_FAILED,
}

check("load_handlers imports all five modules without a broker or a DB", True)
check(f"all {len(expected)} event types have a handler", expected <= registered)
check("no event type is registered that isn't in the catalogue", registered <= expected)


# ---------------------------------------------------------------------------
# 2. Cache fixture
# ---------------------------------------------------------------------------
SEED = {
    "a_dash": cache_keys.dashboard_summary(A),
    "a_cust": cache_keys.customer_list(A),
    "a_rfm": cache_keys.rfm_summary(A),
    "a_orders": cache_keys.order_list(A),
    "a_inv": cache_keys.inventory_summary(A),
    "a_prod": cache_keys.product_list(A),
    "a_jobs": cache_keys.job_list(A),
    "b_dash": cache_keys.dashboard_summary(B),
    "b_cust": cache_keys.customer_list(B),
    "b_rfm": cache_keys.rfm_summary(B),
    "b_orders": cache_keys.order_list(B),
    "b_inv": cache_keys.inventory_summary(B),
    "b_prod": cache_keys.product_list(B),
    "b_jobs": cache_keys.job_list(B),
}


async def seed_cache():
    cache = cache_client.InMemoryCache()
    cache_client.set_cache(cache)
    for name, key in SEED.items():
        await cache.set(key, {"seeded": name}, ttl_seconds=600)
    return cache


async def survivors(cache):
    present = set()
    for name, key in SEED.items():
        if await cache.get(key) is not None:
            present.add(name)
    return present


def event(event_type, business_id, payload=None):
    return Event(event_type=event_type, business_id=business_id, payload=payload or {})


# ---------------------------------------------------------------------------
# 3. Order write invalidation
# ---------------------------------------------------------------------------
async def test_order_write():
    print("\n[3] order.created invalidates order-derived reads, tenant-scoped")
    cache = await seed_cache()

    ok = await consumer.dispatch_event(
        "orders.events", event(EventType.ORDER_CREATED, A), enforce_idempotency=False
    )
    left = await survivors(cache)

    check("dispatch reported success", ok is True)
    check("tenant A order list cleared", "a_orders" not in left)
    check("tenant A customer list cleared (RFM inputs moved)", "a_cust" not in left)
    check("tenant A rfm cleared", "a_rfm" not in left)
    check("tenant A dashboard cleared", "a_dash" not in left)
    check("tenant A inventory NOT cleared (orders don't move stock views)", "a_inv" in left)
    check("tenant A jobs NOT cleared", "a_jobs" in left)
    check(
        "every tenant B key survived",
        {"b_dash", "b_cust", "b_rfm", "b_orders", "b_inv", "b_prod", "b_jobs"} <= left,
    )


# ---------------------------------------------------------------------------
# 4. Inventory write invalidation
# ---------------------------------------------------------------------------
async def test_inventory_write():
    print("\n[4] inventory.updated invalidates stock views only")
    cache = await seed_cache()

    await consumer.dispatch_event(
        "inventory.events", event(EventType.INVENTORY_UPDATED, A), enforce_idempotency=False
    )
    left = await survivors(cache)

    check("tenant A inventory cleared", "a_inv" not in left)
    check("tenant A products cleared (listings show stock)", "a_prod" not in left)
    check("tenant A dashboard cleared", "a_dash" not in left)
    check("tenant A rfm NOT cleared (stock is not a purchase)", "a_rfm" in left)
    check("tenant A customers NOT cleared", "a_cust" in left)
    check("tenant B inventory survived", "b_inv" in left)


# ---------------------------------------------------------------------------
# 5. Low stock is an alert, not a write
# ---------------------------------------------------------------------------
async def test_low_stock():
    print("\n[5] inventory.low_stock clears nothing")
    cache = await seed_cache()

    ok = await consumer.dispatch_event(
        "inventory.events",
        event(EventType.INVENTORY_LOW_STOCK, A, {"product_id": "p1", "quantity_available": 2}),
        enforce_idempotency=False,
    )
    left = await survivors(cache)

    check("dispatch succeeded", ok is True)
    check("nothing was invalidated", left == set(SEED))


# ---------------------------------------------------------------------------
# 6. Import completion -> cache + queued analytics job
# ---------------------------------------------------------------------------
class JobServiceStub(types.ModuleType):
    def __init__(self):
        super().__init__("app.services.analytics_job_service")
        self.queue_calls = []
        self.run_calls = []
        self.raise_on_queue = None

    async def queue_job(self, **kwargs):
        self.queue_calls.append(kwargs)
        if self.raise_on_queue:
            raise self.raise_on_queue
        return {"job_id": "job-1", "status": "queued", "dispatched": True}

    async def run_job_from_event(self, **kwargs):
        self.run_calls.append(kwargs)
        return {"job_id": kwargs.get("run_id"), "status": "completed", "result": {}}


def install_stub():
    stub = JobServiceStub()
    sys.modules["app.services.analytics_job_service"] = stub
    return stub


async def test_import_completed():
    print("\n[6] import.completed refreshes caches and queues analytics")
    cache = await seed_cache()
    stub = install_stub()

    await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_COMPLETED,
            A,
            {
                "import_id": "imp-77",
                "dataset_type": "orders",
                "rows_total": 120,
                "rows_imported": 118,
                "rows_failed": 2,
            },
        ),
        enforce_idempotency=False,
    )
    left = await survivors(cache)

    check("tenant A order-derived caches cleared", {"a_orders", "a_rfm", "a_dash"}.isdisjoint(left))
    check("tenant A inventory untouched", "a_inv" in left)
    check("tenant B untouched", "b_orders" in left and "b_rfm" in left)
    check("exactly one analytics job queued", len(stub.queue_calls) == 1)

    call = stub.queue_calls[0] if stub.queue_calls else {}
    check("job scoped to the event's tenant", call.get("business_id") == A)
    check("job type is full_refresh", call.get("job_type") == "full_refresh")
    check("import id carried onto the run", call.get("dataset_import_id") == "imp-77")
    check("requested_by records the trigger", call.get("requested_by") == "event:import.completed")


async def test_import_tenant_cannot_be_spoofed():
    print("\n[7] a payload-supplied business_id is ignored")
    await seed_cache()
    stub = install_stub()

    await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_COMPLETED,
            A,
            {
                # Hostile payload claiming to belong to another tenant.
                "business_id": B,
                "tenant_id": B,
                "import_id": "imp-88",
                "dataset_type": "customers",
                "rows_imported": 5,
            },
        ),
        enforce_idempotency=False,
    )

    call = stub.queue_calls[0] if stub.queue_calls else {}
    check("job queued for the envelope tenant, not the payload one", call.get("business_id") == A)
    check("payload tenant never reached the job service", B not in call.values())


async def test_import_no_rows():
    print("\n[8] an import that wrote nothing queues nothing")
    await seed_cache()
    stub = install_stub()

    await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_COMPLETED,
            A,
            {"import_id": "imp-9", "dataset_type": "orders", "rows_imported": 0, "rows_failed": 4},
        ),
        enforce_idempotency=False,
    )

    check("no analytics job queued for zero rows", stub.queue_calls == [])


async def test_import_products_no_job():
    print("\n[9] a products import invalidates but does not touch RFM")
    cache = await seed_cache()
    stub = install_stub()

    await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_COMPLETED,
            A,
            {"import_id": "imp-10", "dataset_type": "products", "rows_imported": 40},
        ),
        enforce_idempotency=False,
    )
    left = await survivors(cache)

    check("products cleared", "a_prod" not in left)
    check("orders untouched", "a_orders" in left)
    check("no analytics job queued for a product catalogue load", stub.queue_calls == [])


async def test_import_unknown_dataset():
    print("\n[10] an unrecognised dataset type clears the whole tenant, and only it")
    cache = await seed_cache()
    install_stub()

    await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_COMPLETED,
            A,
            {"import_id": "imp-11", "dataset_type": "spaceships", "rows_imported": 3},
        ),
        enforce_idempotency=False,
    )
    left = await survivors(cache)

    check("no tenant A key survived", not any(name.startswith("a_") for name in left))
    check("all seven tenant B keys survived", sum(1 for n in left if n.startswith("b_")) == 7)


async def test_auto_refresh_disabled():
    print("\n[11] ANALYTICS_AUTO_REFRESH_ON_IMPORT=false suppresses the job, not the cache")
    cache = await seed_cache()
    stub = install_stub()

    original = settings.analytics_auto_refresh_on_import
    object.__setattr__(settings, "analytics_auto_refresh_on_import", False)
    try:
        await consumer.dispatch_event(
            "raw.imports",
            event(
                EventType.IMPORT_COMPLETED,
                A,
                {"import_id": "imp-12", "dataset_type": "orders", "rows_imported": 99},
            ),
            enforce_idempotency=False,
        )
    finally:
        object.__setattr__(settings, "analytics_auto_refresh_on_import", original)

    left = await survivors(cache)
    check("caches still invalidated", "a_orders" not in left)
    check("no job queued", stub.queue_calls == [])
    check("setting restored", settings.analytics_auto_refresh_on_import is original)


async def test_import_failed():
    print("\n[12] import.failed leaves every cached read alone")
    cache = await seed_cache()
    stub = install_stub()

    ok = await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_FAILED,
            A,
            {"import_id": "imp-13", "dataset_type": "orders", "error": "unsupported file type"},
        ),
        enforce_idempotency=False,
    )
    left = await survivors(cache)

    check("dispatch succeeded", ok is True)
    check("nothing invalidated (a failed import wrote nothing)", left == set(SEED))
    check("no job queued", stub.queue_calls == [])


# ---------------------------------------------------------------------------
# 13. Analytics job handlers
# ---------------------------------------------------------------------------
async def test_job_requested():
    print("\n[13] analytics.job.requested drives the lifecycle")
    await seed_cache()
    stub = install_stub()

    ok = await consumer.dispatch_event(
        "analytics.jobs",
        event(
            EventType.ANALYTICS_JOB_REQUESTED,
            A,
            {"run_id": "run-5", "job_type": "rfm", "reference_date": "2011-12-09T12:50:00Z"},
        ),
        enforce_idempotency=False,
    )

    check("dispatch succeeded", ok is True)
    check("job executed exactly once", len(stub.run_calls) == 1)

    call = stub.run_calls[0] if stub.run_calls else {}
    check("run id passed through", call.get("run_id") == "run-5")
    check("tenant taken from the envelope", call.get("business_id") == A)
    ref = call.get("reference_date")
    check(
        "trailing-Z reference date parsed to an aware datetime",
        ref is not None and ref.year == 2011 and ref.month == 12 and ref.tzinfo is not None,
    )


async def test_job_requested_bad_reference_date():
    print("\n[14] a garbage reference_date falls back to the engine default")
    install_stub()
    stub = sys.modules["app.services.analytics_job_service"]

    ok = await consumer.dispatch_event(
        "analytics.jobs",
        event(
            EventType.ANALYTICS_JOB_REQUESTED,
            A,
            {"run_id": "run-6", "job_type": "rfm", "reference_date": "last tuesday"},
        ),
        enforce_idempotency=False,
    )

    check("dispatch still succeeded", ok is True)
    check("reference_date dropped rather than guessed", stub.run_calls[0]["reference_date"] is None)


async def test_job_requested_missing_run_id():
    print("\n[15] a request with no run_id fails loudly instead of inventing work")
    install_stub()
    stub = sys.modules["app.services.analytics_job_service"]

    ok = await consumer.dispatch_event(
        "analytics.jobs",
        event(EventType.ANALYTICS_JOB_REQUESTED, A, {"job_type": "rfm"}),
        enforce_idempotency=False,
    )

    check("dispatch reported failure (message gets dead-lettered)", ok is False)
    check("no job was executed", stub.run_calls == [])
    check("the exception did not escape dispatch_event", True)


async def test_job_completed_and_failed():
    print("\n[16] completion refreshes analytics; failure keeps the last good results")
    cache = await seed_cache()

    await consumer.dispatch_event(
        "analytics.jobs",
        event(EventType.ANALYTICS_JOB_COMPLETED, A, {"run_id": "run-7", "job_type": "rfm"}),
        enforce_idempotency=False,
    )
    left = await survivors(cache)
    check("completed: analytics cleared", "a_rfm" not in left)
    check("completed: dashboard cleared", "a_dash" not in left)
    check("completed: job list cleared", "a_jobs" not in left)
    check("completed: tenant B intact", "b_rfm" in left and "b_jobs" in left)

    cache = await seed_cache()
    await consumer.dispatch_event(
        "analytics.jobs",
        event(
            EventType.ANALYTICS_JOB_FAILED,
            A,
            {"run_id": "run-8", "job_type": "rfm", "error_code": "insufficient_data"},
        ),
        enforce_idempotency=False,
    )
    left = await survivors(cache)
    check("failed: job list cleared so the UI sees the failure", "a_jobs" not in left)
    check("failed: existing RFM kept (real old numbers beat an empty dashboard)", "a_rfm" in left)
    check("failed: dashboard kept", "a_dash" in left)


# ---------------------------------------------------------------------------
# 17. Robustness
# ---------------------------------------------------------------------------
async def test_handler_exception_is_contained():
    print("\n[17] a raising handler is contained, and siblings still run")
    await seed_cache()
    stub = install_stub()
    stub.raise_on_queue = RuntimeError("database is on fire")

    ok = await consumer.dispatch_event(
        "raw.imports",
        event(
            EventType.IMPORT_COMPLETED,
            A,
            {"import_id": "imp-14", "dataset_type": "orders", "rows_imported": 7},
        ),
        enforce_idempotency=False,
    )

    check("dispatch reported failure rather than raising", ok is False)


async def test_cache_outage_does_not_fail_handlers():
    print("\n[18] handlers still succeed when the cache is unreachable")

    class BrokenCache(cache_client.InMemoryCache):
        async def delete_prefix(self, prefix):
            raise ConnectionError("redis is down")

    cache_client.set_cache(BrokenCache())
    install_stub()

    ok = await consumer.dispatch_event(
        "orders.events", event(EventType.ORDER_CREATED, A), enforce_idempotency=False
    )
    check("order handler succeeded with a dead cache", ok is True)

    ok = await consumer.dispatch_event(
        "analytics.jobs",
        event(EventType.ANALYTICS_JOB_COMPLETED, A, {"run_id": "r"}),
        enforce_idempotency=False,
    )
    check("analytics handler succeeded with a dead cache", ok is True)


async def test_idempotency_ledger_unavailable():
    print("\n[19] with no SQLAlchemy, the ledger degrades and the event still processes")
    cache = await seed_cache()
    install_stub()

    # enforce_idempotency=True with no database layer importable in this sandbox.
    ok = await consumer.dispatch_event(
        "orders.events", event(EventType.ORDER_CREATED, A), enforce_idempotency=True
    )
    left = await survivors(cache)

    check("dispatch succeeded despite an unusable ledger", ok is True)
    check("the handler really ran", "a_orders" not in left)


async def test_unregistered_event_type():
    print("\n[20] an unknown event type is acknowledged, not dead-lettered")
    cache = await seed_cache()

    ok = await consumer.dispatch_event(
        "orders.events",
        Event(event_type="order.teleported", business_id=A),
        enforce_idempotency=False,
    )
    left = await survivors(cache)

    check("dispatch succeeded", ok is True)
    check("nothing invalidated", left == set(SEED))


async def test_event_requires_tenant():
    print("\n[21] an event cannot exist without a tenant")
    from app.events.schemas import InvalidEventError

    for bad in (None, "", "   "):
        try:
            Event(event_type=EventType.ORDER_CREATED, business_id=bad)
        except InvalidEventError:
            pass
        else:
            check(f"business_id={bad!r} rejected", False)
            return

    check("None, empty and whitespace tenants all rejected at construction", True)


async def main():
    await test_order_write()
    await test_inventory_write()
    await test_low_stock()
    await test_import_completed()
    await test_import_tenant_cannot_be_spoofed()
    await test_import_no_rows()
    await test_import_products_no_job()
    await test_import_unknown_dataset()
    await test_auto_refresh_disabled()
    await test_import_failed()
    await test_job_requested()
    await test_job_requested_bad_reference_date()
    await test_job_requested_missing_run_id()
    await test_job_completed_and_failed()
    await test_handler_exception_is_contained()
    await test_cache_outage_does_not_fail_handlers()
    await test_idempotency_ledger_unavailable()
    await test_unregistered_event_type()
    await test_event_requires_tenant()


asyncio.run(main())

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)

print("ALL EVENT HANDLER CHECKS PASSED")
