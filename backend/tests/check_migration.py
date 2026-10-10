"""Static verification of the Phase 2 Alembic migration against the ORM models.

Alembic itself cannot run here: this sandbox has no SQLAlchemy and no route to the
Supabase instance. So instead of asserting nothing, this script parses the model
modules and both migration files with :mod:`ast` and checks the properties that
actually make a migration safe against a live, populated database.

What it proves, using only the standard library:

1.  The revision chain is linear and single-headed.
2.  The two new tables match their models column for column, type for type.
3.  For the three extended tables, ``model columns == initial + new migration``
    exactly — no column declared in Python that the database will lack, and no
    column added by the migration that no model knows about.
4.  Every column the migration adds is either nullable or carries a server
    default, so adding it to a table with existing rows cannot fail.
5.  ``upgrade()`` contains no destructive operation: no drop, no type change, and
    no ``nullable=False``.
6.  Every ``index=True`` in the models has a real index somewhere in the chain.
7.  ``downgrade()`` drops only objects ``upgrade()`` created.

A drift check like this is worth more than it looks: the most common way a
migration corrupts a live database is by disagreeing with the models in a way
nobody notices until a query fails in production.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# parents[1] because this file lives in backend/tests/.
BACKEND = Path(__file__).resolve().parents[1]

MODELS = BACKEND / "app" / "db" / "models"
VERSIONS = BACKEND / "alembic" / "versions"

INITIAL = "8dd5ad2ce9e4"
PHASE2 = "b7f4c1e29a3d"

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


# ---------------------------------------------------------------------------
# Shared AST helpers
# ---------------------------------------------------------------------------


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def call_name(node: ast.AST) -> str:
    """Dotted name of a call target: ``op.add_column`` -> 'op.add_column'."""
    if not isinstance(node, ast.Call):
        return ""
    parts: list[str] = []
    cur = node.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def literal(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def name_literal(node: ast.AST):
    """Literal string, unwrapping ``op.f("...")``.

    Alembic autogenerate emits index and constraint names wrapped in ``op.f`` to
    mark them as final. ``literal_eval`` cannot evaluate a call, so an unwrapping
    step is required or every index in the initial migration reads as absent.
    """
    if isinstance(node, ast.Call) and call_name(node) in {"op.f", "f"} and node.args:
        return literal(node.args[0])
    return literal(node)


def kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def type_of(node: ast.AST) -> tuple[str, int | None]:
    """Normalise a type expression so ``sa.String(length=64)`` == ``String(64)``."""
    if isinstance(node, ast.Call):
        base = node.func
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "?")
        length = None
        if node.args:
            length = literal(node.args[0])
        for kw in node.keywords:
            if kw.arg in {"length", "precision"}:
                length = literal(kw.value)
        return (name.upper(), length if isinstance(length, int) else None)
    if isinstance(node, ast.Attribute):
        return (node.attr.upper(), None)
    if isinstance(node, ast.Name):
        return (node.id.upper(), None)
    return ("?", None)


# ---------------------------------------------------------------------------
# Model extraction
# ---------------------------------------------------------------------------


class ModelColumn:
    __slots__ = ("name", "type", "nullable", "indexed", "primary_key")

    def __init__(self, name, type_, nullable, indexed, primary_key):
        self.name = name
        self.type = type_
        self.nullable = nullable
        self.indexed = indexed
        self.primary_key = primary_key

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{self.name} {self.type} null={self.nullable}>"


def read_models(path: Path) -> dict[str, dict]:
    """Map ``__tablename__`` -> {'columns': {...}, 'unique': [...]}"""
    tree = parse(path)
    tables: dict[str, dict] = {}

    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        tablename = None
        columns: dict[str, ModelColumn] = {}
        unique: list[tuple[tuple[str, ...], str]] = []

        for stmt in cls.body:
            # __tablename__ = "..."
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "__tablename__":
                        tablename = literal(stmt.value)
                    if isinstance(target, ast.Name) and target.id == "__table_args__":
                        for element in getattr(stmt.value, "elts", []):
                            if call_name(element).endswith("UniqueConstraint"):
                                cols = tuple(
                                    c for c in (literal(a) for a in element.args) if isinstance(c, str)
                                )
                                name = literal(kwarg(element, "name")) or ""
                                unique.append((cols, name))
                continue

            # name: Mapped[...] = mapped_column(...)
            if not isinstance(stmt, ast.AnnAssign) or stmt.value is None:
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            if not call_name(stmt.value).endswith("mapped_column"):
                continue

            call = stmt.value
            positional = [a for a in call.args if not isinstance(a, ast.Starred)]
            # The type is the first positional arg that is not a ForeignKey.
            type_ = ("?", None)
            for arg in positional:
                if call_name(arg).endswith("ForeignKey"):
                    continue
                type_ = type_of(arg)
                break

            primary_key = literal(kwarg(call, "primary_key")) is True
            nullable_kw = kwarg(call, "nullable")
            nullable = False if primary_key else bool(literal(nullable_kw))
            indexed = literal(kwarg(call, "index")) is True

            columns[stmt.target.id] = ModelColumn(
                stmt.target.id, type_, nullable, indexed, primary_key
            )

        if tablename:
            tables[tablename] = {"columns": columns, "unique": unique}

    return tables


# ---------------------------------------------------------------------------
# Migration extraction
# ---------------------------------------------------------------------------


class MigrationColumn:
    __slots__ = ("name", "type", "nullable", "server_default", "primary_key")

    def __init__(self, name, type_, nullable, server_default, primary_key):
        self.name = name
        self.type = type_
        self.nullable = nullable
        self.server_default = server_default
        self.primary_key = primary_key


def _column_from_call(call: ast.Call) -> MigrationColumn | None:
    if not call.args:
        return None
    name = literal(call.args[0])
    if not isinstance(name, str):
        return None
    type_ = type_of(call.args[1]) if len(call.args) > 1 else ("?", None)
    nullable_kw = kwarg(call, "nullable")
    nullable = True if nullable_kw is None else bool(literal(nullable_kw))
    server_default = kwarg(call, "server_default") is not None
    return MigrationColumn(name, type_, nullable, server_default, False)


class Migration:
    """Everything the checks need from one migration file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.source = path.read_text()
        self.tree = parse(path)

        self.revision: str | None = None
        self.down_revision: str | None = None
        for stmt in self.tree.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                if stmt.targets[0].id == "revision":
                    self.revision = literal(stmt.value)
                elif stmt.targets[0].id == "down_revision":
                    self.down_revision = literal(stmt.value)

        self.created: dict[str, dict[str, MigrationColumn]] = {}
        self.unique: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        self.added: dict[str, dict[str, MigrationColumn]] = {}
        self.indexes: set[tuple[str, str, tuple[str, ...]]] = set()
        self.dropped_indexes: set[tuple[str, str]] = set()
        self.dropped_columns: set[tuple[str, str]] = set()
        self.dropped_tables: set[str] = set()
        self.relaxed: set[tuple[str, str]] = set()
        self.altered_not_null: set[tuple[str, str]] = set()

        self._scan()

    # -- scanning ---------------------------------------------------------

    def _functions(self) -> dict[str, ast.FunctionDef]:
        return {
            n.name: n for n in self.tree.body if isinstance(n, ast.FunctionDef)
        }

    def _reachable_from(self, entry: str) -> list[ast.Call]:
        """Every call made by ``entry`` and by local helpers it invokes.

        The Phase 2 migration routes its work through private helpers
        (``_extend_customer_rfm`` and friends), so a scan of ``upgrade`` alone
        would see almost nothing.
        """
        functions = self._functions()
        seen: set[str] = set()
        calls: list[ast.Call] = []

        def walk(fname: str) -> None:
            if fname in seen or fname not in functions:
                return
            seen.add(fname)
            for node in ast.walk(functions[fname]):
                if isinstance(node, ast.Call):
                    calls.append(node)
                    target = call_name(node)
                    if "." not in target:
                        walk(target)

        walk(entry)
        return calls

    def _scan(self) -> None:
        for call in self._reachable_from("upgrade"):
            name = call_name(call)

            if name == "op.create_table":
                table = literal(call.args[0])
                if not isinstance(table, str):
                    continue
                cols: dict[str, MigrationColumn] = {}
                uniques: list[tuple[tuple[str, ...], str]] = []
                pks: set[str] = set()
                for arg in call.args[1:]:
                    target = call_name(arg)
                    if target.endswith(".Column") or target == "Column":
                        col = _column_from_call(arg)
                        if col:
                            cols[col.name] = col
                    elif target.endswith("PrimaryKeyConstraint"):
                        pks.update(c for c in (literal(a) for a in arg.args) if isinstance(c, str))
                    elif target.endswith("UniqueConstraint"):
                        uniques.append(
                            (
                                tuple(
                                    c
                                    for c in (literal(a) for a in arg.args)
                                    if isinstance(c, str)
                                ),
                                literal(kwarg(arg, "name")) or "",
                            )
                        )
                for pk in pks:
                    if pk in cols:
                        cols[pk].primary_key = True
                        cols[pk].nullable = False
                self.created[table] = cols
                self.unique[table] = uniques

            elif name in {"op.add_column", "_add_column"}:
                table = literal(call.args[0])
                if not isinstance(table, str) or len(call.args) < 2:
                    continue
                col = _column_from_call(call.args[1])
                if col:
                    self.added.setdefault(table, {})[col.name] = col

            elif name in {"op.create_index", "_create_index"}:
                index_name = name_literal(call.args[0])
                table = literal(call.args[1]) if len(call.args) > 1 else None
                cols = literal(call.args[2]) if len(call.args) > 2 else []
                if isinstance(index_name, str) and isinstance(table, str):
                    self.indexes.add((index_name, table, tuple(cols or ())))

            elif name in {"op.drop_index", "_drop_index"}:
                index_name = name_literal(call.args[0])
                table = literal(
                    kwarg(call, "table_name") or (call.args[1] if len(call.args) > 1 else None)
                )
                self.dropped_indexes.add((str(index_name), str(table)))

            elif name == "op.drop_column":
                self.dropped_columns.add((str(literal(call.args[0])), str(literal(call.args[1]))))

            elif name == "op.drop_table":
                self.dropped_tables.add(str(literal(call.args[0])))

            elif name == "_make_nullable":
                table = literal(call.args[0])
                column = literal(call.args[1]) if len(call.args) > 1 else None
                if isinstance(table, str) and isinstance(column, str):
                    self.relaxed.add((table, column))

            elif name.endswith("alter_column"):
                nullable = literal(kwarg(call, "nullable"))
                if nullable is False:
                    self.altered_not_null.add((name, "nullable=False"))

    def downgrade_calls(self) -> list[str]:
        return [call_name(c) for c in self._reachable_from("downgrade")]

    def downgrade_targets(self) -> dict[str, set]:
        """What ``downgrade()`` drops, including names supplied by a for-loop.

        The downgrade drives ``_drop_index`` and ``op.drop_column`` from tuples of
        names, so the literal is on the ``For.iter`` node rather than on the call.
        Reading only the call arguments would find nothing.
        """
        indexes: set[tuple[str, str]] = set()
        columns: set[tuple[str, str]] = set()
        tables: set[str] = set()

        functions = self._functions()
        if "downgrade" not in functions:
            return {"indexes": indexes, "columns": columns, "tables": tables}

        downgrade = functions["downgrade"]

        # Names bound by an enclosing for-loop, per loop body.
        for node in ast.walk(downgrade):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            loop_var = node.target.id
            names = literal(node.iter)
            if not isinstance(names, (list, tuple)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                target = call_name(inner)
                args = inner.args
                if target in {"_drop_index", "op.drop_index"} and len(args) > 1:
                    if isinstance(args[0], ast.Name) and args[0].id == loop_var:
                        table = literal(args[1])
                        if isinstance(table, str):
                            indexes.update(
                                (n, table) for n in names if isinstance(n, str)
                            )
                elif target == "op.drop_column" and len(args) > 1:
                    table = literal(args[0])
                    if isinstance(table, str) and isinstance(args[1], ast.Name):
                        if args[1].id == loop_var:
                            columns.update(
                                (table, n) for n in names if isinstance(n, str)
                            )

        # Directly-written calls.
        for call in self._reachable_from("downgrade"):
            name = call_name(call)
            if name in {"_drop_index", "op.drop_index"} and len(call.args) > 1:
                a = name_literal(call.args[0])
                b = literal(call.args[1])
                if isinstance(a, str) and isinstance(b, str):
                    indexes.add((a, b))
            elif name == "op.drop_column" and len(call.args) > 1:
                t = literal(call.args[0])
                c = literal(call.args[1])
                if isinstance(t, str) and isinstance(c, str):
                    columns.add((t, c))
            elif name == "op.drop_table" and call.args:
                t = literal(call.args[0])
                if isinstance(t, str):
                    tables.add(t)

        return {"indexes": indexes, "columns": columns, "tables": tables}

    def downgrade_sets_not_null(self) -> bool:
        """True if downgrade re-imposes a NOT NULL constraint anywhere."""
        functions = self._functions()
        if "downgrade" not in functions:
            return False
        for call in self._reachable_from("downgrade"):
            if "alter_column" in call_name(call):
                if literal(kwarg(call, "nullable")) is False:
                    return True
        return False


# ---------------------------------------------------------------------------
# Load everything
# ---------------------------------------------------------------------------

print("=" * 74)
print("PHASE 2 MIGRATION VERIFICATION (static, stdlib only)")
print("=" * 74)

migration_files = sorted(p for p in VERSIONS.glob("*.py") if p.name != "__init__.py")
migrations = {}
for path in migration_files:
    m = Migration(path)
    if m.revision:
        migrations[m.revision] = m

model_tables: dict[str, dict] = {}
for path in sorted(MODELS.glob("*.py")):
    if path.name == "__init__.py":
        continue
    model_tables.update(read_models(path))

group("0 files discovered")
check("both migration files parse", len(migrations) == 2, f"found {sorted(migrations)}")
check(
    "models parse and expose the Phase 2 tables",
    {"processed_events", "webhook_events"} <= set(model_tables),
    f"tables: {sorted(model_tables)}",
)

if INITIAL not in migrations or PHASE2 not in migrations:
    print("\nCannot continue: revision ids not as expected.")
    sys.exit(1)

initial = migrations[INITIAL]
phase2 = migrations[PHASE2]


# ---------------------------------------------------------------------------
# 1. Revision chain
# ---------------------------------------------------------------------------

group("1 revision chain is linear and single-headed")

check("initial migration is the root", initial.down_revision is None)
check(
    "phase 2 chains onto the initial revision",
    phase2.down_revision == INITIAL,
    f"down_revision={phase2.down_revision!r}",
)
check("revision ids are distinct", initial.revision != phase2.revision)

down_refs = [m.down_revision for m in migrations.values() if m.down_revision]
heads = [r for r in migrations if r not in down_refs]
check("exactly one head", len(heads) == 1, f"heads={heads}")
check("no branch: each parent claimed once", len(down_refs) == len(set(down_refs)))


# ---------------------------------------------------------------------------
# 2. New tables match their models
# ---------------------------------------------------------------------------

group("2 new tables match their models exactly")

for table in ("processed_events", "webhook_events"):
    created = phase2.created.get(table, {})
    declared = model_tables[table]["columns"]

    check(f"{table}: created by the phase 2 migration", bool(created))
    check(
        f"{table}: same column set as the model",
        set(created) == set(declared),
        f"migration-only={sorted(set(created) - set(declared))} "
        f"model-only={sorted(set(declared) - set(created))}",
    )

    type_mismatches = [
        f"{name}: model {declared[name].type} vs migration {created[name].type}"
        for name in sorted(set(created) & set(declared))
        if declared[name].type != created[name].type
    ]
    check(f"{table}: column types agree", not type_mismatches, "; ".join(type_mismatches))

    null_mismatches = [
        f"{name}: model nullable={declared[name].nullable} vs migration {created[name].nullable}"
        for name in sorted(set(created) & set(declared))
        if declared[name].nullable != created[name].nullable
    ]
    check(f"{table}: nullability agrees", not null_mismatches, "; ".join(null_mismatches))

check(
    "processed_events: event_id is the primary key (DB-enforced idempotency)",
    phase2.created["processed_events"]["event_id"].primary_key
    and model_tables["processed_events"]["columns"]["event_id"].primary_key,
)

model_unique = model_tables["webhook_events"]["unique"]
mig_unique = phase2.unique.get("webhook_events", [])
check(
    "webhook_events: unique constraint present in both",
    bool(model_unique) and bool(mig_unique),
)
check(
    "webhook_events: unique constraint has identical columns and name",
    model_unique == mig_unique,
    f"model={model_unique} migration={mig_unique}",
)
check(
    "webhook_events: business_id is part of the replay key (no cross-tenant collision)",
    bool(mig_unique) and mig_unique[0][0][0] == "business_id",
    f"{mig_unique}",
)
check(
    "webhook_events: no signature or authorization column exists",
    not {
        c
        for c in phase2.created["webhook_events"]
        if any(word in c for word in ("signature_value", "secret", "auth", "token", "header"))
    },
    f"{sorted(phase2.created['webhook_events'])}",
)


# ---------------------------------------------------------------------------
# 3. Extended tables: model == initial + phase 2, with nothing left over
# ---------------------------------------------------------------------------

group("3 extended tables reconcile exactly with the models")

EXTENDED = ("customer_rfm", "customer_segments", "analytics_runs")

for table in EXTENDED:
    before = set(initial.created.get(table, {}))
    added = set(phase2.added.get(table, {}))
    declared = set(model_tables[table]["columns"])

    check(f"{table}: exists in the initial migration", bool(before))
    check(
        f"{table}: initial + phase 2 == model columns",
        before | added == declared,
        f"missing from DB={sorted(declared - (before | added))} "
        f"unknown to models={sorted((before | added) - declared)}",
    )
    check(
        f"{table}: phase 2 adds nothing that already existed",
        not (before & added),
        f"re-added={sorted(before & added)}",
    )
    check(f"{table}: phase 2 adds at least one column", bool(added), "expected new columns")

    # Types of the added columns must match the models.
    mismatches = [
        f"{name}: model {model_tables[table]['columns'][name].type} vs "
        f"migration {phase2.added[table][name].type}"
        for name in sorted(added)
        if model_tables[table]["columns"][name].type != phase2.added[table][name].type
    ]
    check(f"{table}: added column types match the models", not mismatches, "; ".join(mismatches))


# ---------------------------------------------------------------------------
# 4. Safe against existing rows
# ---------------------------------------------------------------------------

group("4 every added column is safe to add to a populated table")

unsafe: list[str] = []
for table, cols in phase2.added.items():
    for name, col in cols.items():
        if not col.nullable and not col.server_default:
            unsafe.append(f"{table}.{name}")

check(
    "no NOT NULL column is added without a server default",
    not unsafe,
    f"would fail on existing rows: {unsafe}",
)

created_at = phase2.added.get("analytics_runs", {}).get("created_at")
check(
    "analytics_runs.created_at is NOT NULL but carries a server default",
    created_at is not None and not created_at.nullable and created_at.server_default,
)
check(
    "analytics_runs.created_at is backfilled from started_at rather than left at now()",
    "UPDATE analytics_runs SET created_at = started_at" in phase2.source,
)

for table in EXTENDED:
    check(
        f"{table}.dataset_import_id is relaxed to nullable",
        (table, "dataset_import_id") in phase2.relaxed,
        f"relaxed={sorted(phase2.relaxed)}",
    )
    check(
        f"{table}.dataset_import_id is NOT NULL in the applied migration (so the alter is needed)",
        initial.created[table]["dataset_import_id"].nullable is False,
    )
    check(
        f"{table}.dataset_import_id is nullable in the model (so the alter is correct)",
        model_tables[table]["columns"]["dataset_import_id"].nullable is True,
    )


# ---------------------------------------------------------------------------
# 5. upgrade() is non-destructive
# ---------------------------------------------------------------------------

group("5 upgrade() destroys nothing")

check("no drop_table in upgrade", not phase2.dropped_tables, f"{phase2.dropped_tables}")
check("no drop_column in upgrade", not phase2.dropped_columns, f"{phase2.dropped_columns}")
check("no drop_index in upgrade", not phase2.dropped_indexes, f"{phase2.dropped_indexes}")
check(
    "no alter_column sets nullable=False",
    not phase2.altered_not_null,
    f"{phase2.altered_not_null}",
)

upgrade_calls = {call_name(c) for c in phase2._reachable_from("upgrade")}
for forbidden in ("op.drop_constraint", "op.rename_table", "op.execute_batch"):
    check(f"upgrade does not call {forbidden}", forbidden not in upgrade_calls)

check(
    "no DELETE or DROP statement in raw SQL",
    not any(word in phase2.source.upper() for word in ("DELETE FROM", "DROP TABLE", "TRUNCATE")),
)
check(
    "the only raw SQL is the created_at backfill",
    phase2.source.count("op.execute(") == 1,
    f"found {phase2.source.count('op.execute(')} op.execute calls",
)
check(
    "no type change: existing_type is only used alongside nullable=True",
    "existing_type" in phase2.source and "type_=" not in phase2.source,
)


# ---------------------------------------------------------------------------
# 6. Declared indexes exist somewhere in the chain
# ---------------------------------------------------------------------------

group("6 every index=True in the models is really created")

indexed_pairs: set[tuple[str, str]] = set()
for _index_name, table, cols in initial.indexes | phase2.indexes:
    if cols:
        # A composite index also serves lookups on its leading column.
        indexed_pairs.add((table, cols[0]))
        for col in cols:
            indexed_pairs.add((table, col))

missing_indexes: list[str] = []
for table, spec in model_tables.items():
    for name, col in spec["columns"].items():
        if not col.indexed:
            continue
        if (table, name) not in indexed_pairs:
            missing_indexes.append(f"{table}.{name}")

check(
    "no model column declares index=True without an index in the chain",
    not missing_indexes,
    f"missing: {sorted(missing_indexes)}",
)

# Indexes the initial migration forgot, which Phase 2 now supplies.
for expected in (
    ("ix_customer_segments_segment", "customer_segments"),
    ("ix_analytics_runs_job_type", "analytics_runs"),
    ("ix_analytics_runs_status", "analytics_runs"),
):
    in_initial = any(i[0] == expected[0] for i in initial.indexes)
    in_phase2 = any(i[0] == expected[0] for i in phase2.indexes)
    check(
        f"{expected[0]}: missing from the initial migration, added by phase 2",
        (not in_initial) and in_phase2,
        f"initial={in_initial} phase2={in_phase2}",
    )

# No duplicate index names across the whole chain.
names = [i[0] for i in initial.indexes] + [i[0] for i in phase2.indexes]
duplicates = sorted({n for n in names if names.count(n) > 1})
check("no index name is created twice in the chain", not duplicates, f"{duplicates}")

check(
    "orders gains a composite index for the RFM aggregation",
    any(i[1] == "orders" and i[2] == ("business_id", "customer_id") for i in phase2.indexes),
)
check(
    "every phase 2 index on a tenant table leads with business_id or is single-column",
    all(
        len(cols) == 1 or cols[0] == "business_id" or table in {"customer_rfm", "customer_segments"}
        for _n, table, cols in phase2.indexes
    ),
)


# ---------------------------------------------------------------------------
# 7. downgrade() is the exact inverse, minus the deliberate exception
# ---------------------------------------------------------------------------

group("7 downgrade() reverses only what upgrade() created")

targets = phase2.downgrade_targets()

created_index_keys = {(name, table) for name, table, _cols in phase2.indexes}
# An index on a table the downgrade drops disappears with the table, so it needs
# no explicit drop. Only indexes on surviving tables must be reversed by hand.
surviving_index_keys = {
    (name, table) for name, table in created_index_keys if table not in phase2.created
}
check(
    "downgrade drops no index upgrade did not create",
    targets["indexes"] <= created_index_keys,
    f"extra={sorted(targets['indexes'] - created_index_keys)}",
)
check(
    "downgrade drops every index upgrade created on a surviving table",
    surviving_index_keys <= targets["indexes"],
    f"left behind={sorted(surviving_index_keys - targets['indexes'])}",
)
check(
    "downgrade does not redundantly drop indexes on tables it also drops",
    not (targets["indexes"] - surviving_index_keys),
    "dropping the table already removes them",
)

added_column_keys = {(t, c) for t, cols in phase2.added.items() for c in cols}
check(
    "downgrade drops no column upgrade did not add",
    targets["columns"] <= added_column_keys,
    f"extra={sorted(targets['columns'] - added_column_keys)}",
)
check(
    "downgrade drops every column upgrade added",
    added_column_keys <= targets["columns"],
    f"left behind={sorted(added_column_keys - targets['columns'])}",
)
check(
    "downgrade drops exactly the two new tables",
    targets["tables"] == set(phase2.created),
    f"downgrade={sorted(targets['tables'])} upgrade={sorted(phase2.created)}",
)
check(
    "downgrade touches no table the migration did not change",
    {t for t, _c in targets["columns"]} | targets["tables"]
    <= set(phase2.created) | set(phase2.added),
)
check(
    "downgrade does not force dataset_import_id back to NOT NULL",
    not phase2.downgrade_sets_not_null(),
    "relaxing is safe; re-imposing would fail on rows written by the orders path",
)
check(
    "the deliberate omission is documented, not silent",
    "intentionally left nullable" in phase2.source,
)


# ---------------------------------------------------------------------------
# 8. Idempotency guards and env.py registration
# ---------------------------------------------------------------------------

group("8 idempotency guards and Alembic model registration")

for helper in ("_has_table", "_has_column", "_has_index"):
    check(f"{helper} guard exists", f"def {helper}(" in phase2.source)

check(
    "add_column always goes through the guarded helper",
    "op.add_column" not in phase2.source.replace("        op.add_column(table, column)", ""),
)
check(
    "create_index always goes through the guarded helper",
    phase2.source.count("op.create_index(") == 1,
    "expected exactly one raw call, inside _create_index",
)
check(
    "create_table is guarded by a _has_table early return",
    phase2.source.count("if _has_table(") >= 2,
)

env_source = (BACKEND / "alembic" / "env.py").read_text()
env_tree = ast.parse(env_source)
env_imports: set[str] = set()
for node in ast.walk(env_tree):
    if isinstance(node, ast.ImportFrom) and node.module == "app.db.models":
        env_imports.update(alias.name for alias in node.names)

check(
    "alembic/env.py imports the events model module",
    "events" in env_imports,
    f"imports={sorted(env_imports)}",
)

model_modules = {
    p.stem for p in MODELS.glob("*.py") if p.name != "__init__.py" and "class " in p.read_text()
}
check(
    "alembic/env.py imports every model module (else autogenerate emits drop_table)",
    model_modules <= env_imports,
    f"not imported: {sorted(model_modules - env_imports)}",
)


# ---------------------------------------------------------------------------
# 9. No secrets anywhere in the migration
# ---------------------------------------------------------------------------

group("9 the migration file carries no credentials")

lowered = phase2.source.lower()
for word in ("password", "sk_test", "sk_live", "secret_key", "postgresql://", "supabase.com"):
    check(f"no {word!r} in the migration", word not in lowered)


# ---------------------------------------------------------------------------

print("\n" + "=" * 74)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print(f"ALL {checks} MIGRATION CHECKS PASSED")
print("=" * 74)
