"""The Streamlit app with AppTest and fakes (interface design, Sections 3, 7, 9 and 10).

Fake clients (the tests/test_tools.py patterns), the committed chunk files and the audit functions on a
throwaway schema in the local container. The services come in through `st.session_state["services"]`;
building a real model or embedding client raises (autouse guard). No key, no paid call."""
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_tools as T  # noqa: E402
from audit_testlib import audit_db, no_paid_clients  # noqa: E402,F401
from app import cards  # noqa: E402
from app import copy as ui  # noqa: E402
from app.services import Services  # noqa: E402
from generation import generate as gen  # noqa: E402
from interface.identity import LOCAL, Identity  # noqa: E402
from interface.service import AppService  # noqa: E402
from interface.startup import StartupResult, shipped_config  # noqa: E402
from test_app_service import HASHES, FakeGenerate  # noqa: E402

MAIN = str(ROOT / "app" / "main.py")
HISTORY = str(ROOT / "app" / "pages" / "my_questions.py")
SNAPSHOT = "2026-10-18"


def file_chunks(ids):
    return T.STORE.chunks(list(ids))


def make_services(audit_db, tmp_path, generate_fn=None, *, identity=None, startup=None, **kwargs):
    start_id = audit_db.start_id()
    service = AppService(store=audit_db.store, gate=kwargs.pop("gate", None) or __import__("interface.limits", fromlist=["x"]).LocalGate(),
                         start_id=start_id, config=shipped_config(), chunk_hashes=lambda ids: {i: HASHES[i] for i in ids},
                         generate_fn=generate_fn or FakeGenerate(), jsonl_dir=None, close_retry_delay_s=0.01,
                         parity=cards.make_parity(file_chunks))
    ident = identity or LOCAL
    return Services("local", startup or StartupResult(True, [], start_id, SNAPSHOT), service, file_chunks,
                    (lambda: ident) if isinstance(ident, Identity) else ident, **kwargs)


def new_app(services, page=MAIN):
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["services"] = services
    return at.run()


def ask(at, question):
    at.text_area(key="question_box").set_value(question)
    next(b for b in at.button if b.label == ui.ASK_BUTTON).click()
    return at.run()


def texts(at):
    out = []
    for kind in ("markdown", "caption", "text", "info", "warning", "error", "success"):
        out += [e.value for e in getattr(at, kind)]
    return out


def test_first_render_shows_the_fixed_texts(audit_db, tmp_path):
    at = new_app(make_services(audit_db, tmp_path))
    assert not at.exception
    shown = texts(at)
    assert gen.DISCLAIMER in shown                                  # the imported constant, not retyped
    assert ui.STATUS_STRIP.format(date="18 October 2026") in shown  # the snapshot date comes from the database load
    assert ui.SCOPE_TEXT in shown and ui.PRIVACY_LINE in shown and ui.SINGLE_TURN_LINE in shown
    assert at.title[0].value == ui.PAGE_TITLE
    assert len(at.text_area) == 1 and at.text_area[0].max_chars == 2000
    assert at.chat_input == []  # deviation: a text area, so that examples and Edit question can fill it


# Helpers for the scenarios ---------------------------------------------------------------------------------------

from test_audit_write import ALL, EXPECTED, SCENARIOS, make  # noqa: E402
from audit.project import APP_ERROR_TEXT  # noqa: E402

DOWN = "What does section 231 of the Nigeria Tax Act 2025 say?"
QUESTIONS = {
    "answered_calc": T.E01, "answered_rate_not_confirmed": T.E01, "tool_error": T.E01, "api_error": T.E01,
    "low_relevance": T.E01, "unsupported_answer": T.E01, "verification_unavailable": T.E01,
    "not_covered": T.QUESTION, "invalid_question": "???",
    "no_such_section": DOWN, "section_outside_v1": "What does section 58 of the Nigeria Tax Act 2025 say?",
    "foreign_law": "How much corporate income tax does a company in Ghana pay under Ghanaian law?",
}


class ScenarioGenerate:
    """generate_fn that runs one of the Build A scenarios into the service's temp log dir."""

    def __init__(self, name, monkeypatch):
        self.name, self.monkeypatch, self.calls = name, monkeypatch, 0

    def __call__(self, question, *, config, log_dir):
        self.calls += 1
        if self.name == "invalid_question":
            from test_audit_write import run
            return run(log_dir, question)[1]
        return make(self.name, log_dir, self.monkeypatch)[1]


def rows(db, sql, *args):
    return db.conn.execute(sql.replace("{q}", db.names.schema), args).fetchall()


def block_texts(entry_card):
    return [b.text for b in entry_card.blocks]


def last_card(at):
    return at.session_state["entries"][-1]["card"]


# Parity (D-155): the joined block texts equal `rendered`, for every fixture status -------------------------------


@pytest.mark.parametrize("name", ALL)
def test_parity_for_every_fixture_status(name, tmp_path, monkeypatch):
    record, answer = ScenarioRun(name, tmp_path, monkeypatch)
    ids = list(dict.fromkeys(p.chunk_id for p in answer.passages))
    chunks = file_chunks(ids)
    blocks = cards.answer_blocks(record, answer, chunks)
    assert cards.joined(blocks) == answer.rendered
    assert cards.parity_ok(record, answer, chunks)
    assert blocks[-1].kind == "disclaimer" and blocks[-1].text == gen.DISCLAIMER
    kinds = [b.kind for b in blocks]
    assert (answer.status, answer.refusal_reason) == EXPECTED[name]
    if answer.status == "answered":
        assert "claim" in kinds and "sources" in kinds
    elif answer.status == "not_covered":
        assert "decline" in kinds and "gaps" in kinds
    else:
        assert kinds[0] == "refusal" and "claim" not in kinds
    # chips only name chunks that exist in the answer's passages
    assert {c for b in blocks for c in b.chunk_ids} <= set(ids)


def ScenarioRun(name, tmp_path, monkeypatch):
    if name == "invalid_question":
        from test_audit_write import run
        return run(tmp_path, "???")
    return make(name, tmp_path, monkeypatch)


def test_parity_fails_on_a_card_that_differs_from_rendered(tmp_path, monkeypatch):
    record, answer = ScenarioRun("answered_calc", tmp_path, monkeypatch)
    chunks = file_chunks([p.chunk_id for p in answer.passages])
    answer.rendered = answer.rendered + " "
    assert not cards.parity_ok(record, answer, chunks)
    assert not cards.parity_ok(record, answer, {})  # a missing chunk is a parity failure, never an error


def test_answered_calc_blocks_carry_chips_and_the_pipeline_calculation_text(tmp_path, monkeypatch):
    record, answer = ScenarioRun("answered_calc", tmp_path, monkeypatch)
    blocks = cards.answer_blocks(record, answer, file_chunks([p.chunk_id for p in answer.passages]))
    calc = next(b for b in blocks if b.kind == "calculation")
    assert calc.text.startswith(gen.CALCULATION_HEADING) and calc.chunk_ids  # one chip for each rate section
    claim = next(b for b in blocks if b.kind == "claim")
    assert claim.text.endswith("]") and claim.chunk_ids
    assert next(b for b in blocks if b.kind == "removal").text in answer.verification.removal_lines
    sources = next(b for b in blocks if b.kind == "sources")
    assert sources.chunk_ids == tuple(answer.cited_chunk_ids)


def test_a_rate_not_confirmed_calculation_gives_the_withheld_line(tmp_path, monkeypatch):
    record, answer = ScenarioRun("answered_rate_not_confirmed", tmp_path, monkeypatch)
    blocks = cards.answer_blocks(record, answer, file_chunks([p.chunk_id for p in answer.passages]))
    assert [b.kind for b in blocks if b.kind in ("calculation", "withheld")] == ["withheld"]
    assert cards.joined(blocks) == answer.rendered


# The page: start-up failure, examples, the screen ----------------------------------------------------------------


def test_a_failed_startup_check_shows_no_question_box(audit_db, tmp_path):
    bad = StartupResult(False, ["config_p7_sha256"], None, SNAPSHOT)
    services = make_services(audit_db, tmp_path, startup=bad)
    at = new_app(services)
    assert not at.exception
    assert at.text_area == [] and at.chat_input == [] and at.button == []
    assert ui.UNAVAILABLE_TEXT in [e.value for e in at.error]
    assert gen.DISCLAIMER in texts(at)  # the disclaimer is still shown
    assert "config_p7_sha256" not in " ".join(texts(at))  # check names go to the log, not to the user


def test_no_service_means_no_question_box(audit_db, tmp_path):
    services = make_services(audit_db, tmp_path)
    services.service = None
    at = new_app(services)
    assert at.text_area == [] and ui.UNAVAILABLE_TEXT in [e.value for e in at.error]


def test_examples_fill_the_box_and_send_nothing(audit_db, tmp_path):
    fake = FakeGenerate()
    at = new_app(make_services(audit_db, tmp_path, fake))
    examples = [b for b in at.button if b.key and b.key.startswith("example-")]
    assert len(examples) == 10
    examples[3].click()
    at.run()
    assert at.text_area(key="question_box").value == examples[3].label
    assert fake.calls == 0 and rows(audit_db, "SELECT count(*) FROM {q}.queries") == [(0,)]
    assert "pending" not in at.session_state


def test_the_examples_disappear_once_a_question_has_been_asked(audit_db, tmp_path):
    at = new_app(make_services(audit_db, tmp_path))
    at = ask(at, T.E01)
    assert [b for b in at.button if b.key and b.key.startswith("example-")] == []


def test_a_blocked_question_is_not_sent_not_stored_and_has_only_edit_question(audit_db, tmp_path):
    fake = FakeGenerate()
    at = new_app(make_services(audit_db, tmp_path, fake))
    blocked = "Our contact is jane.doe@example.org and the invoice is for ₦1,000,000"
    at = ask(at, blocked)
    assert not at.exception
    assert [b.label for b in at.button] == [ui.EDIT_BUTTON]
    assert at.text_area == [] and at.session_state["entries"] == []   # no way to send it from here
    assert ui.SCREEN_TEXT in " ".join(texts(at))
    assert "jane.doe" not in " ".join(texts(at))
    assert fake.calls == 0
    for table in ("queries", "passages", "claims", "deliveries", "feedback", "admission_counts"):
        assert rows(audit_db, f"SELECT count(*) FROM {{q}}.{table}") == [(0,)]
    # "Edit question" brings the text back into the box, and sending it is blocked again
    at.button[0].click()
    at.run()
    assert at.text_area(key="question_box").value == blocked
    at = ask(at, blocked)
    assert [b.label for b in at.button] == [ui.EDIT_BUTTON] and fake.calls == 0


def test_an_empty_question_is_not_sent(audit_db, tmp_path):
    fake = FakeGenerate()
    at = new_app(make_services(audit_db, tmp_path, fake))
    at = ask(at, "   ")
    assert ui.EMPTY_QUESTION in [w.value for w in at.warning] and fake.calls == 0


# One question end to end -----------------------------------------------------------------------------------------


def test_an_answered_question_is_logged_then_shown_as_a_card(audit_db, tmp_path):
    fake = FakeGenerate()
    at = new_app(make_services(audit_db, tmp_path, fake))
    at = ask(at, T.E01)
    assert not at.exception and fake.calls == 1
    card = last_card(at)
    assert card.status == "answered" and card.blocks is not None and not card.ui_fallback
    shown = texts(at)
    for block in card.blocks:                                     # every pipeline block is on the page, verbatim
        assert block.text.replace("$", "\\$") in shown
    assert gen.DISCLAIMER in shown and ui.REFERENCE.format(reference=card.reference) in shown
    assert f"**{ui.STATUS_LINES['answered']}**" in shown
    (row,) = rows(audit_db, "SELECT state, status, rendered_sha256, record_id FROM {q}.queries")
    assert row[:2] == ("final", "answered")
    deliveries = rows(audit_db, "SELECT outcome, shown_sha256 FROM {q}.deliveries")
    assert deliveries == [("shown", row[2])]                      # what the card displayed is the logged rendered text
    assert cards.sha256_text(card.text) == row[2]
    assert at.text_area(key="question_box").value == ""           # cleared after sending
    assert [b.label for b in at.button if b.label == ui.ASK_BUTTON] == [ui.ASK_BUTTON]


def test_the_citation_popover_shows_the_verbatim_chunk_text(audit_db, tmp_path):
    at = new_app(make_services(audit_db, tmp_path))
    at = ask(at, T.E01)
    card = last_card(at)
    chip = next(c for b in card.blocks for c in b.chunk_ids)
    assert card.chunks[chip]["text"] in [t.value for t in at.text]
    assert any(k.startswith("card-0-b") and k.endswith(f"-read-{chip}") for k in (b.key for b in at.button if b.key))


def test_read_the_whole_section_opens_the_unit(audit_db, tmp_path):
    at = new_app(make_services(audit_db, tmp_path))
    at = ask(at, T.E01)
    button = next(b for b in at.button if b.key and "-read-" in b.key)
    button.click()
    at.run()
    assert not at.exception
    assert ui.READER_NOTE in " ".join(texts(at))


@pytest.mark.parametrize("name", ALL)
def test_each_status_shows_its_blocks_and_tip(name, audit_db, tmp_path, monkeypatch):
    fake = ScenarioGenerate(name, monkeypatch)
    at = new_app(make_services(audit_db, tmp_path, fake))
    at = ask(at, QUESTIONS[name])
    assert not at.exception, name
    card = last_card(at)
    assert (card.status, card.refusal_reason) == EXPECTED[name] and card.blocks is not None
    shown = texts(at)
    assert all(b.text.replace("$", "\\$") in shown for b in card.blocks)
    assert gen.DISCLAIMER in shown
    tip = cards.hint_for(card.status, card.refusal_reason)
    if card.status == "answered" or tip is None:
        assert not any(s.startswith(f"{ui.HINT_LABEL}: ") for s in shown)
    else:
        assert f"{ui.HINT_LABEL}: {tip}" in shown
    assert rows(audit_db, "SELECT status, refusal_reason FROM {q}.queries") == [EXPECTED[name]]
    assert [d[0] for d in rows(audit_db, "SELECT outcome FROM {q}.deliveries")] == ["shown"]


def test_an_app_error_shows_the_error_text_and_disclaimer(audit_db, tmp_path):
    def boom(question, *, config, log_dir):
        raise OSError("embedding service unreachable")
    at = new_app(make_services(audit_db, tmp_path, boom))
    at = ask(at, T.E01)
    card = last_card(at)
    assert card.status == "app_error" and card.text == APP_ERROR_TEXT
    shown = texts(at)
    assert APP_ERROR_TEXT in shown and f"{ui.HINT_LABEL}: {ui.HINTS[('app_error', None)]}" in shown
    assert "embedding service unreachable" not in " ".join(shown)   # no exception text reaches the user
    (row,) = rows(audit_db, "SELECT status, record_id, app_error FROM {q}.queries")
    assert row[0] == "app_error" and row[1] is None and row[2].startswith("OSError")
    assert rows(audit_db, "SELECT outcome, shown_sha256 FROM {q}.deliveries") == [
        ("shown", cards.sha256_text(APP_ERROR_TEXT))]


def test_a_parity_failure_shows_plain_text_and_flags_the_row(audit_db, tmp_path):
    services = make_services(audit_db, tmp_path)
    services.service.parity = lambda record, answer: False
    at = new_app(services)
    at = ask(at, T.E01)
    card = last_card(at)
    assert card.ui_fallback and card.blocks is None
    assert not [b for b in at.button if b.key and "-read-" in b.key]   # no chips in the fallback
    assert card.text in " ".join(e.value for e in at.markdown).replace("\\$", "$")
    (row,) = rows(audit_db, "SELECT flags FROM {q}.queries")
    assert "ui_fallback" in row[0]
    assert [d[0] for d in rows(audit_db, "SELECT outcome FROM {q}.deliveries")] == ["shown_plain"]


# Failures (Section 9) -------------------------------------------------------------------------------------------


class FailingStore:
    def __init__(self, real, fail=(), close_failures=0):
        self.real, self.fail, self.close_failures, self.calls = real, set(fail), close_failures, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            if name in self.fail or (name == "close_query" and self.calls.count("close_query") <= self.close_failures):
                raise OSError("database unavailable")
            return getattr(self.real, name)(*args, **kwargs)
        return call


def test_a_receipt_failure_shows_the_message_and_makes_no_model_call(audit_db, tmp_path):
    fake = FakeGenerate()
    services = make_services(audit_db, tmp_path, fake)
    services.service.store = FailingStore(audit_db.store, fail={"open_query"})
    at = ask(new_app(services), T.E01)
    assert ui.UNRECORDED_TEXT in [e.value for e in at.error]
    assert fake.calls == 0 and fake.model_requests == 0
    assert at.session_state["entries"] == []


def test_a_close_failure_withholds_the_answer(audit_db, tmp_path):
    services = make_services(audit_db, tmp_path)
    services.service.store = FailingStore(audit_db.store, close_failures=2)
    at = ask(new_app(services), T.E01)
    (entry,) = at.session_state["entries"]
    assert entry["kind"] == "message" and "Reference Q-" in entry["text"]
    assert entry["text"] == ui.WITHHELD_TEXT.format(reference=entry["reference"])
    assert [e.value for e in at.error] == [entry["text"]]
    assert "at the rate of 7.5%" not in " ".join(texts(at))   # the answer text is nowhere on the page
    assert rows(audit_db, "SELECT state FROM {q}.queries") == [("received",)]
    assert [d[0] for d in rows(audit_db, "SELECT outcome FROM {q}.deliveries")] == ["withheld_log_failure"]


def test_the_ui_wait_times_out_with_a_reference_and_the_worker_still_logs(audit_db, tmp_path):
    release = threading.Event()
    fake = FakeGenerate(before=lambda: release.wait(20))
    services = make_services(audit_db, tmp_path, fake, ui_wait_s=0.4, poll_s=0.05, stage_seconds=(0.1, 0.2))
    at = ask(new_app(services), T.E01)
    (entry,) = at.session_state["entries"]
    assert entry["text"] == ui.TIMEOUT_TEXT.format(reference=entry["reference"])
    assert "pending" not in at.session_state and not at.exception
    assert [d[0] for d in rows(audit_db, "SELECT outcome FROM {q}.deliveries")] == ["ui_timeout"]
    release.set()                                     # the worker finishes and logs, whatever the UI did
    deadline = time.time() + 20
    while time.time() < deadline and rows(audit_db, "SELECT state FROM {q}.queries") != [("final",)]:
        time.sleep(0.1)
    assert rows(audit_db, "SELECT state, status FROM {q}.queries") == [("final", "answered")]


# Limits (Section 8) ---------------------------------------------------------------------------------------------


from decimal import Decimal  # noqa: E402
from interface.identity import ANONYMOUS  # noqa: E402
from interface.limits import TierGate  # noqa: E402
from app import main as main_module  # noqa: E402,F401


def tier_gate(**kw):
    values = dict(cost_cap_day_usd=None, reserve_usd=Decimal("0.05"), anonymous_per_session=None,
                  anonymous_pool_day=None, signed_in_per_day=None, reviewer_per_day=None)
    values.update(kw)
    return TierGate(**values)


def test_the_anonymous_session_limit_disables_the_box_at_the_limit(audit_db, tmp_path):
    services = make_services(audit_db, tmp_path, identity=ANONYMOUS, gate=tier_gate(anonymous_per_session=1))
    services.deployment = "test"
    at = new_app(services)
    assert ui.SIDEBAR_LEFT.format(n=1) in texts(at)
    at = ask(at, T.E01)                                              # the one question available
    assert last_card(at).status == "answered"
    ask_button = next(b for b in at.button if b.label == ui.ASK_BUTTON)
    assert ui.SIDEBAR_LEFT.format(n=0) in texts(at) and ask_button.disabled   # the box is disabled at the limit
    assert not at.exception
    sub = services.service.submit(T.E01, ANONYMOUS, at.session_state["session_key"])   # open_query refuses it too
    assert (sub.kind, sub.reason) == ("limited", "session_limit")


@pytest.mark.parametrize("gate,identity,reason,expected", [
    (dict(cost_cap_day_usd=Decimal("0.04")), ANONYMOUS, "cost_cap", ui.LIMIT_COST_CAP),
    (dict(anonymous_pool_day=0), ANONYMOUS, "anonymous_pool", ui.LIMIT_ANONYMOUS_POOL),
    (dict(anonymous_per_session=0), ANONYMOUS, "session_limit", ui.LIMIT_SESSION.format(n=0)),
    (dict(signed_in_per_day=0), Identity("signed_in", "ab" * 32), "daily_limit", ui.LIMIT_DAILY.format(n=0)),
])
def test_each_limit_reason_shows_its_text_and_nothing_is_sent(gate, identity, reason, expected, audit_db, tmp_path):
    fake = FakeGenerate()
    services = make_services(audit_db, tmp_path, fake, identity=identity, gate=tier_gate(**gate))
    services.deployment = "test"
    audit_db.store.touch_user("ab" * 32, "google")
    sub = services.service.submit(T.E01, identity, "s1")
    assert (sub.kind, sub.reason) == ("limited", reason)
    assert main_module.limit_text(services, identity, "s1", reason) == expected
    assert fake.calls == 0
    assert rows(audit_db, "SELECT count(*) FROM {q}.queries") == [(0,)]
    assert rows(audit_db, "SELECT reason, n FROM {q}.admission_counts") == [(reason, 1)]


def test_the_limit_text_is_shown_in_the_page_when_a_cap_stops_a_question(audit_db, tmp_path):
    services = make_services(audit_db, tmp_path, identity=ANONYMOUS, gate=tier_gate(cost_cap_day_usd=Decimal("0.04")))
    services.deployment = "test"
    at = ask(new_app(services), T.E01)
    assert ui.LIMIT_COST_CAP in [e.value for e in at.error] and at.session_state["entries"] == []


# Export, feedback, the section reader ------------------------------------------------------------------------------


def test_the_export_holds_the_question_the_rendered_answer_the_disclaimer_and_the_snapshot(audit_db, tmp_path):
    at = ask(new_app(make_services(audit_db, tmp_path)), T.E01)
    card = last_card(at)
    md = cards.export_markdown(card)
    assert card.question in md and card.reference in md
    assert card.text in md                                            # rendered verbatim, claims through disclaimer
    assert gen.DISCLAIMER in md and "corpus snapshot 18 October 2026" in md
    assert card.config_sha256 and card.config_sha256 in md
    cited = [c for b in card.blocks for c in b.chunk_ids]
    assert cited
    for chunk_id in dict.fromkeys(cited):
        chunk = card.chunks[chunk_id]
        assert chunk["citation"] in md and chunk["text"] in md
        assert all(label in md for label in chunk["page_labels"])
    assert ui.EXPORT_FILE.format(reference=card.reference) == f"taxy-answer-{card.reference}.md"
    assert any(b.key == "card-0-export" for b in at.get("download_button")) or at.get("download_button")


def test_feedback_writes_a_row_with_the_fixed_reason(audit_db, tmp_path):
    at = ask(new_app(make_services(audit_db, tmp_path)), T.E01)
    card = last_card(at)
    at.get("feedback")[0].set_value(0)                                # thumbs down
    at.run()
    at.selectbox(key="card-0-reason").set_value("wrong_citation")
    at.run()
    at.button(key="card-0-send").click()
    at.run()
    assert not at.exception
    assert ui.FEEDBACK_THANKS in texts(at)
    fb = rows(audit_db, "SELECT query_id::text, record_id::text, rating, reason, session_key FROM {q}.feedback")
    (row,) = rows(audit_db, "SELECT query_id::text, record_id::text FROM {q}.queries")
    assert fb == [(row[0], row[1], -1, "wrong_citation", at.session_state["session_key"])]


def test_feedback_has_no_free_text_input(audit_db, tmp_path):
    at = ask(new_app(make_services(audit_db, tmp_path)), T.E01)
    assert [t.key for t in at.text_area] == ["question_box"] and at.text_input == []
    assert set(ui.FEEDBACK_REASONS) == {"wrong_citation", "missing_information", "should_have_answered",
                                        "should_not_have_answered", "hard_to_read", "other"}


def test_the_reader_shows_a_definition_alone_not_the_120_of_section_202():
    ids = cards.unit_chunk_ids("nta2025:s202:def:small-company")
    assert ids == ["nta2025:s202:def:small-company"]
    ids = cards.unit_chunk_ids("nta2025:s189:def:agricultural-seeds-and-seedlings")
    assert ids == ["nta2025:s189:def:agricultural-seeds-and-seedlings"]


def test_a_split_definition_is_shown_whole():
    meta = [{"chunk_id": f"nta2025:s202:def:term{suffix}", "unit_type": "definition", "act_id": "nta2025",
             "section": "202", "schedule": None} for suffix in ("", ":2", ":3")]
    meta.append({"chunk_id": "nta2025:s202:def:other", "unit_type": "definition", "act_id": "nta2025",
                 "section": "202", "schedule": None})
    assert cards.unit_chunk_ids("nta2025:s202:def:term:2", meta) == [m["chunk_id"] for m in meta[:3]]


def test_the_reader_shows_a_subsection_group_with_its_whole_section_in_file_order():
    ids = cards.unit_chunk_ids("nta2025:s4(1)-(5)")
    assert ids == ["nta2025:s4(1)-(5)", "nta2025:s4(6)(a)", "nta2025:s4(6)(b)-(e)"]   # not ntaa2025:s4
    assert cards.unit_chunk_ids("nta2025:s4(6)(a)") == ids
    assert cards.unit_chunk_ids("ntaa2025:s4") == ["ntaa2025:s4"]
    assert cards.unit_chunk_ids("nta2025:s59") == ["nta2025:s59"] or all(i.startswith("nta2025:s59") for i in
                                                                       cards.unit_chunk_ids("nta2025:s59"))


def test_the_reader_groups_a_schedule_chunk_by_schedule_part_and_paragraph():
    ids = cards.unit_chunk_ids("nta2025:sch1:pt1:table1")
    assert "nta2025:sch1:pt1:table1" in ids and not any(":def:" in i for i in ids)
    assert all(i.startswith("nta2025:sch1:pt1") for i in ids)
    assert cards.unit_chunk_ids("no-such-chunk") == []


def test_the_reader_dialog_shows_a_subsection_group_and_a_definition(audit_db, tmp_path):
    steps = (T.final([("Tax is imposed on the profits of companies.", ["P1"]),
                      ("A small company is a defined term.", ["P2"])]),)
    fake = FakeGenerate(*steps, retriever=T.FakeRetriever(["nta2025:s4(1)-(5)", "nta2025:s202:def:small-company"]))
    at = ask(new_app(make_services(audit_db, tmp_path, fake)), "What does the Act say about tax on companies?")
    card = last_card(at)
    assert card.status == "answered" and card.blocks is not None, card.text
    group = next(b for b in at.button if b.key and b.key.endswith("-read-nta2025:s4(1)-(5)"))
    group.click()
    at.run()
    assert not at.exception
    shown = [t.value for t in at.text]
    for chunk_id in cards.unit_chunk_ids("nta2025:s4(1)-(5)"):          # the whole section, three chunks
        assert file_chunks([chunk_id])[chunk_id]["text"] in shown
    assert ui.READER_NOTE in texts(at) and ui.READER_THIS_PASSAGE in texts(at)
    definition = next(b for b in at.button if b.key and b.key.endswith("-read-nta2025:s202:def:small-company"))
    definition.click()
    at.run()
    assert not at.exception and ui.READER_NOTE in texts(at)


# My questions (Section 7.3) --------------------------------------------------------------------------------------


KEY_A, KEY_B = "ab" * 32, "cd" * 32


def history_services(audit_db, tmp_path, key, signed_out_calls=None):
    ident = Identity("signed_in", key, "someone@example.test")
    services = make_services(audit_db, tmp_path, identity=ident)
    if signed_out_calls is not None:
        services.extra["sign_out"] = lambda: signed_out_calls.append(key)
    return services


def seed_history(audit_db, tmp_path):
    out = {}
    for key, question in ((KEY_A, T.E01 + " (user A)"), (KEY_B, T.E01 + " (user B)")):
        audit_db.store.touch_user(key, "google")
        services = history_services(audit_db, tmp_path, key)
        sub = services.service.submit(question, services.identity(), f"session-{key[:2]}")
        outcome = services.service.wait(sub, timeout=30)
        assert outcome.kind == "final"
        out[key] = (question, outcome.reference, sub.query_id)
    return out


def test_two_signed_in_users_see_only_their_own_history(audit_db, tmp_path):
    seeded = seed_history(audit_db, tmp_path)
    for key, other in ((KEY_A, KEY_B), (KEY_B, KEY_A)):
        at = new_app(history_services(audit_db, tmp_path, key), page=HISTORY)
        assert not at.exception
        shown = " ".join(texts(at))
        assert seeded[key][1] in shown and seeded[key][0] in shown
        assert seeded[other][1] not in shown and seeded[other][0] not in shown
        assert [b.key for b in at.button if b.key and b.key.startswith("open-")] == [f"open-{seeded[key][2]}"]


def test_opening_an_answer_shows_it_with_chips_and_records_shown_in_history(audit_db, tmp_path):
    seeded = seed_history(audit_db, tmp_path)
    at = new_app(history_services(audit_db, tmp_path, KEY_A), page=HISTORY)
    at.button(key=f"open-{seeded[KEY_A][2]}").click()
    at.run()
    assert not at.exception
    (stored,) = rows(audit_db, "SELECT rendered, rendered_sha256 FROM {q}.queries WHERE query_id = %s", seeded[KEY_A][2])
    assert stored[0].replace("$", "\\$") in texts(at)
    assert [b for b in at.button if b.key and "-read-" in b.key]      # chips for the supporting passages
    deliveries = rows(audit_db, "SELECT outcome, shown_sha256 FROM {q}.deliveries WHERE query_id = %s", seeded[KEY_A][2])
    assert ("shown_in_history", stored[1]) in deliveries
    assert not rows(audit_db, "SELECT 1 FROM {q}.deliveries WHERE query_id = %s AND outcome = 'shown_in_history'",
                    seeded[KEY_B][2])


def test_delete_my_history_asks_first_then_removes_only_that_users_rows(audit_db, tmp_path):
    seeded = seed_history(audit_db, tmp_path)
    signed_out = []
    at = new_app(history_services(audit_db, tmp_path, KEY_A, signed_out), page=HISTORY)
    at.button(key="delete-ask").click()
    at.run()
    assert ui.DELETE_CONFIRM in [w.value for w in at.warning]
    assert rows(audit_db, "SELECT count(*) FROM {q}.queries") == [(2,)]   # nothing deleted before confirming
    at.button(key="delete-no").click()
    at.run()
    assert rows(audit_db, "SELECT count(*) FROM {q}.queries") == [(2,)]
    at.button(key="delete-ask").click()
    at.run()
    at.button(key="delete-yes").click()
    at.run()
    assert not at.exception
    assert rows(audit_db, "SELECT user_key FROM {q}.queries") == [(KEY_B,)]
    assert rows(audit_db, "SELECT user_key FROM {q}.users") == [(KEY_B,)]
    assert signed_out == [KEY_A]
    assert ui.DELETE_DONE.format(n=1) in texts(at)


def test_the_history_page_needs_a_sign_in(audit_db, tmp_path):
    seed_history(audit_db, tmp_path)
    at = new_app(make_services(audit_db, tmp_path, identity=LOCAL), page=HISTORY)
    assert ui.HISTORY_NOT_SIGNED_IN in [i.value for i in at.info]
    assert not [b for b in at.button if b.key and b.key.startswith("open-")]
    at = new_app(make_services(audit_db, tmp_path, identity=ANONYMOUS), page=HISTORY)
    assert ui.HISTORY_NOT_SIGNED_IN in [i.value for i in at.info]


def test_the_history_page_fails_closed_when_the_startup_check_failed(audit_db, tmp_path):
    services = make_services(audit_db, tmp_path, startup=StartupResult(False, ["x"], None, SNAPSHOT))
    at = new_app(services, page=HISTORY)
    assert ui.UNAVAILABLE_TEXT in [e.value for e in at.error] and at.button == []


def test_a_signed_in_question_is_stored_under_the_users_key(audit_db, tmp_path):
    audit_db.store.touch_user(KEY_A, "google")
    at = ask(new_app(history_services(audit_db, tmp_path, KEY_A)), T.E01)
    assert rows(audit_db, "SELECT tier, user_key FROM {q}.queries") == [("signed_in", KEY_A)]
    assert not at.exception


# Examples and wording ----------------------------------------------------------------------------------------------


def test_examples_are_ten_new_questions_that_pass_the_screen_and_the_free_pre_check():
    from app import main
    from generation.guardrails import load_guardrails_policy, pre_check
    from interface import screen
    examples = main.load_examples()
    assert len(examples) == 10
    assert len({e["topic"] for e in examples}) == 10 and len({e["question"] for e in examples}) == 10
    policy, corpus = load_guardrails_policy(), T.STORE.sections()
    for example in examples:
        question = example["question"]
        assert len(question) <= ui.QUESTION_MAX_CHARS
        assert not screen.blocks(question), question
        check = pre_check(question, policy, corpus)
        assert check.refusal is None, (question, check.refusal)
    assert any("₦" in e["question"] for e in examples)               # at least one calculation


APP_FILES = sorted(p for p in (ROOT / "app").rglob("*") if p.suffix in (".py", ".yml"))


def test_no_section_sign_anywhere_in_the_new_files():
    audit = [ROOT / "python" / "audit" / n for n in ("show_query.py", "compare_log.py", "report.py", "grant_review.py")]
    for path in [*APP_FILES, *(p for p in audit if p.exists())]:
        assert "§" not in path.read_text(encoding="utf-8"), path.name


def test_the_disclaimer_is_imported_never_retyped():
    for path in APP_FILES:
        text = path.read_text(encoding="utf-8")
        assert "licensed tax professional" not in text and "not a substitute" not in text, path.name


REVIEW_TEXT_CALLS = {"title", "subheader", "error", "warning", "info", "success", "button", "text_input", "selectbox",
                     "checkbox", "date_input", "set_page_config"}


def test_every_ui_text_lives_in_copy_py():
    """No literal user-facing text is passed to a Streamlit call in the app files: it is read from copy.py."""
    import ast
    import re
    exempt = {"feedback", "chat_message", "form"}   # widget kinds and ids, not text
    offenders = []
    for path in APP_FILES:
        if path.suffix != ".py" or path.name == "copy.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            base = node.func.value
            if not (isinstance(base, ast.Name) and base.id in ("st", "status", "left", "right")) \
                    and not (isinstance(base, ast.Attribute) and base.attr == "sidebar"):
                continue
            if node.func.attr in exempt or not node.args:
                continue
            # app/review.py is the reviewer-only viewer: its field labels (score, distance, state, ...) are the audit
            # column names and stay inline; its titles, messages, buttons and filters must still come from copy.py.
            if path.name == "review.py" and node.func.attr not in REVIEW_TEXT_CALLS:
                continue
            first = node.args[0]
            parts = [first.value] if isinstance(first, ast.Constant) and isinstance(first.value, str) else (
                [v.value for v in first.values if isinstance(v, ast.Constant)] if isinstance(first, ast.JoinedStr) else [])
            for part in parts:
                if re.search(r"[A-Za-z]{3,}", part):
                    offenders.append((path.name, node.lineno, part[:40]))
    assert offenders == []


def test_hints_say_how_to_rephrase_not_what_the_law_says():
    for hint in filter(None, ui.HINTS.values()):
        assert not any(word in hint.lower() for word in ("section ", "rate of", "%", "must pay", "exempt from"))
