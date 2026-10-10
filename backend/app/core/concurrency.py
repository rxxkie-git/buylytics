"""Run blocking work without blocking the event loop.

Why this module exists
----------------------
FastAPI dispatches handlers two different ways, and the difference is invisible
at the call site:

* ``async def`` runs **on the event loop**. One loop serves every connection, so
  any synchronous call inside it stops the entire process — not just that
  request.
* plain ``def`` is run by Starlette in the anyio worker threadpool, so blocking
  there costs a thread, not the server.

This application's handlers were ``async def`` while calling synchronous
SQLAlchemy, so every database round-trip froze the loop. The visible symptom was
the whole API going unresponsive during a large CSV import, including ``/health``
— an endpoint that returns a literal dict and touches no dependency. That is the
signature of loop starvation and nothing else: a saturated connection pool or an
unreachable broker cannot slow down a function that returns a constant.

Two things fix that, and both are needed:

1. Handlers whose bodies are entirely synchronous become plain ``def``. Free, and
   it is what FastAPI's threadpool is for.
2. Work that must stay in an ``async def`` — because it awaits an upload stream
   first — gets handed to a thread through :func:`run_blocking`.

On timeouts, honestly
---------------------
:func:`run_blocking` bounds how long the *caller waits*. It does not and cannot
abort the thread: Python has no safe thread-kill, and a thread sitting inside
psycopg's C extension is not interruptible from the interpreter. So a timeout
here frees the request and returns a real status code, while the orphaned thread
runs to completion in the background.

That makes the timeout a liveness guarantee for the API, not a resource bound on
the work. The actual bound on the work has to come from the layer doing it —
``statement_timeout`` on the database session (see ``app.db.session``), which
PostgreSQL enforces server-side and which therefore *does* stop a running query.
The two are complementary, and using only the asyncio timeout would leak threads
under load.

The limiter is the other half. Unbounded offload replaces "the loop is blocked"
with "40 threads are each trying to check out a database connection", which
exhausts a 20-connection pool and starves every read in the application. A
capacity limiter sized against the pool keeps imports from doing that.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

import anyio
import anyio.to_thread

from ..config import settings
from .logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class BlockingWorkTimeout(Exception):
    """Blocking work outlived its wall-clock budget.

    Carries the budget so the HTTP layer can say how long it waited without
    hardcoding the number in a message. Callers map this to 504.
    """

    def __init__(self, operation: str, timeout_seconds: float) -> None:
        super().__init__(f"{operation} exceeded its {timeout_seconds:.0f}s budget")
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class BlockingWorkBusy(Exception):
    """No import slot was available within the queue budget.

    Distinct from a timeout on purpose: this request did no work at all, so it is
    safe to retry immediately and the correct status is 503 with Retry-After,
    not 504.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} is at capacity; retry shortly")
        self.operation = operation


#: Bounds concurrent import/preview work, one limiter per event loop.
#:
#: Created lazily rather than at module scope, for two independent reasons.
#:
#: Constructing an anyio synchronisation primitive dispatches through
#: ``sniffio.current_async_library()`` to pick a backend, so instantiating one at
#: import time — when no loop is running — depends on a library detail that has
#: changed between anyio releases. Deferring it to first use inside the loop makes
#: that question moot.
#:
#: And the tests drive endpoints with a fresh ``asyncio.run()`` per call, so a
#: single cached limiter would be created under one loop and then awaited under
#: another. Keying by loop keeps each one self-consistent; ``WeakKeyDictionary``
#: means a finished loop's entry disappears with it instead of accumulating.
_limiters: WeakKeyDictionary[Any, anyio.CapacityLimiter] = WeakKeyDictionary()


def import_limiter() -> anyio.CapacityLimiter:
    """The limiter for the running loop, created on first use."""
    loop = asyncio.get_running_loop()
    limiter = _limiters.get(loop)

    if limiter is None:
        limiter = anyio.CapacityLimiter(max(1, settings.import_max_concurrency))
        _limiters[loop] = limiter

    return limiter


def import_slots_in_use() -> int:
    """Borrowed slots, for the readiness payload and for tests.

    Returns 0 when nothing has been offloaded yet, and never creates a limiter as
    a side effect of being asked — this is called from ``/health/ready``, which
    must stay a read-only observation.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0

    limiter = _limiters.get(loop)

    return int(limiter.borrowed_tokens) if limiter is not None else 0


async def run_blocking(
    func: Callable[..., T],
    /,
    *args: Any,
    operation: str,
    timeout_seconds: float | None = None,
    limiter: anyio.CapacityLimiter | None = None,
    **kwargs: Any,
) -> T:
    """Run ``func`` in a worker thread, bounded by time and concurrency.

    Args:
        func: Synchronous callable. Must not touch the event loop.
        operation: Short stable label used in logs and error messages. Never
            interpolate user data into this — it lands in log fields.
        timeout_seconds: Wall-clock ceiling on the *wait*. ``None`` means wait
            indefinitely, which is only appropriate for work already bounded
            elsewhere.
        limiter: Concurrency limiter. ``None`` uses anyio's default threadpool
            limiter, i.e. effectively unbounded for our purposes.

    Raises:
        BlockingWorkTimeout: The budget elapsed.
        BlockingWorkBusy: A slot could not be acquired in time.
    """
    started = time.perf_counter()
    call = partial(func, *args, **kwargs)

    # abandon_on_cancel=True is deliberate and is the whole reason a timeout can
    # return promptly. The default (False) makes cancellation *wait* for the
    # thread to finish, so `asyncio.wait_for` would not return until the blocking
    # call completed — the timeout would be decorative. The cost is the orphaned
    # thread described in the module docstring.
    runner = anyio.to_thread.run_sync(call, abandon_on_cancel=True, limiter=limiter)

    try:
        if timeout_seconds is None:
            return await runner

        return await asyncio.wait_for(runner, timeout=timeout_seconds)

    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - started
        logger.error(
            "Blocking operation exceeded its time budget",
            extra={
                "operation": operation,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": round(elapsed, 3),
                # The thread is still running. Said plainly in the log because
                # the alternative is someone concluding the work was rolled back.
                "thread_abandoned": True,
            },
        )
        raise BlockingWorkTimeout(operation, float(timeout_seconds)) from None


async def run_import_blocking(
    func: Callable[..., T],
    /,
    *args: Any,
    operation: str,
    timeout_seconds: float | None = None,
    queue_timeout_seconds: float = 5.0,
    **kwargs: Any,
) -> T:
    """:func:`run_blocking` on the import limiter, with a bounded queue wait.

    Splitting the queue wait from the execution budget is what lets the two
    failures produce different status codes. Requests that never started are
    retryable (503); requests that started and overran are not necessarily (504),
    because their database work may still land.
    """
    limiter = import_limiter()

    # Fast, honest rejection when every slot is busy and the queue is not moving.
    # Without this, `asyncio.wait_for` around acquisition would report a timeout
    # for a request that never got to run — the caller cannot tell "too slow"
    # from "too busy", and those want different retry behaviour.
    if limiter.available_tokens < 1:
        try:
            with anyio.fail_after(queue_timeout_seconds):
                await limiter.acquire()
        except TimeoutError:
            logger.warning(
                "Import capacity exhausted; rejecting rather than queueing",
                extra={
                    "operation": operation,
                    "limit": limiter.total_tokens,
                    "in_use": int(limiter.borrowed_tokens),
                },
            )
            raise BlockingWorkBusy(operation) from None

        # Slot held manually, so hand the thread the *default* limiter and
        # release ours in `finally`. Passing `limiter` here instead would raise
        # RuntimeError from anyio — a task may hold only one token of a given
        # CapacityLimiter, and run_sync would try to take a second.
        try:
            return await run_blocking(
                func,
                *args,
                operation=operation,
                timeout_seconds=timeout_seconds,
                limiter=None,
                **kwargs,
            )
        finally:
            limiter.release()

    return await run_blocking(
        func,
        *args,
        operation=operation,
        timeout_seconds=timeout_seconds,
        limiter=limiter,
        **kwargs,
    )
