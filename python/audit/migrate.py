"""Audit schema migrations (interface design, Section 4.2; D-151).

    .venv\\Scripts\\python python\\audit\\migrate.py            apply pending migrations to schema audit
    .venv\\Scripts\\python python\\audit\\migrate.py --check    check only; apply nothing

The migrations in `migrations/` are templates with three names: the schema (`audit`, or a throwaway
`test_audit_<hex>` in tests), the app role and the reviewer role. Each name is checked against a
plain-identifier pattern before it is put into the SQL. Each pending file is applied in one
transaction with search_path set to the schema, and its version, name and sha256 (of the file as
committed) are recorded in `schema_migrations`. If an applied file's sha256 no longer matches its
record, the runner stops: an applied migration is never edited, and a change is a new numbered file.

After every migration, in the same transaction, the runner revokes EXECUTE on every function in the
schema from PUBLIC. PostgreSQL cannot revoke the global PUBLIC EXECUTE default per schema
(`ALTER DEFAULT PRIVILEGES ... IN SCHEMA` only adds), so this is what keeps a function added by a
later migration from starting out executable by PUBLIC (Build A correction to D-151).

The connection string and password are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from psycopg import sql  # noqa: E402

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
SCHEMA_LABEL = "audit-schema/1"
SCHEMA_VERSIONS = {SCHEMA_LABEL: (1, 2, 3)}  # the migrations that make up each schema label
DEFAULT_SCHEMA, DEFAULT_APP_ROLE, DEFAULT_REVIEW_ROLE = "audit", "taxy_app", "taxy_review"
TABLES = ("schema_migrations", "app_starts", "users", "queries", "passages", "claims", "gaps", "computations",
          "deliveries", "feedback", "admission_counts")
# The app role's ten functions (Section 4.6), as PostgreSQL prints their input argument types.
APP_FUNCTIONS = (
    "record_start(text, text, text, text, text, text, text, text, bigint, date, text, text, text[])",
    "open_query(uuid, bigint, text, text, text, text, numeric, jsonb)",
    "close_query(uuid, jsonb)",
    "add_delivery(uuid, text, text)",
    "add_feedback(uuid, text, integer, text)",
    "touch_user(text, text)",
    "user_history(text, integer, integer)",
    "erase_user(text)",
    "spend_since(timestamp with time zone)",
    "count_since(text, text, timestamp with time zone)",
)
# Owner-only: the two maintenance functions and the two helpers.
OWNER_ONLY_FUNCTIONS = ("purge_expired(integer)", "erase_query(uuid)", "wat_day_start(timestamp with time zone)",
                        "queries_guard()")
POLICY_NAME = "review_select"

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_RESERVED_SCHEMAS = ("public", "information_schema", "extensions")
_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_TOKEN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class MigrationError(RuntimeError):
    """A migration cannot be applied, or an applied one no longer matches its file."""


@dataclass(frozen=True)
class Names:
    schema: str = DEFAULT_SCHEMA
    app_role: str = DEFAULT_APP_ROLE
    review_role: str = DEFAULT_REVIEW_ROLE

    def __post_init__(self):
        for field_name in ("schema", "app_role", "review_role"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not _NAME.match(value):
                raise MigrationError(f"{field_name} must be a plain lower-case identifier")
        if self.schema in _RESERVED_SCHEMAS or self.schema.startswith("pg_"):
            raise MigrationError("the audit schema must be a schema of its own")
        if self.app_role == self.review_role:
            raise MigrationError("the app role and the reviewer role must differ")

    def tokens(self) -> dict[str, str]:
        return {"schema": self.schema, "app_role": self.app_role, "review_role": self.review_role}


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sha256: str
    text: str


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """The numbered files, in order. Versions must run 1, 2, 3, ... with no gap or duplicate."""
    out = []
    for path in sorted(directory.glob("*.sql")):
        m = _FILE.match(path.name)
        if not m:
            raise MigrationError(f"{path.name} is not named NNNN_name.sql")
        data = path.read_bytes()
        out.append(Migration(int(m.group(1)), m.group(2), path, hashlib.sha256(data).hexdigest(),
                             data.decode("utf-8")))
    versions = [m.version for m in out]
    if versions != list(range(1, len(out) + 1)):
        raise MigrationError(f"migration versions must run 1 to {len(out)} with no gap: {versions}")
    return out


def render(text: str, names: Names) -> str:
    """The template with its three names filled in; any other token is an error."""
    tokens = names.tokens()

    def fill(m: re.Match) -> str:
        if m.group(1) not in tokens:
            raise MigrationError(f"unknown template token {m.group(1)!r}")
        return tokens[m.group(1)]

    return _TOKEN.sub(fill, text)


def applied(conn, schema: str) -> dict[int, tuple[str, str]]:
    """{version: (name, sha256)} recorded in the schema, or {} before migration 1."""
    if conn.execute("SELECT to_regclass(%s)", (f"{schema}.schema_migrations",)).fetchone()[0] is None:
        return {}
    rows = conn.execute(sql.SQL("SELECT version, name, sha256 FROM {}.schema_migrations ORDER BY version")
                        .format(sql.Identifier(schema))).fetchall()
    return {version: (name, digest) for version, name, digest in rows}


def _check_applied(done: dict[int, tuple[str, str]], files: list[Migration]) -> None:
    by_version = {m.version: m for m in files}
    for version, (name, digest) in sorted(done.items()):
        m = by_version.get(version)
        if m is None:
            raise MigrationError(f"applied migration {version:04d}_{name} has no file")
        if m.sha256 != digest:
            raise MigrationError(f"migration {version:04d}_{name} was edited after it was applied (sha256 differs); "
                                 "an applied migration is never edited: add a new numbered file")


def migrate(conn, names: Names = Names(), directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply every pending migration, each in its own transaction. Returns the versions applied
    (empty when the schema is up to date). Raises MigrationError before applying anything if an
    applied file was edited."""
    files = migration_files(directory)
    lock = f"taxy-audit-migrate:{names.schema}"
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (lock,))
    try:
        done = applied(conn, names.schema)
        _check_applied(done, files)
        ran = []
        for m in files:
            if m.version in done:
                continue
            schema = sql.Identifier(names.schema)
            with conn.transaction():
                conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
                conn.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp").format(schema))
                conn.execute(render(m.text, names))
                conn.execute(sql.SQL("INSERT INTO {}.schema_migrations (version, name, sha256) VALUES (%s, %s, %s)")
                             .format(schema), (m.version, m.name, m.sha256))
                conn.execute(sql.SQL("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA {} FROM PUBLIC").format(schema))
            ran.append(m.version)
        return ran
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock,))


def function_privileges(conn, schema: str, roles: tuple[str, ...]) -> list[tuple[str, bool, dict[str, bool]]]:
    """(signature, security definer with pinned search_path, {role: may execute}) for every
    function in the schema. `roles` may include 'public'."""
    rows = conn.execute(
        "SELECT p.oid, p.proname || '(' || oidvectortypes(p.proargtypes) || ')', p.prosecdef, p.proconfig "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %s ORDER BY 2",
        (schema,)).fetchall()
    pinned = f"search_path={schema}, pg_catalog, pg_temp"
    out = []
    for oid, signature, secdef, config in rows:
        may = {role: conn.execute("SELECT has_function_privilege(%s, %s::oid, 'EXECUTE')", (role, oid)).fetchone()[0]
               for role in roles}
        out.append((signature, bool(secdef and config and pinned in config), may))
    return out


def check_schema(conn, names: Names = Names(), label: str = SCHEMA_LABEL,
                 directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Problem names (no values) for the start-up check (Section 3.8, item 5); [] when the schema is
    at `label`, every recorded sha256 matches its file, and the privileges are as designed."""
    try:
        files = {m.version: m for m in migration_files(directory)}
    except MigrationError:
        return ["audit_migration_files"]
    done = applied(conn, names.schema)
    if not done:
        return ["audit_schema_missing"]
    problems = []
    if tuple(sorted(done)) != SCHEMA_VERSIONS[label]:
        problems.append("audit_schema_version")
    if any(v not in files or files[v].sha256 != digest for v, (_, digest) in done.items()):
        problems.append("audit_migration_sha256")
    funcs = function_privileges(conn, names.schema, ("public", names.app_role, names.review_role))
    if any(may["public"] for _, _, may in funcs):
        problems.append("audit_public_execute")
    if sorted(sig for sig, _, may in funcs if may[names.app_role]) != sorted(APP_FUNCTIONS):
        problems.append("audit_app_grants")
    if any(may[names.review_role] for _, _, may in funcs):
        problems.append("audit_review_execute")
    if not all(pinned for _, pinned, _ in funcs):
        problems.append("audit_function_definer")
    tables = conn.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'",
        (names.schema,)).fetchall()
    if sorted(t for t, _, _ in tables) != sorted(TABLES) or not all(rls and not forced for _, rls, forced in tables):
        problems.append("audit_rls")
    policies = conn.execute("SELECT tablename, policyname, cmd, roles::text[], permissive FROM pg_policies "
                            "WHERE schemaname = %s", (names.schema,)).fetchall()
    expected = sorted((t, POLICY_NAME, "SELECT", [names.review_role], "PERMISSIVE") for t in TABLES)
    if sorted((t, p, c, list(r), perm) for t, p, c, r, perm in policies) != expected:
        problems.append("audit_policies")
    for table in TABLES:
        qualified = f"{names.schema}.{table}"
        if conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')",
                        (names.app_role, qualified)).fetchone()[0]:
            problems.append("audit_app_table_privileges")
            break
    return problems


def main(argv: list[str] | None = None) -> int:
    from check_pgvector import connect

    parser = argparse.ArgumentParser(description="Apply or check the audit schema migrations.")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--app-role", default=DEFAULT_APP_ROLE)
    parser.add_argument("--review-role", default=DEFAULT_REVIEW_ROLE)
    parser.add_argument("--check", action="store_true", help="check only; apply nothing")
    args = parser.parse_args(argv)
    names = Names(args.schema, args.app_role, args.review_role)
    with connect() as conn:
        conn.autocommit = True
        if not args.check:
            ran = migrate(conn, names)
            print(f"applied: {ran if ran else 'none (up to date)'}")
        problems = check_schema(conn, names)
        print(f"schema {names.schema}: {SCHEMA_LABEL if not problems else 'problems'}; "
              f"versions {sorted(applied(conn, names.schema))}")
        for problem in problems:
            print(f"  problem: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
