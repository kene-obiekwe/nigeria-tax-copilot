"""The app service with fakes (interface design, Sections 2, 3.6, 4.5, 7, 8, 9 and 10).

Fake model, verifier, tagger and retriever clients (the tests/test_tools.py patterns) and the committed
chunk files; the audit store is either the real functions on a throwaway schema in the local container
or a fake that fails on purpose. No key, no paid call: building a real client raises."""
import json
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import test_tools as T
from audit.project import APP_ERROR_TEXT
from audit.store import Admission
from audit_testlib import PaidClientBuilt, audit_db, no_paid_clients  # noqa: F401
from embed.clients import EmbeddingError, OpenAIEmbeddingClient
from embed.load import CHUNKS, read_chunks
from generation import generate as gen
from generation.audit_log import read_records
from generation.llm import AnthropicLLMClient
from interface import screen
from interface.identity import (ANONYMOUS, LOCAL, FakeIdentity, Identity, IdentityError, resolve_identity,
                                secret_from_env, user_key)
from interface.limits import LIMITS_FILE, LocalGate, RequestContext, TierGate, day_start, gate_for, next_reset
from interface.service import AppService, Submitted, append_jsonl, read_one_record, sha256_text
from interface.startup import shipped_config

HASHES = {c["chunk_id"]: c["text_sha256"] for c in read_chunks(CHUNKS)}
SECRET = b"test-only-secret"
CALC_STEPS = (T.tool_turn(T.use("t1", T.VAT)), T.final([T.VAT_CLAIM], calculations=["C1"]))


class FakeGenerate:
    """generate() with fresh fake clients on every call; counts calls and every model request."""

    def __init__(self, *steps, retriever=None, before=None):
        self.steps, self.retriever, self.before = steps or CALC_STEPS, retriever, before
        self.calls, self.model_requests = 0, 0
        self.lock = threading.Lock()

    def __call__(self, question, *, config, log_dir):
        with self.lock:
            self.calls += 1
        if self.before:
            self.before()
        llm = T.ScriptLLM(*self.steps)
        try:
            return gen.generate(question, retriever=self.retriever or T.FakeRetriever(), llm=llm, store=T.STORE,
                                config=config, log_dir=log_dir, verifier=T.FakeVerifier(), tagger=T.FakeTagger([]))
        finally:
            with self.lock:
                self.model_requests += len(llm.requests)


class FailingStore:
    """Delegates to a real store (or nothing) and fails the named calls."""

    def __init__(self, real=None, fail=(), close_failures=0):
        self.real, self.fail, self.close_failures = real, set(fail), close_failures
        self.calls: list[str] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            if name in self.fail or (name == "close_query" and self.calls.count("close_query") <= self.close_failures):
                raise OSError("database unavailable")
            if self.real is None:
                raise AssertionError(f"unexpected store call {name}")
            return getattr(self.real, name)(*args, **kwargs)
        return call


def service(db_store, tmp_path, generate_fn=None, **kwargs):
    kwargs.setdefault("start_id", kwargs.pop("start", 1))
    return AppService(store=db_store, gate=kwargs.pop("gate", LocalGate()), config=shipped_config(),
                      chunk_hashes=lambda ids: {i: HASHES[i] for i in ids}, generate_fn=generate_fn or FakeGenerate(),
                      jsonl_dir=kwargs.pop("jsonl_dir", tmp_path / "jsonl"), close_retry_delay_s=0.01, **kwargs)


def query_row(db, query_id):
    cur = db.conn.execute(f"SELECT * FROM {db.q('queries')} WHERE query_id = %s", (query_id,))
    row = cur.fetchone()
    return None if row is None else dict(zip([d.name for d in cur.description], row))


def deliveries(db, query_id):
    return db.conn.execute(f"SELECT outcome, shown_sha256 FROM {db.q('deliveries')} WHERE query_id = %s "
                           "ORDER BY delivery_id", (query_id,)).fetchall()


def count(db, table):
    return db.conn.execute(f"SELECT count(*) FROM {db.q(table)}").fetchone()[0]


# The guard itself ------------------------------------------------------------------------------------


def test_building_a_real_client_raises_in_these_tests():
    with pytest.raises(PaidClientBuilt):
        AnthropicLLMClient(api_key="not-a-key")
    with pytest.raises(PaidClientBuilt):
        OpenAIEmbeddingClient(api_key="not-a-key")


# The happy path, end to end on the real functions -----------------------------------------------------


def test_an_answer_is_logged_before_it_is_shown(audit_db, tmp_path):
    fake = FakeGenerate()
    svc = service(audit_db.store, tmp_path, fake, start=audit_db.start_id())
    sub = svc.submit(T.E01, LOCAL, "session-1")
    assert sub.kind == "running" and sub.reference == "Q-" + sub.query_id.replace("-", "")[:8]
    out = svc.wait(sub, timeout=30)
    assert out.kind == "final" and out.showable and out.shown_text == out.answer.rendered
    row = query_row(audit_db, sub.query_id)
    assert (row["state"], row["status"], str(row["record_id"])) == ("final", "answered", out.answer.record_id)
    assert row["config_file"] == "eval/tools/config_p7.json" and row["latency_ms"] >= 0
    assert row["rendered_sha256"] == sha256_text(out.answer.rendered)
    # the local JSONL holds the same record, byte for byte
    (day_file,) = (tmp_path / "jsonl").glob("*.jsonl")
    (record,) = read_records(day_file)
    assert record["record_id"] == out.answer.record_id == out.record["record_id"]
    assert row["gen_log_file"].endswith(day_file.name)
    svc.deliver(sub.query_id, "shown", out.shown_text)
    assert deliveries(audit_db, sub.query_id) == [("shown", row["rendered_sha256"])]
    assert fake.calls == 1


def test_the_hosted_service_keeps_no_jsonl(audit_db, tmp_path):
    svc = service(audit_db.store, tmp_path, start=audit_db.start_id(), jsonl_dir=None)
    out = svc.wait(svc.submit(T.E01, ANONYMOUS, "s"), timeout=30)
    assert out.kind == "final" and query_row(audit_db, out.query_id)["gen_log_file"] is None
    assert not (tmp_path / "jsonl").exists()


def test_the_parity_hook_flags_ui_fallback(audit_db, tmp_path):
    svc = service(audit_db.store, tmp_path, start=audit_db.start_id(), parity=lambda record, answer: False)
    out = svc.wait(svc.submit(T.E01, LOCAL, "s"), timeout=30)
    assert out.ui_fallback and "ui_fallback" in query_row(audit_db, out.query_id)["flags"]


# Receipt and close failures (D-153) ----------------------------------------------------------------------


def test_a_receipt_failure_means_no_model_call(tmp_path):
    fake = FakeGenerate()
    store = FailingStore(fail={"open_query"})
    sub = service(store, tmp_path, fake).submit(T.E01, LOCAL, "s")
    assert sub == Submitted("unrecorded")
    assert (fake.calls, fake.model_requests, store.calls) == (0, 0, ["open_query"])
    assert not (tmp_path / "jsonl").exists()


def test_a_close_failure_withholds_the_answer_and_records_the_delivery(audit_db, tmp_path):
    store = FailingStore(audit_db.store, close_failures=2)
    svc = service(store, tmp_path, start=audit_db.start_id())
    sub = svc.submit(T.E01, LOCAL, "s")
    out = svc.wait(sub, timeout=30)
    assert out.kind == "withheld" and not out.showable and out.shown_text is None
    assert store.calls.count("close_query") == 2  # retried once
    assert query_row(audit_db, sub.query_id)["state"] == "received"  # still counts at its reserve
    assert deliveries(audit_db, sub.query_id) == [("withheld_log_failure", None)]
    # locally the full JSONL record already exists
    (day_file,) = (tmp_path / "jsonl").glob("*.jsonl")
    assert read_records(day_file)[0]["record_id"] == out.answer.record_id


def test_one_close_failure_is_retried_and_succeeds(audit_db, tmp_path):
    store = FailingStore(audit_db.store, close_failures=1)
    svc = service(store, tmp_path, start=audit_db.start_id())
    out = svc.wait(svc.submit(T.E01, LOCAL, "s"), timeout=30)
    assert out.kind == "final" and store.calls.count("close_query") == 2


class AdmitAll:
    def open_query(self, **kwargs):
        return Admission(True, None, None)


def test_a_close_and_delivery_failure_still_withholds(tmp_path):
    store = FailingStore(AdmitAll(), fail={"close_query", "add_delivery"})
    svc = service(store, tmp_path)
    out = svc.wait(svc.submit(T.E01, LOCAL, "s"), timeout=30)
    assert out.kind == "withheld" and store.calls.count("close_query") == 2 and "add_delivery" in store.calls


# The temp-dir record (D-150) ------------------------------------------------------------------------


def test_the_temp_dir_record_is_read_and_its_record_id_checked(tmp_path):
    answer, _, _ = T.run(T.E01, *CALC_STEPS, tmp_path=tmp_path)
    record, path = read_one_record(tmp_path, answer.record_id)
    assert record["record_id"] == answer.record_id and path.parent == tmp_path
    with pytest.raises(RuntimeError, match="record_id"):
        read_one_record(tmp_path, str(uuid.uuid4()))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="one log file"):
        read_one_record(empty, answer.record_id)


def test_a_record_that_does_not_match_withholds_the_answer(audit_db, tmp_path):
    def wrong_id(question, *, config, log_dir):
        answer = FakeGenerate()(question, config=config, log_dir=log_dir)
        answer.record_id = str(uuid.uuid4())
        return answer

    svc = service(audit_db.store, tmp_path, wrong_id, start=audit_db.start_id())
    sub = svc.submit(T.E01, LOCAL, "s")
    out = svc.wait(sub, timeout=30)
    assert out.kind == "withheld" and query_row(audit_db, sub.query_id)["state"] == "received"
    assert deliveries(audit_db, sub.query_id) == [("withheld_log_failure", None)]


def test_no_record_at_all_withholds_the_answer(audit_db, tmp_path):
    def unlogged(question, *, config, log_dir):
        return FakeGenerate()(question, config=config, log_dir=None)

    svc = service(audit_db.store, tmp_path, unlogged, start=audit_db.start_id())
    assert svc.wait(svc.submit(T.E01, LOCAL, "s"), timeout=30).kind == "withheld"


def test_concurrent_answers_append_whole_lines_under_the_lock(audit_db, tmp_path):
    svc = service(audit_db.store, tmp_path, start=audit_db.start_id())
    subs = [svc.submit(T.E01, LOCAL, f"s{i}") for i in range(8)]
    outs = [svc.wait(s, timeout=60) for s in subs]
    assert all(o.kind == "final" for o in outs)
    (day_file,) = (tmp_path / "jsonl").glob("*.jsonl")
    lines = day_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8 and sorted(json.loads(l)["record_id"] for l in lines) == sorted(
        o.answer.record_id for o in outs)


def test_append_jsonl_copies_bytes(tmp_path):
    source = tmp_path / "2026-11-12.jsonl"
    source.write_bytes('{"a": "₦"}\n'.encode("utf-8"))
    append_jsonl(source, tmp_path / "out")
    append_jsonl(source, tmp_path / "out")
    assert (tmp_path / "out" / source.name).read_bytes() == source.read_bytes() * 2


# app_error (Section 9) --------------------------------------------------------------------------------


def test_an_embedding_failure_is_an_app_error_with_no_record(audit_db, tmp_path):
    def raises(question, *, config, log_dir):
        raise EmbeddingError("APIConnectionError (no status)")

    svc = service(audit_db.store, tmp_path, raises, start=audit_db.start_id())
    sub = svc.submit(T.E01, LOCAL, "s")
    out = svc.wait(sub, timeout=30)
    assert (out.kind, out.shown_text) == ("app_error", APP_ERROR_TEXT)
    row = query_row(audit_db, sub.query_id)
    assert (row["state"], row["status"], row["record_id"], row["app_error"]) == (
        "final", "app_error", None, "EmbeddingError: APIConnectionError (no status)")


# The worker outlives the script (D-157) ----------------------------------------------------------------


def test_the_worker_finishes_and_logs_after_a_rerun_and_a_ui_timeout(audit_db, tmp_path):
    release = threading.Event()
    fake = FakeGenerate(before=lambda: release.wait(10))
    svc = service(audit_db.store, tmp_path, fake, start=audit_db.start_id())
    sub = svc.submit(T.E01, LOCAL, "s")
    query_id, future = sub.query_id, sub.future
    assert svc.wait(sub, timeout=0.2) is None  # the UI gives up
    assert deliveries(audit_db, query_id) == [("ui_timeout", None)]
    del sub, svc  # a Streamlit rerun: the script drops everything it held
    release.set()
    out = future.result(timeout=30)  # the worker still finishes
    assert out.kind == "final" and query_row(audit_db, query_id)["state"] == "final"


# Limits (Section 8) ------------------------------------------------------------------------------------


def test_a_limit_refusal_stores_nothing_and_makes_no_call(audit_db, tmp_path):
    gate = TierGate(None, Decimal("0.05"), 1, None, None, None)
    fake = FakeGenerate()
    svc = service(audit_db.store, tmp_path, fake, start=audit_db.start_id(), gate=gate)
    first = svc.submit(T.E01, ANONYMOUS, "s")
    assert first.kind == "running" and first.remaining == 0
    svc.wait(first, timeout=30)
    second = svc.submit(T.E01, ANONYMOUS, "s")
    assert (second.kind, second.reason) == ("limited", "session_limit")
    assert fake.calls == 1 and count(audit_db, "queries") == 1
    usage = svc.usage_today(ANONYMOUS, "s")
    assert usage["questions"] == 1 and usage["spend_usd"] > 0


def test_tier_gate_reads_the_limits_file():
    gate = TierGate.from_file(LIMITS_FILE)
    now = datetime.now(timezone.utc)
    ctx = lambda tier, key=None: RequestContext(tier, key, "s", now)  # noqa: E731
    assert gate.limits_for(ctx("anonymous")).as_json() == {"per_session": 3, "per_user_day": None,
                                                          "anonymous_pool_day": 20, "cost_cap_day_usd": "0.50"}
    assert gate.limits_for(ctx("signed_in", "k")).per_user_day == 10   # the final values (D-200)
    assert gate.limits_for(ctx("reviewer", "k")).per_user_day == 50
    assert gate.limits_for(ctx("local")).cost_cap_day_usd == Decimal("0.50")
    assert {gate.limits_for(ctx(t, "k")).reserve_usd for t in ("anonymous", "signed_in")} == {Decimal("0.05")}
    with pytest.raises(ValueError):
        gate.limits_for(ctx("admin"))


def test_tier_gate_refuses_bad_values(tmp_path):
    bad = tmp_path / "limits.yml"
    bad.write_text("cost_cap_day_usd: '-1'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        TierGate.from_file(bad)
    bad.write_text("tiers: {anonymous: {per_session: 2.5}}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        TierGate.from_file(bad)


def test_local_gate_and_the_hosted_switch():
    limits = LocalGate().limits_for(RequestContext("local", None, "s", datetime.now(timezone.utc)))
    assert limits.as_json() == {"per_session": None, "per_user_day": None, "anonymous_pool_day": None,
                                "cost_cap_day_usd": None} and limits.reserve_usd == Decimal("0.05")
    assert isinstance(gate_for("local"), LocalGate) and isinstance(gate_for("test"), LocalGate)
    hosted = gate_for("hosted", {})
    assert isinstance(hosted, TierGate)  # Phase 11: the hosted gate is TierGate with the final values
    assert (hosted.cost_cap_day_usd, hosted.reserve_usd) == (Decimal("0.50"), Decimal("0.05"))
    assert (hosted.anonymous_per_session, hosted.anonymous_pool_day) == (3, 20)
    assert (hosted.signed_in_per_day, hosted.reviewer_per_day) == (10, 50)
    with pytest.raises(RuntimeError, match="no gate"):
        gate_for("staging")


def write_limits(tmp_path, text):
    path = tmp_path / "limits.yml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD_LIMITS = "cost_cap_day_usd: '0.50'\nreserve_usd: '0.05'\ntiers: {anonymous: {per_session: 3, pool_per_day: 20}}\n"


@pytest.mark.parametrize("env,expected", [
    ({}, "0.50"), ({"TAXY_COST_CAP_DAY_USD": ""}, "0.50"), ({"TAXY_COST_CAP_DAY_USD": "  "}, "0.50"),
    ({"TAXY_COST_CAP_DAY_USD": "0.20"}, "0.20"), ({"TAXY_COST_CAP_DAY_USD": "0"}, "0"),
    ({"TAXY_COST_CAP_DAY_USD": "0.50"}, "0.50"),
    ({"TAXY_COST_CAP_DAY_USD": "5"}, "0.50"), ({"TAXY_COST_CAP_DAY_USD": "1e999999999999"}, "0.50")])  # raising is ignored
def test_the_cost_cap_override_can_only_lower_the_cap(env, expected):
    gate = gate_for("hosted", env)
    assert gate.cost_cap_day_usd == Decimal(expected)
    assert gate.limits_for(RequestContext("anonymous", None, "s", datetime.now(timezone.utc))).cost_cap_day_usd ==         Decimal(expected)


def test_the_override_applies_to_every_hosted_tier_and_is_ignored_locally():
    gate = gate_for("hosted", {"TAXY_COST_CAP_DAY_USD": "0.10"})
    now = datetime.now(timezone.utc)
    for tier, key in (("anonymous", None), ("signed_in", "k"), ("reviewer", "k")):
        assert gate.limits_for(RequestContext(tier, key, "s", now)).cost_cap_day_usd == Decimal("0.10")
    local = gate_for("local", {"TAXY_COST_CAP_DAY_USD": "0.10"})
    assert local.limits_for(RequestContext("local", None, "s", now)).cost_cap_day_usd is None


@pytest.mark.parametrize("value", ["abc", "-1", "NaN", "Infinity", "0.5usd"])
def test_a_bad_override_fails_closed(value):
    with pytest.raises(RuntimeError, match="hosted limits"):
        gate_for("hosted", {"TAXY_COST_CAP_DAY_USD": value})


def test_the_error_never_carries_the_override_value():
    with pytest.raises(RuntimeError) as err:
        gate_for("hosted", {"TAXY_COST_CAP_DAY_USD": "secret-looking-value"})
    assert "secret-looking-value" not in str(err.value) and err.value.__cause__ is None


@pytest.mark.parametrize("text", [
    "cost_cap_day_usd: null\nreserve_usd: '0.05'\n",
    "reserve_usd: '0.05'\n",
    "cost_cap_day_usd: '0.50'\nreserve_usd: '0'\n",
    "cost_cap_day_usd: '0.50'\nreserve_usd: null\n",
    "cost_cap_day_usd: '-1'\nreserve_usd: '0.05'\n",
    "cost_cap_day_usd: '0.50'\nreserve_usd: '0.05'\ntiers: {anonymous: {per_session: 2.5}}\n",
    "cost_cap_day_usd: [unclosed\n"])
def test_a_missing_or_unusable_limits_file_fails_closed(tmp_path, text):
    with pytest.raises(RuntimeError, match="hosted limits"):
        gate_for("hosted", {}, write_limits(tmp_path, text))
    assert isinstance(gate_for("hosted", {}, write_limits(tmp_path, GOOD_LIMITS)), TierGate)


def test_a_missing_limits_file_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="hosted limits"):
        gate_for("hosted", {}, tmp_path / "absent.yml")


def test_a_hosted_cap_of_zero_stops_every_question_in_the_database(audit_db):
    """K2 (design Section 6.3): `TAXY_COST_CAP_DAY_USD=0` gives the cap text and writes no query row."""
    fake = FakeGenerate()
    svc = AppService(store=audit_db.store, gate=gate_for("hosted", {"TAXY_COST_CAP_DAY_USD": "0"}),
                     start_id=audit_db.start_id(), config=shipped_config(),
                     chunk_hashes=lambda ids: {i: HASHES[i] for i in ids}, generate_fn=fake, jsonl_dir=None)
    sub = svc.submit(T.E01, ANONYMOUS, "s")
    assert (sub.kind, sub.reason) == ("limited", "cost_cap")
    assert fake.calls == 0 and count(audit_db, "queries") == 0


def test_the_python_limit_day_matches_west_africa_time():
    assert day_start(datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc)) == datetime(2026, 11, 12, 23, 0,
                                                                                       tzinfo=timezone.utc)
    assert day_start(datetime(2026, 11, 12, 22, 59, 59, tzinfo=timezone.utc)) == datetime(2026, 11, 11, 23, 0,
                                                                                          tzinfo=timezone.utc)
    assert next_reset(datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)) == datetime(2026, 11, 12, 23, 0,
                                                                                       tzinfo=timezone.utc)


def test_the_python_and_database_day_starts_agree(audit_db):
    now = datetime.now(timezone.utc)
    assert audit_db.conn.execute(f"SELECT {audit_db.names.schema}.wat_day_start(%s)", (now,)).fetchone()[0] == \
        day_start(now)


# The personal-data screen (D-160) ------------------------------------------------------------------------


BLOCKED = ["My email is ada.obi@example.com, what is the VAT rate?",
           "Call me on +2348031234567 about VAT.", "Call 2348031234567 about VAT.", "Call 08031234567 about VAT.",
           "Is 07012345678 my number?", "My TIN is 12345678901.", "Account 0123456789 at the bank.",
           "Our NIN 98765432101 and BVN 22233344455.", "Turnover was 1000000000 naira.",
           # phone numbers with separators between digit groups (Kene, option (a), within D-160)
           "Call 0803 123 4567 about VAT.", "Call 0803-123-4567 about VAT.", "Call 0803.123.4567 about VAT.",
           "Call +234 803 123 4567 about VAT.", "Call 234-803-123-4567 about VAT.",
           "Call 0803 1234 567 about VAT.", "Call +234-701-234-5678 about VAT.",
           "Call 0803 - 123 - 4567 about VAT.", "Call 0803  123  4567 about VAT."]
PASSES = ["Turnover was ₦1,000,000,000 last year.", "Turnover was 1,000,000,000 naira.",
          "Turnover was ₦1000000000.", "We are invoicing ₦2600000 before VAT.", "We invoice 2600000 before VAT.",
          "What does Section 202 of the Nigeria Tax Act 2025 say?", "Is the rate 7.5% from 1 January 2026?",
          "Gross turnover of ₦50,000,000 or less.",
          "Turnover of ₦70,000,000 this year.", "Is it 1,000,000,000 or more?",
          "What changed between 2025 and 2026?", "Does Section 163(1)(r) exempt it?",
          "Is the return due by 30 June 2026?", "Is a 7.5% rate charged?", "A 0.75% levy on 2,500,000.",
          "Sections 78, 79 and 80 of the 2025 Act."]


@pytest.mark.parametrize("question", BLOCKED)
def test_the_screen_blocks_personal_details(question):
    assert screen.blocks(question)


@pytest.mark.parametrize("question", PASSES)
def test_the_screen_passes_amounts_and_references(question):
    assert not screen.blocks(question)


@pytest.mark.parametrize("question", BLOCKED)
def test_a_blocked_question_is_not_sent_or_stored(question, audit_db, tmp_path, caplog):
    fake = FakeGenerate()
    store = FailingStore(audit_db.store)
    svc = service(store, tmp_path, fake, start=audit_db.start_id())
    with caplog.at_level("DEBUG"):
        assert svc.submit(question, LOCAL, "s") == Submitted("blocked")
    assert (store.calls, fake.calls, fake.model_requests) == ([], 0, 0)  # no database call, no model call
    assert count(audit_db, "queries") == 0 and count(audit_db, "admission_counts") == 0
    assert not (tmp_path / "jsonl").exists()
    assert question not in caplog.text


def test_the_screen_message_and_its_only_button():
    assert screen.SCREEN_TEXT.startswith("Your question may contain personal details")
    assert "₦1,000,000,000" in screen.SCREEN_TEXT and screen.EDIT_BUTTON == "Edit question"
    assert screen.blocks.__doc__ and not hasattr(screen, "SEND_ANYWAY")


# Identity, history and erasure (D-163, D-164) --------------------------------------------------------------


def test_user_keys_are_hmac_hex_and_never_shown():
    a = user_key(SECRET, "https://iss", "sub-1")
    assert len(a) == 64 and int(a, 16) >= 0 and a == user_key(SECRET, "https://iss", "sub-1")
    assert a != user_key(SECRET, "https://iss", "sub-2") and a != user_key(b"other", "https://iss", "sub-1")
    ident = Identity("signed_in", a, "ada@example.test")
    assert a not in repr(ident) and "ada@" not in repr(ident)
    assert secret_from_env({"TAXY_USER_KEY_SECRET": "x"}) == b"x" and secret_from_env({}) is None


def test_resolve_identity_without_and_with_sign_in():
    touched = []
    touch = lambda key, provider: touched.append((key, provider)) or "signed_in"  # noqa: E731
    assert resolve_identity(deployment="local", claims=None, secret=None, touch=touch) == LOCAL
    assert resolve_identity(deployment="hosted", claims={"is_logged_in": False}, secret=None, touch=touch) == ANONYMOUS
    ident = resolve_identity(deployment="hosted", claims=FakeIdentity("sub-1").claims(), secret=SECRET, touch=touch)
    assert ident.tier == "signed_in" and ident.user_key == user_key(SECRET, "https://accounts.example.test", "sub-1")
    assert touched == [(ident.user_key, "google")]
    with pytest.raises(IdentityError):
        resolve_identity(deployment="hosted", claims=FakeIdentity("sub-1").claims(), secret=None, touch=touch)
    with pytest.raises(IdentityError):
        resolve_identity(deployment="hosted", claims={"is_logged_in": True, "email": "x"}, secret=SECRET, touch=touch)
    with pytest.raises(ValueError):
        Identity("signed_in", None)


def test_two_signed_in_users_see_only_their_own_history(audit_db, tmp_path):
    svc = service(audit_db.store, tmp_path, start=audit_db.start_id())
    ada = resolve_identity(deployment="hosted", claims=FakeIdentity("ada").claims(), secret=SECRET,
                           touch=audit_db.store.touch_user)
    bayo = resolve_identity(deployment="hosted", claims=FakeIdentity("bayo").claims(), secret=SECRET,
                            touch=audit_db.store.touch_user)
    out_a = svc.wait(svc.submit(T.E01, ada, "s-ada"), timeout=30)
    out_b = svc.wait(svc.submit(T.QUESTION, bayo, "s-bayo"), timeout=30)
    assert (out_a.kind, out_b.kind) == ("final", "final")
    (item_a,) = svc.history(ada)
    (item_b,) = svc.history(bayo)
    assert (item_a.query_id, item_a.question) == (out_a.query_id, T.E01)
    assert (item_b.query_id, item_b.question) == (out_b.query_id, T.QUESTION)
    assert svc.history(ANONYMOUS) == [] and svc.history(LOCAL) == []
    assert svc.open_history_item(ada, item_a) == out_a.answer.rendered
    assert deliveries(audit_db, out_a.query_id) == [("shown_in_history", sha256_text(out_a.answer.rendered))]
    assert any(p["chunk_id"] == "nta2025:s148" for p in item_a.cited_passages)
    assert svc.erase_history(ada) == 1
    assert svc.history(ada) == [] and [i.query_id for i in svc.history(bayo)] == [out_b.query_id]
    with pytest.raises(PermissionError):
        svc.erase_history(ANONYMOUS)


def test_feedback_through_the_service(audit_db, tmp_path):
    svc = service(audit_db.store, tmp_path, start=audit_db.start_id())
    out = svc.wait(svc.submit(T.E01, LOCAL, "s"), timeout=30)
    svc.feedback(out.query_id, "s", -1, "wrong_citation")
    assert audit_db.conn.execute(f"SELECT rating, reason FROM {audit_db.q('feedback')}").fetchall() == [
        (-1, "wrong_citation")]
    for rating, reason in ((0, None), (1, "free text")):
        with pytest.raises(ValueError):
            svc.feedback(out.query_id, "s", rating, reason)
    with pytest.raises(ValueError):
        svc.deliver(out.query_id, "withheld_log_failure", "x")
