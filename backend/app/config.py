from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_backend_env = Path(__file__).resolve().parent.parent / ".env"
if _backend_env.is_file():
    load_dotenv(dotenv_path=_backend_env)




def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    Accepts the usual truthy spellings so operators are not surprised by
    ``KAFKA_ENABLED=1`` or ``KAFKA_ENABLED=yes`` silently meaning ``False``.
    """
    raw_value = os.getenv(name)

    if raw_value is None or raw_value.strip() == "":
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)

    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return int(raw_value.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)

    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return float(raw_value.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ---- Phase 1 (unchanged) ------------------------------------------------
    app_name: str
    cors_origins: list[str]
    models_dir: Path
    model_version: str
    preprocessing_output_dir: Path
    clerk_secret_key: str
    clerk_issuer: str
    database_url: str | None
    storage_backend: str = "memory"

    # ---- Phase 2: environment / observability -------------------------------
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"

    # ---- Database connection pool ------------------------------------------
    # SQLAlchemy's defaults (pool_size=5, max_overflow=10, pool_timeout=30) were
    # written for a database on the same LAN. Supabase is across the internet, so
    # a checkout costs a TLS handshake and every one of these numbers matters.
    #
    # pool_timeout is the important one for the failure this configuration exists
    # to prevent: with no bound, a request that cannot get a connection waits
    # forever and the backend looks dead rather than busy. Ten seconds turns that
    # into a 503 the caller can act on.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 10
    # Supabase's pooler drops idle connections, and a pooled connection that the
    # server has already closed surfaces as a mid-request failure. Recycling
    # under that window means the client discards it first.
    db_pool_recycle_seconds: int = 900
    # Server-side ceilings, enforced by PostgreSQL itself. This is the only kind
    # of timeout that can actually stop work already running inside the driver:
    # a Python-side timeout abandons the *wait*, not the query, because a thread
    # blocked in a C extension cannot be interrupted.
    db_statement_timeout_seconds: int = 30
    db_connect_timeout_seconds: int = 10
    # Imports run long single statements (batched inserts), so they get their own,
    # larger ceiling applied per-session rather than raising the global default.
    db_import_statement_timeout_seconds: int = 120

    # ---- Blocking-work offload ---------------------------------------------
    # Synchronous DB and pandas work runs in a worker thread so it cannot block
    # the event loop. The limiter is what keeps that from becoming a different
    # outage: unbounded offload lets N concurrent uploads each try to check out
    # connections, exhaust the pool, and starve every read request.
    #
    # Kept at or below db_pool_size so a full complement of import workers can
    # still get a connection each.
    import_max_concurrency: int = 4
    # Wall-clock ceiling for one import, below nginx's proxy_read_timeout (300s)
    # so the API produces the error rather than nginx producing a 504.
    import_timeout_seconds: float = 240.0
    # Previews only read the head of the file, so they are fast and get a tight
    # bound. A preview that takes 30s is a preview nobody waits for.
    preview_timeout_seconds: float = 30.0
    # Rows persisted per flush. Large enough that round-trips amortise, small
    # enough that one batch's parameter list stays inside the driver's limits.
    import_batch_size: int = 1000
    # Hard ceiling on rows accepted from a single file. Without one, "how much
    # memory does an import need" is answered by the person uploading the file.
    import_max_rows: int = 500_000

    # ---- Phase 2: Redis cache ----------------------------------------------
    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    cache_default_ttl_seconds: int = 300
    cache_dashboard_ttl_seconds: int = 120
    cache_analytics_ttl_seconds: int = 600
    cache_job_ttl_seconds: int = 3600
    # When Redis is disabled the cache abstraction can still be exercised with a
    # bounded in-process dict. This keeps dev/test behaviour realistic without
    # requiring infrastructure. PostgreSQL remains the source of truth either way.
    cache_local_fallback: bool = True

    # ---- Phase 2: Kafka event bus ------------------------------------------
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "cbpa-api"
    kafka_consumer_group: str = "cbpa-workers"
    kafka_topic_prefix: str = ""
    kafka_auto_offset_reset: str = "earliest"
    kafka_max_retries: int = 3
    kafka_retry_backoff_seconds: float = 1.0
    # Bounded so a broker problem cannot hold an HTTP request open.
    #
    # aiokafka defaults request_timeout_ms to 40s. With kafka_max_retries=3
    # wrapped around send_and_wait, the arithmetic worst case is two minutes of a
    # request doing nothing but waiting on Kafka — long past the point where the
    # user has given up and nginx has logged a 499.
    #
    # request_timeout_ms is the transport-level bound and the only one aiokafka
    # accepts as a constructor argument. There is no kafka_max_block_ms setting on
    # purpose: `max_block_ms` is a Java/kafka-python option for bounding a
    # *blocking* send(), and AIOKafkaProducer neither accepts it nor has any
    # **kwargs to absorb it — passing it raises TypeError, which
    # init_event_producer catches and turns into a permanent silent fallback to
    # the inline transport. Offering the setting would invite exactly that.
    #
    # kafka_publish_timeout_seconds is the outer asyncio ceiling that covers the
    # whole retry loop, because a per-attempt timeout multiplied by retries is not
    # a bound on the operation. It is what actually bounds the wait max_block_ms
    # was reached for.
    kafka_request_timeout_ms: int = 10_000
    kafka_publish_timeout_seconds: float = 15.0
    # Startup must not hang either: an unreachable broker at boot should degrade
    # to "events disabled" rather than prevent the API from serving anything.
    kafka_startup_timeout_seconds: float = 20.0
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str | None = None
    kafka_sasl_username: str | None = None
    kafka_sasl_password: str | None = None
    # When Kafka is disabled, analytics jobs still need to run. Executing the
    # handler inline preserves the async API contract (job_id + status) on a
    # laptop with no infrastructure running.
    events_inline_fallback: bool = True

    # ---- Phase 2: webhook ingestion ----------------------------------------
    webhook_secret: str = ""
    webhook_signature_header: str = "X-Webhook-Signature"
    webhook_timestamp_header: str = "X-Webhook-Timestamp"
    webhook_event_id_header: str = "X-Webhook-Event-Id"
    webhook_business_header: str = "X-Business-Id"
    webhook_tolerance_seconds: int = 300
    webhook_require_signature: bool = True

    # ---- Phase 2: analytics jobs -------------------------------------------
    # A completed import makes the stored RFM stale. Queueing a refresh
    # automatically keeps the dashboard honest without the user remembering to
    # press a button. Disable it for bulk backfills where one refresh at the end
    # is cheaper than one per file.
    analytics_auto_refresh_on_import: bool = True
    # Below this many units on hand a product is reported as low stock.
    inventory_low_stock_threshold: int = 10

    # ---- Phase 2: API surface ----------------------------------------------
    api_version: str = "v1"
    # Whether the health endpoints may name the host they are probing. A
    # hostname is not a credential, but on an unauthenticated endpoint it is
    # still reconnaissance, so it defaults off in production. Even when enabled,
    # the value is reduced to host and port with any userinfo stripped.
    expose_health_targets: bool = True

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    def public_summary(self) -> dict[str, object]:
        """Non-sensitive configuration summary that is safe to expose over HTTP.

        Deliberately excludes every credential: no Clerk key, no database URL,
        no webhook secret, no SASL password.
        """
        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "database_configured": bool(self.database_url),
            "redis_enabled": self.redis_enabled,
            "kafka_enabled": self.kafka_enabled,
            "webhooks_configured": bool(self.webhook_secret),
            "model_version": self.model_version,
        }


BASE_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = BASE_DIR / "models"
PREPROCESSING_OUTPUT_DIR = BASE_DIR / "preprocessing_outputs"


def _parse_cors_origins(raw_value: str) -> list[str]:
    origins = [item.strip() for item in raw_value.split(",") if item.strip()]
    return origins or [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]


settings = Settings(
    app_name=os.getenv("APP_NAME", "Customer Buying Pattern Analysis API"),
    cors_origins=_parse_cors_origins(
        os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
        )
    ),
    models_dir=MODELS_DIR,
    model_version=os.getenv("MODEL_VERSION", "v1"),
    preprocessing_output_dir=Path(
        os.getenv("PREPROCESSING_OUTPUT_DIR", str(PREPROCESSING_OUTPUT_DIR))
    ),
    clerk_secret_key=os.getenv("CLERK_SECRET_KEY", ""),
    clerk_issuer=os.getenv("CLERK_ISSUER", ""),
    database_url=os.getenv("DATABASE_URL"),
    storage_backend=os.getenv("STORAGE_BACKEND", "memory"),
    # ---- Phase 2 ----
    app_env=os.getenv("APP_ENV", "development"),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    log_format=os.getenv("LOG_FORMAT", "json"),
    db_pool_size=_env_int("DB_POOL_SIZE", 10),
    db_max_overflow=_env_int("DB_MAX_OVERFLOW", 10),
    db_pool_timeout_seconds=_env_int("DB_POOL_TIMEOUT_SECONDS", 10),
    db_pool_recycle_seconds=_env_int("DB_POOL_RECYCLE_SECONDS", 900),
    db_statement_timeout_seconds=_env_int("DB_STATEMENT_TIMEOUT_SECONDS", 30),
    db_connect_timeout_seconds=_env_int("DB_CONNECT_TIMEOUT_SECONDS", 10),
    db_import_statement_timeout_seconds=_env_int("DB_IMPORT_STATEMENT_TIMEOUT_SECONDS", 120),
    import_max_concurrency=_env_int("IMPORT_MAX_CONCURRENCY", 4),
    import_timeout_seconds=_env_float("IMPORT_TIMEOUT_SECONDS", 240.0),
    preview_timeout_seconds=_env_float("PREVIEW_TIMEOUT_SECONDS", 30.0),
    import_batch_size=_env_int("IMPORT_BATCH_SIZE", 1000),
    import_max_rows=_env_int("IMPORT_MAX_ROWS", 500_000),
    redis_enabled=_env_bool("REDIS_ENABLED", False),
    redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    cache_default_ttl_seconds=_env_int("CACHE_DEFAULT_TTL_SECONDS", 300),
    cache_dashboard_ttl_seconds=_env_int("CACHE_DASHBOARD_TTL_SECONDS", 120),
    cache_analytics_ttl_seconds=_env_int("CACHE_ANALYTICS_TTL_SECONDS", 600),
    cache_job_ttl_seconds=_env_int("CACHE_JOB_TTL_SECONDS", 3600),
    cache_local_fallback=_env_bool("CACHE_LOCAL_FALLBACK", True),
    kafka_enabled=_env_bool("KAFKA_ENABLED", False),
    kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    kafka_client_id=os.getenv("KAFKA_CLIENT_ID", "cbpa-api"),
    kafka_consumer_group=os.getenv("KAFKA_CONSUMER_GROUP", "cbpa-workers"),
    kafka_topic_prefix=os.getenv("KAFKA_TOPIC_PREFIX", ""),
    kafka_auto_offset_reset=os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest"),
    kafka_max_retries=_env_int("KAFKA_MAX_RETRIES", 3),
    kafka_retry_backoff_seconds=_env_float("KAFKA_RETRY_BACKOFF_SECONDS", 1.0),
    kafka_request_timeout_ms=_env_int("KAFKA_REQUEST_TIMEOUT_MS", 10_000),
    kafka_publish_timeout_seconds=_env_float("KAFKA_PUBLISH_TIMEOUT_SECONDS", 15.0),
    kafka_startup_timeout_seconds=_env_float("KAFKA_STARTUP_TIMEOUT_SECONDS", 20.0),
    kafka_security_protocol=os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
    kafka_sasl_mechanism=os.getenv("KAFKA_SASL_MECHANISM") or None,
    kafka_sasl_username=os.getenv("KAFKA_SASL_USERNAME") or None,
    kafka_sasl_password=os.getenv("KAFKA_SASL_PASSWORD") or None,
    events_inline_fallback=_env_bool("EVENTS_INLINE_FALLBACK", True),
    webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
    webhook_signature_header=os.getenv("WEBHOOK_SIGNATURE_HEADER", "X-Webhook-Signature"),
    webhook_timestamp_header=os.getenv("WEBHOOK_TIMESTAMP_HEADER", "X-Webhook-Timestamp"),
    webhook_event_id_header=os.getenv("WEBHOOK_EVENT_ID_HEADER", "X-Webhook-Event-Id"),
    webhook_business_header=os.getenv("WEBHOOK_BUSINESS_HEADER", "X-Business-Id"),
    webhook_tolerance_seconds=_env_int("WEBHOOK_TOLERANCE_SECONDS", 300),
    webhook_require_signature=_env_bool("WEBHOOK_REQUIRE_SIGNATURE", True),
    analytics_auto_refresh_on_import=_env_bool("ANALYTICS_AUTO_REFRESH_ON_IMPORT", True),
    inventory_low_stock_threshold=_env_int("INVENTORY_LOW_STOCK_THRESHOLD", 10),
    api_version=os.getenv("API_VERSION", "v1"),
    # Defaults to off in production so an unauthenticated health check does not
    # advertise internal hostnames.
    expose_health_targets=_env_bool(
        "EXPOSE_HEALTH_TARGETS",
        os.getenv("APP_ENV", "development").strip().lower() not in {"production", "prod"},
    ),
)
