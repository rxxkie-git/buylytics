"""The analytics job claim must stay a conditional UPDATE, not a read-then-write.

``test_job_claim.py`` proves the behaviour, but it needs SQLAlchemy, so in an
environment where nothing can be installed it reports a skip and the property is
unguarded. This checker parses the source with ``ast`` instead, and so runs
everywhere.

The two are not redundant. The pytest suite would still pass if someone
reintroduced a Python-side status check *in addition to* the conditional update —
on one session it looks identical. What actually regresses is structural: the
decision moving back out of the database and into the service. That is what is
asserted here.

Run directly:

    python3 tests/check_job_claim.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND / "app" / "repositories" / "analytics_repository.py"
SERVICE = BACKEND / "app" / "services" / "analytics_job_service.py"

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


def function_named(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def calls_in(node: ast.AST) -> list[str]:
    """Every called name in a subtree, as ``attr`` or bare ``id``."""
    names = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            function = child.func
            names.append(
                function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
            )
    return names


# ---------------------------------------------------------------------------

group("1 both modules exist and parse")

check("analytics_repository.py exists", REPOSITORY.is_file(), f"looked in {REPOSITORY}")
check("analytics_job_service.py exists", SERVICE.is_file(), f"looked in {SERVICE}")

if not REPOSITORY.is_file() or not SERVICE.is_file():
    print("\nCannot continue without both modules.")
    sys.exit(1)

repository_source = REPOSITORY.read_text(encoding="utf-8")
service_source = SERVICE.read_text(encoding="utf-8")
repository_tree = ast.parse(repository_source, filename=str(REPOSITORY))
service_tree = ast.parse(service_source, filename=str(SERVICE))

check("analytics_repository.py parses", True)
check("analytics_job_service.py parses", True)


# ---------------------------------------------------------------------------

group("2 the repository claim is a single conditional UPDATE")

claim = function_named(repository_tree, "claim_run")
check("AnalyticsRepository.claim_run exists", claim is not None)

if claim is None:
    print("\nCannot continue without claim_run.")
    sys.exit(1)

claim_calls = calls_in(claim)
claim_source = ast.get_source_segment(repository_source, claim) or ""

check(
    "the claim issues an update()",
    "update" in claim_calls,
    "a claim built from select() cannot be atomic, whatever it does afterwards",
)
check(
    "the claim does not select the row first",
    "select" not in claim_calls and "get_run" not in claim_calls,
    "reading the row before writing it is the race this replaced",
)
check(
    "the update is conditional on the current status",
    ".status.in_(" in claim_source,
    "an UPDATE with no status predicate overwrites a running or finished job",
)
check(
    "the update is scoped to one tenant",
    "AnalyticsRun.business_id ==" in claim_source,
    "without the tenant in the WHERE clause a guessed job id claims another tenant's job",
)
check(
    "the update is scoped to one job",
    "AnalyticsRun.id ==" in claim_source,
    "an UPDATE without the id claims every row that matches the status",
)
check(
    "the outcome is decided by rowcount",
    "rowcount" in claim_source,
    "the caller cannot tell whether it won the claim any other way",
)
check(
    "rowcount is compared to exactly one row",
    "rowcount == 1" in claim_source,
    "a truthiness or >= test would accept a multi-row update as a successful claim",
)
check(
    "claim_run returns a bool",
    isinstance(claim.returns, ast.Name) and claim.returns.id == "bool",
    "the caller branches on this value; anything else invites a truthy object",
)


# ---------------------------------------------------------------------------

group("3 the service delegates the decision and does not re-implement it")

service_claim = function_named(service_tree, "_claim_run")
check("_claim_run exists in the service", service_claim is not None)

if service_claim is None:
    print("\nCannot continue without _claim_run.")
    sys.exit(1)

service_claim_source = ast.get_source_segment(service_source, service_claim) or ""

check(
    "_claim_run calls repository.claim_run",
    "claim_run" in calls_in(service_claim),
    "the service is deciding the transition itself again",
)

# The precise defect being excluded: assigning the running status onto a row the
# service read, which is what made two workers both pass the old status check.
status_assignments = [
    ast.unparse(node)
    for node in ast.walk(service_claim)
    if isinstance(node, ast.Assign)
    for target in node.targets
    if isinstance(target, ast.Attribute) and target.attr == "status"
]

check(
    "_claim_run never assigns .status itself",
    not status_assignments,
    f"found {status_assignments} - the transition belongs in the UPDATE's WHERE clause",
)
check(
    "_claim_run branches on the claim result",
    "if not claimed" in service_claim_source or "if claimed" in service_claim_source,
    "ignoring the return value means losing the race silently proceeds to run the job",
)
check(
    "a lost claim returns None",
    "return None" in service_claim_source,
    "the caller treats None as 'do not run'; anything else runs the job",
)


# ---------------------------------------------------------------------------

group("4 'running' is excluded from the claimable set")

claimable: list[str] = []
for node in ast.walk(service_tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PENDING_STATUSES":
                claimable = [
                    element.value if isinstance(element, ast.Constant) else ast.unparse(element)
                    for element in getattr(node.value, "elts", [])
                ]

check("PENDING_STATUSES is defined", bool(claimable), "the claimable set is what bounds the claim")
check(
    "PENDING_STATUSES carries the queued status",
    "STATUS_QUEUED" in claimable,
    f"found {claimable}",
)
check(
    "PENDING_STATUSES carries the legacy 'pending' status",
    "pending" in claimable,
    "Phase 1 rows use 'pending'; dropping it strands every job created before the rename",
)
check(
    "PENDING_STATUSES excludes the running status",
    "STATUS_RUNNING" not in claimable and "running" not in claimable,
    "a claimable 'running' row is precisely the bug: a redelivery re-runs a job mid-flight",
)

# The old implementation gated on TERMINAL_STATUSES, which excludes 'running' —
# so its presence in the claim path is the signature of the regression.
check(
    "the claim path does not gate on TERMINAL_STATUSES",
    "TERMINAL_STATUSES" not in service_claim_source,
    "'running' is not terminal, so that test lets a redelivered in-flight job through",
)
check(
    "TERMINAL_STATUSES is still used elsewhere",
    "TERMINAL_STATUSES" in service_source,
    "it remains the right test for 'may this job still be updated'",
)


# ---------------------------------------------------------------------------

group("5 the claim is committed before the job runs")

check(
    "_claim_run commits",
    "commit" in calls_in(service_claim),
    "an uncommitted claim is invisible to the other worker, so both would run",
)

commit_index = service_claim_source.find("session.commit()")
claim_index = service_claim_source.find("claim_run(")
check(
    "the commit follows the claim",
    claim_index != -1 and commit_index > claim_index,
    "committing before the UPDATE is issued leaves the claim in the next transaction",
)


# ---------------------------------------------------------------------------

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} of {checks} checks FAILED")
    for label in failures:
        print(f"  - {label}")
    print("=" * 70)
    sys.exit(1)

print(f"All {checks} checks passed")
print("=" * 70)
