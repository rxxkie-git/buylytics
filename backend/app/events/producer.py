"""Event producer.

Three interchangeable implementations behind one interface:

``KafkaEventProducer``
    Real ``aiokafka`` producer. Used when ``KAFKA_ENABLED=true``.
``InlineEventProducer``
    No broker. Dispatches each event to the local handler registry as a
    tracked asyncio task, so async job semantics still hold on a laptop with
    no infrastructure running. This is the development default.
``NullEventProducer``
    Drops events with a log line. Used when Kafka is disabled *and* inline
    fallback is switched off.

``aiokafka`` is imported lazily inside the connect path. The module therefore
imports cleanly in an environment where the library is not installed, and a
misconfigured broker degrades to the fallback instead of blocking startup.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.logging import get_logger
from app.events import topics as topic_catalogue
from app.events.schemas import Event

logger = get_logger(__name__)


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a publish attempt.

    Publishing is best-effort by design: the database write is the source of
    truth, so a broker outage must not fail the user's request. Callers that
    genuinely need delivery confirmation inspect ``delivered``.
    """

    delivered: bool
    transport: str
    topic: str
    event_id: str
    error: str | None = None


class EventProducer:
    """Interface implemented by every producer."""

    transport: str = "unknown"

    async def start(self) -> None:  # pragma: no cover - trivial
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial
        return None

    async def publish(self, topic: str, event: Event) -> PublishResult:
        raise NotImplementedError

    async def health(self) -> dict[str, Any]:
        return {"transport": self.transport, "status": "ok"}


class NullEventProducer(EventProducer):
    transport = "disabled"

    async def publish(self, topic: str, event: Event) -> PublishResult:
        logger.debug(
            "Event dropped (event bus disabled)",
            extra={"topic": topic, **event.log_fields()},
        )
        return PublishResult(
            delivered=False,
            transport=self.transport,
            topic=topic,
            event_id=event.event_id,
            error="event bus disabled",
        )

    async def health(self) -> dict[str, Any]:
        return {"transport": self.transport, "status": "disabled"}


class InlineEventProducer(EventProducer):
    """Execute handlers in-process instead of going through a broker.

    Each event becomes a tracked asyncio task, so the HTTP request returns
    immediately and the job progresses in the background exactly as it would
    with a real consumer. :meth:`drain` lets tests await completion
    deterministically.
    """

    transport = "inline"

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    async def publish(self, topic: str, event: Event) -> PublishResult:
        # Imported here to avoid a circular import: handlers import the
        # producer to emit follow-up events.
        from app.events.consumer import dispatch_event

        logical_topic = topic_catalogue.logical_name(topic)

        try:
            task = asyncio.create_task(
                dispatch_event(logical_topic, event),
                name=f"inline-event:{event.event_type}:{event.event_id[:8]}",
            )
        except RuntimeError as exc:
            # No running loop (e.g. called from sync code outside the app).
            logger.warning(
                "Inline event dispatch could not be scheduled",
                extra={"topic": logical_topic, "reason": str(exc), **event.log_fields()},
            )
            return PublishResult(
                delivered=False,
                transport=self.transport,
                topic=logical_topic,
                event_id=event.event_id,
                error="no running event loop",
            )

        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

        logger.info(
            "Event dispatched inline",
            extra={"topic": logical_topic, **event.log_fields()},
        )

        return PublishResult(
            delivered=True,
            transport=self.transport,
            topic=logical_topic,
            event_id=event.event_id,
        )

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)

        if task.cancelled():
            return

        exception = task.exception()
        if exception is not None:
            # dispatch_event already records handler failures; this catches
            # anything that escaped it so a background error is never silent.
            logger.error(
                "Inline event task raised",
                exc_info=exception,
                extra={"task_name": task.get_name()},
            )

    async def drain(self, timeout: float = 10.0) -> bool:
        """Await in-flight handlers. Returns ``True`` if all finished in time."""
        if not self._tasks:
            return True

        pending = list(self._tasks)
        done, still_pending = await asyncio.wait(pending, timeout=timeout)
        return not still_pending

    async def stop(self) -> None:
        completed = await self.drain(timeout=10.0)
        if not completed:
            logger.warning(
                "Inline event tasks still running at shutdown",
                extra={"pending_count": len(self._tasks)},
            )

    @property
    def pending_count(self) -> int:
        return len(self._tasks)

    async def health(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "status": "ok",
            "pending_tasks": len(self._tasks),
        }


class KafkaEventProducer(EventProducer):
    """Real Kafka producer built on ``aiokafka``.

    Every path into the broker is bounded by wall-clock time. That is the whole
    point of this class's timeout machinery, so it is worth stating what the
    unbounded version cost.

    ``aiokafka`` defaults ``request_timeout_ms`` to 40 s, and that is *per
    operation*. ``publish`` wrapped ``send_and_wait`` in a retry loop, so with
    ``kafka_max_retries=3`` the arithmetic worst case was 3 x 40 s of transport
    waiting plus 1 s + 2 s of backoff — over two minutes, against an nginx
    ``proxy_read_timeout`` of 60 s. A broker that accepted TCP connections but
    never answered therefore produced a 504 on every request that published an
    event, and the request kept running server-side after nginx had already given
    up on it.

    Three bounds fix that:

    ``request_timeout_ms``
        Transport-level. Stops a single broker call from hanging. This is the only
        transport bound aiokafka takes as a constructor argument; there is
        deliberately no ``max_block_ms`` here. That name comes from the Java
        client and kafka-python, where ``send()`` is blocking and needs its own
        ceiling for the metadata and buffer-space wait. ``aiokafka`` is
        coroutine-based — ``send()`` awaits instead of blocking — and
        ``AIOKafkaProducer.__init__`` has no such parameter and no ``**kwargs``,
        so passing it raises ``TypeError`` and, because
        :func:`init_event_producer` degrades on any exception, that surfaces as a
        permanent silent fallback to the inline transport rather than as a crash.
        The wait it was meant to bound is covered by
        ``kafka_publish_timeout_seconds`` below.
    ``kafka_publish_timeout_seconds``
        Wraps the *entire* retry loop. A per-attempt timeout multiplied by a
        retry count is not a bound on the operation; this is.
    ``kafka_startup_timeout_seconds``
        Wraps ``producer.start()``, which performs the initial metadata fetch.

    Plus a cooldown. Without it, a dead broker is worse than slow: ``publish``
    lazily calls ``start()``, ``start()`` serialises on ``_start_lock``, and the
    hundredth concurrent request waits for ninety-nine startup timeouts ahead of
    it. After a failure the producer refuses to redial until
    ``kafka_retry_backoff_seconds`` has elapsed, and events degrade to
    ``delivered=False`` immediately instead of queueing.
    """

    transport = "kafka"

    def __init__(self) -> None:
        self._producer: Any | None = None
        self._start_lock = asyncio.Lock()
        self._last_error: str | None = None
        #: Monotonic deadline before which start() will not be retried.
        self._retry_not_before: float = 0.0

    # -- lifecycle ----------------------------------------------------------

    def _cooling_down(self) -> bool:
        return asyncio.get_running_loop().time() < self._retry_not_before

    def _begin_cooldown(self, error: str) -> None:
        self._last_error = error
        loop = asyncio.get_running_loop()
        # Deliberately short. Long enough to stop a redial storm, short enough
        # that a broker coming back is picked up within seconds.
        self._retry_not_before = loop.time() + max(
            1.0, settings.kafka_retry_backoff_seconds
        )

    async def start(self) -> None:
        async with self._start_lock:
            if self._producer is not None:
                return

            try:
                from aiokafka import AIOKafkaProducer
            except ImportError as exc:
                self._last_error = "aiokafka is not installed"
                logger.error(
                    "Kafka is enabled but aiokafka is not installed; "
                    "install it or set KAFKA_ENABLED=false",
                    extra={"reason": str(exc)},
                )
                raise

            producer_kwargs: dict[str, Any] = {
                "bootstrap_servers": settings.kafka_bootstrap_servers,
                "client_id": settings.kafka_client_id,
                "acks": "all",
                "enable_idempotence": True,
                "security_protocol": settings.kafka_security_protocol,
                # Bounded transport waits. See the class docstring — in
                # particular, aiokafka takes no `max_block_ms`; the wait that
                # option bounds in the Java client is covered here by the
                # asyncio.wait_for around the whole publish.
                "request_timeout_ms": settings.kafka_request_timeout_ms,
                # Small batching window: enough to coalesce a burst of events
                # from one import, short enough not to add perceptible latency.
                "linger_ms": 5,
                # Refresh topic metadata often enough that a broker restart or a
                # container getting a new IP is noticed in seconds rather than
                # after the 5-minute default.
                "metadata_max_age_ms": 30_000,
            }

            if settings.kafka_sasl_mechanism:
                producer_kwargs["sasl_mechanism"] = settings.kafka_sasl_mechanism
                producer_kwargs["sasl_plain_username"] = settings.kafka_sasl_username
                producer_kwargs["sasl_plain_password"] = settings.kafka_sasl_password

            producer = AIOKafkaProducer(**producer_kwargs)

            try:
                # producer.start() performs the initial metadata fetch, which is
                # where an unreachable-but-connectable broker stalls.
                await asyncio.wait_for(
                    producer.start(), timeout=settings.kafka_startup_timeout_seconds
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                # Close the half-built producer so its sockets and background
                # tasks do not leak on every subsequent attempt.
                await self._discard(producer)
                message = (
                    f"broker did not respond within "
                    f"{settings.kafka_startup_timeout_seconds:g}s"
                )
                self._begin_cooldown(message)
                logger.error(
                    "Kafka producer start timed out",
                    extra={
                        "bootstrap_servers": settings.kafka_bootstrap_servers,
                        "timeout_seconds": settings.kafka_startup_timeout_seconds,
                    },
                )
                raise TimeoutError(message) from exc
            except Exception as exc:
                await self._discard(producer)
                self._begin_cooldown(str(exc))
                raise

            self._producer = producer
            self._last_error = None
            self._retry_not_before = 0.0

            # Log the endpoint but never the SASL credentials.
            #
            # Only bounds that are genuinely in force are logged. Reporting a
            # setting that was not applied is how a misconfiguration survives a
            # log review.
            logger.info(
                "Kafka producer started",
                extra={
                    "bootstrap_servers": settings.kafka_bootstrap_servers,
                    "security_protocol": settings.kafka_security_protocol,
                    "sasl_mechanism": settings.kafka_sasl_mechanism or "none",
                    "request_timeout_ms": settings.kafka_request_timeout_ms,
                    "publish_timeout_seconds": settings.kafka_publish_timeout_seconds,
                    "startup_timeout_seconds": settings.kafka_startup_timeout_seconds,
                },
            )

    @staticmethod
    async def _discard(producer: Any) -> None:
        """Best-effort close of a producer that failed to start.

        Bounded, because the thing being cleaned up is by definition already
        unresponsive — an unbounded close here would reintroduce the hang the
        timeout above just prevented.
        """
        try:
            await asyncio.wait_for(producer.stop(), timeout=5.0)
        except Exception:
            # Nothing useful to do: the producer is being thrown away either way.
            pass

    async def stop(self) -> None:
        producer = self._producer
        self._producer = None

        if producer is None:
            return

        try:
            # Bounded so a wedged broker cannot hold up application shutdown and
            # get the container SIGKILLed mid-flush.
            await asyncio.wait_for(producer.stop(), timeout=10.0)
            logger.info("Kafka producer stopped")
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                "Kafka producer did not stop within 10s; abandoning it",
                extra={"bootstrap_servers": settings.kafka_bootstrap_servers},
            )
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("Kafka producer failed to stop cleanly", extra={"reason": str(exc)})

    # -- publishing ---------------------------------------------------------

    def _unavailable(self, topic: str, event: Event, reason: str) -> PublishResult:
        logger.warning(
            "Event not published: broker unavailable",
            extra={"topic": topic, "reason": reason, **event.log_fields()},
        )
        return PublishResult(
            delivered=False,
            transport=self.transport,
            topic=topic,
            event_id=event.event_id,
            error=reason,
        )

    async def publish(self, topic: str, event: Event) -> PublishResult:
        qualified_topic = topic_catalogue.qualified_name(topic)
        loop = asyncio.get_running_loop()
        # One deadline for the whole operation, including the lazy start and
        # every retry. This is the bound that actually matters to the caller.
        deadline = loop.time() + max(0.1, settings.kafka_publish_timeout_seconds)

        if self._producer is None:
            if self._cooling_down():
                # Fail fast rather than making this request pay the startup
                # timeout again, and again for every request queued behind it.
                return self._unavailable(
                    qualified_topic,
                    event,
                    f"producer unavailable: {self._last_error or 'broker unreachable'}",
                )

            try:
                await asyncio.wait_for(
                    self.start(), timeout=max(0.1, deadline - loop.time())
                )
            except (TimeoutError, asyncio.TimeoutError):
                self._begin_cooldown("broker unreachable")
                return self._unavailable(
                    qualified_topic, event, "producer unavailable: start timed out"
                )
            except Exception as exc:
                return self._unavailable(
                    qualified_topic, event, f"producer unavailable: {exc}"
                )

        producer = self._producer

        if producer is None:  # pragma: no cover - start() succeeded but raced a stop()
            return self._unavailable(
                qualified_topic, event, "producer unavailable: not connected"
            )

        attempts = max(1, settings.kafka_max_retries)
        backoff = max(0.0, settings.kafka_retry_backoff_seconds)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            remaining = deadline - loop.time()

            if remaining <= 0:
                last_error = last_error or TimeoutError(
                    f"publish deadline of {settings.kafka_publish_timeout_seconds:g}s "
                    "elapsed"
                )
                break

            try:
                await asyncio.wait_for(
                    producer.send_and_wait(
                        qualified_topic,
                        value=event.to_bytes(),
                        key=event.partition_key.encode("utf-8"),
                    ),
                    timeout=remaining,
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                # The deadline is shared across attempts, so a timeout here means
                # the whole budget is gone. Retrying cannot help.
                last_error = exc
                logger.warning(
                    "Kafka publish timed out",
                    extra={
                        "topic": qualified_topic,
                        "attempt": attempt,
                        "timeout_seconds": round(remaining, 3),
                        **event.log_fields(),
                    },
                )
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Kafka publish attempt failed",
                    extra={
                        "topic": qualified_topic,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "reason": str(exc),
                        **event.log_fields(),
                    },
                )

                if attempt < attempts:
                    # Never sleep past the deadline.
                    nap = min(backoff * attempt, max(0.0, deadline - loop.time()))

                    if nap <= 0:
                        break

                    await asyncio.sleep(nap)
            else:
                logger.info(
                    "Event published",
                    extra={"topic": qualified_topic, "attempt": attempt, **event.log_fields()},
                )
                self._last_error = None
                return PublishResult(
                    delivered=True,
                    transport=self.transport,
                    topic=qualified_topic,
                    event_id=event.event_id,
                )

        self._last_error = str(last_error)
        # Enter the cooldown so the next request is not charged for this outage.
        self._begin_cooldown(str(last_error))
        logger.error(
            "Event publish failed within its deadline; database state is unaffected",
            extra={
                "topic": qualified_topic,
                "attempts": attempts,
                "deadline_seconds": settings.kafka_publish_timeout_seconds,
                "reason": str(last_error),
                **event.log_fields(),
            },
        )

        return PublishResult(
            delivered=False,
            transport=self.transport,
            topic=qualified_topic,
            event_id=event.event_id,
            error=str(last_error),
        )

    async def health(self) -> dict[str, Any]:
        if self._producer is None:
            return {
                "transport": self.transport,
                "status": "disconnected",
                "bootstrap_servers": settings.kafka_bootstrap_servers,
                "detail": self._last_error,
            }

        return {
            "transport": self.transport,
            "status": "ok",
            "bootstrap_servers": settings.kafka_bootstrap_servers,
        }


# ---------------------------------------------------------------------------
# Module-level producer registry
# ---------------------------------------------------------------------------

_producer: EventProducer | None = None


def build_producer() -> EventProducer:
    """Choose a producer implementation from configuration."""
    if settings.kafka_enabled:
        return KafkaEventProducer()

    if settings.events_inline_fallback:
        return InlineEventProducer()

    return NullEventProducer()


async def init_event_producer() -> EventProducer:
    """Create and start the process-wide producer.

    A broker that is unreachable at startup degrades to the inline fallback
    rather than preventing the API from serving traffic — PostgreSQL is the
    source of truth and remains fully functional.
    """
    global _producer

    if _producer is not None:
        return _producer

    candidate = build_producer()

    try:
        await candidate.start()
    except Exception as exc:
        logger.error(
            "Event bus failed to start; falling back",
            extra={"transport": candidate.transport, "reason": str(exc)},
        )
        candidate = InlineEventProducer() if settings.events_inline_fallback else NullEventProducer()
        await candidate.start()

    _producer = candidate
    logger.info("Event bus ready", extra={"transport": _producer.transport})
    return _producer


def get_event_producer() -> EventProducer:
    """Return the active producer, building a lazy one if startup was skipped."""
    global _producer

    if _producer is None:
        _producer = build_producer()

    return _producer


def set_event_producer(producer: EventProducer | None) -> None:
    """Override the active producer. Intended for tests."""
    global _producer
    _producer = producer


async def shutdown_event_producer() -> None:
    global _producer

    if _producer is None:
        return

    await _producer.stop()
    _producer = None


async def publish_event(topic: str, event: Event) -> PublishResult:
    """Publish through the active producer. Never raises."""
    producer = get_event_producer()

    try:
        return await producer.publish(topic, event)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            "Unexpected error publishing event",
            exc_info=exc,
            extra={"topic": topic, **event.log_fields()},
        )
        return PublishResult(
            delivered=False,
            transport=producer.transport,
            topic=topic,
            event_id=event.event_id,
            error=str(exc),
        )
