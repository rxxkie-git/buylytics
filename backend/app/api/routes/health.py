"""Health and readiness endpoints.

Four shapes of question, deliberately kept separate:

``/health``
    Is the process up? Never touches a dependency, so a load balancer keeps
    routing to a pod whose Redis is briefly unreachable rather than removing the
    last healthy replica.
``/health/db``, ``/health/redis``, ``/health/kafka``
    Is one named dependency reachable? For an operator or a dashboard.
``/health/ready``
    Should this instance receive traffic? Only the database can make the answer
    no. Redis is a cache with a Postgres fallback and Kafka is optional, so
    neither being down means the app cannot serve requests.

Nothing here returns a secret. No response contains a URL, DSN, password, token
or raw driver message: a health endpoint is typically unauthenticated and
frequently the first thing scraped, so every field is either a fixed enum value,
a boolean, a count, or a duration. Where a dependency reports a hostname the
value is reduced to a host and port with any userinfo removed, and even that is
gated on ``expose_health_targets``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Response, status

from app.config import settings
from app.core.logging import get_logger
from app.db.session import check_database_connection

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DISABLED = "disabled"
STATUS_UNAVAILABLE = "unavailable"

#: A probe that hangs must not hold a health request open. Redis and Kafka are
#: both network round-trips and both have their own timeouts, but this is the
#: backstop that guarantees the endpoint answers.
PROBE_TIMEOUT_SECONDS = 3.0


def _safe_target(raw_url: str | None) -> str | None:
    """Reduce a connection URL to ``host:port``.

    ``redis://:s3cr3t@cache.internal:6379/0`` becomes ``cache.internal:6379``.
    The password lives in the userinfo section, so returning only ``hostname``
    and ``port`` from the parsed result drops it structurally rather than by
    pattern-matching. A value that will not parse is discarded entirely — better
    to show nothing than to guess at which half was the credential.
    """
    if not raw_url or not settings.expose_health_targets:
        return None

    candidate = raw_url if "://" in raw_url else f"//{raw_url}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    host = parts.hostname

    if not host:
        return None

    return f"{host}:{parts.port}" if parts.port else host


def _kafka_target() -> str | None:
    """The first bootstrap server, host and port only."""
    servers = settings.kafka_bootstrap_servers

    if not servers:
        return None

    return _safe_target(servers.split(",")[0].strip())


async def _timed(coro: Any) -> tuple[Any, float]:
    """Await with a ceiling, reporting how long it took."""
    started = time.perf_counter()

    try:
        result = await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return TimeoutError("probe timed out"), (time.perf_counter() - started) * 1000

    return result, (time.perf_counter() - started) * 1000


# ---------------------------------------------------------------------------
# Individual dependency probes
# ---------------------------------------------------------------------------


async def _probe_database() -> dict[str, Any]:
    """``SELECT 1``, on a worker thread because the driver blocks."""
    outcome, elapsed_ms = await _timed(asyncio.to_thread(check_database_connection))

    if isinstance(outcome, BaseException):
        return {
            "status": STATUS_UNAVAILABLE,
            "detail": "probe timed out",
            "latency_ms": round(elapsed_ms, 2),
        }

    connected, detail = outcome

    return {
        # ``detail`` is already classified by check_database_connection; it never
        # carries the DSN.
        "status": STATUS_OK if connected else STATUS_UNAVAILABLE,
        "detail": detail,
        "latency_ms": round(elapsed_ms, 2),
    }


async def _probe_redis() -> dict[str, Any]:
    """Ask the active cache backend for its health.

    A disabled cache is reported as ``disabled``, not as a failure. Running
    without Redis is a supported configuration, and colouring a dashboard red
    for a deliberate choice trains people to ignore it.

    The backend's own ``detail`` field is **not** forwarded. It carries
    ``str(exc)`` from a failed ping, and a redis-py connection error embeds the
    URL — which, for ``redis://:password@host``, is the password. The status is
    translated into a fixed phrase instead and the exception goes to the log.
    """
    from app.cache import get_cache

    backend = get_cache()

    if not settings.redis_enabled:
        return {
            "status": STATUS_DISABLED,
            "backend": getattr(backend, "backend", type(backend).__name__),
            "detail": "REDIS_ENABLED is false; reads fall back to PostgreSQL",
        }

    outcome, elapsed_ms = await _timed(backend.health())

    if isinstance(outcome, BaseException):
        return {
            "status": STATUS_UNAVAILABLE,
            "backend": "redis",
            "detail": "probe timed out",
            "target": _safe_target(settings.redis_url),
            "latency_ms": round(elapsed_ms, 2),
        }

    snapshot = outcome if isinstance(outcome, dict) else {}
    reported = str(snapshot.get("status", "unknown"))

    status_value, detail = _REDIS_STATES.get(
        reported, (STATUS_UNAVAILABLE, "cache backend reported an unrecognised state")
    )

    if status_value != STATUS_OK:
        logger.warning(
            "Redis health probe reported a problem",
            extra={"reported_status": reported, "backend": snapshot.get("backend")},
        )

    result: dict[str, Any] = {
        "status": status_value,
        "backend": snapshot.get("backend", "redis"),
        "detail": detail,
        "target": _safe_target(settings.redis_url),
        "latency_ms": round(elapsed_ms, 2),
    }

    # Counters are safe: they are integers produced by this process.
    for counter in ("hits", "misses", "entries"):
        if isinstance(snapshot.get(counter), int):
            result[counter] = snapshot[counter]

    return result


#: Maps a backend's self-reported status onto the endpoint's vocabulary and a
#: fixed description. Anything not listed is treated as unavailable.
_REDIS_STATES: dict[str, tuple[str, str]] = {
    "ok": (STATUS_OK, "connected"),
    "disabled": (STATUS_DISABLED, "cache backend is disabled"),
    "disconnected": (STATUS_UNAVAILABLE, "not connected"),
    "error": (STATUS_UNAVAILABLE, "ping failed"),
}


async def _probe_kafka() -> dict[str, Any]:
    """Report the producer's transport and connection state.

    As with Redis, the producer's ``detail`` and ``bootstrap_servers`` fields are
    dropped rather than forwarded — the endpoint derives its own sanitised target
    so a broker address cannot escape through an error string.
    """
    from app.events.producer import get_event_producer

    if not settings.kafka_enabled:
        return {
            "status": STATUS_DISABLED,
            "transport": "inline" if settings.events_inline_fallback else "disabled",
            "detail": (
                "KAFKA_ENABLED is false; events are handled in-process"
                if settings.events_inline_fallback
                else "KAFKA_ENABLED is false and inline fallback is off; events are dropped"
            ),
        }

    producer = get_event_producer()
    outcome, elapsed_ms = await _timed(producer.health())

    if isinstance(outcome, BaseException):
        return {
            "status": STATUS_UNAVAILABLE,
            "transport": "kafka",
            "detail": "probe timed out",
            "target": _kafka_target(),
            "latency_ms": round(elapsed_ms, 2),
        }

    snapshot = outcome if isinstance(outcome, dict) else {}
    reported = str(snapshot.get("status", "unknown"))

    status_value, detail = _KAFKA_STATES.get(
        reported, (STATUS_UNAVAILABLE, "producer reported an unrecognised state")
    )

    if status_value != STATUS_OK:
        logger.warning(
            "Kafka health probe reported a problem", extra={"reported_status": reported}
        )

    result: dict[str, Any] = {
        "status": status_value,
        "transport": snapshot.get("transport", "kafka"),
        "detail": detail,
        "target": _kafka_target(),
        "latency_ms": round(elapsed_ms, 2),
    }

    if isinstance(snapshot.get("pending_tasks"), int):
        result["pending_tasks"] = snapshot["pending_tasks"]

    return result


#: ``disconnected`` is degraded rather than unavailable: publishes fall back
#: instead of failing, so the API is still able to serve traffic.
_KAFKA_STATES: dict[str, tuple[str, str]] = {
    "ok": (STATUS_OK, "connected"),
    "disabled": (STATUS_DISABLED, "event bus is disabled"),
    "disconnected": (STATUS_DEGRADED, "not connected; publishes use the fallback"),
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness. Answers from memory, so it cannot be made slow by a dependency."""
    return {
        "status": STATUS_OK,
        "service": settings.app_name,
        "version": settings.api_version,
    }


@router.get("/health/db")
async def health_database(response: Response) -> dict[str, Any]:
    result = await _probe_database()

    if result["status"] != STATUS_OK:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"dependency": "database", **result}


@router.get("/health/redis")
async def health_redis(response: Response) -> dict[str, Any]:
    result = await _probe_redis()

    if result["status"] == STATUS_UNAVAILABLE:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"dependency": "redis", **result}


@router.get("/health/kafka")
async def health_kafka(response: Response) -> dict[str, Any]:
    result = await _probe_kafka()

    if result["status"] == STATUS_UNAVAILABLE:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"dependency": "kafka", **result}


@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, Any]:
    """Aggregate readiness.

    The three probes run concurrently — sequentially, a slow Redis would add its
    latency to every readiness check, and Kubernetes reads the timeout as a
    failure.

    Only the database is required. This is the distinction that keeps a cache
    outage from becoming an outage.
    """
    database, redis, kafka = await asyncio.gather(
        _probe_database(), _probe_redis(), _probe_kafka()
    )

    ready = database["status"] == STATUS_OK
    degraded = [
        name
        for name, result in (("redis", redis), ("kafka", kafka))
        if result["status"] == STATUS_UNAVAILABLE
    ]

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning("Readiness check failed", extra={"database": database["status"]})

    return {
        "status": STATUS_OK if ready and not degraded else (STATUS_DEGRADED if ready else STATUS_UNAVAILABLE),
        "ready": ready,
        "service": settings.app_name,
        "version": settings.api_version,
        "required": ["database"],
        "degraded": degraded,
        "dependencies": {"database": database, "redis": redis, "kafka": kafka},
    }
