"""Domain reactions to events.

Each module registers handlers with :mod:`app.events.consumer` at import time via
the ``@handles`` decorator, so importing the module is what wires it up. That is
what :func:`app.events.consumer.load_handlers` does.

Two kinds of handler live here:

*cache invalidation* — a write happened, so derived reads are stale. These are
small, fast and idempotent by nature: deleting an already-deleted key is a no-op.

*work* — the analytics handler drives the job lifecycle. It is idempotent through
the ``processed_events`` ledger plus a status check on the job row itself.

Every handler reads its tenant from ``event.business_id``. There is no path here
that takes a business id from a payload field, so a crafted payload cannot make a
handler touch another tenant's data.

This package deliberately holds no imports of its own: the modules are imported
individually so a failure in one is attributable, and so nothing is loaded in
processes that do not consume events.
"""

from __future__ import annotations

__all__ = [
    "analytics_handlers",
    "customer_handlers",
    "import_handlers",
    "inventory_handlers",
    "order_handlers",
]
