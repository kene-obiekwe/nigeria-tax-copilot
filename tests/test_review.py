"""The log viewer, the report, show_query.py, compare_log.py and the reviewer grant (interface design,
Sections 5, 10 and 11; D-169).

Queries are written through the real service on a throwaway audit schema (fake clients, the Build A
scenarios); the viewer and the CLIs read them back. No key, no paid call. The corpus grant of D-169 is
applied only to a throwaway schema and a throwaway role, never to the real corpus tables."""
import copy
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_tools as T  # noqa: E402
from app import copy as ui  # noqa: E402
from app import review as review_app  # noqa: E402
from app.cards import make_parity  # noqa: E402
from audit import compare_log, grant_review, report as rpt, show_query  # noqa: E402
from audit_testlib import audit_db, db_connect, drop_role, no_paid_clients  # noqa: E402,F401
from check_pgvector import connect  # noqa: E402
from interface.identity import LOCAL  # noqa: E402
from interface.limits import LocalGate  # noqa: E402
from interface.service import AppService  # noqa: E402
from interface.startup import shipped_config  # noqa: E402
from test_app import QUESTIONS, ScenarioGenerate, file_chunks  # noqa: E402
from test_app_service import HASHES  # noqa: E402
from test_audit_write import ALL, EXPECTED  # noqa: E402

REVIEW = str(ROOT / "app" / "review.py")


def service_for(audit_db, tmp_path, generate_fn):
    return AppService(store=audit_db.store, gate=LocalGate(), start_id=audit_db.start_id(), config=shipped_config(),
                      chunk_hashes=lambda ids: {i: HASHES[i] for i in ids}, generate_fn=generate_fn,
                      jsonl_dir=tmp_path / "jsonl", close_retry_delay_s=0.01, parity=make_parity(file_chunks))


def write_query(audit_db, tmp_path, monkeypatch, name, *, deliver=True):
    """One question through the real service; returns (query_id, outcome)."""
    svc = service_for(audit_db, tmp_path, ScenarioGenerate(name, monkeypatch))
    sub = svc.submit(QUESTIONS[name], LOCAL, "session-review")
    out = svc.wait(sub, timeout=60)
    assert out.kind == "final", name
    if deliver:
        svc.deliver(sub.query_id, "shown", out.shown_text)
    return sub.query_id, out


def report_of(audit_db, query_id):
    return rpt.build_report(rpt.fetch_query(audit_db.conn, query_id, audit_db.names.schema))


# The report: the eight items of the exit gate -------------------------------------------------------------------


def test_the_report_holds_every_item_of_the_gate_for_an_answered_calculation(audit_db, tmp_path, monkeypatch):
    query_id, out = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    report = report_of(audit_db, query_id)
    h = report["header"]
    assert h["reference"] == "Q-" + query_id.replace("-", "")[:8] and h["record_id"] == out.answer.record_id
    assert h["status"] == "answered" and h["models"]["generator"] and h["models"]["verifier"]
    assert h["prompts"]["generation"] == "gen-prompt/9" and h["tier"] == "local"
    assert report["question"] == T.E01
    assert [p["label"] for p in report["passages"]] == [p.label for p in out.answer.passages]       # item 2
    assert all(p["text_available"] and p["text"] for p in report["passages"])
    assert any(p["origin"] == "calculation_source" for p in report["passages"])
    assert report["checks"]["pre_check"] is not None and report["checks"]["floor_line"]               # item 3
    claims = report["verification"]["claims"]                                                          # item 4
    assert [(c["verdict"], c["action"], c["shown"]) for c in claims] == [("supported", "kept", True),
                                                                          ("unsupported", "removed", False)]
    assert claims[0]["rule"]["tokens"] is not None and claims[0]["llm_verdict"] == "supported"
    assert claims[0]["kept_chunk_ids"] and claims[1]["kept_chunk_ids"] == []
    comps = report["tools"]["computations"]                                                            # item 6
    assert [(c["calc_id"], c["shown"], c["withheld_reason"]) for c in comps] == [("C1", True, None), ("C2", False, "not_listed")]
    assert set(comps[0]["checks"]) == {"K1", "K2", "K3", "K4", "K5", "K6"}
    saw = report["user_saw"]                                                                           # item 8
    assert saw["rendered"] == out.answer.rendered
    assert [(d["outcome"], d["matches_rendered"]) for d in saw["deliveries"]] == [("shown", True)]


def test_evidence_quotes_are_found_and_highlighted_inside_the_cited_chunk_text(audit_db, tmp_path, monkeypatch):
    query_id, _ = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    claim = report_of(audit_db, query_id)["verification"]["claims"][0]
    (ev,) = claim["evidence"]
    assert ev["found"] is True
    hits = [text for text, hit in ev["segments"] if hit]
    assert len(hits) == 1 and " ".join(hits[0].split()).lower() == " ".join(ev["quote"].split()).lower()
    assert "".join(text for text, _ in ev["segments"]).count(hits[0]) >= 1
    md = rpt.render_markdown(report_of(audit_db, query_id))
    assert f"[[{hits[0]}]]" in md and "found in the cited chunk text" in md


def test_highlight_collapses_whitespace_like_the_evidence_check():
    segments = rpt.highlight("A company shall\npay  the tax\non profits.", ["pay the tax on"])
    assert [(t, h) for t, h in segments if h] == [("pay  the tax\non", True)]
    assert "".join(t for t, _ in segments) == "A company shall\npay  the tax\non profits."
    assert rpt.highlight("no match here", ["absent words"]) == [("no match here", False)]
    assert rpt.highlight("abc def", ["abc de", "c def"]) == [("abc def", True)]   # overlapping spans merge


def test_hidden_disclosures_are_marked_not_shown_to_the_user(audit_db, tmp_path, monkeypatch):
    query_id, _ = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    data = rpt.fetch_query(audit_db.conn, query_id, audit_db.names.schema)
    data["query"]["disclosures"] = [{"kind": "hidden", "ref": "DR-001", "text": "hidden text"},
                                    {"kind": "register", "ref": "DR-002", "text": "shown text"}]
    report = rpt.build_report(data)
    marks = {d["ref"]: d["mark"] for d in report["notes"]["disclosures"]}
    assert marks == {"DR-001": "not shown to the user", "DR-002": None}
    md = rpt.render_markdown(report)
    assert "disclosure (hidden, DR-001) [not shown to the user]: hidden text" in md
    assert "disclosure (register, DR-002): shown text" in md


def test_the_drift_flag_shows_when_the_corpus_text_changed(audit_db, tmp_path, monkeypatch):
    query_id, _ = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    data = rpt.fetch_query(audit_db.conn, query_id, audit_db.names.schema)
    assert all(p["text_changed"] is False for p in rpt.build_report(data)["passages"])
    data["passages"][0]["chunk_text_sha256"] = "0" * 64
    report = rpt.build_report(data)
    assert [p["text_changed"] for p in report["passages"]][0] is True
    assert "Text changed since this answer" in rpt.render_markdown(report)


def test_the_config_warning_shows_unless_the_sha_is_the_pinned_one(audit_db, tmp_path, monkeypatch):
    query_id, _ = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    report = report_of(audit_db, query_id)
    assert report["header"]["config"]["from"] == "app start"      # the fixtures' config has no sha: the app start's is shown
    assert report["header"]["config"]["pinned"] is False          # the test app start holds a dummy sha
    assert "WARNING" in rpt.render_markdown(report)
    data = rpt.fetch_query(audit_db.conn, query_id, audit_db.names.schema)
    data["query"]["config_sha256"] = rpt.PINNED_CONFIG_SHA256
    data["query"]["config_file"] = "eval/tools/config_p7.json"
    ok = rpt.build_report(data)
    assert ok["header"]["config"]["pinned"] is True and ok["header"]["config"]["from"] == "query"
    assert "WARNING" not in rpt.render_markdown(ok)


@pytest.mark.parametrize("name", ALL)
def test_every_status_gives_a_report_and_markdown(name, audit_db, tmp_path, monkeypatch):
    query_id, out = write_query(audit_db, tmp_path, monkeypatch, name)
    report = report_of(audit_db, query_id)
    assert (report["header"]["status"], report["header"]["refusal_reason"]) == EXPECTED[name]
    md = rpt.render_markdown(report)
    for heading in ("## 1. Header", "## 4. Retrieval", "## 6. Verification", "## 9. What the user saw"):
        assert heading in md
    assert out.answer.rendered in md
    json.loads(rpt.report_json(report))
    if EXPECTED[name][1] in ("invalid_question", "no_such_section", "section_outside_v1", "foreign_law"):
        assert report["passages"] == [] and "No passages" in md
    if EXPECTED[name][1] == "low_relevance":
        assert "below" in report["checks"]["floor_line"]


# Finding a query ----------------------------------------------------------------------------------------------------


def insert_final_row(db, query_id, record_id):
    start = db.start_id()
    db.conn.execute(
        f"INSERT INTO {db.q('queries')} (query_id, record_id, start_id, state, received_at, finished_at, tier, session_key, "
        "question, question_chars, cost_reserved_usd, status) VALUES (%s, %s, %s, 'final', now(), now(), 'local', 's', 'q', 1, "
        "0.05, 'answered')", (query_id, record_id, start))


def test_a_reference_resolves_by_prefix_query_id_or_record_id_and_ambiguity_is_refused(audit_db):
    one, two = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-1111-4000-8000-000000000002"
    r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
    insert_final_row(audit_db, one, r1)
    insert_final_row(audit_db, two, r2)
    schema = audit_db.names.schema
    conn = audit_db.conn
    with pytest.raises(rpt.ReferenceError_, match="2 queries"):
        rpt.resolve_reference(conn, "Q-aaaaaaaa", schema)
    assert rpt.resolve_reference(conn, "Q-aaaaaaaa0000", schema) == one
    assert rpt.resolve_reference(conn, "aaaaaaaa1111", schema) == two
    assert rpt.resolve_reference(conn, one, schema) == one
    assert rpt.resolve_reference(conn, r2, schema) == two
    assert rpt.resolve_reference(conn, r1.replace("-", "")[:10], schema) == one
    with pytest.raises(rpt.ReferenceError_, match="no query"):
        rpt.resolve_reference(conn, "Q-bbbbbbbb", schema)
    for junk in ("", "hello world", "Q-xyz", "Q-12"):
        with pytest.raises(rpt.ReferenceError_):
            rpt.resolve_reference(conn, junk, schema)


def test_the_list_filters(audit_db, tmp_path, monkeypatch):
    ids = {n: write_query(audit_db, tmp_path, monkeypatch, n)[0] for n in ("answered_calc", "low_relevance", "not_covered")}
    schema, conn = audit_db.names.schema, audit_db.conn
    got = lambda **kw: {r["query_id"] for r in rpt.list_queries(conn, schema, **kw)}  # noqa: E731
    assert got() == set(ids.values())
    assert got(status="answered") == {ids["answered_calc"]}
    assert got(reason="low_relevance") == {ids["low_relevance"]}
    assert got(tier="local") == set(ids.values()) and got(tier="anonymous") == set()
    assert got(unfinished=True) == set() and got(ui_fallback=True) == set()
    assert got(day=datetime.now(timezone.utc).date()) == set(ids.values()) or got() == set(ids.values())
    audit_db.store.add_feedback(ids["answered_calc"], "s", -1, "wrong_citation")
    assert got(rating=-1) == {ids["answered_calc"]} and got(rating=1) == set()
    open_id = str(uuid.uuid4())
    audit_db.store.open_query(query_id=open_id, start_id=audit_db.start_id(), tier="local", user_key=None, session_key="s",
                              question="q", reserve_usd=0, limits={})
    assert got(unfinished=True) == {open_id}


# compare_log.py ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_compare_log_reports_zero_differences_on_a_matching_pair(name, audit_db, tmp_path, monkeypatch):
    query_id, _ = write_query(audit_db, tmp_path, monkeypatch, name)
    ref, diffs = compare_log.compare_query(audit_db.conn, query_id, audit_db.names.schema, tmp_path / "jsonl")
    assert ref.startswith("Q-") and [d.line() for d in diffs] == []


def test_compare_log_names_the_field_that_changed(audit_db, tmp_path, monkeypatch):
    query_id, out = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    data = rpt.fetch_query(audit_db.conn, query_id, audit_db.names.schema)
    record = copy.deepcopy(out.record)
    assert compare_log.compare(data, record) == []
    changed = copy.deepcopy(record)
    changed["rendered"] += " extra"
    changed["verification"]["claims"][0]["verdict"] = "unsupported"
    changed["retrieval"]["chunks"][1]["score"] += 0.5
    changed["tools"]["computations"][0]["shown"] = False
    changed["verification"]["gaps"] = {"gaps": [{"text": "invented", "origin": "model", "class": None, "kept": True,
                                                 "reason": None}]}
    named = {d.where for d in compare_log.compare(data, changed)}
    assert {"queries.rendered", "queries.rendered_sha256", "claims[1].verdict", "passages[P2].score",
            "computations[C1].shown", "gaps.count"} <= named
    assert all(isinstance(d.line(), str) and d.where in d.line() for d in compare_log.compare(data, changed))


def test_compare_log_flags_a_missing_record_and_a_missing_delivery(audit_db, tmp_path, monkeypatch):
    query_id, out = write_query(audit_db, tmp_path, monkeypatch, "answered_calc", deliver=False)
    ref, diffs = compare_log.compare_query(audit_db.conn, query_id, audit_db.names.schema, tmp_path / "jsonl")
    assert [d.where for d in diffs] == ["deliveries"]            # no `shown` delivery was recorded
    for day_file in (tmp_path / "jsonl").glob("*.jsonl"):
        day_file.unlink()
    ref, diffs = compare_log.compare_query(audit_db.conn, query_id, audit_db.names.schema, tmp_path / "jsonl")
    assert [d.where for d in diffs] == ["record"]                # the JSONL record is gone: named, not a crash


def test_compare_log_cli_exit_codes(audit_db, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REVIEW_DATABASE_URL", raising=False)
    query_id, _ = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    ref = "Q-" + query_id.replace("-", "")[:8]
    assert compare_log.main([ref, "--schema", audit_db.names.schema]) == 0
    assert "0 difference(s)" in capsys.readouterr().out
    assert compare_log.main(["Q-bbbbbbbb", "--schema", audit_db.names.schema]) == 2
    assert compare_log.main(["--all-final", "--schema", audit_db.names.schema]) == 0


# show_query.py --------------------------------------------------------------------------------------------------------


def test_show_query_prints_markdown_or_json(audit_db, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REVIEW_DATABASE_URL", raising=False)
    query_id, out = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    ref = "Q-" + query_id.replace("-", "")[:8]
    assert show_query.main([ref, "--schema", audit_db.names.schema]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"# Query {ref}") and out.answer.rendered in captured.out
    assert "not read-only" in captured.err and "postgres" not in captured.err.lower()
    assert show_query.main([ref, "--json", "--schema", audit_db.names.schema]) == 0
    assert json.loads(capsys.readouterr().out)["header"]["reference"] == ref
    assert show_query.main(["Q-bbbbbbbb", "--schema", audit_db.names.schema]) == 2
    assert "no query matches" in capsys.readouterr().err


# The viewer (AppTest) -----------------------------------------------------------------------------------------------


def viewer(audit_db):
    at = AppTest.from_file(REVIEW, default_timeout=60)
    at.session_state["review"] = review_app.ReviewContext(connect=lambda: (connect(), False), schema=audit_db.names.schema)
    return at.run()


def shown_text(at):
    out = []
    for kind in ("markdown", "caption", "text", "info", "warning", "error", "code"):
        out += [e.value for e in getattr(at, kind)]
    return " ".join(out)


def test_the_viewer_opens_a_query_by_reference_and_shows_the_ten_sections(audit_db, tmp_path, monkeypatch):
    query_id, out = write_query(audit_db, tmp_path, monkeypatch, "answered_calc")
    at = viewer(audit_db)
    assert not at.exception and ui.REVIEW_NOT_READ_ONLY in [w.value for w in at.warning]
    at.text_input(key="review_ref").set_value("Q-" + query_id.replace("-", "")[:8])
    at.button(key="review_open").click()
    at.run()
    assert not at.exception
    assert [s.value for s in at.subheader if s.value in ui.REVIEW_SECTIONS.values()] == list(ui.REVIEW_SECTIONS.values())
    text = shown_text(at)
    assert out.answer.rendered in text and ui.REVIEW_CONFIG_WARNING in [e.value for e in at.error]
    marked = [h.value for h in at.get("html") if "<mark>" in h.value]
    assert marked and "<mark>" in marked[0]                     # the evidence quote is highlighted in the chunk text
    assert any("equals rendered_sha256" in m.value for m in at.markdown)


def test_the_viewer_lists_queries_and_opens_one(audit_db, tmp_path, monkeypatch):
    ids = [write_query(audit_db, tmp_path, monkeypatch, n)[0] for n in ("answered_calc", "no_such_section")]
    at = viewer(audit_db)
    picks = [b for b in at.button if b.key and b.key.startswith("review_pick_")]
    assert {b.key for b in picks} == {f"review_pick_{i}" for i in ids}
    at.button(key=f"review_pick_{ids[1]}").click()
    at.run()
    assert not at.exception and "no_such_section" in shown_text(at)
    at.selectbox(key="review_status").set_value("answered")
    at.run()
    assert [b.key for b in at.button if b.key and b.key.startswith("review_pick_")] == [f"review_pick_{ids[0]}"]


def test_the_viewer_says_so_for_an_unknown_or_ambiguous_reference(audit_db):
    one, two = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-1111-4000-8000-000000000002"
    insert_final_row(audit_db, one, str(uuid.uuid4()))
    insert_final_row(audit_db, two, str(uuid.uuid4()))
    at = viewer(audit_db)
    at.text_input(key="review_ref").set_value("Q-aaaaaaaa")
    at.button(key="review_open").click()
    at.run()
    assert any("2 queries" in e.value for e in at.error)
    at.text_input(key="review_ref").set_value("Q-bbbbbbbb")
    at.button(key="review_open").click()
    at.run()
    assert any("no query" in e.value for e in at.error)


def test_the_viewer_fails_with_a_plain_message_when_it_cannot_connect(audit_db):
    def refuse():
        raise OSError("password authentication failed for user secret-user")
    at = AppTest.from_file(REVIEW, default_timeout=30)
    at.session_state["review"] = review_app.ReviewContext(connect=refuse, schema=audit_db.names.schema)
    at.run()
    assert [e.value for e in at.error] == [ui.REVIEW_UNAVAILABLE] and "secret-user" not in shown_text(at)


# The reviewer grant on the corpus tables (D-169) --------------------------------------------------------------------------


def test_the_grant_statements_are_select_only_on_the_four_corpus_tables():
    statements = [s.as_string(None) for s in grant_review.grant_statements()]
    assert statements[0] == 'GRANT USAGE ON SCHEMA "public" TO "taxy_review"'
    assert statements[1:] == [f'GRANT SELECT ON TABLE "public"."{t}" TO "taxy_review"' for t in grant_review.CORPUS_TABLES]
    assert grant_review.CORPUS_TABLES == ("corpus_chunks", "chunk_embeddings", "discrepancy_register", "corpus_loads")
    assert not any(w in " ".join(statements) for w in ("INSERT", "UPDATE", "DELETE", "ALL PRIVILEGES", "CREATE", "PASSWORD"))


def test_the_grant_gives_select_and_nothing_else_on_a_throwaway_schema(audit_db):
    conn, hex_ = audit_db.conn, audit_db.hex
    schema, role = f"test_corpus_{hex_}", f"test_grantee_{hex_}"
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"CREATE ROLE {role} NOLOGIN")
        for table in grant_review.CORPUS_TABLES:
            conn.execute(f"CREATE TABLE {schema}.{table} (x integer)")
        conn.execute(f"CREATE TABLE {schema}.other (x integer)")
        for statement in grant_review.grant_statements(role, schema):
            conn.execute(statement)
        for table in grant_review.CORPUS_TABLES:
            assert conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"{schema}.{table}")).fetchone()[0]
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not conn.execute("SELECT has_table_privilege(%s, %s, %s)",
                                        (role, f"{schema}.{table}", privilege)).fetchone()[0]
        assert not conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"{schema}.other")).fetchone()[0]
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        drop_role(conn, role)


def test_no_test_applies_the_grant_to_the_real_corpus_tables():
    """The grant is an owner-run step: this suite never calls the script with --apply."""
    sources = [p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py")]
    assert not any("grant_review.main(" in s and '"--apply"' in s for s in sources if "def test_no_test_applies" not in s)
