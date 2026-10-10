"""Application entrypoint.

Startup order matters and is deliberate:

1.  Logging first, so every later step's output is structured and redacted. A
    failure during startup is exactly when readable logs matter most.
2.  Exception handlers next, so a request arriving mid-startup gets the
    sanitised error shape rather than a raw traceback.
3.  Cache, then event producer, then handler registration. Handlers are
    registered last because a handler that fires the instant it is registered —
    which the inline transport makes possible — needs the cache and producer to
    already exist.

Shutdown reverses it: stop accepting events, then drain the producer, then close
the cache. Closing the cache first would make in-flight handlers fail on
invalidation.

Nothing in this module fails startup for an optional dependency. Redis and Kafka
are both optional by design, and a laptop with neither running must still be able
to serve the whole API against PostgreSQL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes.health import router as health_router
from .config import settings
from .core.errors import register_exception_handlers
from .core.logging import configure_logging, get_logger
from .core.middleware import RequestContextMiddleware
from .database import initialize_repository
from .services.inference import load_inference_artifacts

configure_logging(level=settings.log_level, log_format=settings.log_format)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bring dependencies up and take them down again.

    Each optional dependency is started inside its own ``try``. One unreachable
    service must not prevent the others from initialising, and none of them is
    allowed to stop the process from booting — the alternative is a crash loop
    triggered by a cache.
    """
    settings.preprocessing_output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Phase 1 state ---------------------------------------------------
    app.state.dataset_repository = initialize_repository(settings)

    try:
        app.state.inference_artifacts = load_inference_artifacts(
            settings.models_dir,
            settings.model_version,
        )
    except Exception:
        # A missing or unreadable model file must not stop the API. Imports,
        # dashboards and RFM do not depend on it; only the legacy prediction
        # route does, and it can report the artefacts as unavailable.
        app.state.inference_artifacts = None
        logger.exception("Could not load inference artifacts; prediction routes will be unavailable")

    # ---- Phase 2: cache --------------------------------------------------
    try:
        from .cache import init_cache

        cache = await init_cache()
        logger.info("Cache initialised", extra={"backend": getattr(cache, "backend", "unknown")})
    except Exception:
        logger.exception("Cache initialisation failed; reads will go to PostgreSQL")

    # ---- Phase 2: event producer ----------------------------------------
    try:
        from .events.producer import init_event_producer

        producer = await init_event_producer()
        logger.info("Event producer initialised", extra={"transport": producer.transport})
    except Exception:
        logger.exception("Event producer initialisation failed; events will not be published")

    # ---- Phase 2: event handlers ----------------------------------------
    try:
        from .events.consumer import load_handlers

        load_handlers()
    except Exception:
        logger.exception("Event handler registration failed; events will not be processed")

    logger.info(
        "Application startup complete",
        extra={"config": settings.public_summary()},
    )

    try:
        yield
    finally:
        # Producer first: draining it may run inline handlers that touch the cache.
        try:
            from .events.producer import shutdown_event_producer

            await shutdown_event_producer()
        except Exception:
            logger.exception("Event producer shutdown failed")

        try:
            from .cache import shutdown_cache

            await shutdown_cache()
        except Exception:
            logger.exception("Cache shutdown failed")

        app.state.dataset_repository = None
        logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
)

# Correlation ids before CORS so every log line from a request — including one
# rejected by CORS — carries the same request id.
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from .api.routes.v1_analytics import router as v1_analytics_router  # noqa: E402
from .api.routes.v1_customers import router as v1_customers_router  # noqa: E402
from .api.routes.v1_dashboard import router as v1_dashboard_router  # noqa: E402
from .api.routes.v1_imports import router as v1_imports_router  # noqa: E402
from .api.routes.v1_inventory import router as v1_inventory_router  # noqa: E402
from .api.routes.v1_orders import router as v1_orders_router  # noqa: E402
from .api.routes.v1_products import router as v1_products_router  # noqa: E402
from .api.routes.v1_webhooks import router as v1_webhooks_router  # noqa: E402

app.include_router(health_router)

app.include_router(v1_dashboard_router)
app.include_router(v1_imports_router)
app.include_router(v1_customers_router)
app.include_router(v1_products_router)
app.include_router(v1_inventory_router)
app.include_router(v1_orders_router)
app.include_router(v1_analytics_router)
app.include_router(v1_webhooks_router)

# Legacy routes, kept mounted for backward compatibility. The new frontend does
# not call these; they remain so Phase 1 clients and the ported product-analysis
# view keep working.
from .routes.analytics import router as analytics_router  # noqa: E402
from .routes.inventory import router as inventory_router  # noqa: E402
from .routes.upload import router as upload_router  # noqa: E402

app.include_router(upload_router)
app.include_router(analytics_router)
app.include_router(inventory_router)
