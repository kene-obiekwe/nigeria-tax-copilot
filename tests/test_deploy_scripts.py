"""Phase 11, step B1.2: the hosted-database SQL and the deploy and gate scripts (deployment design Sections 2, 3.2,
9.1 and 9.2; D-195, D-196).

Free, no network, no key. The SQL files and `check_hosted_db.py` run on the local container inside transactions
that are rolled back (roles included: role changes are transactional), so nothing of the project database
changes; the audit tests use the throwaway schema and roles of `audit_db`. The other scripts run on fakes."""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

import test_tools as T
from audit.grant_review import CORPUS_TABLES, grant_statements
from audit_testlib import audit_db, no_paid_clients  # noqa: F401
from check_pgvector import connect
from interface.service import AppService
from interface.startup import P7_CONFIG_SHA256, shipped_config
from test_app_service import HASHES, FakeGenerate

ROOT = Path(__file__).resolve().parents[1]
SUPABASE = ROOT / "deploy" / "supabase"


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


chd = load("check_hosted_db", "eval/deploy/check_hosted_db.py")
wh = load("with_hosted", "eval/deploy/with_hosted.py")
cr = load("compare_retrieval", "eval/deploy/compare_retrieval.py")
cl = load("compare_live", "eval/deploy/compare_live.py")
cls_ = load("check_live_start", "eval/deploy/check_live_start.py")
srp = load("set_role_password", "deploy/set_role_password.py")
asql = load("apply_sql", "deploy/apply_sql.py")

PASSWORD = "Zq9-never-print-this-password-Zq9"


# ====================================================================================================================
# The SQL files (rolled-back transactions on the local container)
# ====================================================================================================================


@pytest.fixture
def conn():
    c = connect()
    c.autocommit = True
    try:
        yield c
    finally:
        c.close()


def prepare_cluster(c) -> None:
    """What Supabase has and the local container lacks, created inside the transaction."""
    c.execute("CREATE SCHEMA IF NOT EXISTS extensions")
    for role in ("taxy_app", "taxy_review", "anon", "authenticated", "service_role", "authenticator", "postgres"):
        c.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                  f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")


def role_row(c, name):
    return c.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, "
                     "rolinherit, rolconnlimit FROM pg_roles WHERE rolname = %s", (name,)).fetchone()


def has_priv(c, role, table, priv="SELECT"):
    return c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", priv)).fetchone()[0]


def test_0001_creates_two_plain_login_roles_with_no_password_and_grants_the_app_group_its_corpus_read(conn):
    text = (SUPABASE / "0001_hosted_roles.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    assert "PASSWORD" not in code.upper()                                     # no password is set, or written, here
    with conn.transaction(force_rollback=True):
        prepare_cluster(conn)
        conn.execute(text)
        conn.execute(text)                                                    # idempotent: a second run changes nothing
        for login, group, limit in (("taxy_app_login", "taxy_app", 20), ("taxy_review_login", "taxy_review", 3)):
            can_login, superuser, bypass, createdb, createrole, replication, inherit, conn_limit = role_row(conn, login)
            assert can_login and inherit and conn_limit == limit
            assert not (superuser or bypass or createdb or createrole or replication)
            assert conn.execute("SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r "
                                "ON r.oid = m.member WHERE g.rolname = %s AND r.rolname = %s", (group, login)).fetchone()
            assert conn.execute("SELECT setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                                "WHERE r.rolname = %s", (login,)).fetchone()[0] == ["search_path=public, extensions"]
            assert conn.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (login,)).fetchone()[0] is None
        for table in CORPUS_TABLES:
            assert has_priv(conn, "taxy_app_login", table) and not has_priv(conn, "taxy_app_login", table, "INSERT")
        assert conn.execute("SELECT has_schema_privilege('taxy_app_login', 'extensions', 'USAGE'), "
                            "has_schema_privilege('taxy_review_login', 'extensions', 'USAGE')").fetchone() == (True, True)
    assert role_row(conn, "taxy_app_login") is None                           # rolled back: nothing persisted


def test_0001_brings_an_existing_role_to_the_same_state(conn):
    text = (SUPABASE / "0001_hosted_roles.sql").read_text(encoding="utf-8")
    with conn.transaction(force_rollback=True):
        prepare_cluster(conn)
        conn.execute("CREATE ROLE taxy_app_login LOGIN BYPASSRLS CREATEDB CONNECTION LIMIT 99")
        conn.execute(text)
        row = role_row(conn, "taxy_app_login")
        assert (row[2], row[3], row[7]) == (False, False, 20)


def test_0001_refuses_a_group_role_that_can_log_in(conn):
    text = (SUPABASE / "0001_hosted_roles.sql").read_text(encoding="utf-8")
    with pytest.raises(psycopg.errors.RaiseException, match="group role"):
        with conn.transaction(force_rollback=True):
            prepare_cluster(conn)
            conn.execute("ALTER ROLE taxy_app LOGIN")
            conn.execute(text)


def test_0002_removes_every_api_role_privilege_turns_rls_on_and_adds_one_select_policy(conn):
    text = (SUPABASE / "0002_revoke_api_roles.sql").read_text(encoding="utf-8")
    api = ("anon", "authenticated", "service_role")
    with conn.transaction(force_rollback=True):
        prepare_cluster(conn)
        tables = ", ".join(f"public.{t}" for t in CORPUS_TABLES)
        conn.execute(f"GRANT ALL ON TABLE {tables} TO {', '.join(api)}")
        conn.execute(f"GRANT ALL ON SEQUENCE public.corpus_loads_load_id_seq TO {', '.join(api)}")
        conn.execute("GRANT CREATE ON SCHEMA public TO postgres")
        for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            conn.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON {kind} TO {', '.join(api)}")
        conn.execute(text)
        conn.execute(text)                                                    # idempotent
        for role in api:
            for table in CORPUS_TABLES:
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    assert not has_priv(conn, role, table, priv), (role, table, priv)
            assert not conn.execute("SELECT has_sequence_privilege(%s, 'public.corpus_loads_load_id_seq', "
                                    "'USAGE, SELECT, UPDATE')", (role,)).fetchone()[0]
        # what the owner creates later does not start out open to the API roles either
        conn.execute("SET LOCAL ROLE postgres")
        conn.execute("CREATE TABLE public.zz_later_table (x int)")
        conn.execute("CREATE FUNCTION public.zz_later_fn() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        conn.execute("RESET ROLE")
        for role in api:
            assert not has_priv(conn, role, "zz_later_table", "SELECT")
        # (EXECUTE for PUBLIC is PostgreSQL's built-in default and not an API-role grant; the default grants
        # Supabase gives these roles are what the revoke removes: no ACL entry names them)
        acl = conn.execute("SELECT coalesce(proacl::text, '') FROM pg_proc WHERE proname = 'zz_later_fn'").fetchone()[0]
        assert not any(f"{role}=" in acl for role in api)
        rls = conn.execute("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = ANY(%s::regclass[])",
                           ([f"public.{t}" for t in CORPUS_TABLES],)).fetchall()
        assert sorted(rls) == sorted((t, True, False) for t in CORPUS_TABLES)
        policies = conn.execute("SELECT tablename, policyname, cmd, roles::text[] FROM pg_policies "
                                "WHERE schemaname = 'public' AND tablename = ANY(%s) ORDER BY 1",
                                (list(CORPUS_TABLES),)).fetchall()
        assert sorted(policies) == sorted((t, "corpus_select", "SELECT", ["taxy_app", "taxy_review"])
                                          for t in CORPUS_TABLES)
    assert not conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.corpus_chunks'::regclass").fetchone()[0]


def test_apply_sql_runs_a_script_in_the_current_transaction(conn):
    with conn.transaction(force_rollback=True):
        asql.apply_sql(conn, "CREATE TEMP TABLE zz_t (x int); INSERT INTO zz_t VALUES (1), (2);")
        assert conn.execute("SELECT count(*) FROM zz_t").fetchone()[0] == 2


def test_apply_sql_usage_errors_and_failures_print_no_statement_or_host(tmp_path, monkeypatch, capsys):
    assert asql.main([]) == 2 and asql.main(["--help"]) == 2
    other = tmp_path / "x.txt"
    other.write_text("select 1", encoding="utf-8")
    assert asql.main([str(other)]) == 2
    script = tmp_path / "bad.sql"
    script.write_text("SELECT 'password=hunter2-in-a-statement'; SELECT nope_missing_column FROM nowhere;", encoding="utf-8")
    capsys.readouterr()
    assert asql.main([str(script)]) == 1
    out = capsys.readouterr().out
    assert "UndefinedTable" in out and "hunter2" not in out and "nowhere" not in out and "bad.sql" in out
    import check_pgvector

    class Boom:
        def __enter__(self):
            raise RuntimeError("could not connect to postgresql://user:hunter2@host/db")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(check_pgvector, "connect", lambda: Boom())
    assert asql.main([str(script)]) == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "hunter2" not in out and "postgresql://" not in out


# ====================================================================================================================
# set_role_password.py
# ====================================================================================================================


def test_the_scram_verifier_has_postgresqls_format_and_is_salted():
    verifier = srp.scram_verifier(PASSWORD)
    assert re.fullmatch(r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]{24}\$[A-Za-z0-9+/=]{44}:[A-Za-z0-9+/=]{44}", verifier)
    assert PASSWORD not in verifier and verifier != srp.scram_verifier(PASSWORD)
    salt = b"0123456789abcdef"
    assert srp.scram_verifier(PASSWORD, salt=salt) == srp.scram_verifier(PASSWORD, salt=salt)
    assert srp.scram_verifier(PASSWORD, salt=salt) != srp.scram_verifier(PASSWORD + "x", salt=salt)


def test_the_verifier_is_accepted_by_the_real_server_and_logs_the_role_in_only_with_the_right_password(conn):
    """ALTER ROLE with the computed verifier, then a real SCRAM login through the container's host rule."""
    role = f"test_login_{os.urandom(4).hex()}"
    try:
        conn.execute(f"CREATE ROLE {role} LOGIN")
        srp.set_password(conn, role, PASSWORD)
        info = conn.info
        params = dict(host=info.host, port=info.port, dbname=info.dbname, user=role, connect_timeout=5)
        with psycopg.connect(password=PASSWORD, **params) as ok:
            assert ok.execute("SELECT current_user").fetchone()[0] == role
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(password=PASSWORD + "x", **params)
    finally:
        conn.execute(f"DROP ROLE IF EXISTS {role}")


class RecordingConn:
    """A connection that records every statement it is given and finds the role."""

    def __init__(self, role_exists=True, fail=None):
        self.statements, self.role_exists, self.fail, self.autocommit = [], role_exists, fail, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        if self.fail:
            raise self.fail
        text = query if isinstance(query, str) else query.as_string(None)
        self.statements.append(text)
        return self

    def fetchone(self):
        return (1,) if self.role_exists else None


def typed(*answers):
    queue = list(answers)
    asked = []

    def prompt(message):
        asked.append(message)
        return queue.pop(0)

    prompt.asked = asked
    return prompt


def test_the_password_is_asked_only_through_getpass_and_never_printed_logged_or_stored(capsys, caplog):
    rec = RecordingConn()
    with caplog.at_level("DEBUG"):
        code = srp.main(["app"], prompt=typed(PASSWORD, PASSWORD), connect=lambda: rec)
    out = capsys.readouterr()
    assert code == 0 and out.out.strip() == "password set for taxy_app_login"
    assert PASSWORD not in out.out + out.err and PASSWORD not in caplog.text
    (alter,) = [s for s in rec.statements if s.startswith("ALTER ROLE")]
    assert PASSWORD not in alter and "taxy_app_login" in alter and "SCRAM-SHA-256$4096:" in alter
    assert not any(PASSWORD in s for s in rec.statements)


def test_the_real_getpass_is_the_default_prompt(monkeypatch, capsys):
    import getpass
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    rec = RecordingConn()
    assert srp.main(["review"], prompt=getpass.getpass, connect=lambda: rec) == 0
    assert "taxy_review_login" in capsys.readouterr().out


def test_the_script_reads_no_password_from_arguments_the_environment_or_a_file():
    source = (ROOT / "deploy" / "set_role_password.py").read_text(encoding="utf-8")
    assert "getpass.getpass" in source
    for banned in ("os.environ", "os.getenv", "input(", "open(", "read_text", "sys.stdin"):
        assert banned not in source, banned


def test_a_password_given_as_an_argument_is_refused_and_not_echoed(capsys):
    rec = RecordingConn()
    assert srp.main(["app", PASSWORD], prompt=typed(), connect=lambda: rec) == 2
    assert srp.main(["owner"], prompt=typed(), connect=lambda: rec) == 2
    assert srp.main([], prompt=typed(), connect=lambda: rec) == 2
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err and rec.statements == []


@pytest.mark.parametrize("answers", [("short", "short"), (PASSWORD, PASSWORD + "x")])
def test_a_short_or_unconfirmed_password_is_not_set_and_not_echoed(answers, capsys):
    rec = RecordingConn()
    assert srp.main(["app"], prompt=typed(*answers), connect=lambda: rec) == 2
    out = capsys.readouterr()
    assert "not set" in out.out and all(a not in out.out + out.err for a in answers) and rec.statements == []


def test_a_missing_role_or_a_database_error_names_no_password_host_or_statement(capsys):
    assert srp.main(["app"], prompt=typed(PASSWORD, PASSWORD), connect=lambda: RecordingConn(role_exists=False)) == 1
    out = capsys.readouterr().out
    assert "taxy_app_login does not exist" in out and PASSWORD not in out
    boom = RecordingConn(fail=RuntimeError(f"ALTER ROLE x PASSWORD '{PASSWORD}' on postgresql://u:p@host/db"))
    assert srp.main(["app"], prompt=typed(PASSWORD, PASSWORD), connect=lambda: boom) == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out and PASSWORD not in out and "postgresql://" not in out


# ====================================================================================================================
# with_hosted.py
# ====================================================================================================================

ENV_TEXT = """# the three lines, typed by Kene
DATABASE_URL_OWNER=postgresql://owner:OWNER-SECRET@host:5432/db
DATABASE_URL_APP="postgresql://app:APP-SECRET@host:5432/db"
export REVIEW_DATABASE_URL='postgresql://review:REVIEW-SECRET@host:5432/db'
OTHER=ignored
"""
SECRETS = ("OWNER-SECRET", "APP-SECRET", "REVIEW-SECRET")


def test_the_env_file_parser_handles_comments_quotes_and_export():
    values = wh.parse_env_text(ENV_TEXT)
    assert values["DATABASE_URL_OWNER"].endswith("OWNER-SECRET@host:5432/db")
    assert values["DATABASE_URL_APP"].startswith("postgresql://app")                     # quotes removed
    assert values["REVIEW_DATABASE_URL"].startswith("postgresql://review")               # `export` and quotes removed
    assert wh.parse_env_text("EMPTY=\nA=b=c") == {"EMPTY": "", "A": "b=c"}


@pytest.mark.parametrize("role,target,secret", [("owner", "DATABASE_URL", "OWNER-SECRET"),
                                                ("app", "DATABASE_URL", "APP-SECRET"),
                                                ("review", "REVIEW_DATABASE_URL", "REVIEW-SECRET")])
def test_the_child_gets_exactly_one_connection_under_the_name_its_scripts_read(role, target, secret):
    parent = {"PATH": "x", "DATABASE_URL": "parent-url", "REVIEW_DATABASE_URL": "parent-review"}
    env = wh.child_environment(role, ENV_TEXT, parent)
    assert secret in env[target] and env["PATH"] == "x"
    held = [n for n in ("DATABASE_URL", "REVIEW_DATABASE_URL", "DATABASE_URL_OWNER", "DATABASE_URL_APP") if n in env]
    assert held == [target]                                                              # nothing else, not the parent's either
    assert not any(other in " ".join(env.values()) for other in SECRETS if other != secret)


def test_a_missing_or_empty_line_is_named_without_a_value():
    with pytest.raises(wh.HostedEnvError, match="REVIEW_DATABASE_URL is missing or empty"):
        wh.child_environment("review", "DATABASE_URL_OWNER=x\nREVIEW_DATABASE_URL=\n", {})
    with pytest.raises(wh.HostedEnvError, match="role must be one of"):
        wh.child_environment("admin", ENV_TEXT, {})


def test_main_runs_the_script_with_the_line_and_prints_no_value(tmp_path, capfd):
    env_file = tmp_path / ".env.hosted"
    env_file.write_text(ENV_TEXT, encoding="utf-8")
    out_file = tmp_path / "seen.json"
    script = tmp_path / "child.py"
    script.write_text("import json, os, sys\n"
                      "names = ['DATABASE_URL', 'REVIEW_DATABASE_URL', 'DATABASE_URL_OWNER', 'DATABASE_URL_APP']\n"
                      "json.dump({n: os.environ.get(n) for n in names}, open(sys.argv[1], 'w'))\n"
                      "print('child done', sys.argv[2:])\n", encoding="utf-8")
    code = wh.main(["owner", str(script), str(out_file), "--flag"], env_file=env_file, base={"PATH": os.environ.get("PATH", "")})
    seen = json.loads(out_file.read_text(encoding="utf-8"))
    assert code == 0 and seen["DATABASE_URL"].endswith("OWNER-SECRET@host:5432/db")
    assert [n for n, v in seen.items() if v] == ["DATABASE_URL"]
    shown = capfd.readouterr()
    assert "child done ['--flag']" in shown.out and not any(s in shown.out + shown.err for s in SECRETS)


def test_main_returns_the_childs_exit_code(tmp_path):
    env_file = tmp_path / ".env.hosted"
    env_file.write_text(ENV_TEXT, encoding="utf-8")
    script = tmp_path / "fail.py"
    script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    assert wh.main(["app", str(script)], env_file=env_file, base={}) == 7


def test_main_usage_and_missing_file_messages_hold_no_value(tmp_path, capsys):
    script = tmp_path / "ok.py"
    script.write_text("pass\n", encoding="utf-8")
    assert wh.main([], env_file=tmp_path / "no") == 2
    assert wh.main(["owner"], env_file=tmp_path / "no") == 2
    assert wh.main(["root", str(script)], env_file=tmp_path / "no") == 2
    assert wh.main(["owner", str(tmp_path / "absent.py")], env_file=tmp_path / "no") == 2
    assert wh.main(["owner", str(script)], env_file=tmp_path / "no") == 2
    empty = tmp_path / "empty.env"
    empty.write_text("DATABASE_URL_OWNER=\n", encoding="utf-8")
    assert wh.main(["owner", str(script)], env_file=empty, base={}) == 2
    out = capsys.readouterr().out
    assert ".env.hosted is missing" in out and "DATABASE_URL_OWNER is missing or empty" in out


def test_env_hosted_is_git_ignored_and_never_in_the_image():
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env.hosted"], cwd=ROOT).returncode == 0
    assert ignored
    sys.path.insert(0, str(ROOT / "tests"))
    spec = importlib.util.spec_from_file_location("check_image_for_hosted", ROOT / "eval" / "deploy" / "check_image.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    rules = ci.parse_dockerignore((ROOT / ".dockerignore").read_text(encoding="utf-8"))
    assert not ci.allowed(".env.hosted", rules) and ci.forbidden_reason(".env.hosted") is not None
    for path in ("deploy/set_role_password.py", "deploy/supabase/0001_hosted_roles.sql", "eval/deploy/with_hosted.py"):
        assert not ci.allowed(path, rules), path                                         # deploy tooling stays out of the image


# ====================================================================================================================
# check_hosted_db.py (the throwaway audit schema and roles of audit_db, inside rolled-back transactions)
# ====================================================================================================================


def hosted_like(audit_db):
    """A rolled-back transaction in which the throwaway schema looks like the hosted one; returns the Config."""
    c, n, hx = audit_db.conn, audit_db.names, audit_db.hex
    cfg = chd.Config(schema=n.schema, app_login=f"test_applogin_{hx}", app_group=n.app_role,
                     review_login=f"test_revlogin_{hx}", review_group=n.review_role,
                     api_roles=(f"test_anon_{hx}", f"test_auth_{hx}", f"test_absent_{hx}"))
    for role in cfg.api_roles[:2]:
        c.execute(f"CREATE ROLE {role} NOLOGIN")
    c.execute(f"CREATE ROLE {cfg.app_login} LOGIN IN ROLE {cfg.app_group} CONNECTION LIMIT 20")
    c.execute(f"CREATE ROLE {cfg.review_login} LOGIN IN ROLE {cfg.review_group} CONNECTION LIMIT 3")
    for login in (cfg.app_login, cfg.review_login):
        c.execute(f"ALTER ROLE {login} SET search_path = public, extensions")
    tables = ", ".join(f"public.{t}" for t in CORPUS_TABLES)
    c.execute(f"GRANT SELECT ON {tables} TO {cfg.app_group}")
    for statement in grant_statements(cfg.review_group):
        c.execute(statement)
    for table in CORPUS_TABLES:
        c.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    return cfg


def test_a_hosted_like_database_has_no_finding_and_the_report_names_version_and_search_paths(audit_db):
    with audit_db.conn.transaction(force_rollback=True):
        cfg = hosted_like(audit_db)
        findings, report = chd.run_checks(audit_db.conn, cfg)
    assert findings == []
    assert report[0].startswith("vector extversion: 0.") and report[1].startswith("server: PostgreSQL 17")
    assert report[2:] == [f"search_path of {cfg.app_login}: public, extensions",
                          f"search_path of {cfg.review_login}: public, extensions"]


class SelectOnly:
    """Wraps a connection and fails on any statement that is not a SELECT."""

    def __init__(self, conn):
        self.conn, self.n = conn, 0

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(self.conn)
        assert text.lstrip().upper().startswith("SELECT"), text[:60]
        self.n += 1
        return self.conn.execute(query, params)


def test_the_check_only_selects(audit_db):
    with audit_db.conn.transaction(force_rollback=True):
        cfg = hosted_like(audit_db)
        wrapped = SelectOnly(audit_db.conn)
        assert chd.run_checks(wrapped, cfg)[0] == [] and wrapped.n > 50


FINDINGS = [  # (statement with {api} {schema} {app_group} {review_group} {app_login} {review_login}, expected finding)
    ("GRANT INSERT ON public.corpus_chunks TO {api}", "[api] {api} holds relation privilege on public.corpus_chunks:INSERT"),
    ("GRANT USAGE ON SCHEMA {schema} TO {api}", "[api] {api} holds schema privilege {schema}:USAGE"),
    ("GRANT SELECT ON {schema}.queries TO {api}", "[api] {api} holds relation privilege on {schema}.queries:SELECT"),
    ("GRANT EXECUTE ON FUNCTION {schema}.wat_day_start(timestamptz) TO {api}",
     "[api] {api} can execute {schema}.wat_day_start(timestamp with time zone)"),
    ("GRANT USAGE ON SEQUENCE public.corpus_loads_load_id_seq TO {api}",
     "[api] {api} holds sequence privilege on public.corpus_loads_load_id_seq"),
    ("GRANT SELECT (chunk_id) ON public.corpus_chunks TO {api}", "[api] {api} holds column privilege on public.corpus_chunks"),
    ("ALTER TABLE public.corpus_chunks DISABLE ROW LEVEL SECURITY", "[rls] public.corpus_chunks has row-level security off"),
    ("ALTER TABLE {schema}.queries DISABLE ROW LEVEL SECURITY", "[rls] {schema}.queries has row-level security off"),
    ("CREATE TABLE {schema}.extra_t (x int)", "[rls] unexpected table {schema}.extra_t in schema {schema}"),
    ("GRANT INSERT ON public.corpus_loads TO {app_group}", "[app] {app_login} holds public.corpus_loads:INSERT"),
    ("GRANT SELECT ON {schema}.queries TO {app_group}", "[app] {app_login} holds {schema}.queries:SELECT"),
    ("GRANT USAGE ON SEQUENCE public.corpus_loads_load_id_seq TO {app_group}",
     "[app] {app_login} holds sequence privilege on public.corpus_loads_load_id_seq"),
    ("GRANT EXECUTE ON FUNCTION {schema}.purge_expired(integer) TO {app_group}",
     "[app] {app_login} can execute purge_expired(integer)"),
    ("REVOKE EXECUTE ON FUNCTION {schema}.touch_user(text, text) FROM {app_group}",
     "[app] {app_login} cannot execute touch_user(text, text)"),
    ("REVOKE SELECT ON public.corpus_chunks FROM {app_group}", "[app] {app_login} cannot SELECT public.corpus_chunks"),
    ("GRANT CREATE ON SCHEMA public TO {app_group}", "[app] {app_login} holds schema privilege public:CREATE"),
    ("ALTER ROLE {app_login} BYPASSRLS", "[login] {app_login} has BYPASSRLS"),
    ("ALTER ROLE {app_login} CREATEDB", "[login] {app_login} has CREATEDB"),
    ("ALTER ROLE {review_login} CONNECTION LIMIT 9", "[login] {review_login} has connection limit 9, expected 3"),
    ("ALTER ROLE {app_login} RESET search_path", "[login] {app_login} search_path is not 'public, extensions'"),
    ("ALTER ROLE {review_login} SET search_path = public", "[login] {review_login} search_path is not 'public, extensions'"),
    ("REVOKE {app_group} FROM {app_login}", "[login] {app_login} is not a member of {app_group}"),
    ("GRANT UPDATE ON {schema}.queries TO {review_group}", "[review] {review_login} holds {schema}.queries:UPDATE"),
    ("GRANT INSERT ON public.corpus_chunks TO {review_group}", "[review] {review_login} holds public.corpus_chunks:INSERT"),
    ("GRANT EXECUTE ON FUNCTION {schema}.touch_user(text, text) TO {review_group}",
     "[review] {review_login} can execute {schema}.touch_user(text, text)"),
    ("REVOKE SELECT ON public.corpus_chunks FROM {review_group}", "[review] {review_login} cannot SELECT public.corpus_chunks"),
    ("REVOKE SELECT ON {schema}.claims FROM {review_group}", "[review] {review_login} cannot SELECT {schema}.claims"),
]


@pytest.mark.parametrize("statement,expected", FINDINGS)
def test_each_privilege_or_setting_that_should_not_be_there_is_a_finding(audit_db, statement, expected):
    with audit_db.conn.transaction(force_rollback=True):
        cfg = hosted_like(audit_db)
        names = dict(api=cfg.api_roles[0], schema=cfg.schema, app_group=cfg.app_group, review_group=cfg.review_group,
                     app_login=cfg.app_login, review_login=cfg.review_login)
        audit_db.conn.execute(statement.format(**names))
        findings, _ = chd.run_checks(audit_db.conn, cfg)
    assert expected.format(**names) in findings, findings
    assert not any(secret in " ".join(findings) for secret in ("postgresql://", "password"))


def test_a_missing_login_role_is_a_finding_and_a_missing_api_role_is_not(audit_db):
    with audit_db.conn.transaction(force_rollback=True):
        cfg = hosted_like(audit_db)
        gone = chd.Config(**{**cfg.__dict__, "app_login": f"test_nologin_{audit_db.hex}"})
        findings, _ = chd.run_checks(audit_db.conn, gone)
    assert f"[login] {gone.app_login} does not exist" in findings
    assert not any(cfg.api_roles[2] in f for f in findings)                     # `test_absent_` never existed: no finding


def test_an_ann_index_on_the_embeddings_is_a_finding():
    class FakeConn:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, query, params=None):
            return self

        def fetchall(self):
            return self.rows

    assert chd.check_no_ann_index(FakeConn([("chunk_embeddings_pkey", "btree")])) == []
    assert chd.check_no_ann_index(FakeConn([("e_hnsw", "hnsw"), ("e_ivf", "ivfflat")])) == [
        "[index] chunk_embeddings has an ANN index e_hnsw (hnsw)", "[index] chunk_embeddings has an ANN index e_ivf (ivfflat)"]


def test_the_default_config_names_the_designs_roles_tables_and_functions():
    cfg = chd.Config()
    assert (cfg.app_login, cfg.review_login) == ("taxy_app_login", "taxy_review_login")
    assert cfg.api_roles == ("anon", "authenticated", "service_role", "authenticator")
    assert len(cfg.audit_tables) == 11 and len(cfg.corpus_tables) == 4 and len(cfg.app_functions) == 10
    assert (cfg.app_connection_limit, cfg.review_connection_limit) == (20, 3)


# ====================================================================================================================
# compare_retrieval.py
# ====================================================================================================================

LOCAL = {"Q1": [("a", 0.10), ("b", 0.20), ("c", 0.30)], "Q2": [("x", 0.5), ("y", 0.6)]}


def test_equal_rankings_report_the_largest_distance_difference():
    hosted = {"Q1": [("a", 0.10), ("b", 0.2000004), ("c", 0.30)], "Q2": [("x", 0.5), ("y", 0.6)]}
    diffs, largest = cr.compare_rankings(LOCAL, hosted)
    assert diffs == [] and largest == pytest.approx(4e-7)


def test_different_ids_or_order_or_length_are_differences_naming_the_first_rank():
    assert cr.compare_rankings(LOCAL, {"Q1": [("a", 0.1), ("c", 0.2), ("b", 0.3)], "Q2": LOCAL["Q2"]})[0] == [
        "Q1: ranked ids differ from rank 2 (3 local, 3 hosted)"]
    assert cr.compare_rankings(LOCAL, {"Q1": LOCAL["Q1"], "Q2": [("x", 0.5)]})[0] == [
        "Q2: ranked ids differ from rank 2 (2 local, 1 hosted)"]
    assert cr.compare_rankings(LOCAL, {"Q1": LOCAL["Q1"]})[0] == ["question ids differ (2 local, 1 hosted)"]


def test_the_test_figures_are_checked_for_the_final_configuration_only():
    ok = {"overall": {"questions": 30, "hit@5": 30, "hit@1": 22, "mrr": 0.8556}}
    bad = {"overall": {"questions": 30, "hit@5": 29, "hit@1": 22, "mrr": 0.8556}}
    assert cr.figures_problems(ok, "test", "config_final.json") == []
    assert len(cr.figures_problems(bad, "test", "config_final.json")) == 1
    assert cr.figures_problems(bad, "test", "config_v3.json") == [] and cr.figures_problems(bad, "dev", "config_final.json") == []


def test_the_render_flags_a_distance_difference_above_1e_6_as_a_warning_not_a_failure():
    result = cr.Result("config_final.json", "dev", 20, [], 2e-6, {"hit@5": 1, "hit@1": 1, "mrr": 1.0})
    (line,) = cr.render([result])
    assert "WARNING" in line and "0 differences" in line


def have_query_cache() -> bool:
    return (ROOT / "data" / "cache" / "query_embeddings" / "openai__text-embedding-3-small.jsonl").exists()


@pytest.fixture(scope="module")
def retrieval_scripts():
    run_eval = load("run_eval_for_tests", "eval/retrieval/run_eval.py")
    reproduce = load("reproduce_for_tests", "eval/reproduce/reproduce.py")
    return run_eval, reproduce


@pytest.mark.skipif(not have_query_cache(), reason="the published query cache is not present (fetch_assets.py)")
def test_local_against_itself_gives_zero_differences_and_the_g1_figures_through_the_cache_only_client(retrieval_scripts):
    """The real retrieval on the local container, both configurations, from the cached query vectors only."""
    run_eval, reproduce = retrieval_scripts
    with connect() as local, connect() as hosted:
        factory = cr.make_cache_factory(run_eval, reproduce)
        final = cr.compare(local, hosted, run_eval=run_eval, cache_factory=factory, configs=("config_final.json",),
                           splits=("dev", "test"))
        v3 = cr.compare(local, hosted, run_eval=run_eval, cache_factory=factory, configs=("config_v3.json",), splits=("dev",))
    assert [(r.config, r.split, len(r.differences), r.max_distance_difference) for r in final + v3] == [
        ("config_final.json", "dev", 0, 0.0), ("config_final.json", "test", 0, 0.0), ("config_v3.json", "dev", 0, 0.0)]
    test = next(r for r in final if r.split == "test")
    assert (test.questions, test.figures["hit@5"], test.figures["hit@1"], test.figures["mrr"]) == (30, 30, 22, 0.8556)


def test_a_query_cache_miss_fails_and_never_reaches_an_api(retrieval_scripts, tmp_path):
    run_eval, reproduce = retrieval_scripts
    cache = run_eval.QueryCache("openai/text-embedding-3-small", tmp_path)       # an empty cache directory
    with pytest.raises(reproduce.CacheMiss):
        cache.embed("a question that is not cached", client_factory=reproduce.cache_only_client_factory)


def test_the_script_does_not_write_a_result_file_or_call_an_api():
    source = (ROOT / "eval" / "deploy" / "compare_retrieval.py").read_text(encoding="utf-8")
    assert "run_eval.run(" not in source and "results_dir" not in source and "write_text" not in source
    assert "cache_only_client_factory" in source and "get_client" not in source


def test_without_a_hosted_connection_the_script_stops_and_prints_no_value(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cr.main() == 2
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "postgresql" not in out


# ====================================================================================================================
# check_live_start.py
# ====================================================================================================================

COMMIT = "0123456789abcdef0123456789abcdef01234567"


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeStarts:
    """A connection whose app_starts answer changes between polls: rows[i] is the answer of the i-th read."""

    def __init__(self, *rows):
        self.rows, self.reads = list(rows), 0

    def execute(self, query, params=None):
        row = self.rows[min(self.reads, len(self.rows) - 1)]
        self.reads += 1
        return Cursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def start_row(*, outcome="ok", problems=(), config=P7_CONFIG_SHA256, commit=COMMIT, start_id=9):
    return (start_id, "2026-11-20T10:00:00Z", outcome, list(problems), config, commit, "app/1", "3.12.16", "1.66.0")


def run_live(rows, *args, **kwargs):
    fake = FakeStarts(*rows)
    opened, naps = [], []
    code = cls_.main(["--commit", COMMIT[:12], *args], connect=lambda: fake, open_url=opened.append,
                     sleep=naps.append, clock=kwargs.get("clock", iter(range(0, 10_000, 1)).__next__))
    return code, fake, opened, naps


def test_a_clean_row_for_the_approved_commit_passes(capsys):
    code, fake, opened, naps = run_live([start_row()])
    out = capsys.readouterr().out
    assert code == 0 and "clean: ok, no problems, pinned config, approved commit" in out
    assert fake.reads == 1 and opened == [] and naps == []


@pytest.mark.parametrize("row,expected", [
    (start_row(outcome="failed", problems=["live_config"]), ["outcome is failed", "problems recorded: live_config"]),
    (start_row(config="0" * 64), ["config_sha256 is not the pinned Phase 7 value"]),
    (start_row(problems=["hosted_env"]), ["problems recorded: hosted_env"])])
def test_a_row_that_is_not_clean_fails_and_names_each_problem(row, expected, capsys):
    code, *_ = run_live([row])
    out = capsys.readouterr().out
    assert code == 1 and all(f"PROBLEM: {e}" in out for e in expected)


def test_a_row_for_another_commit_is_not_the_approved_deploy(capsys):
    code, *_ = run_live([start_row(commit="f" * 40)])
    assert code == 2 and "different commit" in capsys.readouterr().out


def test_no_hosted_row_yet_asks_for_one_browser_visit(capsys):
    code, *_ = run_live([None])
    assert code == 2 and "open the live URL in a browser once" in capsys.readouterr().out


def test_the_commit_may_be_a_full_hash_or_a_prefix_of_at_least_seven_hex_characters(capsys):
    assert cls_.commit_matches(COMMIT, COMMIT) and cls_.commit_matches(COMMIT, COMMIT[:7].upper())
    assert not cls_.commit_matches(COMMIT, COMMIT[:6]) and not cls_.commit_matches(COMMIT, "zzzzzzzz")
    assert not cls_.commit_matches(None, COMMIT) and not cls_.commit_matches("", COMMIT)
    assert not cls_.commit_matches(COMMIT, "f" * 40)
    for bad in ("abc", "not-hex-at-all", ""):
        assert cls_.main(["--commit", bad], connect=lambda: FakeStarts(None)) == 2
    capsys.readouterr()


def test_with_a_url_it_opens_the_page_once_then_polls_until_the_row_appears(capsys):
    code, fake, opened, naps = run_live([None, None, start_row()], "--url", "https://example.test/")
    assert code == 0 and opened == ["https://example.test/"] and fake.reads == 3 and naps == [5.0, 5.0]


def test_the_wait_ends_and_reports_when_the_row_never_comes(capsys):
    clock = iter([0, 1, 100, 200, 300, 400]).__next__
    code, fake, opened, naps = run_live([None], "--url", "https://example.test/", "--wait", "30", clock=clock)
    assert code == 2 and opened == ["https://example.test/"] and fake.reads >= 2


def test_a_connection_failure_prints_the_error_type_only(capsys):
    def boom():
        raise RuntimeError("could not connect to postgresql://reviewer:hunter2@host/db")

    assert cls_.main(["--commit", COMMIT], connect=boom) == 2
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "hunter2" not in out and "postgresql://" not in out


def test_the_latest_hosted_row_is_read_from_the_real_audit_table(audit_db):
    store = audit_db.store

    def record(**kw):
        base = dict(deployment="hosted", git_commit=COMMIT, app_version="app/1", audit_schema="audit-schema/1",
                    python_version="3.12.16", streamlit_version="1.66.0", config_file="eval/tools/config_p7.json",
                    config_sha256=P7_CONFIG_SHA256, corpus_load_id=None, corpus_snapshot=None, chunks_sha256=None,
                    outcome="ok", problems=[])
        return store.record_start(**{**base, **kw})

    assert cls_.latest_hosted_start(audit_db.conn, audit_db.names.schema) is None
    audit_db.start_id()                                                          # a 'test' start is not a hosted one
    assert cls_.latest_hosted_start(audit_db.conn, audit_db.names.schema) is None
    record(git_commit="a" * 40)
    newest = record()
    start = cls_.latest_hosted_start(audit_db.conn, audit_db.names.schema)
    assert start.start_id == newest and start.git_commit == COMMIT and start.problems == []
    assert cls_.problems_of(start, COMMIT[:8]) == []
    bad = record(outcome="failed", problems=["audit_rls", "database"])
    assert cls_.problems_of(cls_.latest_hosted_start(audit_db.conn, audit_db.names.schema), COMMIT) == [
        "outcome is failed, expected ok", "problems recorded: audit_rls, database"] and bad > newest


def test_the_pinned_config_hash_is_the_start_up_checks():
    from interface import startup
    assert cls_.P7_CONFIG_SHA256 == startup.P7_CONFIG_SHA256


# ====================================================================================================================
# compare_live.py
# ====================================================================================================================


def run_files():
    return [json.loads((ROOT / p).read_text(encoding="utf-8")) for p in cl.E2E_RUNS]


def test_the_draw_is_fixed_by_the_seed_and_draws_five_distinct_subset_t_cases():
    run1, run2 = run_files()
    eligible = cl.eligible_cases(run1, run2)
    assert len(eligible) == 30 and all(i.startswith("Q") for i in eligible) and eligible == sorted(eligible)
    drawn = cl.draw_cases(run1, run2)
    assert drawn == cl.draw_cases(run1, run2) == ["Q005", "Q012", "Q028", "Q031", "Q049"]   # fixed by the seed
    assert len(set(drawn)) == 5 and set(drawn) <= set(eligible)
    assert cl.draw_cases(run1, run2, seed=1) != drawn                              # the seed matters


def test_the_draw_command_prints_the_questions_word_for_word_and_the_two_refusals(capsys):
    assert cl.cmd_draw() == 0
    out = capsys.readouterr().out
    assert "seed 20261114" in out and "Q-e88f33ea (no_such_section)" in out and "Q-67c8ea25 (foreign_law)" in out
    assert "What does Section 412 of the Nigeria Tax Act 2025 say?" in out
    assert "What is the standard VAT rate in Botswana?" in out


def base_row(**kw):
    row = {"config_sha256": "c" * 64, "retrieval_config_sha256": "r" * 64, "guardrails_policy_sha256": "g" * 64,
           "generator_model": "claude-sonnet-5-5", "verifier_model": "claude-haiku-5-5", "tagger_model": "claude-haiku-5-5",
           "prompt_version": "gen-prompt/9", "schema_version": "gen-schema/2", "tool_defs_version": "tool-defs/2",
           "verifier_prompt_version": "ver-prompt/4", "gap_prompt_version": "gap-prompt/3",
           "versions": {"tagger_effort": "low"}, "flags": [], "pre_check": {"refusal": None, "flags": []},
           "corpus": {"source": "database", "load_id": 3, "chunks_sha256": "h" * 64, "register_sha256": "i" * 64},
           "status": "answered", "refusal_reason": None, "refusal_stage": None, "refusal_text": None}
    row.update(kw)
    return row


CASE = {"id": "Q031", "flags": [], "passages": [{"label": f"P{n}", "chunk_id": f"c{n}"} for n in range(1, 11)]}


def test_class_1_passes_when_every_fixed_field_matches_even_with_another_load_id():
    live = base_row(corpus={"source": "database", "load_id": 1, "chunks_sha256": "h" * 64, "register_sha256": "i" * 64})
    assert cl.class1_answered(live, base_row(), CASE) == []


@pytest.mark.parametrize("field", cl.EXACT_FIELDS)
def test_class_1_names_each_fixed_field_that_differs(field):
    assert cl.class1_answered(base_row(**{field: "other"}), base_row(), CASE) == [f"{field}: differs"]


def test_class_1_also_checks_versions_corpus_hashes_flags_and_the_pre_check():
    base = base_row()
    assert cl.class1_answered(base_row(versions={"tagger_effort": "high"}), base, CASE) == ["versions: differs"]
    assert cl.class1_answered(base_row(corpus={"source": "database", "load_id": 3, "chunks_sha256": "x"}), base, CASE) == [
        "corpus: hashes differ"]
    assert cl.class1_answered(base_row(flags=["ui_fallback"]), base, CASE) == ["flags: differ from the Phase 8 case"]
    assert cl.class1_answered(base_row(pre_check={"refusal": "no_such_section"}), base, CASE) == [
        "pre_check: refused an answered case"]


def test_class_1_for_a_refusal_compares_status_reason_stage_text_pre_check_and_hashes():
    refusal = base_row(status="refused", refusal_reason="foreign_law", refusal_stage="pre_check",
                       refusal_text="This tool covers Nigerian tax law only.",
                       pre_check={"refusal": "foreign_law", "foreign_law": {"fired": True}})
    assert cl.class1_refusal(dict(refusal), refusal) == []
    changed = dict(refusal, refusal_text="Something else.", refusal_reason="no_such_section")
    assert cl.class1_refusal(changed, refusal) == ["refusal_reason: differs", "refusal_text: differs"]
    assert cl.class1_refusal(dict(refusal, pre_check={"refusal": None}), refusal) == ["pre_check: differs"]
    assert cl.class1_refusal(dict(refusal, guardrails_policy_sha256="z"), refusal) == ["guardrails_policy_sha256: differs"]


def live_passages(overrides=None, drop=()):
    ids = {f"P{n}": f"c{n}" for n in range(1, 9)}
    ids.update(overrides or {})
    return [{"label": label, "chunk_id": cid} for label, cid in ids.items() if label not in drop] + [
        {"label": "P9", "chunk_id": "tool-only"}]


def test_class_2_equal_passages_pass_and_p9_onward_is_not_compared():
    assert cl.class2(live_passages(), CASE, parity_passed=False) == ([], [])


def test_class_2_a_difference_is_accepted_only_after_the_parity_check_and_is_always_listed():
    differ = live_passages({"P3": "other"})
    failures, accepted = cl.class2(differ, CASE, parity_passed=False)
    assert failures == ["P3: live other, Phase 8 c3"] and accepted == []
    failures, accepted = cl.class2(differ, CASE, parity_passed=True)
    assert failures == [] and accepted == ["P3: live other, Phase 8 c3"]
    failures, _ = cl.class2(live_passages(drop=("P8",)), CASE, parity_passed=False)
    assert failures == ["P8: live absent, Phase 8 c8"]


def good_data():
    return {"query": {"status": "answered", "rendered_sha256": "R", "query_id": "abcdef12-0000"},
            "claims": [{"claim_no": 1, "shown": True, "verdict": "supported", "kept_chunk_ids": ["nta2025:s186"]},
                       {"claim_no": 2, "shown": False, "verdict": "unsupported", "kept_chunk_ids": []}],
            "passages": [{"chunk_id": "nta2025:s186", "cited": True, "chunk_text_sha256": "S"},
                         {"chunk_id": "nta2025:s190", "cited": False, "chunk_text_sha256": "T"}],
            "deliveries": [{"outcome": "shown", "shown_sha256": "R"}],
            "corpus_text": {"nta2025:s186": {"text_sha256": "S"}}}


def run_class3(data, **kw):
    from retrieval.smoke_query import matches
    args = dict(local_sha={"nta2025:s186": "S"}, gold_units=["nta2025:s186"], matches=matches, start_problems=[])
    args.update(kw)
    return cl.class3(data, **args)


def test_class_3_a_good_answered_row_holds():
    assert run_class3(good_data()) == []


def test_class_3_names_each_failed_requirement():
    d = good_data()
    d["query"]["status"] = "not_covered"
    assert run_class3(d) == ["status is not_covered, expected answered"]
    d = good_data()
    d["claims"][0].update(verdict="unsupported", kept_chunk_ids=[])
    assert run_class3(d) == ["claim 1: verdict unsupported, expected supported", "claim 1: no kept citation"]
    d = good_data()
    d["claims"] = []
    assert run_class3(d) == ["no shown claim"]
    assert run_class3(good_data(), local_sha={"nta2025:s186": "DIFFERENT"}) == [
        "nta2025:s186: chunk_text_sha256 differs from the committed chunk text",
        "nta2025:s186: hosted text sha differs from the committed chunk text"]
    d = good_data()
    d["corpus_text"] = {}
    assert run_class3(d) == ["nta2025:s186: not in the hosted corpus"]
    d["corpus_text"] = None
    assert run_class3(d) == ["nta2025:s186: the corpus is not readable by the reviewer"]
    assert run_class3(good_data(), gold_units=["nta2025:s999"]) == ["no cited chunk matches the gold unit"]
    d = good_data()
    d["deliveries"] = [{"outcome": "shown", "shown_sha256": "OTHER"}]
    assert run_class3(d) == ["no shown delivery whose shown_sha256 equals rendered_sha256"]
    d["deliveries"] = [{"outcome": "shown_plain", "shown_sha256": "R"}]
    assert run_class3(d) == ["no shown delivery whose shown_sha256 equals rendered_sha256"]
    assert run_class3(good_data(), start_problems=["git_commit does not match the approved commit"]) == [
        "app start: git_commit does not match the approved commit"]


def test_a_subsection_chunk_matches_its_gold_section():
    d = good_data()
    d["passages"][0]["chunk_id"] = "nta2025:s186(1)-(2)"
    d["corpus_text"] = {"nta2025:s186(1)-(2)": {"text_sha256": "S"}}
    assert run_class3(d, local_sha={"nta2025:s186(1)-(2)": "S"}) == []


def test_class_3_on_a_real_answered_row_from_the_audit_schema(audit_db, tmp_path):
    """The columns class 3 reads exist and mean what it assumes: a row written by the real functions."""
    from audit.report import fetch_query
    from retrieval.smoke_query import matches
    svc = AppService(store=audit_db.store, gate=__import__("interface.limits", fromlist=["x"]).LocalGate(),
                     start_id=audit_db.start_id(), config=shipped_config(),
                     chunk_hashes=lambda ids: {i: HASHES[i] for i in ids}, generate_fn=FakeGenerate(), jsonl_dir=None)
    sub = svc.submit(T.E01, __import__("interface.identity", fromlist=["x"]).LOCAL, "s")
    outcome = svc.wait(sub, timeout=30)
    svc.deliver(sub.query_id, "shown", outcome.shown_text)
    data = fetch_query(audit_db.conn, sub.query_id, audit_db.names.schema)
    cited = [p["chunk_id"] for p in data["passages"] if p["cited"]]
    assert cited
    assert run_class3(data, local_sha=HASHES, gold_units=[cited[0]], matches=matches) == []
    tampered = dict(HASHES, **{cited[0]: "0" * 64})
    problems = run_class3(data, local_sha=tampered, gold_units=[cited[0]], matches=matches)
    assert f"{cited[0]}: chunk_text_sha256 differs from the committed chunk text" in problems


def test_check_answered_and_check_refusal_combine_the_classes_without_printing_text():
    from retrieval.smoke_query import matches
    live = dict(good_data(), passages=[{"label": f"P{n}", "chunk_id": f"c{n}", "cited": n == 1,
                                        "chunk_text_sha256": "S"} for n in range(1, 9)])
    live["query"] = dict(good_data()["query"], **base_row())
    live["query"]["query_id"] = "abcdef12-3456"
    live["corpus_text"] = {"c1": {"text_sha256": "S"}}
    baseline = {"query": base_row()}
    result = cl.check_answered(live, baseline, CASE, local_sha={"c1": "S"}, gold_units=["c1"], matches=matches,
                               start_problems=[], parity_passed=False)
    assert result.failures == [] and result.kind == "Q031" and result.reference == "abcdef12"
    live["passages"][2]["chunk_id"] = "other"
    result = cl.check_answered(live, baseline, CASE, local_sha={"c1": "S"}, gold_units=["c1"], matches=matches,
                               start_problems=["outcome is failed, expected ok"], parity_passed=True)
    assert result.failures == ["app start: outcome is failed, expected ok"] and result.accepted == [
        "P3: live other, Phase 8 c3"]
    refusal = {"query": base_row(status="refused", refusal_reason="foreign_law", refusal_stage="pre_check",
                                 refusal_text="x", query_id="12345678-0")}
    assert cl.check_refusal(refusal, refusal, []).failures == []


def test_pairs_are_parsed_and_a_malformed_one_is_a_usage_error(capsys):
    assert cl.parse_pairs(["Q-1a2b3c4d=Q031", "Q-5e6f7a8b=Q-e88f33ea"]) == [("Q-1a2b3c4d", "Q031"), ("Q-5e6f7a8b", "Q-e88f33ea")]
    for bad in ("Q-1a2b3c4d", "=Q031", "Q-1a2b3c4d="):
        with pytest.raises(ValueError):
            cl.parse_pairs([bad])
    assert cl.main(["check", "--commit", COMMIT, "--case", "nonsense"]) == 2
    assert cl.main(["check", "--commit", COMMIT]) in (2,)                        # nothing to check, or no connection
    capsys.readouterr()


def test_the_phase_8_and_phase_9_inputs_the_script_reads_exist_with_the_fields_it_uses():
    run1, run2 = run_files()
    case = cl.case_of(run1, "Q031")
    assert {"id", "subset", "status", "flags", "passages"} <= set(case) and case["passages"][0].keys() >= {"label", "chunk_id"}
    labels = {r["id"]: r for p in cl.LABELS for r in cl.read_jsonl(p)}
    assert labels["Q031"]["gold_units"]
    records = {r["reference"] for r in cl.read_json(cl.LIVE_CHECK)["results"]}
    assert set(cl.REFUSAL_REFERENCES) <= records and cl.DEFAULT_BASELINE_REF in records
    questions = {q["id"] for q in cl.read_jsonl(cl.QUESTIONS)}
    assert set(cl.draw_cases(run1, run2)) <= questions
