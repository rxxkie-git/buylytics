"""Event consumer: handler registry, dispatch, and the Kafka consumer loop.

Dispatch is deliberately separated from transport. :func:`dispatch_event` takes
a topic and an :class:`~app.events.schemas.Event` and runs the registered
handlers — it does not care whether the event arrived from Kafka or from the
inline fallback. That means the whole handler layer is testable without a
broker.

Delivery guarantees
-------------------
Kafka gives at-least-once delivery, so handlers must be idempotent. Dispatch
enforces this centrally via the ``processed_events`` table: an event whose ID
has already been processed is acknowledged and skipped.

Handler failures are dead-lettered rather than retried forever, so one bad
message cannot block a partition.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from app.config import settings
from app.core.logging import get_logger, logging_context
from app.events import topics as topic_catalogue
from app.events.schemas import Event, InvalidEventError

logger = get_logger(__name__)

HandlerFn = Callable[[Event], Awaitable[None] | None]

_registry: dict[str, list[HandlerFn]] = {}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


def register_handler(event_type: str, handler_fn: HandlerFn) -> None:
    """Register a handler for an event type. Multiple handlers are allowed."""
    handlers = _registry.setdefault(event_type, [])

    if handler_fn not in handlers:
        handlers.append(handler_fn)


def handles(event_type: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator form of :func:`register_handler`."""

    def decorator(handler_fn: HandlerFn) -> HandlerFn:
        register_handler(event_type, handler_fn)
        return handler_fn

    return decorator


def registered_event_types() -> list[str]:
    return sorted(key for key, value in _registry.items() if value)


def clear_registry() -> None:
    """Reset the registry. Intended for tests."""
    _registry.clear()


def load_handlers() -> None:
    """Import handler modules so their decorators run.

    Called from application startup and from the worker entrypoint.
    """
    from app.events.handlers import (  # noqa: F401  (import registers handlers)
        analytics_handlers,
        customer_handlers,
        import_handlers,
        inventory_handlers,
        order_handlers,
    )

    logger.debug(
        "Event handlers loaded",
        extra={"event_types": registered_event_types()},
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _mark_event_started(event: Event, topic: str) -> bool:
    """Claim an event for processing.

    Returns ``True`` if this process should handle it, ``False`` if it was
    already handled (duplicate delivery).

    Runs synchronously against PostgreSQL; callers offload it to a thread. When
    no database is configured the check is skipped and the event is processed —
    dev environments without a DB have nothing to be idempotent about.

    Everything here is inside the ``try``, including the imports: the ledger is
    bookkeeping, and no failure of it — a missing driver, a dropped connection, a
    table that has not been migrated yet — may stop real work from happening.
    """
    session = None

    try:
        from app.db.session import SessionLocal

        if SessionLocal is None:
            return True

        from app.repositories.event_repository import EventRepository

        session = SessionLocal()
        repository = EventRepository(session)
        claimed = repository.claim_event(
            event_id=event.event_id,
            event_type=event.event_type,
            business_id=event.business_id,
            topic=topic,
        )
        session.commit()
        return claimed
    except Exception as exc:
        if session is not None:
            session.rollback()
        # A failing bookkeeping table must not stop real work; log and proceed.
        logger.warning(
            "Idempotency check unavailable; processing event anyway",
            extra={"reason": str(exc), **event.log_fields()},
        )
        return True
    finally:
        if session is not None:
            session.close()


def _mark_event_finished(event: Event, status: str, error: str | None = None) -> None:
    session = None

    try:
        from app.db.session import SessionLocal

        if SessionLocal is None:
            return

        from app.repositories.event_repository import EventRepository

        session = SessionLocal()
        EventRepository(session).complete_event(
            event_id=event.event_id, status=status, error_message=error
        )
        session.commit()
    except Exception as exc:
        if session is not None:
            session.rollback()
        logger.warning(
            "Could not record event completion",
            extra={"reason": str(exc), **event.log_fields()},
        )
    finally:
        if session is not None:
            session.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _run_handler(handler_fn: HandlerFn, event: Event) -> None:
    """Invoke a handler, moving sync handlers off the event loop.

    Analytics handlers do pandas work and synchronous SQLAlchemy queries. Run
    inline on the loop they would stall every other request in the process.
    """
    if inspect.iscoroutinefunction(handler_fn):
        await handler_fn(event)
        return

    result = await asyncio.to_thread(handler_fn, event)

    if inspect.isawaitable(result):
        await result


async def dispatch_event(topic: str, event: Event, *, enforce_idempotency: bool = True) -> bool:
    """Run every handler registered for ``event.event_type``.

    Returns ``True`` when all handlers succeeded (or the event was a duplicate
    and safely skipped), ``False`` when a handler failed.
    """
    logical_topic = topic_catalogue.logical_name(topic)
    handlers = list(_registry.get(event.event_type, ()))

    with logging_context(
        business_id=event.business_id,
        event_id=event.event_id,
        request_id=event.correlation_id,
    ):
        if not handlers:
            logger.debug(
                "No handler registered for event",
                extra={"topic": logical_topic, **event.log_fields()},
            )
            return True

        if enforce_idempotency:
            claimed = await asyncio.to_thread(_mark_event_started, event, logical_topic)
            if not claimed:
                logger.info(
                    "Duplicate event skipped",
                    extra={"topic": logical_topic, **event.log_fields()},
                )
                return True

        failures: list[str] = []

        for handler_fn in handlers:
            handler_name = getattr(handler_fn, "__qualname__", repr(handler_fn))
            try:
                await _run_handler(handler_fn, event)
            except Exception as exc:
                failures.append(f"{handler_name}: {exc}")
                logger.exception(
                    "Event handler failed",
                    extra={
                        "topic": logical_topic,
                        "handler": handler_name,
                        **event.log_fields(),
                    },
                )
            else:
                logger.info(
                    "Event handled",
                    extra={
                        "topic": logical_topic,
                        "handler": handler_name,
                        **event.log_fields(),
                    },
                )

        if failures:
            error_summary = "; ".join(failures)[:2000]
            if enforce_idempotency:
                await asyncio.to_thread(_mark_event_finished, event, "failed", error_summary)
            await _dead_letter(logical_topic, event, error_summary)
            return False

        if enforce_idempotency:
            await asyncio.to_thread(_mark_event_finished, event, "completed", None)

        return True


async def _dead_letter(topic: str, event: Event, reason: str) -> None:
    """Forward a failed event to the DLQ topic.

    Best effort: if the DLQ publish also fails we have already logged the
    original failure with full context, which is what an operator needs.
    """
    if not settings.kafka_enabled:
        logger.error(
            "Event failed and no DLQ is available (Kafka disabled)",
            extra={"topic": topic, "reason": reason, **event.log_fields()},
        )
        return

    from app.events.producer import publish_event

    dlq_event = Event(
        event_type=event.event_type,
        business_id=event.business_id,
        payload={
            "original_topic": topic,
            "original_event_id": event.event_id,
            "failure_reason": reason,
            "original_payload": event.payload,
        },
        correlation_id=event.correlation_id,
        source="worker",
    )

    result = await publish_event(topic_catalogue.DEAD_LETTER, dlq_event)

    if not result.delivered:
        logger.error(
            "Dead-letter publish failed",
            extra={"topic": topic, "reason": result.error, **event.log_fields()},
        )


# ---------------------------------------------------------------------------
# Kafka consumer loop
# ---------------------------------------------------------------------------


class KafkaEventConsumer:
    """Long-running consumer that feeds records into :func:`dispatch_event`.

    Offsets are committed manually *after* dispatch so a crash mid-handler
    causes redelivery rather than silent data loss. Combined with the
    idempotency table this yields effectively-once processing.
    """

    def __init__(self, subscribe_topics: list[str] | None = None) -> None:
        self._topics = subscribe_topics or topic_catalogue.qualified_worker_topics()
        self._consumer: Any | None = None
        self._running = False

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            logger.error(
                "Cannot start consumer: aiokafka is not installed. "
                "Install requirements or run with KAFKA_ENABLED=false."
            )
            raise

        consumer_kwargs: dict[str, Any] = {
            "bootstrap_servers": settings.kafka_bootstrap_servers,
            "group_id": settings.kafka_consumer_group,
            "client_id": f"{settings.kafka_client_id}-consumer",
            "auto_offset_reset": settings.kafka_auto_offset_reset,
            "enable_auto_commit": False,
            "security_protocol": settings.kafka_security_protocol,
        }

        if settings.kafka_sasl_mechanism:
            consumer_kwargs["sasl_mechanism"] = settings.kafka_sasl_mechanism
            consumer_kwargs["sasl_plain_username"] = settings.kafka_sasl_username
            consumer_kwargs["sasl_plain_password"] = settings.kafka_sasl_password

        self._consumer = AIOKafkaConsumer(*self._topics, **consumer_kwargs)
        await self._consumer.start()
        self._running = True

        logger.info(
            "Kafka consumer started",
            extra={
                "topics": self._topics,
                "group_id": settings.kafka_consumer_group,
                "bootstrap_servers": settings.kafka_bootstrap_servers,
            },
        )

    async def stop(self) -> None:
        self._running = False
        consumer = self._consumer
        self._consumer = None

        if consumer is None:
            return

        try:
            await consumer.stop()
            logger.info("Kafka consumer stopped")
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("Kafka consumer failed to stop cleanly", extra={"reason": str(exc)})

    async def run(self) -> None:
        if self._consumer is None:
            await self.start()

        assert self._consumer is not None

        try:
            async for record in self._consumer:
                if not self._running:
                    break

                await self._handle_record(record)
        except asyncio.CancelledError:
            logger.info("Consumer loop cancelled")
            raise
        finally:
            await self.stop()

    async def _handle_record(self, record: Any) -> None:
        try:
            event = Event.from_json(record.value)
        except InvalidEventError as exc:
            # Unparseable message: commit past it so it cannot block the
            # partition. The raw bytes are not logged — they may contain
            # customer data.
            logger.error(
                "Skipping malformed event",
                extra={
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                    "reason": str(exc),
                },
            )
            await self._commit(record)
            return

        await dispatch_event(record.topic, event)
        await self._commit(record)

    async def _commit(self, record: Any) -> None:
        if self._consumer is None:
            return

        try:
            await self._consumer.commit()
        except Exception as exc:
            logger.warning(
                "Offset commit failed; event may be redelivered",
                extra={
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                    "reason": str(exc),
                },
            )
