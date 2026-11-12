"""The projection of gen-log/3 records to audit rows, and the write and read round trip (interface
design, Sections 4.1, 4.4, 4.5 and 10).

Records come from running `generate()` with the existing fake clients (the tests/test_tools.py and
tests/test_verification.py patterns), one per status and reason. The round trip runs against the
local container on a throwaway schema. No key, no paid call."""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from psycopg import errors

import test_tools as T
from audit.project import APP_ERROR_TEXT, Extras, ProjectionError, project, project_app_error, sha256_text
from audit_testlib import audit_db, no_paid_clients  # noqa: F401
from embed.load import CHUNKS, read_chunks
from generation import generate as gen
from generation import llm

HASHES = {c["chunk_id"]: c["text_sha256"] for c in read_chunks(CHUNKS)}
FINISHED = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
EXTRAS = Extras(finished_at=FINISHED, latency_ms=1234, chunk_text_sha256=HASHES, gen_log_file="logs/generation/x.jsonl")
UNSUPPORTED = ("Unsupported: VAT is paid every week.", ["P9"])


class DownVerifier:
    model = llm.VERIFIER_MODEL

    def create(self, params):
        raise llm.GenerationError("APIConnectionError")


def run(tmp_path, question, *steps, **kwargs):
    answer, _, _ = T.run(question, *steps, tmp_path=tmp_path, tagger=kwargs.pop("tagger", T.FakeTagger([])),
                         **kwargs)
    (record,) = T.records(tmp_path)
    return record, answer


def boom(*args, **kwargs):
    raise ValueError("bad table row")


SCENARIOS = {
    # answered: C1 shown, C2 computed but not listed (withheld), one claim removed (a removal line)
    "answered_calc": lambda tmp: run(tmp, T.E01, T.tool_turn(T.use("t1", T.VAT), T.use("t2", T.VAT)),
                                     T.final([T.VAT_CLAIM, UNSUPPORTED], calculations=["C1"])),
    "answered_rate_not_confirmed": lambda tmp: run(
        tmp, T.E01, T.tool_turn(T.use("t1", T.VAT)),
        T.final([("VAT is charged on the value of all taxable supplies.", ["P9"])], calculations=["C1"])),
    "not_covered": lambda tmp: run(tmp, T.QUESTION, T.final([], status="not_covered",
                                                            gaps=["The passages do not settle it."])),
    "invalid_question": lambda tmp: run(tmp, ""),
    "no_such_section": lambda tmp: run(tmp, "What does section 231 of the Nigeria Tax Act 2025 say?"),
    "section_outside_v1": lambda tmp: run(tmp, "What does section 58 of the Nigeria Tax Act 2025 say?"),
    "foreign_law": lambda tmp: run(tmp, "How much corporate income tax does a company in Ghana pay under Ghanaian law?"),
    "low_relevance": lambda tmp: run(tmp, T.E01, retriever=T.FakeRetriever(score=0.1)),
    "unsupported_answer": lambda tmp: run(tmp, T.E01, T.tool_turn(T.use("t1", T.VAT)),
                                          T.final([UNSUPPORTED], calculations=["C1"])),
    "verification_unavailable": lambda tmp: run(tmp, T.E01, T.final([(T.VAT_CLAIM[0], ["P1"])]),
                                                retriever=T.FakeRetriever(["nta2025:s148"]), verifier=DownVerifier()),
    "api_error": lambda tmp: run(tmp, T.E01, T.tool_turn(T.use("t1", T.VAT)), llm.GenerationError("overloaded")),
}
EXPECTED = {"answered_calc": ("answered", None), "answered_rate_not_confirmed": ("answered", None),
            "not_covered": ("not_covered", None), "invalid_question": ("refused", "invalid_question"),
            "no_such_section": ("refused", "no_such_section"), "section_outside_v1": ("refused", "section_outside_v1"),
            "foreign_law": ("refused", "foreign_law"), "low_relevance": ("refused", "low_relevance"),
            "unsupported_answer": ("refused", "unsupported_answer"),
            "verification_unavailable": ("error", "verification_unavailable"), "api_error": ("error", None),
            "tool_error": ("answered", None)}


def make(name, tmp_path, monkeypatch):
    if name == "tool_error":
        monkeypatch.setattr(gen, "calculate", boom)
        return run(tmp_path, T.E01, T.tool_turn(T.use("t1", T.VAT)),
                   T.final([(T.VAT_CLAIM[0], ["P1"])], calculations=["C1"]),
                   retriever=T.FakeRetriever(["nta2025:s148"]))
    return SCENARIOS[name](tmp_path)


ALL = sorted(EXPECTED)


# Projection (pure) -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_every_status_projects_and_agrees_with_the_record(name, tmp_path, monkeypatch):
    record, answer = make(name, tmp_path, monkeypatch)
    assert (answer.status, answer.refusal_reason) == EXPECTED[name]
    rows = project(record, answer, EXTRAS)
    q = rows.query
    assert (q["status"], q["refusal_reason"], q["record_id"]) == (*EXPECTED[name], record["record_id"])
    assert q["rendered"] == answer.rendered == record["rendered"] and q["rendered_sha256"] == sha256_text(answer.rendered)
    assert q["log_schema"] == "gen-log/3" and q["corpus"] == record["corpus"]
    assert q["refusal_flag"] is record["refusal_flag"] and q["error"] == record["error"]
    assert q["total_cost_usd"] == pytest.approx(answer.total_cost_usd, abs=1e-6)
    assert [p["label"] for p in rows.passages] == [p.label for p in answer.passages]
    assert all(p["chunk_text_sha256"] == HASHES[p["chunk_id"]] for p in rows.passages)
    if answer.status in ("refused", "error"):
        assert q["refusal_text"] == answer.rendered.split("\n\n")[0]
    else:
        assert q["refusal_text"] is None
    # D-152: no request or response bodies anywhere in the payload
    payload = json.dumps(rows.payload(), ensure_ascii=False)
    assert '"system"' not in payload and '"messages"' not in payload and '"thinking"' not in payload
    assert all(set(c) == {"kind", "turn", "request_id", "model", "stop_reason", "usage", "cost_usd", "latency_ms",
                          "error"} for c in q["model_calls"])


def test_answered_with_a_shown_and_a_withheld_calculation_and_a_removed_claim(tmp_path, monkeypatch):
    record, answer = make("answered_calc", tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    assert [(c["calc_id"], c["shown"], c["withheld_reason"]) for c in rows.computations] == [
        ("C1", True, None), ("C2", False, "not_listed")]
    assert [(c["claim_no"], c["verdict"], c["action"], c["shown"]) for c in rows.claims] == [
        (1, "supported", "kept", True), (2, "unsupported", "removed", False)]
    assert "claims_removed" in rows.query["flags"]
    p9 = next(p for p in rows.passages if p["label"] == "P9")
    assert (p9["origin"], p9["score"], p9["distance"], p9["cited"], p9["supporting"]) == (
        "calculation_source", None, None, True, True)
    p1 = rows.passages[0]
    assert p1["origin"] == "retrieval" and p1["score"] is not None and p1["supporting"] is False
    assert rows.query["prompt_version"] == "gen-prompt/9" and rows.query["tool_defs_version"] == "tool-defs/2"
    assert rows.query["generator_model"] == llm.MODEL and rows.query["verifier_model"] == llm.VERIFIER_MODEL
    assert json.loads(rows.query["generator_output"])["calculations"] == ["C1"]
    assert [c["kind"] for c in rows.query["model_calls"]].count("generation") == 2
    assert rows.query["tool_uses"][0]["name"] == "calculate_liability"
    assert rows.query["versions"]["calc_versions"] == ["calc/1"]


def test_a_withheld_rate_shows_the_pipeline_line(tmp_path, monkeypatch):
    record, answer = make("answered_rate_not_confirmed", tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    (comp,) = rows.computations
    assert (comp["shown"], comp["withheld_reason"]) == (False, "rate_not_confirmed")
    assert gen.RATE_NOT_CONFIRMED_LINE[0] in rows.query["rendered"]


def test_not_covered_keeps_its_checked_gaps(tmp_path, monkeypatch):
    record, answer = make("not_covered", tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    assert [(g["text"], g["class"], g["kept"], g["shown"]) for g in rows.gaps] == [
        ("The passages do not settle it.", "missing_information", True, True)]
    assert rows.claims == [] and not any(p["supporting"] for p in rows.passages)


@pytest.mark.parametrize("name", ["invalid_question", "no_such_section", "section_outside_v1", "foreign_law"])
def test_a_pre_check_refusal_has_no_model_fields(name, tmp_path, monkeypatch):
    record, answer = make(name, tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    q = rows.query
    assert q["refusal_stage"] == "pre_check" and rows.passages == [] and rows.claims == []
    assert (q["generator_model"], q["prompt_version"], q["generator_output"], q["verifier_model"]) == (None,) * 4
    assert q["retrieval_config_sha256"] is None
    assert q["pre_check"]["refusal"] == name and q["note_tagger"]["status"] == "ok"
    assert q["tagger_model"] is not None  # the tagger runs on every status
    assert [c["kind"] for c in q["model_calls"]] == ["tagger"] * len(q["model_calls"])


def test_low_relevance_records_the_floor(tmp_path, monkeypatch):
    record, answer = make("low_relevance", tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    assert rows.query["refusal_stage"] == "relevance_floor" and rows.query["relevance_floor"]["status"] == "below"
    assert rows.passages and not any(p["cited"] for p in rows.passages)


def test_the_tool_error_is_logged_and_nothing_is_shown(tmp_path, monkeypatch):
    record, answer = make("tool_error", tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    assert [(c["calc_id"], c["status"], c["shown"]) for c in rows.computations] == [("C1", "error", False)]
    assert rows.query["tool_uses"][0]["exception"] == "ValueError: bad table row"


def test_the_projection_refuses_a_record_that_disagrees_with_the_answer(tmp_path, monkeypatch):
    record, answer = make("answered_calc", tmp_path, monkeypatch)
    for change in (lambda r: r.update(record_id=str(uuid.uuid4())), lambda r: r.update(rendered="other"),
                   lambda r: r.update(schema="gen-log/2"), lambda r: r["retrieval"]["chunks"].pop()):
        bad = json.loads(json.dumps(record))
        change(bad)
        with pytest.raises(ProjectionError):
            project(bad, answer, EXTRAS)
    with pytest.raises(ProjectionError, match="text_sha256"):
        project(record, answer, Extras(FINISHED, 1, chunk_text_sha256={}))


def test_app_error_rows_have_no_record_and_a_redacted_message():
    exc = RuntimeError("connection failed for key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
    rows = project_app_error("q", exc, EXTRAS)
    q = rows.query
    assert (q["record_id"], q["status"], q["rendered"]) == (None, "app_error", APP_ERROR_TEXT)
    assert q["app_error"].startswith("RuntimeError: ") and "abcdefghijklmnop" not in q["app_error"]
    assert APP_ERROR_TEXT == gen.ERROR_TEXT + "\n\n" + gen.DISCLAIMER


# Write and read round trip -------------------------------------------------------------------------------


def _norm(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    return value


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def open_row(db, question, start_id=None):
    query_id = str(uuid.uuid4())
    admission = db.store.open_query(query_id=query_id, start_id=start_id or db.start_id(), tier="local",
                                    user_key=None, session_key="s1", question=question, reserve_usd=Decimal("0.05"),
                                    limits={})
    assert admission.admitted
    return query_id


def read_rows(db, table, query_id, order):
    cur = db.conn.execute(f"SELECT * FROM {db.q(table)} WHERE query_id = %s ORDER BY {order}", (query_id,))
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


@pytest.mark.parametrize("name", ALL)
def test_round_trip_through_the_functions(name, audit_db, tmp_path, monkeypatch):
    record, answer = make(name, tmp_path, monkeypatch)
    rows = project(record, answer, EXTRAS)
    query_id = open_row(audit_db, record["question"])
    audit_db.store.close_query(query_id, rows.payload())
    (row,) = read_rows(audit_db, "queries", query_id, "query_id")
    assert row["state"] == "final" and str(row["record_id"]) == record["record_id"] == answer.record_id
    assert row["question"] == record["question"] and row["question_chars"] == len(record["question"])
    for key, value in rows.query.items():
        stored = _norm(row[key])
        expected = _json(value)
        if key == "finished_at":
            expected = FINISHED.isoformat()
        if isinstance(expected, float):
            assert stored == pytest.approx(expected, abs=1e-6), key
        else:
            assert stored == expected, key
    for table, order, projected in (("passages", "rank, label", rows.passages), ("claims", "claim_no", rows.claims),
                                    ("gaps", "gap_no", rows.gaps), ("computations", "calc_id", rows.computations)):
        stored = [{k: _norm(v) for k, v in r.items() if k != "query_id"} for r in read_rows(audit_db, table, query_id,
                                                                                               order)]
        expected = sorted(_json(projected), key=lambda r: tuple(r[c.strip()] for c in order.split(",")))
        assert len(stored) == len(expected), table
        for s, e in zip(stored, expected):
            for k, v in e.items():
                assert (s[k] == pytest.approx(v, abs=1e-6)) if isinstance(v, float) else s[k] == v, (table, k)


def test_close_twice_raises_and_a_final_row_cannot_be_changed(audit_db, tmp_path, monkeypatch):
    record, answer = make("answered_calc", tmp_path, monkeypatch)
    payload = project(record, answer, EXTRAS).payload()
    query_id = open_row(audit_db, record["question"])
    audit_db.store.close_query(query_id, payload)
    with pytest.raises(errors.InsufficientPrivilege, match="already final"):
        audit_db.store.close_query(query_id, payload)
    queries = audit_db.q("queries")
    for statement in (f"UPDATE {queries} SET status = 'refused' WHERE query_id = %s",
                      f"UPDATE {queries} SET state = 'received' WHERE query_id = %s",
                      f"DELETE FROM {queries} WHERE query_id = %s"):
        with pytest.raises(errors.InsufficientPrivilege):  # the trigger, even for the owner
            audit_db.conn.execute(statement, (query_id,))
    assert audit_db.conn.execute(f"SELECT status FROM {queries} WHERE query_id = %s", (query_id,)).fetchone() == (
        "answered",)


def test_a_received_row_cannot_change_its_receipt(audit_db):
    query_id = open_row(audit_db, "What is the VAT rate?")
    with pytest.raises(errors.InsufficientPrivilege, match="receipt columns"):
        audit_db.conn.execute(f"UPDATE {audit_db.q('queries')} SET state = 'final', status = 'answered', "
                              "finished_at = now(), record_id = gen_random_uuid(), question = 'other' "
                              "WHERE query_id = %s", (query_id,))


def test_close_refuses_a_payload_for_another_question(audit_db, tmp_path, monkeypatch):
    record, answer = make("not_covered", tmp_path, monkeypatch)
    query_id = open_row(audit_db, "A different question?")
    with pytest.raises(errors.DataException):
        audit_db.store.close_query(query_id, project(record, answer, EXTRAS).payload())
    assert audit_db.conn.execute(f"SELECT state FROM {audit_db.q('queries')} WHERE query_id = %s",
                                 (query_id,)).fetchone() == ("received",)


def test_a_failing_child_row_rolls_the_whole_close_back(audit_db, tmp_path, monkeypatch):
    record, answer = make("answered_calc", tmp_path, monkeypatch)
    payload = project(record, answer, EXTRAS).payload()
    payload["claims"][0]["verdict"] = "not a verdict"  # breaks a CHECK on the last-but-one insert
    query_id = open_row(audit_db, record["question"])
    with pytest.raises(errors.CheckViolation):
        audit_db.store.close_query(query_id, payload)
    assert audit_db.conn.execute(f"SELECT state FROM {audit_db.q('queries')} WHERE query_id = %s",
                                 (query_id,)).fetchone() == ("received",)
    assert read_rows(audit_db, "passages", query_id, "label") == []


def test_app_error_round_trip_has_a_null_record_id(audit_db):
    query_id = open_row(audit_db, "q")
    audit_db.store.close_query(query_id, project_app_error("q", RuntimeError("embedding failed"), EXTRAS).payload())
    (row,) = read_rows(audit_db, "queries", query_id, "query_id")
    assert (row["state"], row["status"], row["record_id"], row["rendered"]) == ("final", "app_error", None,
                                                                                 APP_ERROR_TEXT)
    assert row["app_error"] == "RuntimeError: embedding failed"


def test_a_final_non_app_error_row_needs_a_record_id(audit_db):
    query_id = open_row(audit_db, "q")
    with pytest.raises(errors.CheckViolation):
        audit_db.store.close_query(query_id, {"question": "q", "query": {"status": "answered", "rendered": "x"}})


def test_deliveries_and_feedback(audit_db, tmp_path, monkeypatch):
    record, answer = make("not_covered", tmp_path, monkeypatch)
    query_id = open_row(audit_db, record["question"])
    with pytest.raises(errors.NoDataFound):  # feedback needs a final row
        audit_db.store.add_feedback(query_id, "s1", 1, None)
    audit_db.store.close_query(query_id, project(record, answer, EXTRAS).payload())
    audit_db.store.add_delivery(query_id, "shown", sha256_text(answer.rendered))
    audit_db.store.add_feedback(query_id, "s1", -1, "hard_to_read")
    audit_db.store.add_feedback(query_id, "s1", 1, None)  # the same session changes its rating
    audit_db.store.add_feedback(query_id, "s2", -1, "other")
    assert [(r["outcome"], r["shown_sha256"]) for r in read_rows(audit_db, "deliveries", query_id, "delivery_id")] == [
        ("shown", sha256_text(answer.rendered))]
    rows = read_rows(audit_db, "feedback", query_id, "session_key")
    assert [(r["session_key"], r["rating"], r["reason"], str(r["record_id"])) for r in rows] == [
        ("s1", 1, None, record["record_id"]), ("s2", -1, "other", record["record_id"])]
    with pytest.raises(errors.CheckViolation):
        audit_db.store.add_feedback(query_id, "s3", 0, None)
    with pytest.raises(errors.CheckViolation):
        audit_db.store.add_delivery(query_id, "seen", None)
