# Phase 2 — Implementation Report

Kafka event streaming, Redis caching, webhook ingestion, asynchronous analytics jobs,
analytics engine integration, and a rebuilt dashboard, added to the existing
`cbpa-major-main` codebase.

The architecture was not replaced. It remains a Python/FastAPI modular monolith over
SQLAlchemy and Supabase PostgreSQL, with Clerk authentication and Alembic migrations, and
the route → service → repository layering is intact. Phase 1 ML code was reused rather than
rewritten.

**Read section 11 and 15 before trusting any claim in this document.** This environment has
no PyPI access and no Docker, so a meaningful set of things could not be executed and are
identified as such rather than assumed to work.

That warning applies to this document's own prose, not only to the code. After a first draft
was written, every factual claim in it — and in `README.md` and `docs/PHASE_2.md` — was
audited against the source. Thirteen were wrong. They are corrected here, the corrections are
marked where the original error is instructive, and two of them turned out to be real defects
rather than description errors. Section 15 lists both.

---

## 1. What was inspected first

Before writing anything I read the existing codebase to find the seams Phase 2 had to fit
into: `app/main.py` for how routers mount and what the app lifecycle looked like,
`app/config.py` for the settings pattern, `app/auth.py` and the `BusinessContext` dependency
for how the tenant is resolved from a Clerk token, the nine `app/db/models/` modules and ten
`app/repositories/` modules for the persistence conventions, the existing Phase 1 route
modules (`upload.py`, `analytics.py`, `inventory.py`), the Phase 1 ML services including
`services/product_analysis.py`, the initial Alembic revision that had already been applied
to the live database, the existing test suites, and the whole `frontend/src` tree.

Three findings shaped the work. The tenant is derived server-side through
`get_business_context`, so every new endpoint could use the same dependency and never accept
a `business_id` from a client. The initial migration was already applied to real data, so the
Phase 2 migration had to be strictly additive. And the frontend's Phase 1 pages were built
around a dataset-upload-then-view flow that the new tenant-scoped API does not match, which
is why the shell was rebuilt rather than retrofitted.

## 2. What was implemented

All six objectives. Kafka event streaming with six topics, a typed envelope, tenant
partitioning, retries and a dead-letter topic. Redis caching with tenant-scoped keys,
prefix invalidation on writes, and three interchangeable backends. Webhook ingestion with
HMAC-SHA256 verification, timestamp tolerance, and an idempotency ledger. An asynchronous
analytics job lifecycle with conditional claiming. An analytics engine wrapping the existing
RFM, segmentation and product-analysis code behind a tenant-scoped service. And a rebuilt
React dashboard on `/api/v1` with eleven pages.

Both infrastructure dependencies are optional and default to off. `KAFKA_ENABLED=false` and
`REDIS_ENABLED=false` produce a working application that reads from PostgreSQL and executes
event handlers in-process, which is the documented development setup.

## 3. Files and directories created

Backend, six new packages:

| Path | Contents |
| --- | --- |
| `app/events/` | `producer.py`, `consumer.py`, `worker.py`, `schemas.py`, `topics.py`, and `handlers/` with five domain handler modules plus `_shared.py` |
| `app/cache/` | `client.py` (three backends), `keys.py` (key scheme and invalidation fan-out), `service.py` (`get_or_set`) |
| `app/webhooks/` | `signature.py`, `service.py`, `mapping.py`, `records.py` |
| `app/analytics/` | `engine.py`, `rfm.py`, `scoring.py`, `segmentation.py`, `job_types.py` |
| `app/api/routes/` | Eight `v1_*.py` route modules plus `health.py` |
| `app/core/` | `errors.py`, `exceptions.py`, `logging.py`, `middleware.py`, `multi_tenancy.py` |

Also `app/services/analytics_job_service.py`, `app/repositories/webhook_repository.py`,
`app/repositories/event_repository.py`, `app/db/models/events.py`, the Alembic revision
`20260815_phase2_events_webhooks_analytics.py`, `docker-compose.yml`,
`backend/requirements-dev.txt`, `frontend/.env.example`, `docs/PHASE_2.md`, and this report.

Frontend: `src/api/` (a seven-module client layer over `client.js`), `src/pages/app/` (ten
new pages), `src/components/shell/`, `src/app/` (`ApiAuthBridge.jsx`,
`SystemStatusProvider.jsx`, `navigation.js`), plus `hooks/useApiResource.js`,
`hooks/useJobPolling.js`, and `utils/format.js`.

Tests: thirteen new files in `backend/tests/` (`check_api_wiring.py`, `check_dependencies.py`,
`check_env_example.py`, `check_event_handlers.py`, `check_job_claim.py`, `check_migration.py`,
`check_rfm_passthrough.py`, `check_webhooks.py`, `test_cache_isolation.py`, `test_compose.py`,
`test_job_claim.py`, `test_scoring.py`, `test_no_committed_secrets.py`), the runner
`run_all.py`, the mutation harness `mutation_probe.py`, and on the frontend
`src/__checks__/wiring.check.mjs`, `src/__checks__/proxy.check.mjs`,
`src/__checks__/mutation_probe.mjs`, `src/app/__checks__/readiness.check.mjs` and
`src/utils/__checks__/format.check.mjs`.

## 4. Files significantly modified

`app/main.py` mounts nine new routers, wires startup and shutdown for the producer, cache and
worker, and installs the error handlers and request-id middleware; the three Phase 1 routers
are still mounted unchanged. `app/config.py` grew from a handful of settings to 43 fields
reading 42 environment variables, with explicit parsing — `_env_bool` exists because the
obvious `bool(os.getenv(...))` makes `KAFKA_ENABLED=0` mean `True`. `app/db/models/analytics.py`, `customer.py` and `order.py`
gained the columns and indexes the migration adds. `frontend/src/App.jsx` was rewritten
around the `/app` shell with Phase 1 URLs kept as redirects. `frontend/vite.config.js` had
its proxy table corrected (section 15). `backend/requirements.txt` was reorganised and five
undeclared dependencies added (section 15). `README.md` was rewritten; it had described
Kafka, Redis, PostgreSQL and Docker as "intentionally not implemented".

`backend/.env.example` was scrubbed of live credentials (section 15 — **this requires
action from you**), and its `CORS_ORIGINS` value was corrected to match the default in
`config.py`; it had named Vite's port 5173, which this project's dev server does not use.

`app/cache/keys.py` gained `assert_tenant_prefix` and `app/cache/client.py` now calls it, and
`assert_tenant_scoped`, on the delete paths that previously had no tenant guard (section 15).

## 5. Kafka architecture

Six topics — `raw.imports`, `customers.events`, `orders.events`, `inventory.events`,
`analytics.jobs`, `events.dlq` — created by `kafka-init` and namespaceable through
`KAFKA_TOPIC_PREFIX`. The first five get three partitions and a seven-day retention;
`events.dlq` gets **one partition and thirty days**, because total ordering matters more than
parallelism on a topic a human reads, and a poison message must outlive the traffic that
produced it. Topic strings exist only in `app/events/topics.py`.

Messages are `Event` envelopes carrying event id, type, timestamp, `business_id` and payload.
Partitioning is by `business_id`, which gives per-tenant ordering and prevents one tenant's
volume from disturbing another's.

Two decisions carry most of the weight. Handlers **commit to Postgres before publishing**: if
the publish then fails the database is still correct and the event can be re-triggered,
whereas publishing first risks a consumer acting on a row that was never written. And the
DLQ is excluded from the worker's subscriptions — a DLQ that is automatically re-consumed is
a retry loop, and the poison message circulates forever.

Consumed events are treated as data, not authority: a handler re-derives the tenant from the
row it operates on rather than trusting the envelope, so a forged event cannot cross tenants.
When Kafka is disabled, `EVENTS_INLINE_FALLBACK` runs the same handler in-process after the
commit.

## 6. Redis architecture

Keys are `cbpa:v1:{business_id}:{namespace}[:{discriminator}]`. The tenant precedes the
namespace so that each tenant's keys form one contiguous prefix, making invalidation a
bounded `SCAN` over `cbpa:v1:{business_id}:*`. Namespace-first would force a full-keyspace
walk that touches other tenants' keys. `assert_tenant_scoped` enforces the shape inside the
backend on reads and writes, and `assert_tenant_prefix` does the same for bulk deletes, so a
hand-built string cannot bypass either. A colon in `business_id` is **rejected** —
`_clean_business_id` raises `CacheKeyError` — so a crafted identifier cannot close its own
segment and forge another tenant's namespace; other key parts are sanitised to `_` instead,
since a filter value containing a colon is ordinary.

The prefix guard was added late, in response to the audit described in section 11: `delete()`
and `delete_prefix()` had no tenant assertion, so `delete_prefix("")` would have cleared every
tenant's entries at once. That is the operation with the largest blast radius in the cache
layer and it was the one operation with no guard on it.

Writes invalidate by prefix, because the writer does not know which discriminators exist. The
fan-out that matters is orders, which invalidates dashboard, RFM, segments and customers —
all derived from order history.

Invalidation uses `SCAN`, never `KEYS`, since `KEYS` blocks the server for a full keyspace
walk. `get_or_set` swallows all backend exceptions and falls through to the loader: a cache
may cost latency, never correctness. `None` is never cached; empty values (`[]`, `{}`, `0`,
`False`, `""`) are cached and served as hits. The Redis URL is never logged because it can
embed a password.

## 7. Webhook architecture

`POST /api/v1/webhooks/{source}` verifies header presence and form, then timestamp freshness
against `WEBHOOK_TOLERANCE_SECONDS`, then the HMAC. The signed message is
`{business_id}.{timestamp}.{raw body}`, HMAC-SHA256, hex, sent as
`X-Webhook-Signature: sha256=<digest>`.

The raw body bytes are signed — re-serialising parsed JSON would change whitespace and key
order and break every correctly computed signature. Comparison is `hmac.compare_digest`,
because an early-exit comparison leaks through timing how much of a forged signature was
correct.

A webhook has no Clerk session, so `business_id` arrives in `X-Business-Id`. This is the only
place a tenant identifier comes from a request, and it is safe because it is inside the signed
message: altering the header invalidates the HMAC, and forging a new one requires the secret.

Verified deliveries are recorded in `webhook_events`, deduplicated by a unique index on
`(business_id, source, external_event_id)` rather than by application logic, in the same
transaction as their effect. A replay is acknowledged with 200 and does no work; a new
delivery returns 202. The tenant and source are part of the key because provider-side ids are
only unique within the provider, and two tenants using the same provider can legitimately
receive the same id. A sender that supplies no id gets one derived as `sha256:` over the
canonical signed message, so byte-identical replays still collide. This is a different table
from `processed_events`, which is the Kafka consumer's redelivery ledger. No secret and no raw
signature header is ever logged; rejections log a reason code only.

Note that `POST /api/v1/webhooks/{source}` is the one Phase 2 endpoint with no Clerk
dependency — it takes only `Depends(get_db)`. That is deliberate and is the point of the
signature scheme, but it does mean the blanket statement "everything added in Phase 2 requires
a Clerk bearer token" is false, and the first draft of the README said exactly that.

## 8. Analytics job flow

`POST /api/v1/analytics/jobs` inserts a `queued` row, commits, then publishes
`analytics.job.requested`. The response reports whether dispatch succeeded, because a queued
but undispatched job is a real state and reporting it as success would hide a broken worker.

The worker claims a job with a conditional `queued → running` update, so two workers handling
the same redelivered event cannot both run it. Terminal states are `completed` or `failed`,
each publishing its own event, and completion invalidates the tenant's analytics cache
prefixes.

That sentence was false until this section was checked against the code, and the correction was
a real bug fix rather than a rewording. `_claim_run` read the row with a plain `SELECT`, tested
`run.status in TERMINAL_STATUSES` in Python, then assigned and committed. Two defects
compounded: between the read and the write a second worker could read the same `queued` row and
pass the same test, and `TERMINAL_STATUSES` is `(completed, failed)`, so a row already in
`running` passed the guard outright. Kafka is at-least-once and a consumer-group rebalance can
replay an in-flight message, so this is an expected sequence rather than a rare interleaving —
and the cost is not a wasted cycle: a job replaces the tenant's RFM and segment snapshot, so two
concurrent runs interleave two sets of writes over the same rows. The `processed_events` ledger
does not save it, because it deliberately re-claims a row still marked `processing`.

The claim is now one statement, `AnalyticsRepository.claim_run`:
`UPDATE analytics_runs SET status='running', started_at=…, error_message=NULL WHERE id=… AND
business_id=… AND status IN ('queued','pending')`, returning `rowcount == 1`. The database picks
the winner. The tenant is in the `WHERE` clause, so a guessed job id from another tenant claims
nothing. The legacy `pending` status is included because Phase 1 rows use it where Phase 2 writes
`queued` — omitting it would strand every pre-rename job as permanently `queued`, and a mutation
covers that too.

`running` is deliberately *not* claimable, which trades one failure for a safer one: a crashed
worker leaves its job unclaimable rather than claimable by two workers at once. Recovering those
needs a lease with an expiry, which is listed in section 15 and recommended for Phase 3.

Two tests, because neither alone is sufficient. `tests/test_job_claim.py` proves the behaviour
against SQLite through SQLAlchemy from **two independent sessions** — one session would pass
against the old code too, since a session sees its own uncommitted write — and needs pytest, so
it did not run here. `tests/check_job_claim.py` asserts the structural property with `ast` and
runs everywhere: that the claim is an `update()` and not a `select()`, that it is conditional on
status, scoped to one tenant and one id, decided by `rowcount == 1`, and that the service no
longer assigns `.status` itself. Six mutations cover the regression paths, including reverting to
the read-then-write and re-adding `running` to the claimable set.

`app/analytics/` wraps the Phase 1 ML functions rather than reimplementing them. RFM is
computed by aggregating `orders` and `order_items`; genuine recency/frequency/monetary
columns in an uploaded file are used directly when present. **Nothing is synthesised** — a
customer with no order history is excluded rather than scored, distributions count only
labelled rows, and the response reports how many rows were unscorable. The ARIMA forecast
returns `None` without `statsmodels` rather than substituting an estimate.

## 9. Frontend features

`/app` is a protected MUI shell — responsive sidebar, top bar, theme toggle, error and
loading states — with eleven pages: Dashboard (KPI cards, revenue trend, RFM and segment
distributions, top customers), Customers (paginated, searchable, drill-through), RFM,
Segments, Products, Orders, Inventory (with low-stock view), Data Import (CSV upload for
customers, products, inventory, orders), Analytics Jobs (submit, and poll status via
`useJobPolling`), Settings, and the ported Product Analysis.

Every page calls `/api/v1` through `src/api/`, which layers on a single `client.js` that
injects the Clerk bearer token via `ApiAuthBridge`. No page sends a `business_id`. Phase 1
URLs redirect into the shell so existing bookmarks work. Route-level `lazy()` splitting keeps
the initial chunk small, with MUI, Recharts and React in separate vendor chunks.

`VITE_API_BASE_URL` defaults to empty, meaning same-origin: the dev proxy and a production
reverse proxy then behave identically, and no `localhost` URL is compiled into the bundle.

## 10. Database changes and migrations

One additive revision, `20260815_phase2_events_webhooks_analytics.py`, on top of the already
applied initial schema. It creates two tables — `processed_events` (the Kafka consumer's
redelivery ledger) and `webhook_events` (the webhook delivery log, which is also where webhook
idempotency is enforced) — adds thirteen columns across `customer_rfm`, `customer_segments`
and `analytics_runs`, and creates 24 indexes, including several the models declared but the
initial migration never created.

Every added column is nullable or carries a server default, so applying it to populated tables
cannot fail. `upgrade()` performs no `DROP`, no type change, and no `SET NOT NULL` on a
pre-existing column. `downgrade()` drops only what `upgrade()` created.

Two qualifications on that, because the plain phrasing "no `nullable=False`" appeared in an
earlier draft of this report and is not true as written. The token occurs sixteen times in the
functions `upgrade()` calls: fifteen are column definitions inside the two `op.create_table`
calls, where non-nullability is free because the table is new and empty, and one adds a
`nullable=False` `created_at` to the pre-existing `analytics_runs` — safe only because it
carries a `CURRENT_TIMESTAMP` server default, and a change that would fail on a populated table
without one.

`upgrade()` also **relaxes** three existing columns from `NOT NULL` to nullable via
`_make_nullable`: `customer_rfm.dataset_import_id`, `customer_segments.dataset_import_id` and
`analytics_runs.dataset_import_id`. Widening a constraint cannot fail on existing rows, but the
mechanism deserves stating: on PostgreSQL the helper emits a plain `ALTER COLUMN`, while on
SQLite it uses `op.batch_alter_table`, which drops and recreates the table. So "no drop" is
true of the production dialect and not of SQLite. The migration's own docstring says this; the
report previously did not.

`check_migration.py` verifies these properties statically, without SQLAlchemy: it parses the
model modules and both revisions and asserts a single-headed linear chain, model/migration
column agreement in both directions for the extended tables, additive-only safety, index
coverage for every `index=True`, and that the migration file contains no credentials.

**The migration has not been run.** No SQLAlchemy, no Alembic runtime and no network route to
Supabase exist here. You need to run `alembic upgrade head`.

## 11. Tests executed, and results

`python3 backend/tests/run_all.py` — **17 passed, 0 failed, 6 skipped**:

| Check | Result |
| --- | --- |
| `check_api_wiring.py` | 120 passed |
| `check_dependencies.py` | 129 passed |
| `check_env_example.py` | 133 passed |
| `check_event_handlers.py` | passed |
| `check_job_claim.py` | 26 passed |
| `check_migration.py` | 87 passed |
| `check_rfm_passthrough.py` | 70 passed |
| `check_webhooks.py` | 132 passed |
| `test_cache_isolation.py` | 30 passed |
| `test_compose.py` | 10 passed |
| `test_no_committed_secrets.py` | 5 passed |
| `test_scoring.py` | 22 passed |
| `wiring.check.mjs` | 1263 passed |
| `resolve.check.mjs` | 534 passed |
| `format.check.mjs` | 94 passed |
| `readiness.check.mjs` | 71 passed |
| `proxy.check.mjs` | 34 passed |

Skipped, each attributed to the specific missing package: `test_db_connectivity.py`
(sqlalchemy), `test_ingestion.py` (pytest), `test_job_claim.py`, `test_models.py`,
`test_repo_crud.py`, `test_repositories.py` (pytest, sqlalchemy). These are the Phase 1 suites,
a connectivity probe, and the new job-claim suite. **They were not run, so I cannot claim the
Phase 1 tests still pass** — 32 test functions by static count across those four modules — only
that nothing in the Phase 1 modules they cover was modified except the three model files, whose
changes the migration check verifies against the migration.

`test_job_claim.py` is the one new suite that could not run here, and it matters more than the
others because it is the behavioural half of the concurrency fix in section 4. Its seven tests
drive the real repository against SQLite from two independent sessions. **Run it:**
`cd backend && pytest tests/test_job_claim.py`. What did run here is
`check_job_claim.py`, which asserts the same property structurally through `ast` and needs
nothing installed.

Which flows are proved by *executed* behaviour rather than source inspection, since the
distinction decides how much the green result is worth:

| Flow | Executed? | By what |
| --- | --- | --- |
| Webhook HMAC, replay window, idempotency, two-tenant exchange | yes | `check_webhooks.py` imports the real `signature`/`records`/`mapping`/`evaluate_delivery` and signs with real HMAC |
| Redis key scheme, tenant isolation, TTL, empty-value caching | yes | `test_cache_isolation.py` drives the real `InMemoryCache`/`RedisCache` |
| Kafka handler dispatch and inline fallback | yes | `check_event_handlers.py` runs the real dispatch path |
| RFM scoring and passthrough | yes | `test_scoring.py`, `check_rfm_passthrough.py` |
| Analytics job claim | structurally here, behaviourally on your machine | `check_job_claim.py` ran; `test_job_claim.py` needs pytest |
| Migration vs models | structurally | `check_migration.py` compares ASTs, column for column |
| API tenancy and wiring | structurally | `check_api_wiring.py` (parts execute the real sanitisers) |

### The frontend production build did not run here

Section 12 has the diagnosis: `node_modules` was installed on macOS, so the Linux bundler
binaries are absent and the registry is 403-blocked. **`cd frontend && npm run build` is yours
to run.**


### The checks were verified capable of failing

A check that has never been seen to fail is evidence that it runs, not that it works. Two
mutation harnesses break one thing at a time on a throwaway copy and require the matching
check to notice:

- `backend/tests/mutation_probe.py` — **49 of 49 caught**
- `frontend/src/__checks__/mutation_probe.mjs` — **17 of 17 caught**

This found real problems five times. A cache test named the right rule and measured the
wrong thing: it asserted the loader re-runs after `None`, which holds even with the guard
deleted, because `None` round-trips through JSON as `"null"` and the read path treats it as a
miss. It now asserts the entry count, and a companion test covers empty-but-real values.
Separately, `run_all.py` was reclassifying genuine failures as skips whenever a checker's
output merely *contained* a `ModuleNotFoundError` string — a checker asserting on error
handling legitimately prints one — which would have reported a green build over a real
regression; it now requires the error to be the terminal line with no verdict markers
present. Third, the probe's own tree was incomplete three times, and said so loudly instead of
passing quietly: it lacked the requirements files, then the root-level scripts, then
`.env.example`, and in each case the omission turned a group of mutations into no-ops rather
than into false confidence. Fourth, one of the new mutations survived correctly — it had been
anchored to the string `REDIS_ENABLED=false` inside a prose comment rather than to the
assignment, so it renamed a word in a sentence; the check was right and the mutation was
wrong, which is a distinction worth keeping straight when a probe reports a survivor.

Sixth, and found while writing a mutation rather than by running one: `resolve.check.mjs`
originally asserted case-exactness using `existsSync`, which made that assertion
unfalsifiable on Linux. A wrong-case import fails *resolution* on a case-sensitive
filesystem, so the resolution check would fire first and the case check could never fail —
while being the only thing that catches the defect on macOS, where a case-insensitive
filesystem is what introduces it. The check now resolves case-insensitively on purpose,
emulating the permissive filesystem, and asserts exactness as a separate step. Both
platforms now fail for the same reason, and the mutation that renames `./app/navigation.js`
to `./app/Navigation.js` is caught here on Linux. The lesson generalises: asking "can I
write a mutation that this catches?" is a design review of the check, not just a test of it.

The two harnesses also disagree in an instructive way with the checks they exercise: three
mutations exist only because the audit in section 15 found the guard they test was missing
entirely. A mutation cannot prove a check exists — only that a check which exists can fail.

### What was not tested

No pytest suite, no live database, no Kafka, no Redis, no Docker, no `vite build`. The
modules that import SQLAlchemy or FastAPI were compiled (`py_compile`) and verified
statically, but the SQL they generate and the ASGI behaviour they produce were exercised only
at the pure-logic boundary — key construction, scoring, signature computation, handler logic
against fakes. The Redis code path was exercised by injecting a fake `redis.asyncio` module
into `sys.modules`, which tests our logic, not the real client's.

## 12. Frontend build result

**Not run**, and the reason is an environment mismatch rather than anything about the code.
`frontend/node_modules` is present and Vite 6 is installed, but it was installed on macOS: the
only platform binaries under it are `@rollup/rollup-darwin-arm64` and `@esbuild/darwin-arm64`,
while this Linux container needs the `linux-arm64` builds. `npm run build` therefore dies with
`Cannot find module @rollup/rollup-linux-arm64-gnu`, and `./node_modules/.bin/esbuild` with
`Exec format error` — the same story from the other bundler. The registry returns 403 for every
request, so neither can be fetched. **`cd frontend && npm run build` is yours to run.**

What was verified instead, by parsing the real source: every relative, lazy and side-effect
import resolves to a file that exists and the named binding is actually exported there, every
route maps to a component that exists, every API function a page calls is exported by the module
it is imported from, no page reads a `business_id` from anywhere, and the dev proxy covers exactly
the paths the routed app requests. That is 1462 assertions across four files. Separately, all 8
bare package imports across 75 source files were confirmed declared in `package.json`
(`@clerk/clerk-react`, `@mui/icons-material`, `@mui/material`, `axios`, `react`, `react-dom`,
`react-router-dom`, `recharts`) — the frontend equivalent of the dependency drift that
`check_dependencies.py` exists to catch on the backend, and the one class of build failure the
resolver checks cannot see.

None of that is a build. What it cannot catch: a JSX syntax error in a file the resolver reaches
but never parses as JSX, and any Rollup-specific failure that only surfaces at chunking time.

## 13. Backend startup result

**Not run.** FastAPI, SQLAlchemy, pydantic and uvicorn are not installed and cannot be. The
existing `backend/.venv` is a macOS Python 3.12 environment and is unusable from this Linux
sandbox.

Verified instead: every module compiles under Python 3.10 (no 3.12-only syntax was used, so
your 3.12 target is safe), and `check_api_wiring.py`'s 120 assertions confirm that every route
module in `app/api/routes/` is mounted in `main.py`, that no route anywhere accepts a
client-supplied `business_id`, and that startup and shutdown wire the producer, cache and worker
in a consistent order. `uvicorn app.main:app` is yours to run.

Both of those first two claims were false when an earlier draft made them, and the fix was to
build the checks rather than to soften the sentence.

The mount assertion covered two routers by name — the webhook and dashboard ones — so the other
seven could have been written, imported and never mounted, which serves 404 for every path in
the module while the module's own contents look perfectly correct in isolation. It now
enumerates the directory.

The tenancy assertion was worse, because it was the one the security constraint rests on. It
looped over two sources, one of which (`dashboard_service.py`) is a service and not a route
module at all, and it matched only three literal spellings of a client-supplied parameter. It
now walks every function in all nine route modules plus the three legacy routers, pairs each
parameter with *its own* default via `ast`, and flags any tenant parameter sourced from `Body`,
`Query`, `Form`, `Header`, `Path` or `Cookie`.

Writing it surfaced a gap that the parameter-shaped check would have missed entirely: the one
route in this codebase that does take a tenant from the request does not declare it as a
parameter at all — `receive_webhook` reads `request.headers.get(...)` directly. A check that
only inspected signatures would have reported the property as globally enforced while never
examining the single place it is actually exercised. The check now also detects a tenant
assigned or passed from `request.headers`, `request.query_params`, `request.cookies` or
`request.path_params`.

That leaves exactly one exemption, `v1_webhooks.py:receive_webhook`, pinned to the function
rather than the file so the next route added to that module is not silently exempt too. Because
an exemption is a hole in the property, three further assertions require its justification to
still hold: the route must delegate to the verifying service, that service must call
`signature.verify(`, and the signed message must still cover the tenant
(`canonical_message(str(business_id)`). Without those, deleting the HMAC check would leave a
route taking its tenant from an unauthenticated header and a suite calling it exempt. A mutation
covers that exact sequence.

## 14. Docker and infrastructure

```bash
docker compose up -d
docker compose run --rm kafka-init
```

Kafka in KRaft mode — no Zookeeper — and Redis with a named `redis-data` volume. Supabase is
hosted and deliberately not containerised: running a second Postgres in a container would
create a competing store for tenant data.

Then set `KAFKA_ENABLED=true` and `REDIS_ENABLED=true` in `backend/.env` and restart the
backend. Confirm with `curl localhost:8000/health/ready`, which reports each dependency's
status without disclosing connection strings.

**Compose was never executed** — no Docker binary here. `test_compose.py` parses the file and
asserts the ten things that are easy to get wrong and expensive to debug, the listener
configuration above all: get it wrong and bootstrap succeeds, metadata returns an address the
client cannot reach, and every produce times out half a minute later with no startup error.
It also checks the single-node replication factors, without which the internal offsets topic
cannot be created and consumer-group commits fail.

## 15. Known limitations

**Two credential findings, one needing action from you.**
`backend/.env.example` contained live Clerk and Supabase credentials, and a test fixture in
what is now `check_api_wiring.py` contained the live Supabase database password verbatim.
Both are now placeholders, and `test_no_committed_secrets.py` scans the tree to keep it that
way. **Deleting a line does not un-publish it — rotate the Clerk secret key and the Supabase
database password.** That the same password appeared independently in two files is itself the
argument that rotation is not optional.

**Untested at the boundaries that matter most.** Sections 11 through 14 list this in full.
The short version: no pytest run, no migration applied, no server started, no bundle built,
no broker or cache contacted. Static verification is a floor, not a substitute.

**Dependencies had drifted badly, and this was found late.** Eight distributions were imported
and declared nowhere: `redis` and `aiokafka` — the entire Phase 2 infrastructure —
`statsmodels`, `pydantic` and `starlette` (imported directly, declared only transitively via
FastAPI), and `pytest`, `PyYAML`, `requests`. Nothing failed locally because a working machine
had most of them installed as transitive dependencies of something else. A clean
`pip install -r requirements.txt` followed by `REDIS_ENABLED=true` would have failed with a
log line telling the operator to "install requirements" — which is what they had just done.
Now fixed, with `requirements-dev.txt` split out and `check_dependencies.py` (129 assertions,
12 mutations) enforcing the correspondence in both directions.

**Dead code that cannot be deleted here.** Eight Phase 1 page components and three
`*UploadTab.jsx` components are no longer routed. Deleting them is safe; this environment
cannot delete files.

**Documentation drifted from the code, and an audit was needed to find it.** After writing
these three documents I had them fact-checked against the source, and thirteen claims were
wrong or misleading — including the wrong table and key for webhook idempotency, wrong DLQ
partition and retention figures, a class name that does not exist (`LocalCache`), a signature
reason code that does not exist, "colons are stripped" where the code raises, and a
"limitation" that was already configured. All are corrected above, and two of them turned out
not to be documentation bugs at all but real defects, described in the next two entries. The
generalisable lesson is that prose asserting a property of code is unverified by construction:
the checks in `backend/tests/` are the only claims in this delivery that are mechanically tied
to the source.

**Two real defects surfaced by that audit, now fixed.** `delete()` and `delete_prefix()` had no
tenant assertion, so `delete_prefix("")` would have cleared every tenant's cache — the widest
blast radius in the cache layer was the one operation with no guard. Fixed with
`assert_tenant_prefix`, which also rejects a prefix stopping mid-identifier, plus two tests and
three mutations. Separately, `.env.example` set `CORS_ORIGINS` to Vite's default port 5173
while `config.py` defaulted to 3000 and 3001, so `cp .env.example .env` produced different CORS
behaviour from running with no `.env` at all. Fixed, and `check_env_example.py` (133 assertions,
3 mutations) now asserts the two agree in both directions.

**A third, more serious defect surfaced the same way: the analytics job claim was a
read-then-write.** Two workers handed the same redelivered message could both execute one job and
interleave two sets of writes over one tenant's RFM and segment snapshot, and a job already in
`running` passed the guard outright because `TERMINAL_STATUSES` does not include it. Section 8
describes the fix — a single conditional `UPDATE` decided by `rowcount == 1` — with a behavioural
suite, a structural checker and six mutations. It is worth naming how this was found: not by a
test, but by checking a *documentation* claim against the code. The prose asserted a property the
implementation did not have, and the prose was written by the same process that wrote the code.

**A crashed worker leaves its job stuck in `running`.** This is the deliberate cost of the fix
above: `running` is excluded from the claimable set, so a worker that dies mid-job leaves a row
no one will pick up, and the job reports `running` indefinitely rather than failing. The
alternative — allowing `running` to be re-claimed — is the bug that was just removed. Closing it
properly needs a lease: a `claimed_until` column, a heartbeat, and a claim predicate of
`status='queued' OR (status='running' AND claimed_until < now())`. Recommended for Phase 3, and
until then a stuck job is an operator concern.

**`NullCache` performs no tenant assertions.** `InMemoryCache` and `RedisCache` both call
`assert_tenant_scoped` on every read and write and `assert_tenant_prefix` on every bulk delete,
but the no-op backend used when caching is disabled entirely accepts anything, including an
unscoped key or `delete_prefix("")`. There is no isolation consequence, because it stores
nothing and returns nothing — but it does mean a malformed key raises in one configuration and
passes silently in another, so a key-shape bug can reach production having never failed locally.
The claim in `docs/PHASE_2.md` that the guards are enforced "in the cache backend itself" is
therefore true of both real backends and not of the null one.

**Redis eviction is configured; the local fallback is the bounded one to watch.** An earlier
draft listed the absence of `maxmemory-policy` as a limitation. That was false:
`docker-compose.yml` sets `--maxmemory 256mb` and `--maxmemory-policy allkeys-lru`. The real
constraint is that 256mb is a development figure chosen without measurement, and that
`InMemoryCache` — the `CACHE_LOCAL_FALLBACK` backend, bounded at 2000 entries — is per-process,
so with multiple workers each holds its own copy and they can disagree until their TTLs expire.

**The worker runs in-process.** `app/events/worker.py` starts with the API. That is correct
for development and wrong for production, where consumers should scale separately from the
web tier.

**No cross-language route-agreement check.** The frontend checks assert the client is
internally consistent and the backend checks assert the routers are mounted, but nothing
verifies that a path the client requests is a path the backend serves. A renamed endpoint
would pass both suites and fail at runtime.

## 16. Recommendations for Phase 3

Rotate the two credentials in section 15 first; nothing else on this list matters as much.

Then close the verification gaps, in the order that buys the most: run the pytest suite and
`alembic upgrade head` against a branch database; add integration tests with
`testcontainers` for Kafka and Redis so the real clients are exercised rather than fakes; and
add the cross-language route-agreement check, which is the one class of defect the current
1400-plus frontend assertions structurally cannot catch.

Operationally, split the worker into its own process, size Redis `maxmemory` against a measured
working set rather than the 256mb development figure, and put a real consumer-lag metric behind
`/health/ready` — a worker that is running but 40,000 events behind currently reports healthy.

Beyond that, the natural product work is the Phase 3 scope already sketched in the original
plan: persisted analytics history with trend comparison, a recommendation service, campaign
and notification services consuming the existing event topics (which is why the topics are
domain-shaped rather than one `events` firehose), and object storage for raw import files
so a failed import can be replayed from its source rather than re-uploaded.
