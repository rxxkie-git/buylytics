"""Standalone event worker.

    python -m app.events.worker

Runs the Kafka consumer loop in its own process, separate from the API. That
separation is the point of the event bus: a long analytics job saturating this
process does not slow down HTTP traffic.

The worker is a *modular monolith* worker, not a microservice — it imports the
same application code as the API and talks to the same database. There is no
second deployable to keep in sync.

With ``KAFKA_ENABLED=false`` there is nothing to consume: the API dispatches
events inline in-process. The worker says so and exits 0 rather than idling, so a
process supervisor does not restart-loop on a development machine.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.events import topics as topic_catalogue

logger = get_logger(__name__)


async def _run() -> int:
    from app.cache import init_cache, shutdown_cache
    from app.events.consumer import KafkaEventConsumer, load_handlers, registered_event_types
    from app.events.producer import init_event_producer, shutdown_event_producer

    load_handlers()

    logger.info(
        "Worker starting",
        extra={
            "app_env": settings.app_env,
            "topics": topic_catalogue.qualified_worker_topics(),
            "consumer_group": settings.kafka_consumer_group,
            "handled_event_types": registered_event_types(),
            "database_configured": bool(settings.database_url),
        },
    )

    # The worker both consumes and produces: completion events and dead letters
    # are published from handlers.
    await init_event_producer()
    await init_cache()

    try:
        from app.db.session import SessionLocal
        if SessionLocal is not None:
            with SessionLocal() as db_session:
                from app.services.analytics_job_service import reap_stale_jobs
                reaped_count = reap_stale_jobs(db_session, max_age_seconds=600)
                if reaped_count:
                    logger.info("Worker reaped stale jobs on boot", extra={"reaped_jobs": reaped_count})
    except Exception as exc:
        logger.warning("Could not reap stale jobs on worker boot", extra={"reason": str(exc)})


    consumer = KafkaEventConsumer()
    consumer_task = asyncio.create_task(consumer.run(), name="kafka-consumer")

    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(signal_name: str) -> None:
        logger.info("Shutdown signal received", extra={"signal": signal_name})
        stop_requested.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except (NotImplementedError, RuntimeError):
            # Windows, or a loop that does not support signal handlers. Ctrl-C
            # still raises KeyboardInterrupt in main().
            pass

    stop_waiter = asyncio.create_task(stop_requested.wait(), name="shutdown-waiter")

    exit_code = 0

    try:
        done, _ = await asyncio.wait(
            {consumer_task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
        )

        if consumer_task in done:
            # The loop ended on its own: either a clean stop or a crash.
            exception = consumer_task.exception()
            if exception is not None:
                logger.error("Consumer loop exited with an error", exc_info=exception)
                exit_code = 1
    finally:
        stop_waiter.cancel()
        await consumer.stop()

        consumer_task.cancel()
        try:
            await consumer_task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown best effort
            pass

        await shutdown_event_producer()
        await shutdown_cache()

        logger.info("Worker stopped", extra={"exit_code": exit_code})

    return exit_code


def main(argv: list[str] | None = None) -> int:
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    if not settings.kafka_enabled:
        logger.warning(
            "KAFKA_ENABLED is false, so there is no broker to consume from. The API "
            "dispatches events in-process instead; no worker is needed. Set "
            "KAFKA_ENABLED=true and start Kafka to run this worker."
        )
        return 0

    if not settings.database_url:
        logger.error(
            "DATABASE_URL is not set. The worker writes analytics results to "
            "PostgreSQL and cannot run without it."
        )
        return 2

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
        return 0
    except Exception as exc:
        logger.error("Worker failed to start", exc_info=exc)
        return 1


def _health_snapshot() -> dict[str, Any]:
    """Configuration the worker is running with. No credentials.

    Exposed for the API's ``/health/kafka`` response so an operator can confirm
    both processes agree on topics and consumer group.
    """
    return {
        "kafka_enabled": settings.kafka_enabled,
        "consumer_group": settings.kafka_consumer_group,
        "topics": topic_catalogue.qualified_worker_topics(),
        "auto_offset_reset": settings.kafka_auto_offset_reset,
    }


if __name__ == "__main__":
    raise SystemExit(main())
