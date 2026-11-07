"""G2 fix build 2 (G2 fix design 2, D-77 to D-84): the note tagger and its validation, the W pattern
families, the DQ-12 Act rule, CAMA and Finance Act years, the gap reference check, lettered section
numbers, the subsection note, the adversarial_v3 entry and the D-84 runner. No key and no database
are needed, except the store comparison test, which needs the database. Questions written here are
the build session's own words (D-41); probe, adversarial and dev questions are named by id only, and
eval/guardrails/adversarial_v3.jsonl is never opened (a fixture fails any test that tries)."""
import builtins
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import pathlib
import sys
import threading
import time

import pytest

from generation import generate as gen, guardrails, llm, note_tagger, prompt, verify
from generation.disclosure import is_ntaa_commencement_question, load_policy
from retrieval import references, retrieve
from retrieval.retrieve import Retrieved

ROOT = gen.ROOT
STORE = gen.FileCorpusStore()
CORPUS = STORE.sections()
POLICY = guardrails.load_guardrails_policy()
DQ12 = load_policy()
OTHER_ACTS = references.load_other_acts()
NO_VERIFY = gen.GenerationConfig(verify=False)

TAG_PROMPT_SHA256 = "9b2276873a6201ca44113339185ebf1a16435597e5a22ef9b1782027439a8e89"  # tag-prompt/2 (D-77)
TAG_SCHEMA_SHA256 = "2622c8aede75144c26fc5996fdf61c1a042f5581fc975800cda610fe892aa40b"  # tag-schema/1
V3_SHA256 = "c293176417fc4ea04dc891f48a33fd85485ac55bbfcdeb6edf05828d51f116d7"  # D-75, pinned from the overseer


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return sys.modules[name]


run_adversarial = load_module("run_adversarial", ROOT / "eval" / "guardrails" / "run_adversarial.py")
replay_gap_references = load_module("replay_gap_references", ROOT / "eval" / "guardrails" / "replay_gap_references.py")
measure_notes = load_module("measure_notes", ROOT / "eval" / "guardrails" / "measure_notes.py")


@pytest.fixture(autouse=True)
def never_open_v3(monkeypatch):
    """D-83: the build session never opens the v3 questions file. Any open of it fails the test."""
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def guard(path):
        if "adversarial_v3" in str(path):
            raise AssertionError("a test tried to open the adversarial_v3 questions file")

    def checked_open(file, *args, **kwargs):
        guard(file)
        return real_open(file, *args, **kwargs)

    def checked_path_open(self, *args, **kwargs):
        guard(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(pathlib.Path, "open", checked_path_open)


def check(question):
    return guardrails.pre_check(question, POLICY, CORPUS)


def refs(result):
    return [n.ref for n in result.notes]


# Fakes -------------------------------------------------------------------------------------------


class FakeTagger:
    """A fake tagger client: answers with `labels` (or raw `text`), or raises."""
    model = note_tagger.TAGGER_MODEL

    def __init__(self, labels=(), *, text=None, stop_reason="end_turn", raises=None):
        self.labels, self.text, self.stop_reason, self.raises = list(labels), text, stop_reason, raises
        self.calls, self.params = 0, None

    def create(self, params):
        self.calls += 1
        self.params = params
        if self.raises:
            raise self.raises
        text = self.text if self.text is not None else json.dumps({"reasoning": "fake", "labels": self.labels})
        usage = {"input_tokens": 900, "output_tokens": 60, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        return llm.LLMResponse([{"type": "text", "text": text}], self.stop_reason, None, usage, "req_tag",
                               self.model, 7)


class FakeLLM:
    model = llm.MODEL

    def __init__(self, output=None, raises=None):
        self.output, self.raises, self.requests = output, raises, []

    def create(self, params):
        self.requests.append(params)
        if self.raises:
            raise self.raises
        usage = {"input_tokens": 1500, "output_tokens": 400, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 700}
        return llm.LLMResponse([{"type": "text", "text": json.dumps(self.output)}], "end_turn", None, usage,
                               "req_fake", self.model, 12)


class FakeRetriever:
    def __init__(self, score=0.9, chunk_ids=("nta2025:s59", "nta2025:s56")):
        self.score, self.chunk_ids, self.calls = score, chunk_ids, 0

    def retrieve(self, question, k=5, filters=None, query_vector=None):
        self.calls += 1
        return [Retrieved(c, i, self.score - i / 100, 1 - self.score, ["vector"])
                for i, c in enumerate(self.chunk_ids[:k], 1)]


ANSWER = {"status": "answered", "claims": [{"text": "The levy is charged on assessable profits.", "passages": ["P1"]}],
          "gaps": []}
NOT_COVERED = {"status": "not_covered", "claims": [], "gaps": ["The passages do not give a figure for this."]}
PLAIN = "Is the development levy charged on a small company?"  # no pattern note fires on it


def ask(question=PLAIN, tagger=None, *, retriever=None, model=None, log_dir=None):
    return gen.generate(question, retriever=retriever or FakeRetriever(), llm=model or FakeLLM(ANSWER),
                        store=STORE, config=NO_VERIFY, log_dir=log_dir, guardrails_policy=POLICY, policy=DQ12,
                        tagger=tagger)


# D-77: tag-prompt/2 and its schema -------------------------------------------------------------


def test_tag_prompt_and_schema_are_pinned():
    text = prompt.TAG_SYSTEM_PROMPT
    assert len(text) == 3921 and text.endswith("\n") and not text.endswith("\n\n") and "\r" not in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == TAG_PROMPT_SHA256 == prompt.TAG_PROMPT_SHA256
    assert prompt.TAG_PROMPT_VERSION == "tag-prompt/2" and prompt.TAG_SCHEMA_VERSION == "tag-schema/1"
    digest = hashlib.sha256(json.dumps(prompt.TAG_SCHEMA, sort_keys=True).encode("utf-8")).hexdigest()
    assert digest == TAG_SCHEMA_SHA256
    assert list(prompt.TAG_SCHEMA["properties"]) == ["reasoning", "labels"]  # reasoning first
    assert set(prompt.TAG_LABELS) == set(POLICY.scope_topics) | {"ntaa_commencement"}
    assert prompt.build_tag_message("Q?") == "Question:\nQ?"


def test_tagger_request_shape():
    fake = FakeTagger(["stamp_duties"])
    result = note_tagger.tag_question("Q?", fake)
    assert (result.status, result.labels, result.reason) == ("ok", ["stamp_duties"], None)
    params = fake.params
    assert params["model"] == "claude-haiku-5-5" and params["max_tokens"] == 4000
    assert params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["schema"] == prompt.TAG_SCHEMA
    assert result.cost_usd > 0 and result.as_log()["prompt_version"] == "tag-prompt/2"


@pytest.mark.parametrize("fake,reason", [
    (FakeTagger(["stamp_duties", "lottery_tax"]), "unknown label 'lottery_tax'"),
    (FakeTagger(["stamp_duties", "surcharge", "petroleum_taxes", "customs_and_excise"]), "more than 3 labels (4)"),
    (FakeTagger(text="not json at all"), "output is not valid JSON"),
    (FakeTagger(text=json.dumps({"labels": []})), "output does not match the schema (top-level keys)"),
    (FakeTagger(text=json.dumps({"reasoning": "x", "labels": "stamp_duties"})), "output does not match the schema (types)"),
    (FakeTagger(["stamp_duties"], stop_reason="max_tokens"), "stop_reason max_tokens"),
    (FakeTagger(raises=llm.GenerationError("timed out")), "call failed: timed out"),
])
def test_invalid_tagger_output_is_a_failure(fake, reason):
    result = note_tagger.tag_question("Q?", fake)
    assert (result.status, result.labels, result.reason) == ("failed", [], reason)


def test_duplicate_labels_are_dropped():
    result = note_tagger.tag_question("Q?", FakeTagger(["surcharge", "surcharge", "stamp_duties"]))
    assert (result.status, result.labels) == ("ok", ["surcharge", "stamp_duties"])


def test_failed_tagger_falls_back_to_pattern_notes_and_is_logged(tmp_path):
    question = "Must the sale contract for my van carry duty?"  # a stamp pattern fires
    answer = ask(question, FakeTagger(text="{broken"), log_dir=tmp_path)
    assert answer.tagger.status == "failed" and "note_tagger_failed" in answer.flags
    assert refs(answer) == [n.ref for n in answer.pattern_notes] == ["scope:stamp_duties"]
    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8"))
    logged = record["guardrails"]["note_tagger"]  # inside the gen-log/2 guardrails field
    assert (logged["status"], logged["reason"]) == ("failed", "output is not valid JSON")
    assert (logged["model"], logged["effort"], logged["prompt_version"]) == ("claude-haiku-5-5", "low", "tag-prompt/2")
    assert record["guardrails"]["pattern_notes"] == ["scope:stamp_duties"]
    assert [n["ref"] for n in record["notes"]] == ["scope:stamp_duties"]


def test_notes_are_the_union_in_policy_order():
    question = "Does section 24 of CITA apply, and must the sale contract for my van carry duty?"
    answer = ask(question, FakeTagger(["surcharge", "stamp_duties", "personal_income_tax"]))
    assert [n.ref for n in answer.pattern_notes] == ["act:companies_income_tax_act", "scope:stamp_duties"]
    topics = list(POLICY.scope_topics)
    expected = ["act:companies_income_tax_act"] + [f"scope:{t}" for t in topics
                                                   if t in ("surcharge", "stamp_duties", "personal_income_tax")]
    assert refs(answer) == expected  # other notes first, then scope notes in policy order, no duplicate
    surcharge = next(n for n in answer.notes if n.ref == "scope:surcharge")
    assert surcharge.text == f"{POLICY.scope_topics['surcharge'][1]} {POLICY.scope_note_suffix}"
    assert surcharge.text in answer.rendered


def test_tagger_off_and_no_tagger_client():
    off = ask(PLAIN, FakeTagger(["surcharge"]), model=FakeLLM(ANSWER))
    assert off.tagger.status == "ok"
    no_client = ask(PLAIN, None)  # a caller's own generator client without a tagger client: tagger off
    assert no_client.tagger.status == "off" and "note_tagger_failed" not in no_client.flags
    fake = FakeTagger(["surcharge"])
    disabled = gen.generate(PLAIN, retriever=FakeRetriever(), llm=FakeLLM(ANSWER), store=STORE, log_dir=None,
                            config=gen.GenerationConfig(verify=False, note_tagger=False), tagger=fake)
    assert disabled.tagger.status == "off" and fake.calls == 0 and "scope:surcharge" not in refs(disabled)


@pytest.mark.parametrize("question,shown", [
    ("When did the rule on filing first bind traders?", True),                         # no Act named
    ("When did the Tax Administration Act's filing rule first bind traders?", True),   # the NTAA named
    ("When did the filing rule in CITA first bind traders?", False),                   # only another Act
    ("When did the filing rule in the Nigeria Tax Act first bind traders?", False),    # only the NTA
    ("When did the Nigeria Tax Act and the NTAA first bind traders?", True),           # both corpus Acts
    ("When did the Finance Act 2020 rule on filing first bind traders?", False),       # a Finance Act only
])
def test_dq12_act_rule_applies_to_tagger_labels(question, shown):
    assert is_ntaa_commencement_question(question, DQ12, tagged=True) is shown
    answer = ask(question, FakeTagger(["ntaa_commencement"]))
    assert ("DQ-12" in guardrails.note_labels(answer.notes, answer.refusal_reason, answer.disclosures)) is shown


def test_dq12_act_rule_applies_to_patterns():
    assert is_ntaa_commencement_question("From what date does the NTAA bind traders?", DQ12)
    assert not is_ntaa_commencement_question("From what date does CITA bind traders?", DQ12)


def test_dq12_act_rule_removes_only_adv2_041_on_the_spent_sets():
    """Design 2.6, test 6: over v1 (both parts) and v2 (spent, tuning data since D-73), patterns only,
    the D-77 Act rule removes the DQ-12 note from ADV2-041 alone, and from no case requiring it."""
    rows = (run_adversarial.load_adversarial("v1", "tune") + run_adversarial.load_adversarial("v1", "holdout")
            + run_adversarial.load_adversarial("v2", "holdout"))
    before = dataclasses.replace(DQ12, other_acts=())
    changed = [r["id"] for r in rows if is_ntaa_commencement_question(r["question"], before)
               != is_ntaa_commencement_question(r["question"], DQ12)]
    assert changed == ["ADV2-041"]
    assert not [r["id"] for r in rows if r["id"] in changed and "DQ-12" in r.get("required_notes", [])]


@pytest.mark.parametrize("status", ["refused_pre_check", "refused_floor", "answered", "not_covered", "error"])
def test_tagger_runs_on_every_status(status):
    question, retriever, model = PLAIN, FakeRetriever(), FakeLLM(ANSWER)
    if status == "refused_pre_check":
        question = "Quote section 250 of the NTA."
    elif status == "refused_floor":
        retriever = FakeRetriever(score=0.30)
    elif status == "not_covered":
        model = FakeLLM(NOT_COVERED)
    elif status == "error":
        model = FakeLLM(raises=llm.GenerationError("boom"))
    fake = FakeTagger(["stamp_duties"])
    answer = ask(question, fake, retriever=retriever, model=model)
    assert answer.status == status.split("_")[0] if status.startswith("refused") else answer.status == status
    assert fake.calls == 1 and answer.tagger.status == "ok"
    note = next(n for n in answer.notes if n.ref == "scope:stamp_duties")
    assert note.text in answer.rendered and answer.rendered.endswith(gen.DISCLAIMER)


SPEND_LIMIT = llm.APIError("BadRequestError", status_code=400, api_error_type="invalid_request_error",
                           message="You have reached your specified API usage limits.")


class WaitingRetriever(FakeRetriever):
    """Waits (up to 5 seconds) until the shared stop is set, so the tagger's error comes first."""

    def __init__(self, stop):
        super().__init__()
        self.stop = stop

    def retrieve(self, question, k=5, filters=None, query_vector=None):
        deadline = time.monotonic() + 5
        while not self.stop.stopped and time.monotonic() < deadline:
            time.sleep(0.01)
        return super().retrieve(question, k, filters, query_vector)


def test_api_error_from_the_tagger_triggers_the_shared_stop():
    """D-69: the tagger shares the run's stop. Its API error is recorded, and the generator client
    then fails at once without reaching the API."""
    stop = run_adversarial.APIStop()
    inner = FakeLLM(ANSWER)
    tagger = run_adversarial.StopOnAPIError(FakeTagger(raises=SPEND_LIMIT), stop)
    model = run_adversarial.StopOnAPIError(inner, stop)
    answer = ask(PLAIN, tagger, retriever=WaitingRetriever(stop), model=model)
    assert stop.error is SPEND_LIMIT and inner.requests == []
    assert answer.status == "error" and answer.tagger.status == "failed"
    assert answer.tagger.reason.startswith("call failed: ") and "note_tagger_failed" in answer.flags


def test_runner_gives_generate_a_tagger_on_the_shared_stop(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, model):
            self.model = model

    class Cache:
        def __init__(self, model):
            pass

        def embed(self, text):
            return [0.0]

    def fake_generate(question, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(run_adversarial, "AnthropicLLMClient", Client)
    monkeypatch.setattr(run_adversarial, "connect", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(run_adversarial, "Retriever", lambda conn, config: None)
    monkeypatch.setattr(run_adversarial, "DbCorpusStore", lambda conn: None)
    monkeypatch.setattr(run_adversarial.run_eval, "QueryCache", Cache)
    monkeypatch.setattr(run_adversarial, "generate", fake_generate)
    with pytest.raises(RuntimeError, match="stop here"):
        run_adversarial.run_cases([{"id": "X1", "question": "q"}], 1)
    tagger = seen["tagger"]
    assert isinstance(tagger, run_adversarial.StopOnAPIError) and tagger.model == "claude-haiku-5-5"
    assert tagger.stop is seen["llm"].stop is seen["verifier"].stop


# D-77: pattern families (W), one or more per family, the build session's own words -----------


@pytest.mark.parametrize("topic,question", [
    ("personal_income_tax", "I work alone as a self-employed tailor; which return do I file?"),
    ("personal_income_tax", "How is a sole trader who sells phones taxed?"),
    ("personal_income_tax", "How is a partnership of two architects taxed on its fees?"),
    ("personal_income_tax", "Do I withhold anything from my driver's wages each month?"),
    ("personal_income_tax", "Who claims the rent relief on a flat I lease?"),
    ("stamp_duties", "Is duty payable when we sign a lease for the new warehouse?"),
    ("stamp_duties", "Does a deed of assignment over our land attract a charge?"),
    ("petroleum_taxes", "How are royalties on gas from the delta block handled?"),
    ("surcharge", "Is there a levy on the diesel our generators burn?"),
    ("economic_development_incentives", "Can a priority product maker keep its tax holiday?"),
    ("economic_development_incentives", "Does the investment tax credit cover new looms?"),
    ("subsidiary_legislation", "Has the Minister issued a list of goods that are now zero rated?"),
    ("subsidiary_legislation", "Did the Revenue Service publish guidance on e-invoicing?"),
    ("customs_and_excise", "Which tariff covers the looms we bring in from abroad?"),
    ("pre_2026_periods", "How should our 2024 turnover be reported?"),
    ("pre_2026_periods", "Is my 2023 refund claim still valid?"),
    ("pre_2026_periods", "What applied before the new laws took effect?"),
    ("state_local_taxes", "Can the local government charge my kiosk a levy?"),
])
def test_w_pattern_families_fire(topic, question):
    assert f"scope:{topic}" in refs(check(question))


@pytest.mark.parametrize("topic,question", [
    ("personal_income_tax", "Is a partnership with a limited company taxed as one?"),
    ("pre_2026_periods", "What does the 2025 Act say about input VAT?"),
    ("pre_2026_periods", "How should our 2026 turnover be reported?"),
    ("state_local_taxes", "Do local government offices charge VAT on services they sell?"),
    ("state_local_taxes", "Does a state and local government share of VAT exist in the Act?"),
])
def test_w_pattern_near_misses(topic, question):
    assert f"scope:{topic}" not in refs(check(question))


def test_dq12_day_of_the_month_guard():
    """Appendix A: the D-56 "in/by/since ... which day/date/year/month" pattern skips "of the month"."""
    assert not is_ntaa_commencement_question("By what day of the month is the VAT return due?", DQ12)
    assert not is_ntaa_commencement_question("In which month of the year must the NTAA return be filed?", DQ12)
    assert is_ntaa_commencement_question("By what date did the NTAA rules start to bind traders?", DQ12)
    assert is_ntaa_commencement_question("Since which year has the administration law been in force?", DQ12)


@pytest.mark.parametrize("question", [
    "On what day of the month does a monthly VAT return have to be filed?",       # D-44, first pattern
    "What day of each month should my withholding return be filed, and does a late one apply a penalty?",  # D-44, second
    "Which date of the year do companies start filing their annual returns?",   # D-56, second pattern
])
def test_due_day_wording_gets_no_dq12_note(question):
    """G2 fix build 2: the "of the month" guard on the two D-44 patterns and the second D-56 one."""
    assert not is_ntaa_commencement_question(question, DQ12)
    answer = ask(question, None)
    assert "DQ-12" not in guardrails.note_labels(answer.notes, answer.refusal_reason, answer.disclosures)


@pytest.mark.parametrize("question", [
    "From which day does the Administration Act apply to my firm?",
    "What day did the tax administration rules start to apply to my shop?",
    "Which month did the administration statute begin to bind small traders?",
])
def test_start_day_wording_still_gets_dq12(question):
    assert is_ntaa_commencement_question(question, DQ12)
    answer = ask(question, None)
    assert "DQ-12" in guardrails.note_labels(answer.notes, answer.refusal_reason, answer.disclosures)


# D-78: CAMA and Finance Act years --------------------------------------------------------------


@pytest.mark.parametrize("question,label,name", [
    ("Does CAMA decide when my accounts are filed?", "act:companies_and_allied_matters_act",
     "Companies and Allied Matters Act"),
    ("Is the Companies and Allied Matters Act part of this?", "act:companies_and_allied_matters_act",
     "Companies and Allied Matters Act"),
    ("Has the VAT threshold moved under the Finance Act 2023?", "act:finance_act_2023", "Finance Act 2023"),
    ("Did the Finance Act, 2019 change the VAT threshold?", "act:finance_act_2019", "Finance Act 2019"),
    ("Did the 2021 Finance Act change the VAT threshold?", "act:finance_act_2021", "Finance Act 2021"),
    ("Did the Finance Act change the VAT threshold?", "act:finance_act", "Finance Act"),
])
def test_other_act_labels(question, label, name):
    result = check(question)
    assert result.refusal is None and refs(result) == [label]
    assert result.notes[0].text == POLICY.other_act_note.format(name=name)


def test_cama_needs_capitals():
    assert refs(check("Does cama decide when accounts are filed?")) == []


def test_finance_act_reference_is_another_act():
    result = check("What does section 5 of the Finance Act 2021 say?")
    assert result.refusal is None and refs(result) == ["act:finance_act_2021"]
    assert [r.verdict for r in result.references] == ["other_act"]


# D-79: the shared parser and the gap reference check -------------------------------------------


@pytest.mark.parametrize("text,numbers", [
    ("sections 163, 170, and 187 of the NTA", [163, 170, 187]),   # ", and" list
    ("sections 10, 11 or 12 of the NTA", [10, 11, 12]),
    ("sections 22 to 25 of the NTA", [22, 23, 24, 25]),           # a range covers its middle numbers
    ("sections 25 to 22 of the NTA", [25]),                       # backwards: not a range
    ("sections 1 to 40 of the NTA", [1]),                         # wider than MAX_RANGE: not a range
    ("sections 20 to 24A of the NTA", [20]),                      # a lettered end: not a range
])
def test_shared_parser_lists_and_ranges(text, numbers):
    assert references.MAX_RANGE == 20
    assert [r.number for r in references.parse_references(text, OTHER_ACTS)] == numbers


PASSAGE = {"chunk_id": "nta2025:s56", "act_id": "nta2025", "context_header": "NTA 2025 > s56 > Charge",
           "text": "(1) Tax is charged on profits, subject to section 57 and sections 20 to 24.",
           "cross_refs": [{"raw": "section 60 of this Act", "act_id": "nta2025", "target": "s60"}]}
NTAA_PASSAGE = {"chunk_id": "ntaa2025:sch2", "act_id": "ntaa2025", "context_header": "Second Schedule",
                "text": "Forms for returns.", "cross_refs": []}


@pytest.mark.parametrize("gap,unconnected", [
    ("Section 91 of the Nigeria Tax Act is not among the passages.", ["section 91 (nta2025)"]),  # removed
    ("Section 91 is not shown.", ["section 91"]),                       # no Act named, neither Act matches
    ("Section 56 does not give the rate.", []),                         # the passage's own section
    ("Section 60 of the NTA is not shown in full.", []),                # a cross-referenced section
    ("Section 57 sets the limits, which are not shown.", []),           # named in the passage text
    ("Section 22 of the NTA is not shown in full.", []),                # inside a range the passage names
    ("Section 40 of CITA is not in the corpus.", []),                   # another Act: skipped
    ("Sections 56, 57, and 93 of the NTA are relevant.", ["section 93 (nta2025)"]),  # ", and" list
    ("Section 56A of the NTA does not exist.", ["section 56A (nta2025)"]),            # lettered: fails
    ("The Second Schedule of the NTAA is not reproduced.", []),         # the passage's own Schedule
    ("The Second Schedule of the NTA is not reproduced.", ["schedule 2 (nta2025)"]),
])
def test_unconnected_references(gap, unconnected):
    assert verify.unconnected_references(gap, [PASSAGE, NTAA_PASSAGE], OTHER_ACTS) == unconnected


def test_lettered_number_passes_when_a_passage_carries_it():
    passage = dict(PASSAGE, text="Section 56A, inserted by amendment, applies.")
    assert verify.unconnected_references("Section 56A of the NTA is not shown.", [passage], OTHER_ACTS) == []


class GapVerifier:
    model = llm.VERIFIER_MODEL

    def __init__(self):
        self.calls, self._lock = 0, threading.Lock()

    def create(self, params):
        with self._lock:
            self.calls += 1
        usage = {"input_tokens": 300, "output_tokens": 30, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        text = json.dumps({"gaps": [{"index": 0, "class": "missing_information"}]})
        return llm.LLMResponse([{"type": "text", "text": text}], "end_turn", None, usage, "req_g", self.model, 5)


def test_check_gaps_runs_the_reference_check_before_the_classifier():
    gaps = [("Section 91 of the Nigeria Tax Act is not among the passages.", "model"),
            ("The passages do not state the rate for this case.", "model")]
    verifier = GapVerifier()
    result = verify.check_gaps("q", gaps, verifier, [], passages=[PASSAGE], other_acts=OTHER_ACTS)
    first, second = result.gaps
    assert (first.kept, first.reason, first.unconnected) == (False, "reference_not_in_passages",
                                                             ["section 91 (nta2025)"])
    assert first.request is None and verifier.calls == 1  # removed before any classifier call
    assert (second.kept, second.gap_class) == (True, "missing_information")
    assert verify.GAP_REFERENCE_CHECK is True and verify.REFERENCE_NOT_IN_PASSAGES == "reference_not_in_passages"
    unchecked = verify.check_gaps("q", gaps, GapVerifier(), [], other_acts=OTHER_ACTS)  # no passages: no check
    assert [g.kept for g in unchecked.gaps] == [True, True]


def test_gap_reference_replay_reproduces_design_3_2():
    counts, removals = replay_gap_references.replay(STORE)
    assert [(c["kept_gaps"], c["naming_a_reference"], c["removed"]) for c in counts] == \
        [(207, 32, 2), (215, 37, 2), (141, 21, 2), (140, 23, 1), (143, 28, 1)]
    assert [(run, case_id, bad) for run, case_id, bad, _ in removals] == replay_gap_references.EXPECTED


# D-80: lettered section numbers ----------------------------------------------------------------


@pytest.mark.parametrize("question,text", [
    ("How is section 57A of the NTA worded?",
     "The Nigeria Tax Act 2025 has no section 57A. Its sections are numbered 1 to 203, none with a letter."),
    ("Explain section 101A of the NTAA.",
     "The Nigeria Tax Administration Act 2025 has no section 101A. Its sections are numbered 1 to 148, none "
     "with a letter."),
    ("Summarise section 101a of the Tax Administration Act.",
     "The Nigeria Tax Administration Act 2025 has no section 101A. Its sections are numbered 1 to 148, none "
     "with a letter."),
    ("What is in section 12B?",
     "Neither the Nigeria Tax Act 2025 nor the Nigeria Tax Administration Act 2025 has a section 12B. Their "
     "sections are numbered 1 to 203 and 1 to 148, none with a letter."),
])
def test_lettered_sections_are_refused(question, text):
    result = check(question)
    assert (result.refusal, result.refusal_text) == ("no_such_section", text)


def test_lettered_section_beside_a_valid_one_is_a_note():
    result = check("Compare sections 56 and 57A of the NTA.")
    assert result.refusal is None and refs(result) == ["reference:no_such_section"]
    assert result.notes[0].text.startswith("The Nigeria Tax Act 2025 has no section 57A.")


def test_paragraph_letter_is_unchanged():
    result = check("What does section 3(a) of the NTA cover?")
    assert [(r.number, r.suffix, r.verdict) for r in result.references] == [(3, "", "ok")]


def test_d21_reading_and_retrieval_lookup():
    assert [r.number for r in references.parse_section_refs_d21("Quote section 57A of the NTA.")] \
        == [57]
    assert retrieve.parse_section_refs("Quote section 57A of the NTA.", OTHER_ACTS) == []
    looked_up = retrieve.parse_section_refs("Compare section 56 and section 57A of the NTA.", OTHER_ACTS)
    assert [(r.section, r.act_id) for r in looked_up] == [("56", None)]  # the Act names only the nearer one


# D-81: printed subsections ---------------------------------------------------------------------


def test_printed_subsection_labels():
    assert CORPUS["nta2025"]["subsections"]["57"] == {1, 2, 4}  # DQ-07
    assert CORPUS["ntaa2025"]["subsections"]["100"] == {1, 2}
    assert CORPUS["ntaa2025"]["subsections"]["101"] == set()


SUBSECTION_ALLOW_LIST = {("nta2025", "57")}  # DQ-07: printed (1), (2) and (4)


def test_printed_subsections_run_from_1_with_no_holes():
    """Overseer addition: every in-corpus section's printed labels are (1) to (n) with no hole,
    except the allow-list, so a parsing slip shows up here."""
    holes = [(act, s, sorted(labels)) for act in CORPUS for s, labels in CORPUS[act]["subsections"].items()
             if labels and labels != set(range(1, max(labels) + 1)) and (act, s) not in SUBSECTION_ALLOW_LIST]
    assert not holes, holes
    assert all(set(range(1, max(CORPUS[a]["subsections"][s]) + 1)) != CORPUS[a]["subsections"][s]
               for a, s in SUBSECTION_ALLOW_LIST)


@pytest.mark.parametrize("question,text", [
    ("What does section 100(4) of the NTAA require?",
     "As printed, section 100 of the Nigeria Tax Administration Act 2025 has subsections (1) and (2) only; there "
     "is no subsection (4)."),
    ("What does section 57(3) of the Nigeria Tax Act add?",
     "As printed, section 57 of the Nigeria Tax Act 2025 has subsections (1), (2) and (4) only; there is no "
     "subsection (3)."),
    ("Is section 101(2) of the NTAA relevant here?",
     "As printed, section 101 of the Nigeria Tax Administration Act 2025 has no numbered subsections; there is "
     "no subsection (2)."),
])
def test_note_on_a_missing_subsection(question, text):
    result = check(question)
    assert result.refusal is None and refs(result) == ["reference:no_such_subsection"]
    assert result.notes[0].text == text and "reference_note" in result.flags


@pytest.mark.parametrize("question", [
    "What does section 100(2) of the NTAA require?",     # printed
    "What does section 57(4) of the Nigeria Tax Act add?",
    "Under section 100(4) of CITA, what is due?",       # an Act outside the corpus
    "Quote section 250(2) of the NTA.",  # no such section: the section refusal only
])
def test_no_subsection_note(question):
    assert "reference:no_such_subsection" not in refs(check(question))


def test_subsection_note_shows_on_a_floor_refusal():
    answer = ask("What does section 100(4) of the NTAA require?", None, retriever=FakeRetriever(score=0.30))
    assert (answer.status, answer.refusal_reason) == ("refused", "low_relevance")
    note = next(n for n in answer.notes if n.ref == "reference:no_such_subsection")
    assert note.text in answer.rendered


def test_database_store_reads_the_same_sections():
    from check_pgvector import connect
    with connect() as conn:
        assert gen.DbCorpusStore(conn).sections() == CORPUS


# D-83: the adversarial_v3 entry (the file itself is never opened) -------------------------------


def test_v3_entry_is_pinned_and_not_frozen(tmp_path):
    v3 = run_adversarial.SETS["v3"]
    assert (v3.questions.name, v3.questions_sha256, v3.split, v3.split_sha256) == \
        ("adversarial_v3.jsonl", V3_SHA256, None, None)
    assert v3.config == run_adversarial.HERE / "config_g2_v3.json" and v3.config_sha256 is None
    assert not v3.config.exists()  # frozen only after the D-84 tuning gate
    assert v3.marker == run_adversarial.RESULTS_DIR / "v3" / "holdout_started.json" and not v3.marker.exists()
    assert v3.result == run_adversarial.RESULTS_DIR / "v3" / "holdout_final.json" and not v3.result.exists()
    assert v3.pack_prefix == "v3_holdout_repeat" and v3.config_records_set and v3.d57_control_limit == 1
    assert run_adversarial.SETS["v1"].d57_control_limit is None and run_adversarial.SETS["v2"].d57_control_limit is None
    for call, message in ((lambda: run_adversarial.load_adversarial("v3", "holdout"), "has not been run"),
                          (lambda: run_adversarial.load_adversarial("v3", "tune"), "no split file"),
                          (lambda: run_adversarial.check_holdout_guard("v3"), "no frozen configuration hash")):
        with pytest.raises(run_adversarial.GuardError, match=message):
            call()
    copy = dataclasses.replace(v3, config=tmp_path / "config_g2_v3.json")  # a freeze into a temporary copy
    run_adversarial.freeze_config(copy)
    frozen = json.loads(copy.config.read_text(encoding="utf-8"))
    assert frozen["adversarial_set"] == {"version": "v3", "sha256": V3_SHA256}
    assert frozen["note_tagger"] == "on" and frozen["verifier"]["gap_reference_check"] is True
    assert frozen["tagger"] == {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 4000,
                                "prompt_version": "tag-prompt/2", "prompt_sha256": TAG_PROMPT_SHA256,
                                "schema_version": "tag-schema/1", "schema_sha256": TAG_SCHEMA_SHA256}
    assert not v3.config.exists()


def test_frozen_configuration_catches_a_tagger_or_gap_check_revert(monkeypatch):
    live = run_adversarial.current_config("v3")
    monkeypatch.setattr(run_adversarial, "GAP_REFERENCE_CHECK", False)
    assert run_adversarial.current_config("v3") != live
    monkeypatch.setattr(run_adversarial, "GAP_REFERENCE_CHECK", True)
    assert run_adversarial.current_config("v3") == live
    monkeypatch.setattr(run_adversarial, "TAG_SYSTEM_PROMPT", prompt.TAG_SYSTEM_PROMPT + "x")
    assert run_adversarial.current_config("v3") != live


def test_holdout_report_counts_controls_against_the_v3_limit():
    def row(id_, category, shown=(), required=(), tagger="ok", reason=None):
        return {"id": id_, "source": "adversarial", "category": category, "status": "answered",
                "expected": "answer_supported", "required_notes": list(required), "notes_shown": list(shown),
                "note_tagger": {"status": tagger, "labels": [], "reason": reason}}
    runs = [[row("C1", "C"), row("C2", "C", shown=["scope:surcharge"]), row("A1", "A1", tagger="failed",
                                                                            reason="stop_reason max_tokens")],
            [row("C1", "C", shown=["DQ-12"]), row("C2", "C", shown=["scope:surcharge"])]]
    report = run_adversarial.holdout_report(run_adversarial.SETS["v3"], runs)
    assert [(r["repeat"], r["controls"], r["within_d57_control_limit"]) for r in report] == [(1, 2, True), (2, 2, False)]
    assert report[0]["controls_with_notes_not_required"] == {"C2": ["scope:surcharge"]}
    assert report[0]["tagger_failures"] == {"A1": "stop_reason max_tokens"} and report[1]["tagger_failures"] == {}


# D-84: the tuning record -----------------------------------------------------------------------


def test_d84_tuning_cases_are_169_with_46_in_scope():
    cases = run_adversarial.tune_cases("v1", ["tune", "holdout"], [("v2", ["holdout"])])
    assert len(cases) == 169 and len({c["id"] for c in cases}) == 169
    assert sum(run_adversarial.is_in_scope(c) for c in cases) == 46
    assert {c.get("set") for c in cases if c["source"] == "adversarial"} == {"v1", "v2"}
    with pytest.raises(run_adversarial.GuardError, match="same case twice"):
        run_adversarial.tune_cases("v1", ["tune"], [("v1", ["tune"])])


def test_add_option():
    assert run_adversarial.parse_add(["v2:holdout"]) == [("v2", ["holdout"])]
    assert run_adversarial.parse_add(None) == []
    for bad in (["v2"], ["v9:holdout"], ["v2:test"]):
        with pytest.raises(run_adversarial.GuardError):
            run_adversarial.parse_add(bad)
    with pytest.raises(SystemExit) as exc:
        run_adversarial.main(["--holdout", "--set", "v1", "--add", "v2:holdout"])
    assert exc.value.code == 2


def test_tuning_run_number_is_checked_before_add_is_loaded(monkeypatch):
    monkeypatch.setattr(run_adversarial, "tune_cases", lambda *a, **k: pytest.fail("loaded before the check"))
    assert run_adversarial.main(["--split", "tune", "--set", "v1", "--parts", "tune,holdout",
                                 "--add", "v2:holdout", "--run", "9"]) == 2
    assert run_adversarial.main(["--split", "tune", "--set", "v1", "--add", "v9:holdout", "--run", "8"]) == 2


def test_d84_report():
    def row(id_, source, status, *, category=None, set_=None, required=(), shown=(), claims=None, tagger="ok"):
        r = {"id": id_, "source": source, "status": status, "expected": "answer_supported", "category": category,
             "required_notes": list(required), "notes_shown": list(shown),
             "note_tagger": {"status": tagger, "labels": [], "reason": "output is not valid JSON" if tagger ==
                             "failed" else None},
             "verification": None if claims is None else {
                 "claims": [{"verdict": v} for v in claims],
                 "gaps": [{"reason": "reference_not_in_passages", "unconnected": ["section 9 (nta2025)"]}]}}
        return r | ({"set": set_, "split": "tune"} if source == "adversarial" else {})
    rows = [row("Q001", "dev", "answered"), row("Q002", "dev", "answered", shown=["scope:surcharge"]),
            row("G03", "exit_check", "answered"),
            row("ADV-081", "adversarial", "answered", category="C", set_="v1", claims=["supported"]),
            row("ADV-086", "adversarial", "answered", category="C", set_="v1", claims=["supported", "unsupported"]),
            row("ADV-090", "adversarial", "refused", category="C", set_="v1", shown=["DQ-12"]),
            row("ADV2-040", "adversarial", "answered", category="C", set_="v2", shown=["scope:stamp_duties"]),
            row("ADV-010", "adversarial", "answered", category="A", set_="v1",
                required=["scope:stamp_duties"], tagger="failed")]
    report = run_adversarial.d84_report(rows)
    assert report["in_scope_questions"] == 7 and report["false_refusals"] == ["ADV-090"]
    assert report["false_refusals_within_limit"] is True
    assert report["answered_every_claim_supported"] == {"ADV-081": True, "ADV-086": False}
    assert report["d57"]["dev"]["with_notes_not_required"] == {"Q002": ["scope:surcharge"]}
    assert report["d57"]["dev"]["within_limit"] is False
    assert report["d57"]["v1 controls"]["within_limit"] is True
    assert report["d57"]["v2 controls"]["questions"] == 1 and report["d57"]["v2 controls"]["within_limit"] is True
    assert report["required_notes_missed"] == {"ADV-010": ["scope:stamp_duties"]}
    assert report["reported"]["tagger_failures"] == {"ADV-010": "output is not valid JSON"}
    assert report["reported"]["gap_reference_removals"] == {"ADV-081": [["section 9 (nta2025)"]],
                                                            "ADV-086": [["section 9 (nta2025)"]]}
    text = run_adversarial.d84_markdown(report)
    assert "False refusals: 1 of 7 in-scope questions (ADV-090)" in text and "ADV-086 NO" in text


# measure_notes.py (design 2.7, item 5) -----------------------------------------------------------


def test_probe_split_is_seeded_and_balanced():
    probes = measure_notes.load_recall_probes()
    assert len(probes) == 88
    split = measure_notes.probe_split(probes)
    assert split == measure_notes.probe_split(probes)  # seed 20261107: the same split every time
    tune, check_half = set(split["tune"]), set(split["check"])
    assert len(tune) == len(check_half) == 44 and not tune & check_half


def test_no_dev_question_gets_a_trigger_note_from_the_w_patterns():
    dev = {q["id"]: q["question"] for q in run_adversarial.run_eval.load_questions("dev")}
    fired = [(qid, r) for qid, q in dev.items() for r in refs(check(q))
             if r.startswith("scope:") or r.startswith("act:")]
    fired += [(qid, "DQ-12") for qid, q in dev.items() if is_ntaa_commencement_question(q, DQ12)]
    assert not fired, fired
