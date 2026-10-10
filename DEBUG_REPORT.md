# CBPA backend hang — debugging report

Investigation of "the backend becomes unresponsive after import activity" (499s, then
504s with `upstream timed out (110: Operation timed out) while sending request to
upstream`, plus `wrong ident in DNS response for backend`).

---

## A. ROOT CAUSE

**Thirty `async def` handlers performed synchronous SQLAlchemy work directly on the
asyncio event loop.** FastAPI runs an `async def` endpoint *on the loop itself*; only
plain `def` endpoints and sync generator dependencies are pushed to the anyio worker
threadpool. A synchronous `session.execute(...)` inside `async def` therefore parks the
one thread that serves every other request.

Among those thirty was `get_business_context` — a dependency of every authenticated
route. So the blocking was not confined to imports; any slow query anywhere froze the
whole API.

The import path made a latent problem into a reliable outage:

1. `pd.read_csv` read the entire uploaded file on the loop.
2. Validation then walked the frame **row at a time** in Python.
3. The "bulk" upsert issued **one SELECT plus one flush per row** against Supabase —
   i.e. across the internet. A 40,000-row file meant ~80,000 sequential round trips,
   each with the latency of a remote database, all on the event loop.

Everything the user observed follows from that single fact:

| Observation | Explanation |
|---|---|
| `/health` and `/health/ready` hang | The loop is not running; nothing can be served |
| 499 on dashboard endpoints | The browser gave up first — nginx logs the client abort |
| 504 on the *next* preview | Requests queued behind the frozen loop until `proxy_read_timeout` elapsed |
| `wrong ident in DNS response` | A retry storm against a frozen upstream; replies arrive after their query is abandoned. **A symptom, not a second bug** |
| It worked at first | The first request touched little data; the failure needs a real file |

### Why Kafka, Redis and the connection pool are excluded as primary cause

This was checked rather than assumed, as required.

`GET /health` returns a static dict. It opens no database session, touches no cache and
never contacts a broker. **Its stalling can therefore only be event-loop starvation.**
Any explanation involving Kafka, Redis or pool exhaustion fails to account for the one
endpoint that depends on none of them.

Reproduced and measured (`t_starvation.py`, real asyncio, 1500 ms of blocking work with
100 concurrent `/health` probes):

```
BEFORE  blocking call made directly in the coroutine
  /health p50            512.00 ms
  /health worst         1502.0 ms      <- ~= the 1500 ms of blocking work
  probes over 100 ms     71 of 100

AFTER   same work handed to a worker thread
  /health p50             10.47 ms
  /health worst           18.1 ms
  probes over 100 ms      0 of 100
```

Worst-case latency improves 83×. That the "before" figure matches the injected blocking
duration almost exactly is what identifies the mechanism.

Kafka, Redis and the pool were nevertheless audited and hardened — they were *secondary*
amplifiers, and one of them (an unbounded publish retry loop) could have produced a
similar symptom under a broker failure.

---

## B. FILES CHANGED

Thirty files. Two are new.

### New

| File | Purpose |
|---|---|
| `backend/app/core/concurrency.py` | `run_blocking` / `run_import_blocking`: anyio `to_thread` offload with a `CapacityLimiter`, a wall-clock ceiling, and `BlockingWorkBusy` / `BlockingWorkTimeout` |
| `backend/app/repositories/bulk.py` | `chunked`, `dedupe_by_key`, `bulk_upsert_by_key`, `fetch_ids_by_external_ids` — set-based writes chunked under PostgreSQL's 65,535 bind-parameter ceiling |

### Modified

**Stopped blocking the loop** — `app/core/multi_tenancy.py`, `app/api/routes/v1_customers.py`,
`v1_products.py`, `v1_orders.py`, `v1_inventory.py`, `v1_analytics.py`, `v1_webhooks.py`,
`app/routes/upload.py`, `app/routes/inventory.py`, `app/services/customer_service.py`,
`product_service.py`, `analytics_job_service.py`, `app/webhooks/service.py`.

**Import path** — `app/api/routes/v1_imports.py` (preview/import separation, status-code
mapping, structured logging), `app/ingestion/importer.py` (batching, transactions),
`app/ingestion/file_parser.py` (CSV hardening, bounded preview).

**Repositories** — `customer_repository.py`, `product_repository.py`, `order_repository.py`,
`inventory_repository.py` (N+1 removal, set-based upserts).

**Infrastructure** — `app/db/session.py` (pool + server-side timeouts), `app/config.py`
(all the new bounds), `app/events/producer.py` (bounded publish, circuit breaker),
`requirements.txt` (declare `anyio`), `docker-compose.yml` (the `worker` service),
`frontend/nginx.conf` (DNS re-resolution, upstream keepalive, timeout ordering),
`frontend/src/components/ui/AsyncBoundary.jsx` and
`frontend/src/pages/app/SettingsPage.jsx` (webhook settings UX).

### Deliberately unchanged

`backend/app/api/routes/health.py` was already correct: `PROBE_TIMEOUT_SECONDS = 3.0`,
every probe wrapped in `asyncio.wait_for`, `_probe_database` dispatched through
`asyncio.to_thread`, and `/health` returning a static dict. Requirement 8 needed
verification, not edits. **The hang was never the health endpoints' own doing** — they
were victims of the starved loop.

---

## C. UPLOAD FIX

### Preview no longer resembles an import

Verified by AST rather than by reading: neither `_build_preview` nor `preview_columns`
contains a call to `commit`, `add`, `add_all`, `flush`, `execute`, `bulk_upsert_by_key`,
`publish`, `queue_job`, `refresh_after_import` or any `import_*` orchestrator —
**"NO write/publish calls"** in both.

Preview reads a bounded head of the file, validates the columns through the deterministic
mapping layer, type-checks and reports invalid rows, and returns a capped sample. It
persists nothing, runs no analytics and emits no Kafka event. Its ceiling is
`preview_timeout_seconds = 30`, separate from the import ceiling.

### Import is batched and off the loop

The whole synchronous import runs inside `run_import_blocking`, so the loop stays free.
Rows are deduplicated by business key, then written by `bulk_upsert_by_key` in chunks of
`import_batch_size = 1000`, with the chunk size clamped so one statement can never exceed
PostgreSQL's 65,535 bind parameters. `import_max_rows = 500_000` bounds memory, so file
size is no longer chosen by whoever uploads.

Measured against **real SQLAlchemy 2.0.52 and a real SQLite engine** with a
`before_cursor_execute` statement counter (`t_sqlalchemy.py`, 31 checks):

- 38,279 rows → **79 SQL statements** (39 SELECT + 39 INSERT) in 0.74 s — 485× fewer
  statements than rows, versus one SELECT + one flush per row before.
- All 38,279 rows verified present by `SELECT count(*)`.
- Re-importing 2,500 unchanged rows → **4 statements, 0 INSERTs**, row count unchanged,
  last value winning.
- 40,000 order lines spanning 12,000 distinct customers → **12 SELECTs**, not 12,000.
- The same business key in two tenants stays two rows, and `business_id` appears in the
  compiled WHERE clause.
- A failing write raised `IntegrityError`, left **no partial row**, and the session was
  usable again after rollback.

### Errors instead of silence

| Condition | Status |
|---|---|
| Unreadable / unmappable file | 400 |
| File over the size limit | 413 |
| Valid CSV, invalid content | 422 |
| All import slots busy | **503** + `Retry-After: 10` |
| Dependency unavailable / pool exhausted | 503 |
| Import exceeded `import_timeout_seconds` | **504 from the app**, naming the dataset |
| Success | 201 |

The 504 is deliberate and honest: `import_timeout_seconds = 240` sits below nginx's
`proxy_read_timeout 300s`, so the application answers first and says *which* dataset ran
long. A request no longer sits until nginx invents an error.

---

## D. KAFKA / REDIS / DB FIX

**Kafka.** Publishing is wrapped in a single `asyncio.wait_for` with one shared deadline
(`kafka_publish_timeout_seconds = 15`) covering the *entire* retry loop — a per-attempt
timeout multiplied by a retry count is not a bound. `request_timeout_ms = 10_000` bounds
each broker call, replacing aiokafka's 40 s default. `kafka_startup_timeout_seconds = 20`
means an unreachable broker at boot degrades to "events disabled" instead of preventing
startup. A circuit breaker with a cooldown stops a redial storm. Kafka remains in the
architecture — it is bounded, not removed.

**Redis.** `socket_connect_timeout=5`, `socket_timeout=5`, `health_check_interval=30`, and
every cache read falls back to PostgreSQL. Redis being down degrades latency, not
availability.

**Database.** `pool_size=10`, `max_overflow=10`, and — the important one —
`pool_timeout=10`, so a request that cannot get a connection fails in ten seconds with a
503 rather than waiting forever and looking dead. `pool_recycle=900` stays under
Supabase's idle-connection reaper. Server-side `statement_timeout` (30 s, 120 s for
imports) and `idle_in_transaction_session_timeout` are set through the libpq `options`
connect arg, because **a Python-side timeout cannot abort a query already executing in
the driver** — only PostgreSQL can. `get_db()` rolls back explicitly on exception.

Crucially, `import_max_concurrency = 4` is held at or below `db_pool_size`, so concurrent
imports cannot exhaust the pool and starve every read — the failure mode requirement 4
names specifically.

---

## E. NGINX / DOCKER FIX

**The `wrong ident` line was diagnosed, not silenced.** It reports a DNS reply whose
transaction id matches no outstanding query — the expected consequence of a retry storm
while the upstream is frozen. Fixing the freeze removes the cause. Two real nginx defects
were fixed alongside it:

- `resolver 127.0.0.11 valid=30s ipv6=off;` with `resolver_timeout 5s` and the **variable**
  form `set $upstream http://backend:8000; proxy_pass $upstream$request_uri;`. Without a
  variable, nginx resolves the hostname once at startup and caches that IP forever, so a
  backend container restarting on a new IP yields 502s until nginx is restarted. No
  hardcoded container IPs were introduced.
- `proxy_http_version 1.1` with `proxy_set_header Connection "";`. Without clearing that
  header nginx sends `Connection: close` upstream and every proxied request pays a fresh
  TCP handshake.

Timeouts are ordered so the application always produces the error: `/api/` gets
`proxy_read_timeout 300s` (above the app's 240 s import ceiling) while `/health` gets 15 s,
since a health probe that takes fifteen seconds has already answered the question.

**Compose** (validated structurally with PyYAML; `docker` is unavailable in this
environment): six services — `kafka`, `redis`, `kafka-init`, `backend`, `worker`,
`frontend` — all on the default network, so `backend` resolves by service name. `backend`
listens on 8000 internally. `kafka-init` runs to completion before `backend` and `worker`
start, both of which also wait for `redis` and `kafka` to be *healthy*.

The new `worker` service runs `python -m app.events.worker` from the same image, exposes
no ports, and has its image-level HTTP healthcheck explicitly disabled — it runs a Kafka
consumer, not uvicorn, so that probe could never pass and would have shown a permanently
`unhealthy` container that was working correctly.

---

## F. TEST RESULTS

Every suite below was executed in this environment; the counts are real output.

| Suite | What it exercises | Result |
|---|---|---|
| `t_starvation.py` | asyncio; reproduces the hang and the fix | 4/4 — worst case 1502 ms → 18.1 ms |
| `t_parser.py` | real pandas 2.3.3 CSV hardening | 40/40 — 50,000 rows validated in 0.059 s |
| `t_bulk.py` | chunking, dedupe, tenant scoping | 39/39 |
| `t_sqlalchemy.py` | **real SQLAlchemy + real SQL statement counts** | 31/31 |
| `t_kafka.py` | wedged-broker timeout behaviour | 40/40 |
| `t_publish.py` | publish bounds, error mapping | 55/55 |
| `t_kafka_kwargs.py` | kwargs vs the **real** aiokafka signature | 19/19 |
| `audit_blocking.py` | every `async def` in `backend/app` | **CLEAN** — 103 files, 123 bodies |
| `verify_frontend.mjs` | 77 frontend files via `@babel/parser` | 15/15 |
| parse check | every backend Python file | 133 files, 0 failures |

**Each audit was proved capable of failing.** A green check that has never failed proves
nothing. `audit_blocking.py` was run against a file that reintroduced the bug and correctly
flagged `pd.read_csv`, `session.execute`, `session.commit` and `open` while ignoring the
offloaded version. `t_kafka_kwargs.py` was run against a mutated producer and caught a bad
kwarg, a plausible typo (`linger_millis`) and a dynamically-computed key, exiting 1.

**One real bug was found by this process, in my own work.** `t_kafka_kwargs.py` compared
the kwargs against the installed aiokafka 0.14.0 and reported that `max_block_ms` — which
I had added as a "bound" — does not exist on `AIOKafkaProducer.__init__`, which also has
no `**kwargs` to absorb it. It is a Java-client / kafka-python option, where `send()`
blocks. Passing it raises `TypeError` inside `init_event_producer()`, which catches
`Exception` and falls back to the inline transport — so the effect would not have been a
crash but **Kafka silently disabled for the process lifetime, behind a log line still
reading "Event bus ready"**. That would have violated "do not remove Kafka from the
architecture just to make the application work" while appearing to comply. Removed from
`producer.py`, from `config.py`, and from the startup log, which no longer advertises a
bound that was never applied.

The lesson is recorded because it generalises: the Kafka suite used a stub producer, and a
stub accepts any keyword argument. Behavioural tests against a double cannot catch a wrong
client signature.

**Two "failures" turned out to be my measurement, not the code** — verified by probe before
touching anything. SQLAlchemy's `insertmanyvalues` flattens the parameter tuple, so
`len(parameters)` gave rows×columns rather than rows; and `IN` compiles to a single
expanding placeholder, so a 1,000-key list reports 2 bind params until you pass
`render_postcompile=True`, which reveals the true 1,001.

### What was NOT tested here, and why

**Docker is not available in this environment, so `docker compose up` was never run.** No
HTTP request has ever reached this application here. `pydantic` cannot even be imported —
`backend/.venv` is a macOS/Python-3.12 tree whose `_pydantic_core` is a darwin `.so` — so
**FastAPI cannot be imported and no route was executed end to end.** `frontend/node_modules`
is likewise macOS-only, so `vite build` cannot run and the frontend was verified by parsing
all 77 source files with `@babel/parser` instead. The commands you need to run locally are
below.

---

## G. REMAINING RISKS

Stated plainly rather than reassuringly.

1. **No end-to-end run happened.** This is the largest gap. The logic is proven by real
   pandas, real SQLAlchemy and real aiokafka signature checks, but FastAPI request/response
   handling, Clerk auth, live Supabase and the nginx proxy have not been exercised together.
   The `max_block_ms` bug is direct evidence that a component can pass its behavioural
   tests and still be wrong at the integration boundary.

2. **Analytics refresh after import is off the request path but only loosely bounded.**
   With `ANALYTICS_AUTO_REFRESH_ON_IMPORT=true` (default), a completed import queues
   `refresh_after_import`. With Kafka enabled the worker runs it; with
   `EVENTS_INLINE_FALLBACK=true` it runs in a background asyncio task. Either way the HTTP
   response returns immediately, but a very large tenant's refresh is bounded only by the
   database `statement_timeout`. If dashboards feel slow after a big import, this is where
   to look. Set `ANALYTICS_AUTO_REFRESH_ON_IMPORT=false` for bulk backfills and refresh
   once at the end.

3. **The `worker` container is now load-bearing.** With `KAFKA_ENABLED=true`, cache
   invalidation and RFM refresh happen in the worker. A deployment that starts `backend`
   but not `worker` will serve **stale dashboards with no error anywhere** — a silent
   failure, the kind that is hardest to notice.

4. **`KAFKA_TOPIC_PREFIX` is a latent trap.** If it is ever set, producer and consumer use
   prefixed topic names while `kafka-init` still creates unprefixed ones. Broker
   auto-creation papers over it, but the DLQ's 30-day retention would not be applied — so a
   poison message could age out before anyone looks. Leave it empty unless you also update
   `kafka-init`.

5. **The 65,535 bind-parameter clamp is driver-specific.** It is correct for PostgreSQL, and
   `IMPORT_BATCH_SIZE=999999` was verified to clamp to 65,525. A different database backend
   would need a different ceiling.

6. **`import_max_concurrency = 4` is a throughput ceiling by design.** A fifth simultaneous
   import gets a 503 with `Retry-After` rather than being queued. That is deliberate —
   queueing is how the pool got exhausted — but under heavy multi-tenant load users will
   see 503s, and raising it requires raising `db_pool_size` in step.

7. **Credentials previously committed to `backend/.env.example` should still be rotated.**
   They were scrubbed from the file, but they were in the repository.

---

## Commands to run locally

Docker is unavailable in this environment, so these must be run on your machine. Please
run them in order and send me any output that differs from what is described.

```bash
# 1. Structure — must print the six services with no error
docker compose config --quiet && docker compose config --services

# 2. Build and start
docker compose up --build

# 3. Liveness and readiness. /health must answer in milliseconds, always.
curl -si  localhost:3000/health        | head -1
curl -s   localhost:8000/health/ready  | python3 -m json.tool

# 4. Previews. Each must return 200 quickly and persist NOTHING.
for d in customers products inventory orders; do
  echo "--- $d preview"
  curl -s -o /dev/null -w "  http=%{http_code}  time=%{time_total}s\n" \
    -X POST localhost:3000/api/v1/imports/$d/preview \
    -F "file=@/path/to/$d.csv"
done

# 5. THE CRITICAL CHECK — the loop must stay free during an import.
#    Run this in a second terminal while step 6 is uploading.
while true; do
  curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" localhost:3000/health
  sleep 0.5
done
# Every line must read 200 with a time in the milliseconds. A rising time or a
# hang means blocking work is back on the event loop.

# 6. Real imports, in dependency order
for d in customers products inventory orders; do
  echo "--- $d import"
  curl -s -w "\n  http=%{http_code} time=%{time_total}s\n" \
    -X POST localhost:3000/api/v1/imports/$d \
    -F "file=@/path/to/$d.csv"
done

# 7. Dashboard must still respond, and now show the imported data
curl -s localhost:3000/api/v1/dashboard/summary | python3 -m json.tool

# 8. Webhook settings must return 200 — "not configured" is data, not an error
curl -s localhost:3000/api/v1/webhooks/config | python3 -m json.tool
curl -s "localhost:3000/api/v1/webhooks/deliveries?limit=50" | python3 -m json.tool

# 9. Repeated uploads — the original bug appeared only after the first success
for i in 1 2 3; do
  curl -s -o /dev/null -w "run $i: http=%{http_code} time=%{time_total}s\n" \
    -X POST localhost:3000/api/v1/imports/customers -F "file=@/path/to/customers.csv"
  curl -s -o /dev/null -w "        health=%{http_code} %{time_total}s\n" localhost:3000/health
done

# 10. Confirm the worker is consuming and the events are landing
docker compose logs worker | tail -30
docker compose ps
```

Structured log lines to look for during step 6, in this order: `import_started`,
`import_validation_started`, `import_validation_completed`, `import_database_started`,
`import_database_completed`, `import_event_publish_started`,
`import_event_publish_completed`, `import_completed`. Each carries `request_id`,
`business_id`, dataset, row counts and duration. None carries a filename, a row value, or
any credential.
