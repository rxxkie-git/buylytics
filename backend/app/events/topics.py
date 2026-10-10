"""Kafka topic catalogue.

Topic names are centralised so no module invents its own string. An optional
``KAFKA_TOPIC_PREFIX`` namespaces every topic, which lets several environments
(dev/staging) share one broker without cross-talk.
"""

from __future__ import annotations

from app.config import settings

# ---------------------------------------------------------------------------
# Logical topic names (unprefixed)
# ---------------------------------------------------------------------------

RAW_IMPORTS = "raw.imports"
CUSTOMER_EVENTS = "customers.events"
ORDER_EVENTS = "orders.events"
INVENTORY_EVENTS = "inventory.events"
ANALYTICS_JOBS = "analytics.jobs"

#: Failed events land here after retries are exhausted so they are not lost.
DEAD_LETTER = "events.dlq"

ALL_TOPICS: tuple[str, ...] = (
    RAW_IMPORTS,
    CUSTOMER_EVENTS,
    ORDER_EVENTS,
    INVENTORY_EVENTS,
    ANALYTICS_JOBS,
    DEAD_LETTER,
)

#: Topics a worker subscribes to. The DLQ is intentionally excluded — it is
#: drained deliberately by an operator, not automatically re-consumed.
WORKER_TOPICS: tuple[str, ...] = (
    RAW_IMPORTS,
    CUSTOMER_EVENTS,
    ORDER_EVENTS,
    INVENTORY_EVENTS,
    ANALYTICS_JOBS,
)


def qualified_name(topic: str, prefix: str | None = None) -> str:
    """Apply the configured environment prefix to a logical topic name."""
    resolved_prefix = settings.kafka_topic_prefix if prefix is None else prefix
    resolved_prefix = (resolved_prefix or "").strip().strip(".")

    if not resolved_prefix:
        return topic

    return f"{resolved_prefix}.{topic}"


def logical_name(topic: str, prefix: str | None = None) -> str:
    """Inverse of :func:`qualified_name` — used when routing a consumed record."""
    resolved_prefix = settings.kafka_topic_prefix if prefix is None else prefix
    resolved_prefix = (resolved_prefix or "").strip().strip(".")

    if resolved_prefix and topic.startswith(f"{resolved_prefix}."):
        return topic[len(resolved_prefix) + 1 :]

    return topic


def qualified_worker_topics(prefix: str | None = None) -> list[str]:
    return [qualified_name(topic, prefix) for topic in WORKER_TOPICS]
