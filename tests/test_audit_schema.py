"""The audit schema, its migrations and its permissions (interface design, Sections 4.2, 4.3, 4.6 and 10).

Against the local container, on throwaway schemas test_audit_<hex> and throwaway NOLOGIN roles that
are removed afterwards. Fails, not skips, when the database is down. No paid call."""
import shutil
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import errors

from audit import migrate as mig
from audit.migrate import APP_FUNCTIONS, OWNER_ONLY_FUNCTIONS, TABLES, MigrationError, Names
from audit_testlib import audit_db, db_connect, drop_role, no_paid_clients  # noqa: F401
from check_pgvector import connect

TS = "timestamp with time zone"
# Section 4.3, column by column: (name, type, NOT NULL).
COLUMNS = {
    "schema_migrations": [("version", "integer", True), ("name", "text", True), ("sha256", "text", True),
                          ("applied_at", TS, True)],
    "app_starts": [("start_id", "bigint", True), ("started_at", TS, True), ("deployment", "text", True),
                   ("git_commit", "text", False), ("app_version", "text", True), ("audit_schema", "text", True),
                   ("python_version", "text", True), ("streamlit_version", "text", True),
                   ("config_file", "text", True), ("config_sha256", "text", True), ("corpus_load_id", "bigint", False),
                   ("corpus_snapshot", "date", False), ("chunks_sha256", "text", False), ("outcome", "text", True),
                   ("problems", "text[]", True)],
    "users": [("user_key", "text", True), ("provider", "text", True), ("tier", "text", True),
              ("first_seen", TS, True), ("last_seen", TS, True)],
    "queries": [("query_id", "uuid", True), ("record_id", "uuid", False), ("start_id", "bigint", True),
                ("state", "text", True), ("received_at", TS, True), ("finished_at", TS, False), ("tier", "text", True),
                ("user_key", "text", False), ("session_key", "text", True), ("question", "text", True),
                ("question_chars", "integer", True), ("cost_reserved_usd", "numeric(8,4)", True),
                ("status", "text", False), ("refusal_flag", "boolean", False), ("refusal_reason", "text", False),
                ("refusal_stage", "text", False), ("refusal_text", "text", False), ("error", "text", False),
                ("app_error", "text", False), ("flags", "text[]", True), ("log_schema", "text", False),
                ("config_file", "text", False), ("config_sha256", "text", False),
                ("retrieval_config_sha256", "text", False), ("guardrails_policy_sha256", "text", False),
                ("corpus", "jsonb", False), ("generator_model", "text", False), ("prompt_version", "text", False),
                ("schema_version", "text", False), ("tool_defs_version", "text", False),
                ("verifier_model", "text", False), ("verifier_prompt_version", "text", False),
                ("gap_prompt_version", "text", False), ("tagger_model", "text", False), ("versions", "jsonb", False),
                ("pre_check", "jsonb", False), ("relevance_floor", "jsonb", False), ("note_tagger", "jsonb", False),
                ("notes", "jsonb", False), ("disclosures", "jsonb", False), ("parse", "jsonb", False),
                ("generator_output", "text", False), ("verification_summary", "jsonb", False),
                ("tool_uses", "jsonb", False), ("model_calls", "jsonb", False), ("rendered", "text", False),
                ("rendered_sha256", "text", False), ("usage", "jsonb", False),
                ("generation_cost_usd", "numeric(10,6)", False), ("verification_cost_usd", "numeric(10,6)", False),
                ("tagger_cost_usd", "numeric(10,6)", False), ("total_cost_usd", "numeric(10,6)", False),
                ("latency_ms", "integer", False), ("gen_log_file", "text", False)],
    "passages": [("query_id", "uuid", True), ("label", "text", True), ("rank", "smallint", True),
                 ("chunk_id", "text", True), ("citation", "text", True), ("context_header", "text", True),
                 ("origin", "text", True), ("score", "double precision", False),
                 ("distance", "double precision", False), ("reasons", "text[]", True), ("disputed", "boolean", True),
                 ("cited", "boolean", True), ("supporting", "boolean", True), ("chunk_text_sha256", "text", True)],
    "claims": [("query_id", "uuid", True), ("claim_no", "smallint", True), ("text", "text", True),
               ("cited_labels", "text[]", True), ("cited_chunk_ids", "text[]", True), ("rule", "jsonb", True),
               ("llm_verdict", "text", False), ("second", "jsonb", False), ("verdict", "text", True),
               ("reason", "text", False), ("sub_reason", "text", False), ("unsupported_part", "text", False),
               ("reasoning", "text", False), ("evidence", "jsonb", True), ("citations", "jsonb", True),
               ("kept_chunk_ids", "text[]", True), ("action", "text", True), ("shown", "boolean", True),
               ("call_error", "text", False), ("cost_usd", "numeric(10,6)", False), ("latency_ms", "integer", False)],
    "gaps": [("query_id", "uuid", True), ("gap_no", "smallint", True), ("text", "text", True),
             ("origin", "text", False), ("class", "text", False), ("kept", "boolean", True), ("reason", "text", False),
             ("shown", "boolean", True)],
    "computations": [("query_id", "uuid", True), ("calc_id", "text", True), ("scenario", "text", True),
                     ("status", "text", True), ("inputs", "jsonb", True), ("input_matches", "jsonb", True),
                     ("constants", "jsonb", True), ("relies_on", "text[]", True), ("steps", "jsonb", True),
                     ("rounding", "text", False), ("result", "text", False), ("result_kind", "text", False),
                     ("result_text", "text", False), ("what", "text", False), ("from_question", "text[]", True),
                     ("assumptions", "text[]", True), ("working", "text[]", True), ("not_computed", "text[]", True),
                     ("declined_code", "text", False), ("declined_reason", "text", False), ("sources", "text[]", True),
                     ("listed", "boolean", True), ("checks", "jsonb", True), ("shown", "boolean", True),
                     ("withheld_reason", "text", False), ("calc_version", "text", True),
                     ("rates_sha256", "text", True)],
    "deliveries": [("delivery_id", "bigint", True), ("query_id", "uuid", True), ("at", TS, True),
                   ("outcome", "text", True), ("shown_sha256", "text", False)],
    "feedback": [("feedback_id", "bigint", True), ("query_id", "uuid", True), ("record_id", "uuid", False),
                 ("at", TS, True), ("session_key", "text", True), ("rating", "smallint", True),
                 ("reason", "text", False)],
    "admission_counts": [("day", "date", True), ("tier", "text", True), ("reason", "text", True),
                         ("n", "integer", True)],
}
CHECKS = {
    "app_starts": ["CHECK ((deployment = ANY (ARRAY['local'::text, 'hosted'::text, 'test'::text])))",
                   "CHECK ((outcome = ANY (ARRAY['ok'::text, 'failed'::text])))"],
    "claims": ["CHECK ((action = ANY (ARRAY['kept'::text, 'removed'::text, 'moved_not_rendered'::text])))",
               "CHECK ((verdict = ANY (ARRAY['supported'::text, 'unsupported'::text, 'contradicted'::text, "
               "'not_a_claim'::text, 'unverified'::text])))"],
    "deliveries": ["CHECK ((outcome = ANY (ARRAY['shown'::text, 'shown_plain'::text, 'withheld_log_failure'::text, "
                   "'ui_timeout'::text, 'shown_in_history'::text])))"],
    "feedback": ["CHECK ((rating = ANY (ARRAY['-1'::integer, 1])))",
                 "CHECK ((reason = ANY (ARRAY['wrong_citation'::text, 'missing_information'::text, "
                 "'should_have_answered'::text, 'should_not_have_answered'::text, 'hard_to_read'::text, "
                 "'other'::text])))"],
    "passages": ["CHECK ((origin = ANY (ARRAY['retrieval'::text, 'attachment'::text, 'lookup'::text, "
                 "'calculation_source'::text])))"],
    "queries": ["CHECK ((state = ANY (ARRAY['received'::text, 'final'::text])))",
                "CHECK (((state = 'received'::text) OR ((status IS NOT NULL) AND (finished_at IS NOT NULL))))",
                "CHECK ((status = ANY (ARRAY['answered'::text, 'not_covered'::text, 'refused'::text, 'error'::text, "
                "'app_error'::text])))",
                "CHECK ((((status = 'app_error'::text) = (record_id IS NULL)) OR (state = 'received'::text)))",
                "CHECK ((tier = ANY (ARRAY['local'::text, 'anonymous'::text, 'signed_in'::text, 'reviewer'::text])))",
                "CHECK (((tier = ANY (ARRAY['signed_in'::text, 'reviewer'::text])) = (user_key IS NOT NULL)))"],
    "users": ["CHECK ((tier = ANY (ARRAY['signed_in'::text, 'reviewer'::text])))"],
}
INDEXES = {
    "CREATE UNIQUE INDEX admission_counts_pkey ON {s}.admission_counts USING btree (day, tier, reason)",
    "CREATE UNIQUE INDEX app_starts_pkey ON {s}.app_starts USING btree (start_id)",
    "CREATE UNIQUE INDEX claims_pkey ON {s}.claims USING btree (query_id, claim_no)",
    "CREATE UNIQUE INDEX computations_pkey ON {s}.computations USING btree (query_id, calc_id)",
    "CREATE UNIQUE INDEX deliveries_pkey ON {s}.deliveries USING btree (delivery_id)",
    "CREATE UNIQUE INDEX feedback_pkey ON {s}.feedback USING btree (feedback_id)",
    "CREATE UNIQUE INDEX feedback_query_id_session_key_key ON {s}.feedback USING btree (query_id, session_key)",
    "CREATE UNIQUE INDEX gaps_pkey ON {s}.gaps USING btree (query_id, gap_no)",
    "CREATE UNIQUE INDEX passages_pkey ON {s}.passages USING btree (query_id, label)",
    "CREATE INDEX queries_received_idx ON {s}.queries USING btree (received_at)",
    "CREATE INDEX queries_session_idx ON {s}.queries USING btree (session_key, received_at)",
    "CREATE INDEX queries_status_idx ON {s}.queries USING btree (status, refusal_reason)",
    "CREATE INDEX queries_user_idx ON {s}.queries USING btree (user_key, received_at) WHERE (user_key IS NOT NULL)",
    "CREATE UNIQUE INDEX queries_pkey ON {s}.queries USING btree (query_id)",
    "CREATE UNIQUE INDEX queries_record_id_key ON {s}.queries USING btree (record_id)",
    "CREATE UNIQUE INDEX schema_migrations_pkey ON {s}.schema_migrations USING btree (version)",
    "CREATE UNIQUE INDEX users_pkey ON {s}.users USING btree (user_key)",
}
# Foreign keys and their delete actions: passage, claim, gap, computation, delivery and feedback rows
# go with their query; a query goes with its user; a corpus reload never touches the audit (no FK).
FOREIGN_KEYS = {("queries", "app_starts", "a"), ("queries", "users", "c"), ("passages", "queries", "c"),
                ("claims", "queries", "c"), ("gaps", "queries", "c"), ("computations", "queries", "c"),
                ("deliveries", "queries", "c"), ("feedback", "queries", "c")}


@contextmanager
def as_role(role: str):
    """A fresh autocommit connection running as `role` (SET ROLE from the owner)."""
    conn = db_connect()
    conn.autocommit = True
    try:
        conn.execute(f"SET ROLE {role}")
        yield conn
    finally:
        conn.close()


def null_call(schema: str, signature: str) -> str:
    """`SELECT schema.name(NULL::t1, ...)` for a signature as PostgreSQL prints it."""
    name, args = signature.split("(", 1)
    args = [a.strip() for a in args.rstrip(")").split(",") if a.strip()]
    return f"SELECT {schema}.{name}({', '.join(f'NULL::{a}' for a in args)})"


def all_functions(db) -> list[str]:
    return [sig for sig, _, _ in mig.function_privileges(db.conn, db.names.schema, ())]


def write_one_query(db) -> str:
    """An open_query and close_query pair with one passage and one claim, through the functions."""
    start = db.start_id()
    query_id = str(uuid.uuid4())
    admission = db.store.open_query(query_id=query_id, start_id=start, tier="local", user_key=None, session_key="s1",
                                    question="What is the VAT rate?", reserve_usd="0.05", limits={})
    assert admission.admitted
    db.store.close_query(query_id, {
        "question": "What is the VAT rate?",
        "query": {"record_id": str(uuid.uuid4()), "status": "answered", "rendered": "x", "flags": []},
        "passages": [{"label": "P1", "rank": 1, "chunk_id": "nta2025:s148", "citation": "c", "context_header": "h",
                      "origin": "retrieval", "score": 0.5, "distance": 0.5, "reasons": ["vector rank 1"],
                      "disputed": False, "cited": True, "supporting": True, "chunk_text_sha256": "a" * 64}],
        "claims": [{"claim_no": 1, "text": "t", "cited_labels": ["P1"], "cited_chunk_ids": ["nta2025:s148"],
                    "rule": {}, "verdict": "supported", "evidence": [], "citations": [],
                    "kept_chunk_ids": ["nta2025:s148"], "action": "kept", "shown": True}]})
    return query_id


# Migrations ----------------------------------------------------------------------------------------


def test_migrations_apply_once_and_reapply_as_a_no_op(audit_db):
    assert mig.migrate(audit_db.conn, audit_db.names) == []
    recorded = audit_db.conn.execute(
        f"SELECT version, name, sha256 FROM {audit_db.q('schema_migrations')} ORDER BY version").fetchall()
    assert recorded == [(m.version, m.name, m.sha256) for m in mig.migration_files()]
    assert [v for v, _, _ in recorded] == list(mig.SCHEMA_VERSIONS[mig.SCHEMA_LABEL])
    assert mig.check_schema(audit_db.conn, audit_db.names) == []


def test_an_edited_applied_file_stops_the_runner(audit_db, tmp_path):
    copy = tmp_path / "migrations"
    shutil.copytree(mig.MIGRATIONS_DIR, copy)
    path = copy / "0002_functions.sql"
    path.write_text(path.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="edited after it was applied"):
        mig.migrate(audit_db.conn, audit_db.names, copy)
    assert "audit_migration_sha256" in mig.check_schema(audit_db.conn, audit_db.names, directory=copy)


def test_migration_files_must_be_numbered_without_gaps(tmp_path):
    shutil.copytree(mig.MIGRATIONS_DIR, tmp_path / "m")
    (tmp_path / "m" / "0006_late.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="no gap"):
        mig.migration_files(tmp_path / "m")


@pytest.mark.parametrize("names", [dict(schema="public"), dict(schema="Audit"), dict(schema="a; DROP"),
                                   dict(app_role="x", review_role="x"), dict(schema="pg_audit")])
def test_template_names_are_checked_identifiers(names):
    with pytest.raises(MigrationError):
        Names(**names)


def test_an_unknown_template_token_is_an_error():
    with pytest.raises(MigrationError, match="unknown template token"):
        mig.render("SELECT '{{owner}}';", Names())


def test_an_existing_role_that_bypasses_rls_is_refused():
    conn = db_connect()
    conn.autocommit = True
    hex_ = uuid.uuid4().hex[:10]
    names = Names(f"test_audit_{hex_}", f"test_app_{hex_}", f"test_rev_{hex_}")
    try:
        conn.execute(f"CREATE ROLE {names.app_role} NOLOGIN BYPASSRLS")
        with pytest.raises(psycopg.Error, match="not a plain NOLOGIN group role"):
            mig.migrate(conn, names)
        assert sorted(mig.applied(conn, names.schema)) == [1, 2]  # 0003 rolled back as a whole
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {names.schema} CASCADE")
        for role in (names.app_role, names.review_role):
            drop_role(conn, role)
        conn.close()


def test_the_group_roles_are_nologin_and_not_superusers(audit_db):
    rows = audit_db.conn.execute(
        "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
        "WHERE rolname IN (%s, %s) ORDER BY rolname", (audit_db.names.app_role, audit_db.names.review_role)).fetchall()
    assert [r[1:] for r in rows] == [(False,) * 5] * 2


# Columns, checks, indexes, keys -----------------------------------------------------------------------


def test_columns_match_the_design(audit_db):
    rows = audit_db.conn.execute(
        "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid WHERE c.relnamespace = %s::regnamespace AND c.relkind = 'r' "
        "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY c.relname, a.attnum", (audit_db.names.schema,)).fetchall()
    actual: dict[str, list] = {}
    for table, column, type_, notnull in rows:
        actual.setdefault(table, []).append((column, type_, notnull))
    assert actual == COLUMNS
    assert sorted(COLUMNS) == sorted(TABLES)


def test_checks_indexes_and_foreign_keys_match_the_design(audit_db):
    s = audit_db.names.schema
    checks: dict[str, list] = {}
    for table, definition in audit_db.conn.execute(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = %s::regnamespace AND contype = 'c' ORDER BY 1, 2", (s,)).fetchall():
        checks.setdefault(table.replace(f"{s}.", ""), []).append(definition)
    assert {t: sorted(d) for t, d in checks.items()} == {t: sorted(d) for t, d in CHECKS.items()}
    indexes = {d.replace(f"{s}.", "{s}.") for (d,) in audit_db.conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = %s", (s,)).fetchall()}
    assert indexes == INDEXES
    keys = {(a.replace(f"{s}.", ""), b.replace(f"{s}.", ""), action) for a, b, action in audit_db.conn.execute(
        "SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype FROM pg_constraint "
        "WHERE connamespace = %s::regnamespace AND contype = 'f'", (s,)).fetchall()}
    assert keys == FOREIGN_KEYS


# Row-level security and policies ----------------------------------------------------------------------


def test_rls_is_on_everywhere_and_the_only_policy_is_the_reviewers_select(audit_db):
    s, review = audit_db.names.schema, audit_db.names.review_role
    tables = audit_db.conn.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relnamespace = %s::regnamespace AND relkind = 'r' ORDER BY 1", (s,)).fetchall()
    assert tables == [(t, True, False) for t in sorted(TABLES)]
    policies = audit_db.conn.execute(
        "SELECT tablename, policyname, permissive, roles::text[], cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = %s ORDER BY 1", (s,)).fetchall()
    assert policies == [(t, "review_select", "PERMISSIVE", [review], "SELECT", "true", None) for t in sorted(TABLES)]


def test_a_role_with_select_but_no_policy_sees_no_rows(audit_db):
    """The Supabase API roles' case: even a table grant shows nothing without a policy."""
    write_one_query(audit_db)
    role = audit_db.role("none")
    audit_db.conn.execute(f"CREATE ROLE {role} NOLOGIN")
    audit_db.conn.execute(f"GRANT USAGE ON SCHEMA {audit_db.names.schema} TO {role}")
    audit_db.conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {audit_db.names.schema} TO {role}")
    with as_role(role) as conn:
        for table in TABLES:
            assert conn.execute(f"SELECT count(*) FROM {audit_db.q(table)}").fetchone()[0] == 0, table


# Grants under SET ROLE --------------------------------------------------------------------------------


def test_the_app_role_cannot_read_or_write_any_table(audit_db):
    query_id = write_one_query(audit_db)
    with as_role(audit_db.names.app_role) as conn:
        for table in TABLES:
            for statement in (f"SELECT * FROM {audit_db.q(table)}",
                              f"INSERT INTO {audit_db.q(table)} DEFAULT VALUES",
                              f"UPDATE {audit_db.q(table)} SET {COLUMNS[table][1][0]} = NULL",
                              f"DELETE FROM {audit_db.q(table)}",
                              f"TRUNCATE {audit_db.q(table)}"):
                with pytest.raises(errors.InsufficientPrivilege):
                    conn.execute(statement)
    assert audit_db.conn.execute(f"SELECT state FROM {audit_db.q('queries')} WHERE query_id = %s",
                                 (query_id,)).fetchone() == ("final",)


def test_the_reviewer_reads_a_row_written_through_the_functions_and_cannot_write(audit_db):
    query_id = write_one_query(audit_db)
    with as_role(audit_db.names.review_role) as conn:
        assert conn.execute(f"SELECT state, status FROM {audit_db.q('queries')} WHERE query_id = %s",
                            (query_id,)).fetchone() == ("final", "answered")
        assert conn.execute(f"SELECT label FROM {audit_db.q('passages')} WHERE query_id = %s",
                            (query_id,)).fetchall() == [("P1",)]
        assert conn.execute(f"SELECT verdict, shown FROM {audit_db.q('claims')} WHERE query_id = %s",
                            (query_id,)).fetchall() == [("supported", True)]
        assert conn.execute(f"SELECT count(*) FROM {audit_db.q('app_starts')}").fetchone()[0] == 1
        for table in TABLES:
            for statement in (f"INSERT INTO {audit_db.q(table)} DEFAULT VALUES",
                              f"UPDATE {audit_db.q(table)} SET {COLUMNS[table][1][0]} = NULL",
                              f"DELETE FROM {audit_db.q(table)}"):
                with pytest.raises(errors.InsufficientPrivilege):
                    conn.execute(statement)
        for signature in all_functions(audit_db):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(null_call(audit_db.names.schema, signature))


def test_a_role_with_no_grants_cannot_execute_any_function_and_sees_nothing(audit_db):
    write_one_query(audit_db)
    role = audit_db.role("none")
    audit_db.conn.execute(f"CREATE ROLE {role} NOLOGIN")
    functions = all_functions(audit_db)
    assert len(functions) == len(APP_FUNCTIONS) + len(OWNER_ONLY_FUNCTIONS)
    with as_role(role) as conn:
        for signature in functions:
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(null_call(audit_db.names.schema, signature))
        for table in TABLES:
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM {audit_db.q(table)}")
    # With schema USAGE (as if exposed by mistake), EXECUTE is still refused on every function.
    audit_db.conn.execute(f"GRANT USAGE ON SCHEMA {audit_db.names.schema} TO {role}")
    with as_role(role) as conn:
        for signature in functions:
            with pytest.raises(errors.InsufficientPrivilege, match="permission denied for function"):
                conn.execute(null_call(audit_db.names.schema, signature))


def test_the_app_role_executes_exactly_its_eleven_functions(audit_db):
    names = audit_db.names
    privileges = mig.function_privileges(audit_db.conn, names.schema, (names.app_role,))
    assert sorted(sig for sig, _, may in privileges if may[names.app_role]) == sorted(APP_FUNCTIONS)
    assert len(APP_FUNCTIONS) == 11
    with as_role(names.app_role) as conn:
        assert conn.execute(f"SELECT {names.schema}.spend_since(now() - interval '1 day')").fetchone()[0] == 0
        for signature in ("purge_expired(integer)", "erase_query(uuid)"):
            with pytest.raises(errors.InsufficientPrivilege, match="permission denied for function"):
                conn.execute(null_call(names.schema, signature))
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(f"SELECT {names.schema}.purge_expired(400)")


def test_check_schema_passes_as_the_app_role_which_cannot_read_the_migrations_table(audit_db):
    """The hosted app runs as its own login with no audit table privilege (D-210): the start-up check's schema
    check must pass as that role, reading the versions through schema_versions(), not the table."""
    names = audit_db.names
    with as_role(names.app_role) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(f"SELECT version FROM {audit_db.q('schema_migrations')}")
        assert mig.check_schema(conn, names) == []
        assert mig.recorded_versions(conn, names.schema) == mig.applied(audit_db.conn, names.schema)


def test_schema_versions_returns_version_name_and_sha256_only_and_the_reviewer_cannot_call_it(audit_db):
    names = audit_db.names
    cur = audit_db.conn.execute(f"SELECT * FROM {names.schema}.schema_versions()")
    assert [c.name for c in cur.description] == ["version", "name", "sha256"]
    assert cur.fetchall() == [(m.version, m.name, m.sha256) for m in mig.migration_files()]
    with as_role(names.review_role) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT * FROM {names.schema}.schema_versions()")


def test_check_schema_reports_a_schema_without_the_versions_function(audit_db):
    audit_db.conn.execute(f"DROP FUNCTION {audit_db.names.schema}.schema_versions()")
    problems = mig.check_schema(audit_db.conn, audit_db.names)
    assert "audit_schema_version" in problems and "audit_app_grants" in problems


def test_every_function_is_security_definer_with_a_pinned_search_path(audit_db):
    privileges = mig.function_privileges(audit_db.conn, audit_db.names.schema, ())
    assert privileges and all(pinned for _, pinned, _ in privileges)


def test_public_cannot_execute_any_function_including_one_added_later(audit_db, tmp_path):
    names = audit_db.names
    copy = tmp_path / "migrations"
    shutil.copytree(mig.MIGRATIONS_DIR, copy)
    (copy / "0005_later.sql").write_text(
        "CREATE FUNCTION later_one() RETURNS integer LANGUAGE sql SECURITY DEFINER "
        "SET search_path = {{schema}}, pg_catalog, pg_temp AS 'SELECT 1';\n", encoding="utf-8")
    assert mig.migrate(audit_db.conn, names, copy) == [5]
    privileges = mig.function_privileges(audit_db.conn, names.schema, ("public", names.app_role, names.review_role))
    assert "later_one()" in [sig for sig, _, _ in privileges]
    assert not any(may["public"] for _, _, may in privileges)
    later = next(may for sig, _, may in privileges if sig == "later_one()")
    assert later == {"public": False, names.app_role: False, names.review_role: False}
    with as_role(names.app_role) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT {names.schema}.later_one()")


def test_the_per_schema_default_privilege_alone_does_not_revoke_public(audit_db):
    """Why the runner revokes after every migration (Build A correction to D-151): a function created
    outside the runner still starts out executable by PUBLIC, despite 0003's per-schema default."""
    s = audit_db.names.schema
    audit_db.conn.execute(f"CREATE FUNCTION {s}.outside_runner() RETURNS integer LANGUAGE sql AS 'SELECT 1'")
    assert audit_db.conn.execute(
        f"SELECT has_function_privilege('public', '{s}.outside_runner()', 'EXECUTE')").fetchone()[0] is True
    assert "audit_public_execute" in mig.check_schema(audit_db.conn, audit_db.names)


def test_check_schema_reports_a_missing_or_partial_schema():
    conn = db_connect()
    conn.autocommit = True
    hex_ = uuid.uuid4().hex[:10]
    names = Names(f"test_audit_{hex_}", f"test_app_{hex_}", f"test_rev_{hex_}")
    try:
        assert mig.check_schema(conn, names) == ["audit_schema_missing"]
    finally:
        conn.close()


def test_dropping_a_throwaway_schema_leaves_no_default_privileges_behind(audit_db):
    oid = audit_db.conn.execute("SELECT %s::regnamespace::oid", (audit_db.names.schema,)).fetchone()[0]
    with connect() as other:
        other.autocommit = True
        other.execute(f"DROP SCHEMA {audit_db.names.schema} CASCADE")
        assert other.execute("SELECT count(*) FROM pg_default_acl WHERE defaclnamespace = %s",
                             (oid,)).fetchone()[0] == 0
