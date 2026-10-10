"""Static validation of ``docker-compose.yml``.

    python3 tests/test_compose.py

Docker is not installed in every environment this repository gets checked out into,
and ``docker compose config`` needs the binary. That leaves the compose file as the
one piece of infrastructure nobody verifies until it fails at 9am — so this asserts
the parts that are easy to get wrong and expensive to debug.

The Kafka listener configuration is the main event. Get it wrong and the symptom is
not a startup error: bootstrap succeeds, metadata comes back naming an address the
client cannot reach, and every produce call times out half a minute later. The
checks below encode the invariants that prevent that, plus the single-node
replication factors that otherwise make the internal offsets topic uncreatable and
break consumer-group commits.

Run with pytest if it is installed, or directly with python3 — the file works both
ways so it can be checked in an environment with no third-party packages.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a dev convenience, not a runtime dep
    print("SKIPPED: PyYAML is not installed, so the compose file cannot be parsed")
    sys.exit(0)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Overridable so the checks can be pointed at a deliberately broken copy. A check
#: nobody has ever seen fail is not evidence of anything, and this is how the
#: assertions below were confirmed to bite.
COMPOSE_PATH = Path(os.environ.get("CBPA_COMPOSE_PATH") or REPO_ROOT / "docker-compose.yml")

#: Must stay in step with ``app.events.topics.ALL_TOPICS``. Duplicated here on
#: purpose: importing the module would need the app's dependencies, and the point
#: of this file is to run without them. The drift test below is what keeps the
#: duplication honest.
EXPECTED_TOPICS = {
    "raw.imports",
    "customers.events",
    "orders.events",
    "inventory.events",
    "analytics.jobs",
    "events.dlq",
}


def load() -> tuple[str, dict]:
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    return raw, yaml.safe_load(raw)


def test_compose_parses_and_defines_expected_services() -> None:
    _, doc = load()

    assert set(doc["services"]) == {
        "kafka",
        "redis",
        "kafka-init",
        "backend",
        "worker",
        "frontend",
    }

    # PostgreSQL must never appear here. Supabase is the source of truth, and a
    # second Postgres in a container is a convincing-looking database sharing no
    # data with the real one.
    for name, service in doc["services"].items():
        image = str(service.get("image", ""))
        assert "postgres" not in image.lower(), f"{name} runs a Postgres image"
        assert "supabase" not in image.lower(), f"{name} tries to containerise Supabase"


def test_a_worker_consumes_what_the_api_publishes() -> None:
    """The event bus needs a reader, and it has to be a separate container.

    Without this service the API happily publishes analytics jobs to Kafka and
    nothing ever consumes them: every job sits in ``pending`` forever, and the
    failure mode is silence rather than an error. It cannot be folded into the
    ``backend`` service either — that one runs uvicorn, so a blocking consumer
    loop there would be the event-loop bug all over again.

    Asserting the entrypoint rather than merely the service name, because a
    ``worker`` container running uvicorn would satisfy the name and consume
    nothing.
    """
    _, doc = load()
    worker = doc["services"]["worker"]

    command = worker.get("command")
    flat = " ".join(command) if isinstance(command, list) else str(command or "")

    assert "app.events.worker" in flat, f"worker does not run the consumer: {flat!r}"
    assert "uvicorn" not in flat, "the worker must not run the HTTP server"
    # A consumer serves no HTTP, so publishing a port would expose a listener
    # whose only purpose would be to be probed.
    assert not worker.get("ports"), "the worker must not publish ports"

    # It reads from Kafka, so it must not start before Kafka and the topics exist.
    depends = worker.get("depends_on") or {}
    assert "kafka" in depends, "worker does not wait for Kafka"
    assert depends.get("kafka-init", {}).get("condition") == "service_completed_successfully", (
        "worker may start before the topics are created"
    )


def test_kafka_listeners_are_internally_consistent() -> None:
    _, doc = load()
    env = doc["services"]["kafka"]["environment"]

    declared = {entry.split("://")[0] for entry in env["KAFKA_LISTENERS"].split(",")}
    advertised = {entry.split("://")[0] for entry in env["KAFKA_ADVERTISED_LISTENERS"].split(",")}
    mapped = {entry.split(":")[0] for entry in env["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"].split(",")}

    assert declared == mapped, f"listener/protocol-map mismatch: {declared ^ mapped}"
    assert advertised <= declared, f"advertised but not declared: {advertised - declared}"

    controller = env["KAFKA_CONTROLLER_LISTENER_NAMES"]
    assert controller in declared
    # Advertising the controller listener to clients invites them to talk to the
    # raft endpoint, which answers nothing useful.
    assert controller not in advertised

    assert env["KAFKA_INTER_BROKER_LISTENER_NAME"] in advertised

    # The startup failure this encodes, verbatim from the broker:
    #
    #   advertised.listeners cannot use the nonroutable meta-address 0.0.0.0.
    #   Use a routable IP address.
    #
    # Checked on `listeners` too, not just `advertised.listeners`. That is the part
    # that took a real debugging session: advertised.listeners was already explicit
    # and correct, but when Kafka has to derive an advertised endpoint it falls back
    # to `listeners`, so a wildcard there reaches the same validation. An empty host
    # (`DOCKER://:9092`) binds to every interface identically without naming the
    # meta-address.
    for key in ("KAFKA_LISTENERS", "KAFKA_ADVERTISED_LISTENERS"):
        assert "0.0.0.0" not in env[key], f"{key} names the nonroutable meta-address"

    # And the internal listener must advertise the compose service name, which is
    # what `hostname:` pins so anything Kafka derives from its own name resolves.
    internal = env["KAFKA_INTER_BROKER_LISTENER_NAME"]
    internal_advertised = next(
        entry for entry in env["KAFKA_ADVERTISED_LISTENERS"].split(",") if entry.startswith(f"{internal}://")
    )
    assert internal_advertised.split("://")[1].split(":")[0] == "kafka"
    assert doc["services"]["kafka"].get("hostname") == "kafka"

    voter_port = env["KAFKA_CONTROLLER_QUORUM_VOTERS"].rsplit(":", 1)[-1]
    controller_port = next(
        entry for entry in env["KAFKA_LISTENERS"].split(",") if entry.startswith(f"{controller}://")
    ).rsplit(":", 1)[-1]
    assert voter_port == controller_port, (
        f"quorum voter port {voter_port} does not match the controller listener port {controller_port}"
    )

    # The host-facing listener has to advertise a name the host can resolve. A
    # container hostname here is the classic "bootstrap works, produce times out"
    # configuration.
    host_advertised = next(
        entry for entry in env["KAFKA_ADVERTISED_LISTENERS"].split(",") if entry.startswith("HOST://")
    )
    assert "localhost" in host_advertised or "127.0.0.1" in host_advertised

    # Three ports have to agree for host access to work, and they are not all the
    # same number: the HOST listener binds 19092 inside the container because the
    # DOCKER listener already holds 9092, the publication maps host 9092 to that
    # 19092, and the advertised port must be the *host* side — that is the only one a
    # client on the host can dial. Getting the last one wrong fails silently:
    # bootstrap succeeds, then the client is handed a port nothing is listening on.
    host_bound = next(
        entry for entry in env["KAFKA_LISTENERS"].split(",") if entry.startswith("HOST://")
    ).rsplit(":", 1)[-1]

    published = [str(mapping) for mapping in doc["services"]["kafka"]["ports"]]
    assert len(published) == 1, f"expected one published Kafka port, got {published}"
    parts = published[0].split(":")
    assert len(parts) == 3, f"expected an address-scoped mapping like 127.0.0.1:9092:19092, got {published[0]}"
    _address, host_side, container_side = parts

    assert container_side == host_bound, (
        f"published container port {container_side} is not the HOST listener port {host_bound}"
    )
    assert host_advertised.rsplit(":", 1)[-1] == host_side, (
        f"HOST advertises port {host_advertised.rsplit(':', 1)[-1]} but is published on {host_side}"
    )

    # The DOCKER listener must not be published. It is advertised as kafka:9092, a
    # name only resolvable inside the compose network, so a host client bootstrapping
    # there would be told to reconnect somewhere it cannot reach.
    docker_bound = next(
        entry for entry in env["KAFKA_LISTENERS"].split(",") if entry.startswith("DOCKER://")
    ).rsplit(":", 1)[-1]
    assert container_side != docker_bound, "the DOCKER listener must not be published to the host"


def test_kraft_mode_has_no_zookeeper() -> None:
    _, doc = load()

    # Checked structurally rather than by scanning the text: the file explains in a
    # comment *why* there is no Zookeeper, and a substring search on the raw source
    # fails on that explanation.
    for name, service in doc["services"].items():
        assert "zookeeper" not in str(service.get("image", "")).lower(), f"{name} runs Zookeeper"
        assert "zookeeper" not in name.lower(), f"{name} is a Zookeeper service"

        for key in service.get("environment", {}):
            assert "ZOOKEEPER" not in key.upper(), f"{name} sets {key}"

    env = doc["services"]["kafka"]["environment"]
    assert "controller" in env["KAFKA_PROCESS_ROLES"]
    assert "broker" in env["KAFKA_PROCESS_ROLES"]
    assert env["CLUSTER_ID"], "KRaft needs a cluster id to format storage"


def test_single_node_replication_factors() -> None:
    _, doc = load()
    env = doc["services"]["kafka"]["environment"]

    for key in (
        "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR",
        "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR",
        "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR",
        "KAFKA_DEFAULT_REPLICATION_FACTOR",
        "KAFKA_MIN_INSYNC_REPLICAS",
    ):
        assert int(env[key]) == 1, f"{key} must be 1 on a single-node broker"


def test_services_are_bound_to_loopback_only() -> None:
    _, doc = load()

    for name in ("kafka", "redis", "backend", "frontend"):
        for mapping in doc["services"][name].get("ports", []):
            assert str(mapping).startswith("127.0.0.1:"), (
                f"{name} publishes {mapping}; an unauthenticated service must not leave this host"
            )


def test_no_credentials_in_the_compose_file() -> None:
    raw, doc = load()

    # No `\b` before the keyword: an underscore is a word character, so `\bpassword`
    # does not match inside `REDIS_PASSWORD` — which is exactly how a credential would
    # be spelled in a compose environment block. Found by mutation-testing this file.
    for pattern in (
        r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S",
        r"sk_(test|live)_\w+",
        r"whsec_\w+",
        r"postgres(ql)?://\S*:\S*@",
    ):
        assert not re.search(pattern, raw), f"compose file matches {pattern}"

    # SASL is for managed brokers and is configured through the backend's own
    # environment, not here.
    env = doc["services"]["kafka"]["environment"]
    assert not any(key.startswith("KAFKA_SASL") for key in env)


def test_init_topics_match_the_application_catalogue() -> None:
    _, doc = load()
    script = doc["services"]["kafka-init"]["command"][0]

    # `^\s*create\s+([\w.]+)` rather than `(\S+)`: the shell function definition
    # line is `create () {`, and a looser pattern captures "()" as a topic name.
    created = set(re.findall(r"^\s*create\s+([\w.]+)", script, re.MULTILINE))

    assert created == EXPECTED_TOPICS, f"topic drift against app.events.topics: {created ^ EXPECTED_TOPICS}"

    # Compose interpolates `$VAR` itself; `$$VAR` is how a literal dollar reaches
    # the shell. An unescaped variable would expand to empty at compose time and
    # the script would run against an empty broker address.
    assert "$$" in script
    for fragment in re.findall(r"(?<!\$)\$\{?\w+", script):
        assert fragment.startswith("$$"), f"unescaped variable {fragment} would be eaten by compose"

    # The dead-letter topic wants long retention: the whole point is that a human
    # looks at it days later.
    dlq_retention = int(re.search(r"create\s+events\.dlq\s+\d+\s+(\d+)", script).group(1))
    assert dlq_retention >= 7 * 24 * 3600 * 1000, "the DLQ needs more than a week of retention"


def test_redis_is_configured_as_a_cache_not_a_database() -> None:
    _, doc = load()
    command = " ".join(str(part) for part in doc["services"]["redis"]["command"])

    # A cache without a ceiling grows until the host swaps and the fast path
    # becomes the slow one.
    assert "--maxmemory" in command
    assert "allkeys-lru" in command, "eviction must be enabled; a cache may drop keys, a database may not"


def test_both_services_have_healthchecks() -> None:
    _, doc = load()

    for name in ("kafka", "redis"):
        assert "healthcheck" in doc["services"][name], f"{name} has no healthcheck"

    # Kafka's must exercise the protocol. A TCP port check passes while the broker
    # is still forming its quorum and rejecting every request — exactly the window
    # a dependent service needs to wait through.
    kafka_test = " ".join(str(part) for part in doc["services"]["kafka"]["healthcheck"]["test"])
    assert "kafka-topics" in kafka_test or "kafka-broker-api-versions" in kafka_test

    assert doc["services"]["kafka-init"]["depends_on"]["kafka"]["condition"] == "service_healthy"


def test_kafka_init_runs_on_up_and_gates_the_backend() -> None:
    _, doc = load()

    # Not behind a profile. `docker compose up --build` has to bring the whole
    # application up in one command, and that includes creating the topics.
    #
    # It was under `profiles: ["init"]` in an earlier revision, which meant a plain
    # `up` skipped it and left the broker to auto-create the topics on first use —
    # with default retention, including a dead-letter topic that expired in a week.
    # `--create --if-not-exists` is a no-op once the topic exists, so running the
    # init job afterwards could not repair it.
    assert "profiles" not in doc["services"]["kafka-init"], (
        "kafka-init must run as part of `up`, not behind a profile"
    )

    # And the backend waits for it to exit 0, so the API never publishes to a topic
    # the broker would have auto-created with the wrong configuration.
    assert (
        doc["services"]["backend"]["depends_on"]["kafka-init"]["condition"]
        == "service_completed_successfully"
    )


def test_containers_address_each_other_by_service_name() -> None:
    """localhost inside a container means that container, not the host.

    This is the single most common way a compose file that looks right fails at
    runtime: the backend's own .env says ``redis://localhost:6379`` because that is
    correct when uvicorn runs on the host, and copied into a container it becomes a
    connection to nothing. The compose ``environment`` block overrides those two
    values, and ``environment`` beats ``env_file`` — which is the whole reason they
    are set here rather than documented as a manual step.
    """
    _, doc = load()
    env = doc["services"]["backend"]["environment"]

    assert env["REDIS_URL"] == "redis://redis:6379/0"
    assert env["KAFKA_BOOTSTRAP_SERVERS"] == "kafka:9092"

    # The Kafka address must be the DOCKER listener. The HOST listener advertises
    # itself as `localhost:19092`, so a containerised client that bootstraps there
    # gets told to reconnect to itself and every produce times out.
    advertised = dict(
        entry.split("://") for entry in doc["services"]["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"].split(",")
    )
    assert advertised["DOCKER"] == env["KAFKA_BOOTSTRAP_SERVERS"]

    for key, value in env.items():
        assert "localhost" not in str(value), f"backend {key} points at localhost"
        assert "127.0.0.1" not in str(value), f"backend {key} points at 127.0.0.1"

    # DATABASE_URL must NOT be listed. Supabase is external and its URL comes from
    # backend/.env; an `environment` entry of the `${DATABASE_URL:-}` form would
    # override that file with an empty string whenever the host shell had no such
    # variable, which is almost always.
    assert "DATABASE_URL" not in env, "DATABASE_URL must come from backend/.env, not from compose"
    assert "./backend/.env" in doc["services"]["backend"]["env_file"]

    # kafka-init is a container too, and it is the one place a broker address is
    # written as a shell variable rather than an environment key — which is how it
    # escaped this check until a mutation test pointed at it. `localhost` there is the
    # init container itself, so topic creation would fail against nothing while the
    # backend waited on it to exit 0.
    script = doc["services"]["kafka-init"]["command"][0]
    broker = re.search(r"BROKER=(\S+)", script).group(1)
    assert broker == env["KAFKA_BOOTSTRAP_SERVERS"], (
        f"kafka-init uses {broker} but the backend uses {env['KAFKA_BOOTSTRAP_SERVERS']}"
    )
    assert "localhost" not in broker and "127.0.0.1" not in broker


def test_frontend_bundle_contains_no_developer_hostname() -> None:
    """VITE_API_BASE_URL has to default to empty.

    Vite inlines build args into the JavaScript, so a value here is compiled into a
    bundle that every browser downloads — and `http://localhost:8000` in a bundle
    tells each visitor's browser to call its own machine. Empty means relative
    requests, which nginx proxies to the backend over the compose network.
    """
    raw, doc = load()
    args = doc["services"]["frontend"]["build"]["args"]

    assert args["VITE_API_BASE_URL"] == "${VITE_API_BASE_URL:-}"
    for key, value in args.items():
        assert str(value).endswith(":-}"), f"{key} must default to empty, not to a literal"

    # No hardcoded host anywhere in the file except Kafka's HOST listener (which is
    # advertised to clients on the host, where localhost is the correct answer) and
    # the loopback-scoped port publications.
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "KAFKA_ADVERTISED_LISTENERS" in stripped:
            continue
        if "localhost" in stripped:
            assert "9092" in stripped, f"unexpected localhost in compose: {stripped}"


def main() -> int:
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0

    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failed += 1
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok   {test.__name__}")

    print()
    if failed:
        print(f"{failed} of {len(tests)} COMPOSE CHECKS FAILED")
        return 1

    print(f"ALL {len(tests)} COMPOSE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
