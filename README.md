# Customer Intelligence & Analytics Platform

A multi-tenant retail analytics platform: a Python/FastAPI modular monolith backed by
PostgreSQL (Supabase), with Kafka for event streaming, Redis for caching, and a React
dashboard. Every business-owned record, cache key, event and analytics job is scoped by
`business_id`, and that identifier is derived from the authenticated Clerk session on the
server — never taken from the request body.

## Architecture

The backend is one deployable process. Requests enter through a route module, business
logic lives in a service, and all database access goes through a repository, so the
layering is route → service → repository → SQLAlchemy → PostgreSQL. Nothing skips a
layer. PostgreSQL is the only source of truth; Redis is a cache whose absence costs
latency and not correctness, and Kafka is an event bus whose absence delays derived work
without losing data.

Both Kafka and Redis default to **off** (`KAFKA_ENABLED=false`, `REDIS_ENABLED=false`) and
their client libraries are imported lazily. A checkout with neither service running starts,
serves every endpoint, and reads straight from PostgreSQL. That is the intended development
setup, not a degraded mode.

| Concern | Technology |
| --- | --- |
| API | FastAPI, Uvicorn |
| ORM and migrations | SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL (Supabase) via `psycopg` 3 |
| Auth | Clerk, JWT verified against the JWKS endpoint |
| Events | Kafka via `aiokafka`, KRaft mode |
| Cache | Redis via `redis.asyncio` |
| ML | scikit-learn, pandas, numpy; statsmodels for ARIMA |
| Frontend | React 18, Vite 6, MUI 5, Recharts |

## Getting started

The backend and frontend run independently. Neither needs Docker, Kafka, or Redis to start.

If you would rather not read the next two sections, `scripts/dev.sh` does the same thing:

```bash
./scripts/dev.sh doctor    # what is missing, and nothing else
./scripts/dev.sh setup     # venv, both dependency sets, .env from the example
./scripts/dev.sh build     # the check suite, pytest, and the production bundle
./scripts/dev.sh run       # migrations, then the API and dev server together
```

`doctor` is worth running first even on a working checkout, because two of the failures it
names are otherwise slow to diagnose. A `.venv` or `node_modules` copied from another
machine resolves correctly and then fails at build time on a missing platform binary, and
an empty `WEBHOOK_SECRET` lets the app boot while rejecting every delivery. It reports
`.env` keys as set or empty and never prints a value.

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # or requirements.txt without the test tooling
cp .env.example .env                  # then fill in DATABASE_URL and the Clerk values
alembic upgrade head
uvicorn app.main:app --reload
```

`requirements-dev.txt` installs the runtime set as well, so it is the single command for a
working checkout. Beyond the runtime set it adds three packages that should not ship to a
production image: pytest, PyYAML for the compose test, and requests for the two by-hand
upload scripts at the backend root.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env                  # then set VITE_CLERK_PUBLISHABLE_KEY
npm run dev
```

The dev server listens on port 3000 and proxies `/api`, `/health` and `/product-analysis`
to `http://127.0.0.1:8000`. Point it elsewhere with `VITE_DEV_API_TARGET`. Because the
proxy exists, `VITE_API_BASE_URL` should normally stay **empty**: the client issues
same-origin relative requests, so the URL that works in development is the one that works
behind a production reverse proxy. Set it only when the API is genuinely on another origin,
and expect to add that origin to `CORS_ORIGINS` on the backend.

### Optional infrastructure

```bash
docker compose up -d
docker compose run --rm kafka-init     # creates the topics
```

This starts Kafka in KRaft mode (no Zookeeper) and Redis. Supabase is a hosted service and
is deliberately not containerised. Then set `KAFKA_ENABLED=true` and `REDIS_ENABLED=true`
in `backend/.env` and restart the backend.

## API

Everything added in Phase 2 lives under `/api/v1`. Every endpoint below requires a Clerk
bearer token and resolves the tenant from it, with one deliberate exception:
`POST /api/v1/webhooks/{source}` is unauthenticated, because the caller is another server
that has no Clerk session. There the HMAC signature is the credential and the tenant comes
from a signed header — see [docs/PHASE_2.md](docs/PHASE_2.md) for why that is safe.

| Prefix | Endpoints |
| --- | --- |
| `/api/v1/dashboard` | `summary`, `revenue-trend`, `rfm-distribution`, `segment-distribution`, `top-customers`, `inventory-summary` |
| `/api/v1/customers` | list, `{customer_id}` |
| `/api/v1/products` | list, `{product_id}` |
| `/api/v1/orders` | list, `{order_id}` |
| `/api/v1/inventory` | list, `low-stock`, `{inventory_id}` |
| `/api/v1/analytics` | `POST jobs`, `GET jobs`, `GET jobs/{job_id}`, `POST jobs/rfm`, `GET rfm`, `GET segments` |
| `/api/v1/imports` | `POST customers`, `products`, `inventory`, `orders` |
| `/api/v1/webhooks` | `POST {source}`, `GET config`, `GET deliveries` |

Health endpoints are unauthenticated and report dependency status without disclosing
connection strings:

```bash
curl http://localhost:8000/health         # liveness
curl http://localhost:8000/health/ready   # readiness: db, plus redis/kafka when enabled
curl http://localhost:8000/health/db
curl http://localhost:8000/health/redis
curl http://localhost:8000/health/kafka
```

The Phase 1 routes are still mounted: `/upload`, `/upload-product`, `/preview`, `/dashboard`,
`/analyze`, `/results`, `/insights`, `/product-analysis`, the inventory CRUD set
(`GET`/`POST /inventory`, `PUT`/`DELETE /inventory/{item_id}`), `/inventory/upload` and
`/inventory/sync`. The new UI does not call them apart from `/product-analysis`, which the
ported Product Analysis page uses.

## Frontend

`/app` is the authenticated shell, with pages for the dashboard, customers, RFM, segments,
products, orders, inventory, imports, analytics jobs, product analysis and settings. The
Phase 1 URLs redirect into it so existing bookmarks keep working.

## Configuration

`backend/.env.example` documents every variable the backend reads, with the same default the
code uses. That agreement is enforced by `backend/tests/check_env_example.py` rather than by
review: it previously drifted, and an example file that disagrees with the code is worse than
no example file, because copying it silently changes behaviour. The ones that matter most:

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | — | Required. `postgresql+psycopg://...` |
| `CLERK_ISSUER`, `CLERK_SECRET_KEY` | — | Required for authenticated routes |
| `CORS_ORIGINS` | `localhost:3000,3001` and the `127.0.0.1` pair | Comma-separated |
| `REDIS_ENABLED` | `false` | When false, `REDIS_URL` is still read but never connected to |
| `KAFKA_ENABLED` | `false` | `EVENTS_INLINE_FALLBACK` handles events in-process when off |
| `WEBHOOK_SECRET` | — | Required only if a webhook source is used |
| `WEBHOOK_REQUIRE_SIGNATURE` | `true` | Leave on outside local testing |
| `CACHE_*_TTL_SECONDS` | varies | Per-namespace cache lifetimes |

No `.env` file is committed. `backend/.env.example` and `frontend/.env.example` contain
placeholders only, and `backend/tests/test_no_committed_secrets.py` scans the tree to keep
it that way.

## Tests

```bash
cd backend && python3 tests/run_all.py     # everything that needs no infrastructure
cd backend && pytest                       # the Phase 1 SQLAlchemy suites
```

`run_all.py` is a subprocess runner, not a test framework, because the checks have to work
in an environment where nothing can be installed. It reports a skip whenever a dependency
is missing, so "nothing ran" never reads the same as "everything passed". Two file-naming
conventions are load-bearing: `test_*.py` files are pytest-collectable, while `check_*.py`
files assert at import and call `sys.exit`, which would abort a pytest run if collected.

Two mutation harnesses verify that the checks can actually fail — `backend/tests/mutation_probe.py`
(49 defects) and `frontend/src/__checks__/mutation_probe.mjs` (17). They are run by hand and
are not part of the suite. A check nobody has ever seen fail is evidence that it runs, not
that it works, and both harnesses have already caught checks that passed for the wrong
reason.

## Further reading

`docs/PHASE_2.md` covers the event topology, cache key scheme, webhook verification and
analytics job lifecycle in detail. `PHASE_2_COMPLETE.md` is the implementation report.
