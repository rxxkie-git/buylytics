# Phase 2 architecture

How event streaming, caching, webhook ingestion and asynchronous analytics fit together,
and why each is built the way it is. For setup instructions see the README.

The organising constraint is that **PostgreSQL is the only source of truth**. Redis holds
copies of things Postgres can recompute. Kafka carries notifications that work has happened.
Losing either one costs latency or delay, never data — and every read path is written so
that an unavailable dependency falls through to Postgres rather than failing.

The second constraint is that **`business_id` never comes from the client**. It is derived
from the verified Clerk session and threaded through every query, cache key, event envelope
and job row. The one exception is a webhook, where there is no user session; that case is
handled explicitly below.

## Event streaming

### Topics

Six topics, created by `docker compose run --rm kafka-init`. `KAFKA_TOPIC_PREFIX` namespaces
them all, so several environments can share one broker without cross-talk.

| Topic | Partitions | Retention | Carries |
| --- | --- | --- | --- |
| `raw.imports` | 3 | 7 days | `import.received`, `import.completed`, `import.failed` |
| `customers.events` | 3 | 7 days | `customer.created`, `customer.updated`, `customers.imported` |
| `orders.events` | 3 | 7 days | `order.created`, `order.updated`, `orders.imported` |
| `inventory.events` | 3 | 7 days | `inventory.updated`, `inventory.low_stock`, `inventory.imported` |
| `analytics.jobs` | 3 | 7 days | `analytics.job.requested`, `.completed`, `.failed` |
| `events.dlq` | 1 | 30 days | Anything that exhausted its retries |

The dead-letter topic differs on both counts, and deliberately. One partition, because total
ordering across the whole topic is worth more than parallelism on a topic that should be
empty and is read by a human. Thirty days rather than seven, because a poison message that
arrives on a Friday before a holiday still needs to be there when someone looks; a DLQ that
expires on the same schedule as the traffic it failed to process loses exactly the evidence
it exists to keep.

It is also excluded from the worker's subscription list. A DLQ that is automatically
re-consumed is not a DLQ — it is a retry loop with extra steps, and the poison message goes
round forever. Draining it is an operator decision.

Topic names live only in `app/events/topics.py`. No other module builds a topic string,
which is what makes the prefix scheme reliable.

### Envelope

Every message is an `Event` (`app/events/schemas.py`) carrying an event id, type, timestamp,
`business_id` and payload. The id is what makes redelivery safe, and Kafka guarantees
at-least-once delivery, so redelivery is a certainty rather than an edge case.

Partitioning is by `business_id`. Two consequences follow: all of one tenant's events are
ordered relative to each other, and no tenant's traffic can starve another's ordering
guarantees.

### Publishing, and why the order matters

Handlers commit to Postgres **before** publishing. If the publish then fails, the database
is still correct and the event is lost — which is recoverable, because the derived work can
be re-triggered from state that exists. Publishing first and committing second produces the
opposite and much worse failure: a consumer acting on an event describing a row that was
never written.

When `KAFKA_ENABLED=false`, `EVENTS_INLINE_FALLBACK` runs the handler in-process
immediately after the commit. Same handler, same code path, no broker. This is what lets the
whole feature set work in development, and it means the handlers are exercised by the test
suite without any infrastructure at all.

### Consuming

`app/events/worker.py` subscribes to the five non-DLQ topics as one consumer group.
Handlers live in `app/events/handlers/` and are keyed by event type. Each is idempotent,
because it will eventually be called twice with the same event.

The `business_id` on a consumed event is treated as data, not authority: handlers re-derive
the tenant from the row they are operating on rather than trusting the envelope. A forged or
mangled event therefore cannot make one tenant's handler write into another's data. There is
a check for this specific property (`check_event_handlers.py`,
`test_import_tenant_cannot_be_spoofed`).

## Caching

### Key scheme

```
cbpa:v1:{business_id}:{namespace}[:{discriminator}]
```

The tenant sits **immediately after the version prefix, before the namespace**, and that
ordering is the whole design. It makes every tenant's keys a single contiguous prefix, so
invalidating one tenant is a bounded `SCAN` over `cbpa:v1:{business_id}:*`. With the
namespace first, that operation would have to walk the entire keyspace and filter — which is
both slower and, far worse, an operation that touches other tenants' keys.

`assert_tenant_scoped` rejects any key that does not match the shape, and `assert_tenant_prefix`
does the same for the prefix passed to a bulk delete. Both are enforced in the cache backend
itself rather than only at the call sites, so a hand-built string cannot slip through.

The two guards are separate because a prefix is not a key. `tenant_prefix()` legitimately
stops at the tenant segment, which the key guard rejects for having no namespace, so reusing
it would have failed every legitimate invalidation. The prefix guard therefore asserts
something narrower: the prefix names exactly one tenant. `""`, `cbpa:v1:` and `*` are all
refused, and each would otherwise clear every tenant's entries at once. So is
`cbpa:v1:{business_id}` without its trailing colon, which also matches any tenant whose id
merely starts with that one's — a real collision when identifiers are sequential or share a
customer-name stem.

A colon in `business_id` is **rejected**, not stripped: `_clean_business_id` raises
`CacheKeyError`. A tenant identifier containing `:` could otherwise close its own segment and
forge another tenant's namespace, and silently rewriting an identifier that arrives in that
shape would hide the fact that something upstream produced it. Other key parts are sanitised
rather than refused — a filter value containing a colon is ordinary — so there the separator
becomes `_`.

`v1` is a global escape hatch. A change to a cached payload's shape can be rolled out by
bumping it, which orphans every old entry at once instead of requiring a correct
invalidation for each namespace.

### Invalidation

Writes invalidate by prefix, not by key, because a write affects entries whose
discriminators the writer does not know — a customer list is cached per filter combination.
`app/cache/keys.py` defines the fan-out per write type, and the interesting one is orders:
an order write invalidates the order, customer, analytics and dashboard namespaces, because
all of them are derived from order history. Getting this wrong produces a dashboard that
quietly disagrees with the database, which is the failure mode users report as "the numbers
are wrong" weeks later.

Invalidation uses `SCAN`, never `KEYS`. `KEYS` blocks the Redis server for the duration of a
full keyspace walk, so on a large multi-tenant keyspace one tenant's import can stall every
other tenant's reads.

### Failure behaviour

`get_or_set` catches every backend exception and falls through to the loader. A cache that
raises must not fail a request — the cache exists to make correct answers faster, so its
absence can only cost time. There are three backends: `RedisCache`, an `InMemoryCache`
bounded at 2000 entries, and `NullCache` for when caching is off entirely. `build_cache`
picks the in-memory one whenever Redis is disabled and `CACHE_LOCAL_FALLBACK` is on, and
`init_cache` falls back to it a second time if an enabled Redis fails to connect — so it
covers both "no Redis configured" and "Redis unreachable", not only the latter.

Two subtleties, both of which were wrong at some point and are now pinned by tests:

- `None` is never written. It is not a value, it is the absence of one, and caching it fills
  a bounded store with `"null"` entries that convey nothing.
- Empty results — `[]`, `{}`, `0`, `False`, `""` — **are** cached and **are** served as
  hits. A truthiness guard instead of an `is not None` guard means every tenant whose
  analytics have nothing to report yet re-runs the full query on every single read. That is
  precisely the tenant whose queries are cheapest to cache and most likely to be hit
  repeatedly while they wait for their first import to finish.

The Redis URL is never logged, because it can embed a password. Connection failures log a
reason, not a target.

## Webhooks

### Verification

`POST /api/v1/webhooks/{source}` verifies, in this order: the signature header is present
and well-formed, the timestamp is within `WEBHOOK_TOLERANCE_SECONDS`, and the HMAC matches.

The signed message is:

```
{business_id}.{timestamp}.{raw body bytes}
```

HMAC-SHA256, hex-encoded, sent as `X-Webhook-Signature: sha256=<digest>`.

The **raw** body is signed. Re-serialising parsed JSON changes whitespace and key order and
would break every signature that was ever computed correctly, so the handler holds the bytes
as received and parses only after verification succeeds.

Including `business_id` and the timestamp *inside* the signed message is what makes them
tamper-proof. Signing the body alone would let an attacker replay a valid payload against a
different tenant by editing a header the signature does not cover.

Comparison is `hmac.compare_digest`. A byte-by-byte comparison with early exit leaks how
much of a forged signature was correct through its timing, and that is enough to reconstruct
a valid one digit at a time.

Nothing about the secret, and no raw signature header, is ever logged. A rejection logs the
reason code. Signature verification contributes seven — `missing_signature`,
`malformed_signature`, `missing_timestamp`, `malformed_timestamp`,
`timestamp_out_of_range`, `signature_mismatch` and `webhook_secret_not_configured` — and the
service layer adds four for deliveries that never reach verification or fail after it:
`unsupported_source`, `missing_business_id`, `unsupported_event_type` and `invalid_payload`.
All eleven are what an operator needs and an attacker cannot use.

### Tenancy without a session

A webhook has no Clerk token, so `business_id` arrives in the `X-Business-Id` header. This
is the one place a tenant identifier comes from the request, and it is safe for exactly one
reason: it is covered by the signature. An attacker can change the header, but then the HMAC
no longer matches, because the header value is part of the signed message. Without the
shared secret they cannot produce a signature for a header they have altered.

### Idempotency

Verified deliveries are recorded in `webhook_events`, and what decides a duplicate is a
database constraint rather than application logic: a unique index on
`(business_id, source, external_event_id)` named `uq_webhook_business_source_event`. A replay
is acknowledged with `200` and does no work, while a newly accepted delivery returns `202` —
a sender retrying because it missed our response must not produce a second order, and senders
retry routinely.

The key is composite rather than the event id alone for two reasons. The tenant is included
because two businesses using the same upstream provider can legitimately receive deliveries
carrying the same provider-side id, and global uniqueness would let the first tenant's
delivery suppress the second's. The source is included because ids are only unique within the
system that issued them.

Senders that supply no id at all still have to be deduplicated, so one is derived:
`sha256:` over the same canonical message the signature covers. A byte-identical replay
collapses to the same id and hits the unique index; a genuinely different body, or the same
body at a later timestamp, does not. The digest is taken over the message and never over the
signature, so no secret-derived value reaches the database.

The row is written in the same transaction as the effect, so "processed" and "the effect
happened" cannot disagree.

`webhook_events` is not to be confused with `processed_events`, which is the **Kafka
consumer's** ledger for at-least-once redelivery, keyed on the event id from the envelope.
They are separate tables because they deduplicate different things at different boundaries:
one guards the edge where an outside system talks to us, the other guards the internal step
where a consumer may see the same message twice.

## Analytics jobs

### Lifecycle

```
queued  →  running  →  completed
                    ↘  failed
```

`POST /api/v1/analytics/jobs` inserts a `queued` row, commits, then publishes
`analytics.job.requested` to `analytics.jobs`. The response reports the queued job
including whether dispatch succeeded — a job that is queued but undispatched is a real
state, and saying so is better than reporting a success that will never progress.

The worker claims the row by transitioning `queued → running`, which is a conditional update
rather than a read-then-write, so two workers consuming the same redelivered event cannot
both run it.

Terminal states carry either a result reference or an error, and publish
`analytics.job.completed` or `.failed`.

### The engine

`app/analytics/` wraps the existing Phase 1 ML code rather than reimplementing it. RFM
scoring, segmentation and product analysis are the same functions; the Phase 2 addition is
a tenant-scoped service boundary, persistence of results, and cache invalidation on
completion.

RFM is computed by aggregating `orders` and `order_items` in Postgres. If an uploaded
customer file already carries genuine recency, frequency and monetary columns those are used
directly. **Nothing is synthesised**: a customer with no order history is excluded from the
scored population rather than given an invented score, and the distribution counts only
labelled rows. An unscorable row is left unscored, and the API says how many there were.

The ARIMA demand forecast needs `statsmodels`. Without it the forecast returns `None` and
that section of the response is absent — it is not replaced with an estimate.

## Verification

The checks live in `backend/tests/` and `frontend/src/__checks__/`, and
`python3 backend/tests/run_all.py` runs everything that needs no infrastructure. What is
worth knowing about them:

They are written to run in an environment where **nothing can be installed**, which is why
several are plain scripts that parse source with `ast` instead of importing it. The
migration check, for example, compares the Alembic revisions against the ORM models
column-for-column without SQLAlchemy present, and asserts the properties that make a
migration safe against a populated database: additive only, every added column nullable or
defaulted, no type changes, `downgrade` dropping only what `upgrade` created.

Two of them exist because a specific class of defect is invisible in the environment the code
was written in and only appears on a clean install. `check_dependencies.py` asserts that the
requirements files and the imports agree in both directions; it was written after eight
distributions turned out to be imported and declared nowhere, which nothing failed on locally
because a working machine already had them as transitive dependencies. `check_env_example.py`
asserts that `.env.example` and `config.py` agree; it was written after `CORS_ORIGINS` was
found to differ between them, so that `cp .env.example .env` produced different behaviour from
running with no `.env` at all.

Two mutation harnesses exist to answer "can these checks fail?" —
`backend/tests/mutation_probe.py` (39 defects) and
`frontend/src/__checks__/mutation_probe.mjs` (9). Each breaks one thing on a throwaway copy
and requires the matching check to notice. They have already earned their place several times:
one caught a cache test that asserted the right rule for the wrong reason and would have
passed with the guard deleted; another caught `run_all.py` reclassifying a genuine failure as
a skip, which would have reported a green build over a real regression; and a third round
found the copied tree was missing files the checkers read, which had quietly turned a whole
group of mutations into no-ops.
