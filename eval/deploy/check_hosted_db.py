r"""Read-only check of the hosted database's privileges (deployment design Section 2.6, step B2; free).

    .venv\Scripts\python eval\deploy\with_hosted.py owner eval\deploy\check_hosted_db.py

Run as the owner. It reads only catalogs: it changes nothing, reads no audit or corpus row, and prints no
connection string, password or row text. Findings name a role and an object, never a value. Exit code 0: no
finding. 1: at least one finding (any finding fails the step).

What it must find:

1. `anon`, `authenticated`, `service_role` and `authenticator` hold **no** privilege on schema `audit`, on any
   audit table, sequence or function, or on the four corpus tables (and their sequences).
2. Row-level security is on for all 15 tables: the 11 audit tables and the 4 corpus tables.
3. The app login (`taxy_app_login`) can execute exactly the eleven audit functions (D-210 adds schema_versions), can SELECT the four corpus
   tables, and holds nothing else on any table, sequence or function of `audit` or `public`.
4. The review login (`taxy_review_login`) holds SELECT only (on the 11 audit tables and the 4 corpus tables),
   no EXECUTE on any audit function, and nothing else.
5. Both logins are plain login roles (not superuser, no BYPASSRLS, no CREATEDB or CREATEROLE), members of their
   group role, with their connection limits (app 20, review 3), and their `search_path` is `public, extensions`.
6. There is no ANN index (`hnsw` or `ivfflat`) on `chunk_embeddings` (exact search only, Section 2.3).

It also reports `extversion` of `vector`, `version()` and each login's `search_path` setting.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from audit.grant_review import CORPUS_TABLES  # noqa: E402
from audit.migrate import APP_FUNCTIONS, TABLES  # noqa: E402

TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
COLUMN_PRIVILEGES = "SELECT, INSERT, UPDATE, REFERENCES"
SEQUENCE_PRIVILEGES = "USAGE, SELECT, UPDATE"
RELATION_KINDS = ("r", "v", "m", "p", "f", "S")
ANN_METHODS = ("hnsw", "ivfflat")
SEARCH_PATH = "public, extensions"


@dataclass(frozen=True)
class Config:
    schema: str = "audit"
    app_login: str = "taxy_app_login"
    app_group: str = "taxy_app"
    review_login: str = "taxy_review_login"
    review_group: str = "taxy_review"
    api_roles: tuple[str, ...] = ("anon", "authenticated", "service_role", "authenticator")
    corpus_tables: tuple[str, ...] = CORPUS_TABLES
    audit_tables: tuple[str, ...] = TABLES
    app_functions: tuple[str, ...] = APP_FUNCTIONS
    app_connection_limit: int = 20
    review_connection_limit: int = 3


def _relations(conn, schemas: tuple[str, ...]) -> list[tuple[str, str, str, str]]:
    """(schema, name, kind, quoted qualified name) of every relation in the schemas."""
    return conn.execute(
        "SELECT n.nspname, c.relname, c.relkind, format('%%I.%%I', n.nspname, c.relname) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) AND c.relkind = ANY(%s) "
        "ORDER BY 1, 2", (list(schemas), list(RELATION_KINDS))).fetchall()


def _functions(conn, schema: str) -> list[tuple[str, int]]:
    return conn.execute(
        "SELECT p.proname || '(' || oidvectortypes(p.proargtypes) || ')', p.oid::int FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %s ORDER BY 1", (schema,)).fetchall()


def _corpus_sequences(conn, tables: tuple[str, ...]) -> set[str]:
    rows = conn.execute(
        "SELECT n.nspname || '.' || s.relname FROM pg_depend d JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S' "
        "JOIN pg_class t ON t.oid = d.refobjid JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i') "
        "AND n.nspname = 'public' AND t.relname = ANY(%s)", (list(tables),)).fetchall()
    return {r[0] for r in rows}


def _role(conn, name: str):
    return conn.execute(
        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolconnlimit "
        "FROM pg_roles WHERE rolname = %s", (name,)).fetchone()


def held(conn, role: str, schema_names: tuple[str, ...], functions: list[tuple[str, int]], audit_schema: str,
         ) -> dict[str, list[str]]:
    """Every privilege `role` holds, by kind: schema, relation (table or view), column, sequence, function.
    Inherited privileges count (the has_*_privilege functions follow membership and PUBLIC)."""
    out: dict[str, list[str]] = {"schema": [], "relation": [], "column": [], "sequence": [], "function": []}
    for schema in schema_names:
        for priv in ("USAGE", "CREATE"):
            if conn.execute("SELECT has_schema_privilege(%s, %s, %s)", (role, schema, priv)).fetchone()[0]:
                out["schema"].append(f"{schema}:{priv}")
    for schema, name, kind, qualified in _relations(conn, schema_names):
        if kind == "S":
            if conn.execute("SELECT has_sequence_privilege(%s, %s, %s)",
                            (role, qualified, SEQUENCE_PRIVILEGES)).fetchone()[0]:
                out["sequence"].append(f"{schema}.{name}")
            continue
        for priv in TABLE_PRIVILEGES.split(", "):
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, qualified, priv)).fetchone()[0]:
                out["relation"].append(f"{schema}.{name}:{priv}")
        if conn.execute("SELECT has_any_column_privilege(%s, %s, %s)",
                        (role, qualified, COLUMN_PRIVILEGES)).fetchone()[0]:
            out["column"].append(f"{schema}.{name}")
    for signature, oid in functions:
        if conn.execute("SELECT has_function_privilege(%s, %s::oid, 'EXECUTE')", (role, oid)).fetchone()[0]:
            out["function"].append(f"{audit_schema}.{signature}")
    return out


def check_api_roles(conn, cfg: Config, functions) -> list[str]:
    findings = []
    audit_names = {f"{s}.{n}" for s, n, _, _ in _relations(conn, (cfg.schema,))}
    watched = audit_names | {f"public.{t}" for t in cfg.corpus_tables} | _corpus_sequences(conn, cfg.corpus_tables)
    for role in cfg.api_roles:
        if _role(conn, role) is None:
            continue  # a role that does not exist holds nothing
        privs = held(conn, role, (cfg.schema, "public"), functions, cfg.schema)
        findings += [f"[api] {role} holds schema privilege {item}" for item in privs["schema"]
                     if item.startswith(f"{cfg.schema}:")]
        for kind in ("relation", "column", "sequence"):
            findings += [f"[api] {role} holds {kind} privilege on {item}" for item in privs[kind]
                         if item.split(":")[0] in watched]
        findings += [f"[api] {role} can execute {item}" for item in privs["function"]]
    return findings


def check_rls(conn, cfg: Config) -> list[str]:
    rows = conn.execute(
        "SELECT n.nspname || '.' || c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') AND ((n.nspname = %s) OR (n.nspname = 'public' AND c.relname = ANY(%s)))",
        (cfg.schema, list(cfg.corpus_tables))).fetchall()
    have = dict(rows)
    expected = [f"{cfg.schema}.{t}" for t in cfg.audit_tables] + [f"public.{t}" for t in cfg.corpus_tables]
    findings = [f"[rls] {t} is missing" for t in expected if t not in have]
    findings += [f"[rls] {t} has row-level security off" for t in expected if t in have and not have[t]]
    findings += [f"[rls] unexpected table {t} in schema {cfg.schema}" for t in sorted(have) if t not in expected]
    return findings


def check_login_role(conn, name: str, group: str, limit: int) -> list[str]:
    row = _role(conn, name)
    if row is None:
        return [f"[login] {name} does not exist"]
    can_login, superuser, bypass, createdb, createrole, replication, conn_limit = row
    findings = []
    if not can_login:
        findings.append(f"[login] {name} cannot log in")
    for label, flag in (("superuser", superuser), ("BYPASSRLS", bypass), ("CREATEDB", createdb),
                        ("CREATEROLE", createrole), ("REPLICATION", replication)):
        if flag:
            findings.append(f"[login] {name} has {label}")
    if conn_limit != limit:
        findings.append(f"[login] {name} has connection limit {conn_limit}, expected {limit}")
    member = conn.execute(
        "SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.member "
        "WHERE g.rolname = %s AND r.rolname = %s", (group, name)).fetchone()
    if not member:
        findings.append(f"[login] {name} is not a member of {group}")
    return findings


def search_path_of(conn, role: str) -> str | None:
    row = conn.execute(
        "SELECT s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
        "WHERE r.rolname = %s AND s.setdatabase = 0", (role,)).fetchone()
    if not row:
        row = conn.execute(
            "SELECT s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole JOIN pg_database d "
            "ON d.oid = s.setdatabase WHERE r.rolname = %s AND d.datname = current_database()", (role,)).fetchone()
    for item in (row[0] if row else None) or []:
        if item.startswith("search_path="):
            return item.split("=", 1)[1]
    return None


def check_search_path(conn, role: str) -> list[str]:
    value = search_path_of(conn, role)
    normalised = None if value is None else ", ".join(part.strip() for part in value.split(","))
    return [] if normalised == SEARCH_PATH else [f"[login] {role} search_path is not '{SEARCH_PATH}'"]


def check_app_login(conn, cfg: Config, functions) -> list[str]:
    findings = check_login_role(conn, cfg.app_login, cfg.app_group, cfg.app_connection_limit)
    if _role(conn, cfg.app_login) is None:
        return findings
    privs = held(conn, cfg.app_login, (cfg.schema, "public"), functions, cfg.schema)
    allowed_tables = {f"public.{t}:SELECT" for t in cfg.corpus_tables}
    findings += [f"[app] {cfg.app_login} holds {item}" for item in privs["relation"] if item not in allowed_tables]
    findings += [f"[app] {cfg.app_login} cannot {item.split(':')[1]} {item.split(':')[0]}"
                 for item in sorted(allowed_tables - set(privs["relation"]))]
    findings += [f"[app] {cfg.app_login} holds column privileges on {item}" for item in privs["column"]
                 if f"{item}:SELECT" not in allowed_tables]
    findings += [f"[app] {cfg.app_login} holds sequence privilege on {item}" for item in privs["sequence"]]
    findings += [f"[app] {cfg.app_login} holds schema privilege {item}" for item in privs["schema"]
                 if item != f"{cfg.schema}:USAGE" and item != "public:USAGE"]
    can = sorted(item.removeprefix(f"{cfg.schema}.") for item in privs["function"])
    wanted = sorted(cfg.app_functions)
    findings += [f"[app] {cfg.app_login} cannot execute {f}" for f in wanted if f not in can]
    findings += [f"[app] {cfg.app_login} can execute {f}" for f in can if f not in wanted]
    return findings + check_search_path(conn, cfg.app_login)


def check_review_login(conn, cfg: Config, functions) -> list[str]:
    findings = check_login_role(conn, cfg.review_login, cfg.review_group, cfg.review_connection_limit)
    if _role(conn, cfg.review_login) is None:
        return findings
    privs = held(conn, cfg.review_login, (cfg.schema, "public"), functions, cfg.schema)
    wanted = {f"{cfg.schema}.{t}:SELECT" for t in cfg.audit_tables} | {f"public.{t}:SELECT" for t in cfg.corpus_tables}
    findings += [f"[review] {cfg.review_login} holds {item}" for item in privs["relation"] if item not in wanted]
    findings += [f"[review] {cfg.review_login} cannot {item.split(':')[1]} {item.split(':')[0]}"
                 for item in sorted(wanted - set(privs["relation"]))]
    findings += [f"[review] {cfg.review_login} holds sequence privilege on {item}" for item in privs["sequence"]]
    findings += [f"[review] {cfg.review_login} holds column privileges on {item}" for item in privs["column"]
                 if f"{item}:SELECT" not in wanted]
    findings += [f"[review] {cfg.review_login} holds schema privilege {item}" for item in privs["schema"]
                 if item not in (f"{cfg.schema}:USAGE", "public:USAGE")]
    findings += [f"[review] {cfg.review_login} can execute {f}" for f in privs["function"]]
    return findings + check_search_path(conn, cfg.review_login)


def check_no_ann_index(conn) -> list[str]:
    rows = conn.execute(
        "SELECT i.relname, am.amname FROM pg_index x JOIN pg_class t ON t.oid = x.indrelid JOIN pg_class i "
        "ON i.oid = x.indexrelid JOIN pg_am am ON am.oid = i.relam JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = 'public' AND t.relname = 'chunk_embeddings'").fetchall()
    return [f"[index] chunk_embeddings has an ANN index {name} ({method})" for name, method in rows
            if method in ANN_METHODS]


def run_checks(conn, cfg: Config = Config()) -> tuple[list[str], list[str]]:
    """(findings, report lines). Read-only."""
    functions = _functions(conn, cfg.schema)
    findings = (check_api_roles(conn, cfg, functions) + check_rls(conn, cfg) + check_app_login(conn, cfg, functions)
                + check_review_login(conn, cfg, functions) + check_no_ann_index(conn))
    report = []
    ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    report.append(f"vector extversion: {ext[0] if ext else 'not installed'}")
    if not ext:
        findings.append("[extension] vector is not installed")
    report.append(f"server: {conn.execute('SELECT version()').fetchone()[0]}")
    for role in (cfg.app_login, cfg.review_login):
        report.append(f"search_path of {role}: {search_path_of(conn, role) or 'not set'}")
    return findings, report


def main() -> int:
    from check_pgvector import connect  # noqa: PLC0415

    with connect() as conn:
        conn.read_only = True
        findings, report = run_checks(conn)
    for line in report:
        print(line)
    if findings:
        print(f"{len(findings)} finding(s):")
        for finding in findings:
            print("  " + finding)
        return 1
    print("clean: no finding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
