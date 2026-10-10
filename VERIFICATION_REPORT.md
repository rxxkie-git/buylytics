# End-to-end verification report

Date: 2026-08-16. Scope: the two UI bugs reported from Docker/UI testing, plus the
mandated end-to-end verification against `dataset/`.

**Bottom line up front:** the two reported bugs are fixed and both fixes are
regression-guarded by checks that fail when the bug is reintroduced. The
import → database → RFM → dashboard-summary chain is verified against the real
datasets. But **Docker, nginx, the browser, `npm run build`, and the Kafka
publish/consume round trip were never executed in this environment**, so the
answer to "does it work end-to-end in Docker" is *not yet proven*. Section 19 and
the "What you must run" section say exactly what is left and how to run it.

---

## 1. Customer import — root cause and fix

**Root cause.** `ImportOrchestrator.import_customers` returned
`{"records": [<Customer ORM instances>], ...}`. The route returned that dict, so
after the transaction had already committed, FastAPI had to run `jsonable_encoder`
over every persisted row and then `json.dumps` the result.

Measured on the real `dataset/df_Customers.csv` (89,316 rows):

| | |
|---|---|
| `jsonable_encoder` | 0.93 s (on the event loop, after commit) |
| `json.dumps` | 0.13 s |
| response body | **24.81 MiB** |
| the same response without `records` | **394 bytes** |

`Session` is configured with `expire_on_commit=False`, so those instances retain
every loaded column after the commit — returning them is never cheap, and the cost
scales linearly with file size.

`upload_orders` was the only one of the four that stripped it
(`result.pop("records", None)`), which is exactly why **orders succeeded in the UI
while customers, products and inventory failed.** The import itself was working:
it committed, and then the response serialisation blew up.

**Fix.** Structural, not cosmetic: no import method returns ORM instances at all.
The response carries counts and mapping metadata only (`rows_processed`,
`rows_persisted`, `rows_failed`, `errors`, plus `mapping_summary(resolved)`).
`rows_persisted` is deliberately separate from `rows_processed` because upserts
collapse duplicate keys.

**Honesty note.** I measured the encode cost and body size directly. I did not
observe the browser's network failure myself — that came from your screenshots. The
mechanism (a multi-megabyte body plus ~1 s of post-commit event-loop work per
import) is consistent with the client-side timeout you saw, but the precise
client-side failure mode is inferred, not observed.

## 2. Product import — root cause and fix

Identical cause and identical fix (`import_products` returned the same `records`
key). Nothing product-specific was wrong: the mapper, the upsert and the batching
were all fine.

## 3. Inventory import — root cause and fix

Identical cause and identical fix (`import_inventory` returned `records`).

Separately verified while tracing this path: the inventory import does **not**
invent products. Rows whose `external_product_id` is not already in the catalogue
are skipped and reported in one aggregated message ("Import the products file
first, then retry"), and if *nothing* matched, the import is recorded as **failed**
rather than completed — so you cannot get "Imported successfully" over an empty
table. `rows_processed` counts what was written, not what parsed.

## 4. Dashboard `LOCALE is not defined` — root cause and fix

**Root cause.** `src/utils/format.js` line 26 does

```js
export { CURRENCY, LOCALE } from "../config/env.js";
```

That is a *pure re-export*. It forwards the binding to importers of the module and
creates **no local binding** in the module itself. `formatDate` and `formatDateTime`
referenced the bare name `LOCALE`, which is therefore a `ReferenceError` — not a
lookup of the re-exported name.

Two things hid it: it only fires when the function is **called** (not on import, not
at build time), and both functions guard blank input first, so `formatDate(null)`
returns `"—"` without touching `Intl`. The dashboard was the first page to call them
**with real data**, which is why the crash appeared only after a successful import.

**Fix**, using the project's existing configuration convention rather than a new
global: a second, aliased *import* alongside the re-export, so the module has real
local bindings.

```js
import { CURRENCY as CURRENCY_CODE, LOCALE as LOCALE_TAG } from "../config/env.js";
```

`config/env.js` reads `VITE_LOCALE` with an `en-IN` default and `VITE_CURRENCY`
likewise, so locale and currency stay consistent and configurable from one place.
Every `Intl` formatter and both `toLocale*` calls in the module now use the aliases.
(This also removes the Phase 1 defect where `Intl.NumberFormat("en-IN", {currency:
"USD"})` rendered Indian digit grouping with a dollar sign.)

**Verified.** `frontend/src/utils/__checks__/format.check.mjs` → **132 checks pass**.
Reintroducing the exact bug (`LOCALE_TAG` → `LOCALE` at the two `toLocale*` sites)
makes **8 of 132 fail** with `ReferenceError: LOCALE is not defined` — the same
message you saw. A separate 112-assertion runtime probe calling every export with
the values from the imported database also passes clean and fails on the mutation.
`format.js` was restored byte-identical (SHA-256 verified) after each mutation.

## 5. Files changed

`git` is not available in this sandbox (`fatal: not a git repository`), so this is
by area rather than a diff. Principal files for the four fixes:

- `backend/app/ingestion/importer.py` — the `records` removal for all four
  entities; aggregated missing-product reporting; failed-when-nothing-written.
- `backend/app/ingestion/column_mapping.py` — deterministic mapping, `empty_fields()`
  so a field that will land NULL is declared up front.
- `backend/app/repositories/bulk.py`, `customer_repository.py`,
  `product_repository.py`, `inventory_repository.py`, `order_repository.py` —
  chunked bulk upserts, bind-parameter-limit safe.
- `backend/app/core/errors.py`, `backend/app/api/routes/v1_imports.py` — structured
  error codes instead of a bare 500.
- `frontend/src/utils/format.js` — the LOCALE fix.
- `frontend/src/api/client.js`, `frontend/src/api/imports.js`,
  `frontend/src/pages/app/ImportPage.jsx` — surface the real backend error and code
  instead of "An unexpected error occurred".
- `frontend/src/components/ui/ErrorBoundary.jsx` — shows the actual runtime message.

## 6. Dataset files tested

`dataset/` contains **five** files, all 89,316 data rows:

| file | maps to | usable? |
|---|---|---|
| `df_Customers.csv` | customers | yes |
| `df_Orders.csv` | orders | yes |
| `df_Products.csv` | products | yes |
| `df_OrderItems.csv` | — | **no** — see below |
| `df_Payments.csv` | — | **no** — see below |

`df_OrderItems.csv` (`order_id,product_id,seller_id,price,shipping_charges`) and
`df_Payments.csv` (`order_id,payment_sequential,payment_type,payment_installments,
payment_value`) are keyed **only by `order_id`**. Run through the real mapper as an
orders file, `df_OrderItems.csv` does map `total_amount ← price`, but it is
hard-refused with `missing_required=['external_customer_id']`. So there is no import
path for this dataset's money. Nothing in `app/ingestion` constructs an `OrderItem`.

**There is no inventory file in `dataset/`.** To exercise the inventory path at all I
derived a 500-row `inventory_master.csv` (`id,itemName,category,quantity,price,
supplier`). This is disclosed rather than presented as a real result: **inventory was
not tested against real project data, because there is none.**

## 7. Source row counts vs imported row counts

| entity | source rows | distinct key | in DB | lost |
|---|---|---|---|---|
| customers | 89,316 | 89,316 | **89,316** | 0 |
| orders | 89,316 | 89,316 | **89,316** | 0 |
| products | 89,316 | **27,451** | **27,451** | 0 |
| inventory | 500 (derived) | 500 | **500** | 0 |

`df_Products.csv` is one row per order-item, so `product_id` repeats — 89,316 rows
carry only 27,451 distinct products. The upsert collapses them correctly; this is
why `rows_persisted` differs from `rows_processed`.

The products table holds **27,951** rows for this tenant = 27,451 from
`df_Products.csv` + 500 created by importing the derived inventory file as products
first (FK order). Verified by set difference: every one of the 27,451 source ids is
present, the 500 extras are exactly the `SKU-*` ids, and their category histogram
matches `inventory_master.csv` exactly (Apparel 75, Home Decor 69, Stationery 64,
Kitchenware 63, Electronics 63).

## 8. Database verification

Not from UI messages — every number below was checked with independent SQL.

- Counts as in §7. Tenant association: every row carries the expected `business_id`;
  a second tenant holds 10,000 customers / 100 products / 1 inventory row and never
  leaks into the first tenant's reads (58 assertions).
- **Foreign keys:** all 500 inventory rows join to a real product (0 orphans). Every
  order resolves to a customer.
- **Duplicates:** none — `COUNT(DISTINCT external_product_id) == COUNT(*)` (27,951),
  and re-running an import does not double-write.
- **NULL/invalid:** 141 products have `category IS NULL`. This is faithful: exactly
  141 distinct `product_id`s have a blank `product_category_name` in the source
  (308 raw rows). `name`, `sku` and `price` are NULL for all products from
  `df_Products.csv` — the file has no such columns, and the mapper declares
  `empty: ['name','sku','price']` *before* import. `total_amount` is NULL for all
  89,316 orders, declared as `empty: ['total_amount']`.
- No NULL `quantity` or `reorder_level` anywhere, so the low-stock comparison is safe.
- **Skipped rows:** 0 unintentional. The 409 customers with no RFM row are
  accounted for exactly: all 409 have orders, and every one of those orders has
  status `canceled`, which the RFM engine excludes by design.

## 9. RFM verification

Real, and honest about what is empty. 88,907 rows written for tenant A.

- **Recency: real** — 0.00 to 728.49 days, reference date 2018-09-03 (the newest
  order), populated for every row.
- **Frequency: populated, but constant at 1** — `df_Orders.csv` has 89,316 distinct
  `order_id` and 89,316 distinct `customer_id`, i.e. **exactly one order per
  customer**. So F carries no discriminating information in this dataset.
- **Monetary: 0.00 for every row** — because no order carries an amount (§6). The
  run metadata records `monetary_recovered_from_order_items: 0`, and the mapper
  warned `total_amount` would be empty. **Nothing is fabricated; it is empty and
  declared empty.** But "monetary values are populated" is **FALSE** for this
  dataset and cannot be made true without a money column.
- Run metadata matches independent SQL: `customers_with_orders: 88,907`,
  `rows_written: 88,907`, `orders_excluded_by_status: 409`, `source: "orders"`.
- Rule-based segment distribution is real and R-driven: New Customers 35,564 (40%),
  Hibernating 35,563 (40%), About To Sleep 17,780 (20%).
- Every RFM row is tagged with its `analytics_run_id`, so a snapshot is attributable.

**Traced one real customer end to end.** CSV `customer_id=hCT0x9JiGXBQ` with one
order `Axfy13Hk4PIk`, status `delivered`, `2017-10-22 18:57:54` →
`customers` row (city `varzea paulista`) → `orders` row with the same external id,
status and timestamp, `total_amount=None` → `customer_rfm` recency 315.59,
frequency 1, monetary 0.0, scores R=2 F=1 M=1, `source='orders'`, run-tagged. Every
value derives from that order.

## 10. Segmentation verification — completes, but quality is UNVERIFIED

The job runs, reaches `completed`, and writes one row per customer
(`rows_written` matches `COUNT(*)` in `customer_segments`).

**But the clustering itself degraded to the single-cluster fallback**, and I must be
clear that **this is my sandbox, not your application**:

- `backend/app/services/ml_algorithm.py` imports sklearn at module scope, and
  **scikit-learn is not installed here** (`ModuleNotFoundError: No module named
  'sklearn'`); under the test harness it is a stub that imports but raises on use.
- `_fit_kmeans` catches `Exception` and returns `None`, so no candidate is scored,
  `best_labels` falls back to `np.zeros(...)`, and `selected_algorithm` stays
  `"fallback"` with `silhouette_score: 0.0`.
- Result: all 88,907 customers land in cluster 1, labelled "High Value Customers".
- `scikit-learn>=1.8.0,<2.0.0` **is** declared in `requirements.txt`, so in the
  Docker image KMeans will actually run.

So: **segmentation quality must be re-checked in Docker.** I cannot claim it works.

**Genuine latent issue found while establishing that** (worth fixing regardless): a
real sklearn failure in production would silently produce the same degenerate
result — everyone labelled "High Value Customers" with `silhouette_score: 0.0`. It
is auditable, because the run metadata records `selected_algorithm: "fallback"`, but
the Segments page would show one segment with no explanation. Note that a second
tenant with genuine spread (monetary 747–3,738, frequency 3–15) produced the *same*
single cluster here — confirming the fallback is environmental, and also
demonstrating how invisible this degradation is from the data alone.

## 11. Product analytics verification — NOT working, and not for import reasons

`frontend/src/pages/ProductAnalysis.jsx` is **structurally orphaned**:

- it gates on `hasDataset = Boolean(getProductDatasetId())`, read from
  `sessionStorage["cbpa-product-upload-context"]`;
- that key is written only by `uploadProductDataset` (`api.js:88`), called only from
  `ProductUploadTab.jsx`, rendered only by `DataHub.jsx`;
- **`App.jsx` never imports `DataHub` and no route serves it.**

So the page always renders `NoDatasetState` and never issues its request. Its retry
CTA navigates to `/upload-product`, which matches no route and falls through the
catch-all to `/`. Additionally, the Phase 2 import writes to Postgres via
`ImportOrchestrator` and never touches `app.state.dataset_repository`, which is the
in-memory store this page reads — so even if it were routed, it would find nothing.

`run_product_analytics` was **never executed** here (module-scope `KMeans`,
`LinearRegression`, `StandardScaler`, plus statsmodels ARIMA).

I deliberately did **not** fix this: rewiring the page to the Postgres catalogue is a
design change, and you asked for no broad rewrites. Flagging it for your decision.

Product *data* is imported correctly (27,451 rows, §7), but `name`, `sku` and
`price` are NULL because the source file has no such columns — so "verify
product/SKU data in the database" resolves to: the SKU column is empty by
declaration, not by failure.

## 12. Inventory analytics verification

500 rows, `total_quantity=27,227`, min 0, max 149, **55 at or below reorder level**
(matches independent SQL; 445 above, so the filter is not returning everything).
All 500 join to a real product. Low-stock tenant scoping verified; inverting the
comparison is caught by the checks.

**Caveat that matters: this is derived data, not project data** (§6). There is no
inventory file in `dataset/`.

## 13. Dashboard verification

- `LOCALE is not defined` is fixed and regression-guarded (§4).
- `dashboard_service.get_summary` was executed through its real async entry point
  and cache-aside path: `analytics_ready: true`, `rfm_rows: 88,907` matching SQL,
  and `last_completed_job` correctly naming the completed `rfm` run.
- No sample or hardcoded data: revenue is empty because no order carries an amount,
  and the app says so rather than substituting a number.
- **Not browser-verified.** No vite dev server, no `npm run build`, no nginx, no
  browser here. KPI cards and charts rendering, and the absence of console errors,
  are **not** confirmed by me.

## 14. Kafka / worker verification

**Worker-side path: verified.** `create_job` → `_claim_run` → `execute_job` →
`_finish_run` → `get_job`/`list_jobs`, executed for real:

- an unknown job type is rejected, naming all supported types;
- a queued job reaches `completed` with metadata matching independent SQL;
- **redelivery is safe** — a completed job returns `skipped`, its completion time and
  result are untouched, and no second snapshot is written (Kafka is at-least-once);
- a job already `running` is refused rather than double-claimed;
- **failures are visible, not swallowed** — a tenant with no orders yields
  `status: failed`, `error_code: insufficient_data`, an actionable message stored on
  the row and surfaced by the API read;
- an unconfigured database raises `ConfigurationError` with no credential in the
  message;
- job reads are tenant-scoped (another tenant gets `NotFoundError`).

Four mutations (accepting an already-running job, recording failure as success,
ignoring the claim result, dropping the tenant filter) were each detected.

**Not verified: the publish/consume half.** `submit_job` and `run_job_from_event`
need a real broker and event loop. No Kafka here.

## 15. Redis verification

- 30 cache-isolation checks pass, including tenant-scoped key construction and the
  delete guard; cross-tenant cache reads are not possible.
- The dashboard summary was fetched through the real cache-aside path with
  `REDIS_URL` empty, i.e. the **fallback-to-database path is executed and works** —
  no hang, no error.
- **Not verified:** behaviour against a live Redis, and real timeout/failure
  handling under load.

## 16. Large-upload responsiveness

- The full 89,316-row customer import ran to completion; the products import
  collapsed 89,316 rows to 27,451 via chunked upserts.
- Batch operations are provably in use: per-row `SELECT`s were replaced by chunked
  `IN` queries (inventory product resolution is the clearest case), and
  `test_bind_parameter_limits.py` (5 cases) proves the chunk size stays under the
  driver's bind-parameter ceiling.
- The event-loop fix that this report's §1 removes the last piece of: nothing
  CPU-bound or blocking runs on the loop during an import.
- **Not verified:** `/health` latency during a large import **under Docker**. I could
  not run concurrent request load against a real uvicorn+nginx stack here. This is
  the single most important thing left to confirm.

## 17. Tests and builds

`cd backend && python3 tests/run_all.py` → **24 passed, 0 failed, 3 skipped.**

Includes: 178 env-example, 87 migration, 82 RFM passthrough, 44 column-mapping,
35 error-surfacing, 30 cache-isolation, 22 scoring, 13 compose, 9+8+7 repository/model,
6 ingestion, 5 bind-parameter, 5 secret-scan; frontend 1,275 wiring, 537 resolve,
132 formatter, 71 readiness, 34 proxy checks, and a 17/17 frontend mutation probe.

Skipped for missing packages: `check_webhooks.py` (anyio), `test_import_preview.py`
(fastapi), `test_job_claim.py` (sklearn).

Additional stage checks written for this task: 58 read-path assertions against
independent SQL, with 5/5 mutations detected; the job-lifecycle stage with 4/4
mutations detected. All mutated files restored byte-identical (SHA-256 verified).

**`npm run build` did NOT run.** `node_modules` was installed on your macOS host, and
this sandbox is Linux aarch64, so rollup's native binary is missing
(`Cannot find module '@rollup/rollup-linux-arm64-gnu'`). Installing it is blocked —
the npm registry returns 403 here. You must run this locally.

## 18. Docker services verified

**None. Docker is not available in this sandbox** (`docker`, `docker-compose`,
`nginx` all absent), so **`docker compose config --quiet` and
`docker compose up --build` were not run.**

Static verification only: `docker-compose.yml` declares six services — `kafka`,
`redis`, `kafka-init`, `backend`, `worker`, `frontend` — plus a `redis-data` volume;
`backend/Dockerfile`, `frontend/Dockerfile` and `frontend/nginx.conf` are present;
`test_compose.py` passes 13 structural checks.

## 19. Remaining issues

1. **Docker / nginx / browser path unverified** — the headline gap (§18).
2. **Segmentation quality unverified** and it degrades silently when sklearn fails
   (§10). Recommend surfacing `selected_algorithm == "fallback"` in the UI.
3. **Product Analysis page is unreachable and reads a store the import never
   writes** (§11). Needs a decision, not a patch.
4. **This dataset cannot produce monetary analytics** (§6, §9) — revenue, AOV and
   RFM-M will stay empty until a file with amounts *and* a customer column exists.
5. **No inventory dataset** in `dataset/` (§6).
6. **Unbounded list endpoints.** `/api/v1/customers`, `/products`, `/inventory` take
   no pagination params. Measured: customers 89,316 rows → 1.15 s, 20.64 MB JSON,
   224.5 MB peak. **Do not "fix" this by adding a default `limit` alone** — those
   three pages search, sort and filter *client-side* over the whole list, so a
   server-side bound would silently shrink search scope ("no customers match" for a
   customer who exists). The real fix is server-side search plus paginated UI,
   following `v1_orders.py` (which correctly caps at 500 in the service, not just in
   `Query(le=500)`).
7. **Detail reads are unscoped in the service layer.** `CustomerService.get_customer`,
   `ProductService.get_product` and `InventoryService.get_inventory` return another
   tenant's row when asked — proven executably. The tenant check lives in the route,
   so the API is safe today, but the guard is load-bearing and easy to drop.
   `OrderService.get_order_detail` is the better in-repo pattern.
8. **Legacy Phase 1 surface has no tenant scoping.** All 14 handlers in
   `app/routes/{analytics,upload,inventory}.py` depend on `get_current_user` and
   never use it; `ClerkUser` carries no business field; `InMemoryRepository` is
   process-global, so `GET /product-analysis` with no `datasetId` returns whichever
   product dataset any user uploaded most recently. No routed UI reaches these, and
   `main.py` documents keeping them deliberately — reporting rather than removing.

---

## Does the application now work correctly end-to-end with the real datasets in `dataset/`?

**Not fully — and I will not claim otherwise, because Docker was never run here.**

What **is** proven against the real datasets, outside Docker: both reported bugs are
fixed with mutation-tested regression guards; all four imports complete; 89,316
customers, 89,316 orders, 27,451 products and 500 inventory rows persist with zero
unintentional row loss and no orphans; RFM computes real recency and frequency for
88,907 customers, traceable from a CSV row to a scored customer; the analytics job
lifecycle completes, is redelivery-safe and reports failures visibly; the dashboard
summary reports readiness from real data.

What is **not** proven: the Dockerised stack, the browser → nginx → frontend path,
`npm run build`, the Kafka publish/consume round trip, live Redis behaviour,
`/health` latency during a large import, segmentation quality with real sklearn, and
Product Analysis (which is broken for a structural reason, §11).

Also, two acceptance criteria are **false for this dataset and cannot be made true**
by fixing code: monetary values cannot be populated, and there is no inventory
dataset.

## What you must run locally to close the gap

```bash
# 1. Compose validity, then the real stack
docker compose config --quiet
docker compose up --build

# 2. Frontend build (must run on your host; the sandbox has the wrong-platform
#    node_modules and no registry access)
cd frontend && npm install && npm run build

# 3. Backend suite, including the three skipped here
cd backend && pip install -r requirements-dev.txt && pytest
cd backend && python3 tests/run_all.py

# 4. While a large import runs, in another shell:
while true; do curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' \
  http://localhost:8000/health; sleep 1; done
```

Then check three things in the browser: the dashboard renders with KPI cards and no
console error; the Segments page shows more than one segment (if it shows exactly
one, §10 is real in production too); and the Analytics Jobs page shows a job moving
`queued → running → completed`, which is the only way to confirm the Kafka round
trip.
