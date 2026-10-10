from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from ..core.logging import get_logger

logger = get_logger(__name__)


def _build_engine() -> Engine | None:
    if not settings.database_url:
        return None

    database_url = settings.database_url

    # Use psycopg v3 instead of psycopg2
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )

    return create_engine(
        database_url,
        # A pooled connection the server has already dropped otherwise surfaces
        # as a mid-request failure. Costs one round-trip per checkout, which is
        # worth it against a database across the internet.
        pool_pre_ping=True,
        future=True,
        # ---- Pool sizing --------------------------------------------------
        # Explicit rather than inherited. SQLAlchemy's defaults (5 + 10 overflow,
        # 30s wait) are tuned for a local database; more importantly, the default
        # 30s pool_timeout is longer than a user will wait, so pool contention
        # presented as an unexplained stall instead of an error.
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # The bound that stops one bad upload from making the whole API look
        # dead: when no connection is free, wait this long and then raise
        # TimeoutError, which the error handler turns into a 503. Queueing
        # indefinitely is what turns pool exhaustion into an outage.
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        connect_args={
            # libpq parameters, passed through by psycopg. Without connect_timeout
            # a TCP connection to an unreachable host hangs for the OS default —
            # over two minutes on Linux — inside a worker thread.
            "connect_timeout": settings.db_connect_timeout_seconds,
            # Server-side ceiling on every statement, set at connection time so it
            # applies to all work by default. This is the only timeout that can
            # interrupt a query already executing: PostgreSQL cancels it itself
            # and psycopg raises. A Python-side timeout cannot, because a thread
            # blocked in the driver's C code is not interruptible.
            #
            # idle_in_transaction_session_timeout covers the other half: a
            # transaction left open by a crashed worker holds locks and a slot
            # until something closes it.
            "options": (
                f"-c statement_timeout={int(settings.db_statement_timeout_seconds) * 1000}"
                f" -c idle_in_transaction_session_timeout="
                f"{int(settings.db_statement_timeout_seconds) * 2 * 1000}"
            ),
        },
    )


engine = _build_engine()

SessionLocal = (
    sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    if engine is not None
    else None
)


def get_db() -> Generator[Session, None, None]:
    """Request-scoped session.

    A plain generator, not an async one, and that is deliberate: FastAPI runs sync
    generator dependencies in the threadpool, so the session's synchronous work
    never touches the event loop. Making this ``async def`` would put every
    checkout and every ``close()`` back on the loop.

    The explicit rollback matters for pool health. ``close()`` alone returns the
    connection with the pool's own reset, but if the handler raised mid-transaction
    the session is left in a failed state, and any later use of it on this request
    raises ``PendingRollbackError`` — a confusing second error that hides the
    first. Rolling back here means the connection goes back clean.
    """
    if SessionLocal is None:
        raise RuntimeError(
            "Database is not configured. Set DATABASE_URL before using the DB layer."
        )

    session = SessionLocal()

    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def statement_timeout(session: Session, seconds: int) -> Generator[None, None, None]:
    """Raise this transaction's statement timeout above the engine default.

    Imports issue deliberately large batched statements that can legitimately
    outrun the 30s default, and raising the engine-wide default to suit them would
    remove the protection from every ordinary query.

    Transaction-scoped on purpose. A bare ``SET statement_timeout`` persists for
    the life of the *connection*, so it would follow the connection back into the
    pool and silently apply to whichever unrelated request borrowed it next.
    ``set_config(..., is_local => true)`` reverts on commit or rollback.

    ``set_config`` rather than ``SET LOCAL`` because ``SET`` takes no bind
    parameters — it would need string interpolation, and a parameterised call is
    simply better than an interpolation that happens to be safe today.
    """
    if engine is None:
        yield
        return

    try:
        session.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(int(seconds) * 1000)},
        )
    except Exception as exc:
        # Never fail the import because the ceiling could not be widened; the
        # engine default still applies, which is the safe direction to fail.
        logger.warning(
            "Could not widen the statement timeout for this transaction",
            extra={"error_type": type(exc).__name__},
        )

    yield


def check_database_connection() -> tuple[bool, str]:
    """Probe the database with ``SELECT 1``.

    The returned message is a fixed classification, never the driver's own text.
    A psycopg or SQLAlchemy connection error routinely embeds the connection
    string — which carries the database password — and this value is served
    straight out of ``/health`` to unauthenticated callers. The exception detail
    goes to the log, where the redacting formatter can handle it, and the caller
    gets a category it can act on.
    """
    if engine is None:
        return False, "database not configured"

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return True, "connected"

    except Exception as exc:
        logger.warning(
            "Database health probe failed",
            extra={"error_type": type(exc).__name__},
            exc_info=True,
        )
        return False, _classify_connection_error(exc)


#: Matched against the lower-cased exception text. Ordered most specific first;
#: every phrase here is a driver-independent substring, so this keeps working if
#: the DSN inside the message changes.
_ERROR_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("password authentication failed", "authentication failed"),
    ("no password supplied", "authentication failed"),
    ("role \"", "authentication failed"),
    ("timeout expired", "connection timed out"),
    ("timed out", "connection timed out"),
    ("could not translate host name", "host could not be resolved"),
    ("name or service not known", "host could not be resolved"),
    ("connection refused", "connection refused"),
    ("does not exist", "database or role does not exist"),
    ("too many clients", "connection pool exhausted"),
    ("ssl", "tls negotiation failed"),
    ("server closed the connection", "connection closed by server"),
)


def _classify_connection_error(exc: Exception) -> str:
    """Reduce a driver exception to a safe, actionable category."""
    text_form = str(exc).lower()

    for signature, description in _ERROR_SIGNATURES:
        if signature in text_form:
            return f"unavailable: {description}"

    # Fall back to the exception class name. That is generated from source code,
    # not from the connection string, so it cannot carry a credential.
    return f"unavailable: {type(exc).__name__}"