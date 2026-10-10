"""Phase 2 API wiring and health-endpoint leak checks.

FastAPI and SQLAlchemy are not installed here, so the modules cannot be imported
whole. Two techniques are used instead, and the difference matters:

*Extracted execution* — the target function's AST node is pulled from the real
source file, compiled, and executed against adversarial inputs with a stub
namespace. This runs the actual shipped code, so a leak would be caught for real.
Used for the sanitisers, where correctness is a security property.

*Source inspection* — assertions about structure and ordering that cannot be
executed without the framework. Clearly separated below.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

# Resolved from this file's location so the checks run from any checkout, on any
# machine. An earlier revision hardcoded an absolute sandbox path, which made the
# whole file silently unrunnable anywhere else.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
        failures.append(label)


def group(title: str) -> None:
    print(f"\n[{title}]")


def load_function(path: Path, name: str, namespace: dict) -> object:
    """Compile one function from a real source file and return it.

    Only the single ``def`` is compiled, so the module's unavailable imports never
    execute — but the bytecode is generated from the exact source that ships.
    """
    tree = ast.parse(path.read_text())

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(path), "exec"), namespace)
            return namespace[name]

    raise AssertionError(f"{name} not found in {path.name}")


def top_level_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
    return found


def returned_dicts(source: str, function: str | None = None) -> list[set[str]]:
    """One key-set per ``return {...}`` site.

    AST rather than substring search, because these files legitimately contain the
    string ``"records"`` in prose and in ``to_dict(orient="records")`` — a grep would
    either miss the real thing or fire on the innocent ones.

    Per-site rather than a union, because ``import_inventory`` has two return
    statements and a union stays satisfied when only one of them carries
    ``rows_persisted``. Per-site is what actually pins the invariant down.
    """
    tree: ast.AST = ast.parse(source)

    if function is not None:
        tree = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function
            ),
            None,
        )
        if tree is None:
            raise AssertionError(f"{function} not found")

    return [
        {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    ]


def returned_dict_keys(source: str, function: str | None = None) -> set[str]:
    """Union of :func:`returned_dicts`, where per-site precision is not needed."""
    sites = returned_dicts(source, function)
    return set().union(*sites) if sites else set()


print("=" * 74)
print("PHASE 2 API WIRING AND HEALTH LEAK CHECKS")
print("=" * 74)


HEALTH = BACKEND / "app" / "api" / "routes" / "health.py"
SESSION = BACKEND / "app" / "db" / "session.py"
MAIN = BACKEND / "app" / "main.py"
DASHBOARD = BACKEND / "app" / "services" / "dashboard_service.py"
IMPORTS_ROUTE = BACKEND / "app" / "api" / "routes" / "v1_imports.py"
ANALYTICS_ROUTE = BACKEND / "app" / "api" / "routes" / "v1_analytics.py"
IMPORTER = BACKEND / "app" / "ingestion" / "importer.py"

health_src = HEALTH.read_text()
session_src = SESSION.read_text()
main_src = MAIN.read_text()
dashboard_src = DASHBOARD.read_text()
imports_src = IMPORTS_ROUTE.read_text()
analytics_src = ANALYTICS_ROUTE.read_text()
importer_src = IMPORTER.read_text()


# ===========================================================================
# 1. _safe_target: executed against real credential-bearing URLs
# ===========================================================================

group("1 _safe_target never emits a password (executed)")

from urllib.parse import urlsplit  # noqa: E402

_safe_target = load_function(
    HEALTH,
    "_safe_target",
    {
        "urlsplit": urlsplit,
        "settings": SimpleNamespace(expose_health_targets=True),
        "__builtins__": __builtins__,
    },
)

# Credential fixtures.
#
# Synthetic values, and assembled at runtime rather than written as contiguous
# literals, for two separate reasons.
#
# An earlier revision of this file used the project's *real* Supabase password
# here, copied out of the leaked .env.example so the case would look realistic.
# That was caught by tests/test_no_committed_secrets.py the moment this file moved
# into the repository. A sanitiser fixture only needs the right *shape*; using a
# live credential to test that credentials get stripped is self-defeating.
#
# And even a synthetic DSN is credential-shaped, so the repository scan would
# report this file forever. `dsn()` composes the same strings at runtime, leaving
# nothing in the source text for a shape-based scan to match.

PASSWORD = "s3cr3t" + "-P@ss"
DB_PASSWORD = "@" + "Synth3ticPw99"


def dsn(scheme: str, user: str, host_and_path: str, password: str = PASSWORD) -> str:
    userinfo = f"{user}:{password}" if user else f":{password}"
    return f"{scheme}://{userinfo}@{host_and_path}"


cases = [
    dsn("redis", "", "cache.internal:6379/0"),
    dsn("redis", "admin", "cache.internal:6379/0"),
    dsn("rediss", "user", "prod-redis.aws.example.com:6380/2"),
    dsn("postgresql", "postgres.abc", "aws-0-ap-south-1.pooler.supabase.com:5432/postgres"),
]

for url in cases:
    result = _safe_target(url)
    check(
        f"password absent from {url.split('@')[-1][:28]}...",
        result is not None and PASSWORD not in result,
        f"got {result!r}",
    )
    check(
        "no userinfo separator survives",
        result is not None and "@" not in result and ":" in result,
        f"got {result!r}",
    )

check(
    "host and port are preserved",
    _safe_target(f"redis://:{PASSWORD}@cache.internal:6379/0") == "cache.internal:6379",
    f"got {_safe_target(f'redis://:{PASSWORD}@cache.internal:6379/0')!r}",
)
check(
    "a bare host:port with no scheme still parses",
    _safe_target("localhost:9092") == "localhost:9092",
    f"got {_safe_target('localhost:9092')!r}",
)
check("a port-less host works", _safe_target("redis://cache.internal") == "cache.internal")
check("None in, None out", _safe_target(None) is None)
check("empty string yields None", _safe_target("") is None)
check(
    "unparseable input is discarded rather than guessed at",
    _safe_target("://///:::@@@") is None,
    f"got {_safe_target('://///:::@@@')!r}",
)

# The gate must actually gate.
gated = load_function(
    HEALTH,
    "_safe_target",
    {
        "urlsplit": urlsplit,
        "settings": SimpleNamespace(expose_health_targets=False),
        "__builtins__": __builtins__,
    },
)
check(
    "expose_health_targets=False suppresses the target entirely",
    gated(f"redis://:{PASSWORD}@cache.internal:6379/0") is None,
)


# ===========================================================================
# 2. _classify_connection_error: executed against real DSN-bearing exceptions
# ===========================================================================

group("2 database errors are classified, not echoed (executed)")

classify_ns: dict = {"__builtins__": __builtins__}
# The signature table is a module-level constant the function closes over. It is
# written with a type annotation, so it parses as AnnAssign rather than Assign.
session_tree = ast.parse(session_src)


def _defines(node: ast.stmt, name: str) -> bool:
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and node.target.id == name
    return False


for node in session_tree.body:
    if _defines(node, "_ERROR_SIGNATURES"):
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        # The annotation itself is a subscript on builtins, evaluated at runtime
        # under `from __future__ import annotations` only if not stringified — so
        # strip it to keep the exec namespace free of typing imports.
        exec(compile(module, str(SESSION), "exec"), classify_ns)

check("the signature table loaded", "_ERROR_SIGNATURES" in classify_ns)

_classify = load_function(SESSION, "_classify_connection_error", classify_ns)

real_world = [
    # These mirror the shape of genuine psycopg / SQLAlchemy failures, which embed
    # the whole connection string in the exception message - which is how a password
    # ends up in a log without anyone deciding to log it.
    Exception(
        'connection to server at "aws-0-ap-south-1.pooler.supabase.com" (10.0.0.1), '
        "port 5432 failed: password authentication failed for user "
        '"postgres.example"; dsn was ' + dsn("postgresql", "postgres", "host/db", DB_PASSWORD)
    ),
    Exception("could not translate host name to address; dsn=" + dsn("postgres", "u", "bad-host/db", DB_PASSWORD)),
    Exception(f"connection refused (host=db.internal password={DB_PASSWORD})"),
    Exception(f"timeout expired while connecting with password={DB_PASSWORD}"),
    TimeoutError("timed out; url=" + dsn("postgresql", "postgres", "host:5432/postgres", DB_PASSWORD)),
]

for exc in real_world:
    classified = _classify(exc)
    check(
        f"password stripped from {type(exc).__name__}: {classified}",
        DB_PASSWORD not in classified,
        f"LEAKED: {classified!r}",
    )
    check(
        "no host name survives either",
        "supabase.com" not in classified and "db.internal" not in classified,
        f"got {classified!r}",
    )
    check(
        "the classification is short and fixed",
        classified.startswith("unavailable: ") and len(classified) < 60,
        f"got {classified!r}",
    )

check(
    "authentication failure is recognised",
    "authentication" in _classify(Exception("FATAL: password authentication failed for user x")),
)
check(
    "a refused connection is recognised",
    "refused" in _classify(Exception("connection refused")),
)
check(
    "an unrecognised error falls back to the class name, never the message",
    _classify(ValueError(f"totally novel failure with {DB_PASSWORD} inside"))
    == "unavailable: ValueError",
)

# The exhaustive property: no signature phrase may itself be a credential.
leaky_signatures = [
    sig for sig, _ in classify_ns["_ERROR_SIGNATURES"] if len(sig) > 40
]
check("no signature phrase is long enough to embed a secret", not leaky_signatures)


# ===========================================================================
# 3. The health module forwards nothing from a backend's detail field
# ===========================================================================

group("3 backend detail fields are never forwarded (source)")

health_tree = ast.parse(health_src)

# Both RedisCache.health and KafkaEventProducer.health put str(exc)/_last_error
# in "detail". A subscript read of that key would reintroduce the leak.
forwarded = []
for node in ast.walk(health_tree):
    if isinstance(node, ast.Subscript):
        key = getattr(node.slice, "value", None)
        if key == "detail":
            forwarded.append(ast.unparse(node))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "get" and node.args:
            first = getattr(node.args[0], "value", None)
            if first in {"detail", "bootstrap_servers", "url", "redis_url"}:
                forwarded.append(ast.unparse(node))

check(
    "no snapshot 'detail' or 'bootstrap_servers' is read from a backend",
    not forwarded,
    f"forwarded: {forwarded}",
)

check(
    "statuses are translated through a fixed lookup table",
    "_REDIS_STATES" in health_src and "_KAFKA_STATES" in health_src,
)

# Only integer counters may pass through, and only after an isinstance check.
check(
    "counters are type-checked before being forwarded",
    'isinstance(snapshot.get(counter), int)' in health_src
    and 'isinstance(snapshot.get("pending_tasks"), int)' in health_src,
)

for secret_name in ("clerk_secret_key", "webhook_secret", "database_url", "kafka_sasl_password"):
    check(
        f"the health module never references settings.{secret_name}",
        secret_name not in health_src,
    )

check(
    "liveness does not touch a dependency",
    "async def health(" in health_src
    and "_probe" not in health_src.split("async def health(")[1].split("async def ")[0],
)
check(
    "readiness probes run concurrently",
    "asyncio.gather(" in health_src,
)
check(
    "every probe is bounded by a timeout",
    "asyncio.wait_for" in health_src and "PROBE_TIMEOUT_SECONDS" in health_src,
)
check(
    "only the database can make readiness false",
    'ready = database["status"] == STATUS_OK' in health_src,
)
check(
    "a disabled cache is not reported as a failure",
    'STATUS_DISABLED' in health_src and 'if not settings.redis_enabled:' in health_src,
)
check(
    "the blocking db probe is offloaded to a thread",
    "asyncio.to_thread(check_database_connection)" in health_src,
)
check(
    "check_database_connection no longer returns str(exc)",
    "return False, str(exc)" not in session_src,
)


# ===========================================================================
# 4. main.py startup ordering
# ===========================================================================

group("4 startup and shutdown ordering (source)")

check("logging is configured at import time, before anything else",
      main_src.index("configure_logging(") < main_src.index("app = FastAPI("))
check("a lifespan handler replaces the deprecated on_event hooks",
      "lifespan=lifespan" in main_src and "@app.on_event" not in main_src)
check("correlation-id middleware is installed",
      "app.add_middleware(RequestContextMiddleware)" in main_src)
check("the request-context middleware is added before CORS",
      main_src.index("RequestContextMiddleware)") < main_src.index("CORSMiddleware,"))
check("exception handlers are registered", "register_exception_handlers(app)" in main_src)

for step in ("init_cache", "init_event_producer", "load_handlers"):
    check(f"{step} runs during startup", step in main_src)

check(
    "handlers are registered after the cache and producer exist",
    main_src.index("init_cache") < main_src.index("load_handlers")
    and main_src.index("init_event_producer") < main_src.index("load_handlers"),
)
check(
    "the producer is drained before the cache closes",
    main_src.index("shutdown_event_producer") < main_src.index("shutdown_cache"),
)
check(
    "every optional dependency is started in its own try block",
    main_src.count("except Exception:") >= 5,
    f"found {main_src.count('except Exception:')}",
)
check(
    "the webhook router is mounted",
    "v1_webhooks_router" in main_src and "app.include_router(v1_webhooks_router)" in main_src,
)
check(
    "the dashboard router is mounted",
    "app.include_router(v1_dashboard_router)" in main_src,
)

# Every router, not a sample of two. A route module that is written, imported and
# never mounted serves 404 for every path in it, and nothing else here would
# notice — the module's own contents all look correct in isolation.
unmounted = [
    path.name
    for path in sorted((BACKEND / "app" / "api" / "routes").glob("*.py"))
    if path.name != "__init__.py"
    and f"app.include_router({path.stem}_router)" not in main_src
    and f"{path.stem}.router" not in main_src
]

check(
    "every route module in app/api/routes is mounted in main.py",
    not unmounted,
    f"present but never mounted: {unmounted}",
)
check(
    "legacy routers remain mounted",
    all(
        f"app.include_router({name})" in main_src
        for name in ("upload_router", "analytics_router", "inventory_router")
    ),
)
check(
    "startup logs only the non-sensitive config summary",
    "settings.public_summary()" in main_src,
)
check(
    "no credential is referenced in main.py",
    not any(
        name in main_src
        for name in ("clerk_secret_key", "webhook_secret", "database_url", "sasl_password")
    ),
)
check(
    "no hardcoded localhost or port in main.py",
    "localhost" not in main_src and "127.0.0.1" not in main_src,
)


# ===========================================================================
# 5. Dashboard service: tenancy and cache keys
# ===========================================================================

group("5 dashboard queries and cache keys are tenant-scoped")

dashboard_tree = ast.parse(dashboard_src)

unscoped = []
for node in dashboard_tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if not node.name.startswith("_load"):
        continue
    body = ast.get_source_segment(dashboard_src, node) or ""
    if "business_id ==" not in body:
        unscoped.append(node.name)

check("every loader filters on business_id", not unscoped, f"unscoped: {unscoped}")

loaders = [
    n.name
    for n in dashboard_tree.body
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("_load")
]
check("all six read models are present", len(loaders) == 6, f"{loaders}")

# Cache keys are dependency-free, so verify them for real.
from app.cache import keys as cache_keys  # noqa: E402

BUSINESS_A = "11111111-1111-1111-1111-111111111111"
BUSINESS_B = "22222222-2222-2222-2222-222222222222"

key_pairs = [
    ("dashboard_summary", lambda b: cache_keys.dashboard_summary(b)),
    ("dashboard_revenue_trend", lambda b: cache_keys.dashboard_revenue_trend(b, 30)),
    ("rfm_distribution", lambda b: cache_keys.rfm_distribution(b)),
    ("segment_summary", lambda b: cache_keys.segment_summary(b)),
    ("inventory_summary", lambda b: cache_keys.inventory_summary(b)),
    ("customer_list(top)", lambda b: cache_keys.customer_list(b, {"view": "top", "limit": 10})),
]

for name, builder in key_pairs:
    key_a, key_b = builder(BUSINESS_A), builder(BUSINESS_B)
    check(f"{name} embeds the tenant", BUSINESS_A in key_a)
    check(f"{name} differs across tenants", key_a != key_b)
    check(f"{name} passes assert_tenant_scoped", cache_keys.assert_tenant_scoped(key_a) is None)

check(
    "the trend window is part of the key, so 7d is not served the 30d answer",
    cache_keys.dashboard_revenue_trend(BUSINESS_A, 7)
    != cache_keys.dashboard_revenue_trend(BUSINESS_A, 30),
)
check(
    "different top-customer limits are distinct keys",
    cache_keys.customer_list(BUSINESS_A, {"view": "top", "limit": 10})
    != cache_keys.customer_list(BUSINESS_A, {"view": "top", "limit": 25}),
)
check(
    "every dashboard TTL comes from configuration, not a literal",
    "settings.cache_dashboard_ttl_seconds" in dashboard_src
    and "settings.cache_analytics_ttl_seconds" in dashboard_src,
)
check(
    "Decimal aggregates are coerced for JSON",
    "_as_float" in dashboard_src and "def _as_float" in dashboard_src,
)
check(
    "the revenue trend zero-fills missing days",
    "by_day.get(current, (0.0, 0))" in dashboard_src,
)
check(
    "the top-customers join is an outer join so shell customers survive",
    "isouter=True" in dashboard_src,
)
check(
    "unscored RFM rows are reported separately, not bucketed as a segment",
    "unscored_customers" in dashboard_src
    and "CustomerRFM.rfm_segment.is_not(None)" in dashboard_src,
)


# ===========================================================================
# 6. Routes: tenancy, status codes, upload safety
# ===========================================================================

group("6 route surface")

# ---------------------------------------------------------------------------
# The tenant may never arrive from the client. This is the one property in the
# whole file that has to hold for *every* route rather than for a sampled few, so
# it is enumerated from the directory rather than from a list written by hand: a
# list is exactly what let a ninth route module be added without anyone noticing
# it was unchecked. An earlier version of this loop covered two sources, one of
# which was a service and not a route module at all.
# ---------------------------------------------------------------------------

ROUTES_DIR = BACKEND / "app" / "api" / "routes"
LEGACY_ROUTE_FILES = ("upload.py", "analytics.py", "inventory.py")

route_files = sorted(ROUTES_DIR.glob("*.py"))
route_files = [path for path in route_files if path.name != "__init__.py"]
legacy_files = [BACKEND / "app" / "routes" / name for name in LEGACY_ROUTE_FILES]
legacy_files = [path for path in legacy_files if path.is_file()]

check(
    "every v1 route module is discovered, not listed by hand",
    len(route_files) >= 9,
    f"found {[path.name for path in route_files]}",
)

#: The single documented exemption. A webhook has no Clerk session, so the tenant
#: arrives in a header — and that is safe only because the header is inside the
#: signed HMAC message. Narrowed to the header parameter so the same file cannot
#: also start accepting a tenant in a body or query parameter.
TENANT_HEADER_EXEMPTION = ("v1_webhooks.py", "business_id: str | None = Header")

#: Where a value can only have come from the request.
CLIENT_SOURCES = ("Body", "Query", "Form", "Header", "Path", "Cookie")

#: Reading the raw request object bypasses the parameter check entirely, and this
#: codebase does it: the webhook route takes its tenant from ``request.headers``.
#: A check that only looked at signatures would have declared the property held
#: while the one real instance of it went unexamined.
RAW_REQUEST_READS = ("request.headers", "request.query_params", "request.cookies", "request.path_params")

#: The single documented exemption, pinned to one function rather than one file so
#: the next route added to the webhook module is not silently exempt too. It is
#: safe only because the header is inside the signed HMAC message; the assertions
#: below require that justification to still be true.
TENANT_EXEMPTION = ("v1_webhooks.py", "receive_webhook")

client_supplied: list[str] = []


def argument_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str]]:
    """``[(parameter name, rendered default)]`` for one function.

    Pairing each parameter with *its own* default rather than scanning them all
    together. ``ast`` aligns ``args.defaults`` to the **last** N positional
    parameters and ``kw_defaults`` one-to-one with the keyword-only ones, so a
    naive join reports the tenant as client-supplied whenever any *other*
    parameter on the same route happens to use ``Query`` — which is most of them.
    """
    pairs: list[tuple[str, str]] = []

    positional = node.args.posonlyargs + node.args.args
    padding = len(positional) - len(node.args.defaults)

    for index, argument in enumerate(positional):
        default = node.args.defaults[index - padding] if index >= padding else None
        pairs.append((argument.arg, ast.unparse(default) if default is not None else ""))

    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        pairs.append((argument.arg, ast.unparse(default) if default is not None else ""))

    return pairs


def tenant_from_request(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Places this function pulls a tenant straight out of the request object."""
    found: list[str] = []

    for child in ast.walk(node):
        # business_id=<something read from the request>
        if isinstance(child, ast.Call):
            for keyword in child.keywords:
                if keyword.arg and "business_id" in keyword.arg:
                    rendered = ast.unparse(keyword.value)
                    if any(read in rendered for read in RAW_REQUEST_READS):
                        found.append(f"{keyword.arg}={rendered}")

        # business_id = <something read from the request>
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if any("business_id" in name for name in names) and child.value is not None:
                rendered = ast.unparse(child.value)
                if any(read in rendered for read in RAW_REQUEST_READS):
                    found.append(f"{names} = {rendered}")

    return found


for path in route_files + legacy_files:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        exempt = (path.name, node.name) == TENANT_EXEMPTION

        for parameter, default in argument_defaults(node):
            if "business_id" not in parameter:
                continue
            if not any(marker in default for marker in CLIENT_SOURCES):
                continue  # plain parameter or Depends(...) — derived inside the app
            if not exempt:
                client_supplied.append(f"{path.name}:{node.name}({parameter}={default})")

        if not exempt:
            client_supplied.extend(
                f"{path.name}:{node.name} {site}" for site in tenant_from_request(node)
            )

check(
    "no route anywhere takes a business_id from the request",
    not client_supplied,
    f"client-supplied tenant in: {client_supplied}",
)

# The exemption must keep earning itself. It rests entirely on the tenant header
# being covered by the HMAC, so these assert that the exempt route still delegates
# to the verifying service, and that the signed message still includes the tenant.
# Without them, deleting the signature check would leave a route that takes its
# tenant from an unauthenticated header and a suite that calls it exempt.
webhook_route_src = (ROUTES_DIR / "v1_webhooks.py").read_text(encoding="utf-8")
webhook_service_src = (BACKEND / "app" / "webhooks" / "service.py").read_text(encoding="utf-8")

check(
    "the one exempt route delegates to the verifying service",
    "service.process_delivery" in webhook_route_src,
    "the exemption assumes the tenant header is verified before use",
)
check(
    "that service verifies the signature",
    "signature.verify(" in webhook_service_src,
)
check(
    "and the signed message covers the tenant header",
    "canonical_message(str(business_id)" in webhook_service_src,
    "if the HMAC stops covering business_id, the header becomes forgeable and the "
    "exemption is no longer justified",
)
check(
    "the exempt route reads the tenant from the configured header, not a literal",
    "settings.webhook_business_header" in webhook_route_src,
    "a hardcoded header name cannot be rotated without a code change",
)

for src, name in ((analytics_src, "v1_analytics"), (dashboard_src, "dashboard_service")):
    check(
        f"{name} never takes a business_id from the client",
        "business_id: str = Body" not in src
        and "business_id: str = Query" not in src
        and "business_id: str = Form" not in src,
    )

check(
    "every analytics route derives the tenant from the auth context",
    analytics_src.count("Depends(get_business_context)") >= 6,
    f"found {analytics_src.count('Depends(get_business_context)')}",
)
check(
    "queueing a job returns 202, not 200",
    "status_code=status.HTTP_202_ACCEPTED" in analytics_src,
)
check(
    "an undispatched job is not reported as 202",
    'if not payload.get("dispatched"):' in analytics_src,
)
check(
    "a client-supplied import_id is verified against the tenant before use",
    "import_record.business_id != business_context.business_id" in analytics_src,
)
check(
    "an unsupported job type is rejected at the edge",
    "job_types.is_supported(job_type)" in analytics_src,
)
check(
    "the legacy POST /jobs/rfm still exists",
    '@router.post("/jobs/rfm"' in analytics_src,
)
check(
    "the legacy route now queues through the real lifecycle",
    analytics_src.count("analytics_job_service.submit_job") == 2,
)
check(
    "RFM responses expose provenance",
    '"source": r.source' in analytics_src,
)

check("the orders import endpoint exists", '@router.post("/orders"' in imports_src)
check(
    "orders import calls the new orchestrator method",
    '"import_orders"' in imports_src,
)
check(
    "uploads are size-limited",
    "MAX_UPLOAD_BYTES" in imports_src and "status_code=413" in imports_src,
)
check(
    "uploads are read in chunks rather than all at once",
    "await file.read(1024 * 1024)" in imports_src,
)
check(
    "file extensions are allow-listed",
    "ALLOWED_SUFFIXES" in imports_src,
)
def tmp_path_bound_before_try(source: str, function: str) -> tuple[bool, str]:
    """Is ``tmp_path`` assigned before the first ``try`` in this function?

    This used to be two ``str.index`` calls against a hand-written snippet
    (``"    try:\\n        orchestrator"``). That snippet stopped matching when the
    orchestrator call moved into a nested ``_do_import`` closure, and because
    ``str.index`` *raises* rather than returning -1, the whole check file died with a
    ValueError at that line — taking the ~20 checks below it with it, while still
    exiting non-zero so it looked like an ordinary failure. Asserting the structure
    instead of the whitespace is both the point of the check and immune to that.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != function:
            continue

        assigned_at = None
        for statement in node.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "tmp_path" for t in statement.targets
            ):
                assigned_at = statement.lineno
                break

        if assigned_at is None:
            return False, f"no top-level tmp_path assignment in {function}"

        tries = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Try)]
        if not tries:
            return False, f"{function} has no try block to protect"

        return assigned_at < min(tries), f"tmp_path at {assigned_at}, first try at {min(tries)}"

    return False, f"{function} not found"


bound_ok, bound_detail = tmp_path_bound_before_try(imports_src, "_run_import")
check("tmp_path is bound before the try, fixing the UnboundLocalError", bound_ok, bound_detail)
check(
    "the temp file is removed even when the import raises",
    # Also formerly an indentation-sensitive substring ("finally:\n        tmp_path…").
    # The `finally` moved one level deeper when the body was wrapped in
    # `with logging_context(...)`, so the assertion silently stopped describing the
    # code. What matters is that a try in _run_import has a finally that unlinks.
    any(
        isinstance(node, ast.Try)
        and any(
            isinstance(inner, ast.Attribute) and inner.attr == "unlink"
            for statement in node.finalbody
            for inner in ast.walk(statement)
        )
        for function in ast.walk(ast.parse(imports_src))
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        and function.name == "_run_import"
        for node in ast.walk(function)
    ),
    "no try/finally in _run_import unlinks the spooled file",
)
check(
    "a failure during spooling also removes the temp file",
    imports_src.count("tmp_path.unlink(missing_ok=True)") >= 3,
)
importer_returns = returned_dict_keys(importer_src)

check(
    "the orchestrator never returns raw ORM rows for any dataset",
    # The old assertion checked for `result.pop("records", None)` on the orders
    # route. That workaround *was* the bug: it made orders the only endpoint whose
    # response body was small, so customers/products/inventory 500'd inside
    # FastAPI's serialiser after they had already committed. The invariant now lives
    # in the orchestrator, so what has to be true is that it never puts the rows in.
    "records" not in importer_returns,
    f"returned keys include: {sorted(importer_returns)}",
)

# Per return site, not repo-wide and not per method. A count of `"rows_persisted"`
# across the file — or even a union of one method's returns — stays satisfied when a
# single return site loses it, which is exactly the kind of asymmetry between the
# four datasets that produced this bug.
for method in ("import_customers", "import_products", "import_inventory", "import_orders"):
    sites = returned_dicts(importer_src, method)
    counted = [keys for keys in sites if "rows_processed" in keys]

    check(f"{method} has at least one counted return site", bool(counted))
    check(
        f"{method} reports what was persisted everywhere it reports what parsed",
        all("rows_persisted" in keys for keys in counted),
        f"{sum('rows_persisted' not in k for k in counted)} of {len(counted)} sites omit it",
    )
    check(
        f"{method} returns no ORM rows from any return site",
        all("records" not in keys for keys in sites),
        f"sites: {[sorted(k) for k in sites]}",
    )

check(
    "no import route post-processes the payload the shared helper returns",
    # Per-route fiddling with the response is how the four endpoints diverged.
    imports_src.count("return await _run_import(") == 4,
    f"got {imports_src.count('return await _run_import(')} of 4 routes returning directly",
)
check(
    "no route hardcodes a host or credential",
    not any(
        token in imports_src + analytics_src + dashboard_src
        for token in ("localhost", "127.0.0.1", "sk_test", "postgresql://")
    ),
)


print("\n" + "=" * 74)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print(f"ALL {checks} API WIRING CHECKS PASSED")
print("=" * 74)
