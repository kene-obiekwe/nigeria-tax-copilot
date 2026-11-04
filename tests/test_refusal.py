"""Refusal path (Phase 6; guardrails design, sections 4 and 10, groups 7 to 10, 12 and 14): the
section validator, other-Act and scope notes, the relevance floor, the DQ-12 trigger, the pinned
texts and the runner guards, plus the D-29 and held-out text guards. No key and no database are
needed. Questions written here are new wording for the tests; dev questions are named by id only."""
import hashlib
import importlib.util
import json
import os
import sys

import pytest

from generation import generate as gen, guardrails, llm
from generation.disclosure import Disclosure, is_ntaa_commencement_question, load_policy
from retrieval.retrieve import Retrieved

ROOT = gen.ROOT
STORE = gen.FileCorpusStore()
CORPUS = STORE.sections()
POLICY = guardrails.load_guardrails_policy()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return sys.modules[name]


run_eval = load_module("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
run_exit_check = load_module("run_exit_check", ROOT / "eval" / "generation" / "run_exit_check.py")
run_adversarial = load_module("run_adversarial", ROOT / "eval" / "guardrails" / "run_adversarial.py")
run_calibration = load_module("run_calibration", ROOT / "eval" / "guardrails" / "run_calibration.py")


def check(question):
    return guardrails.pre_check(question, POLICY, CORPUS)


def in_scope():
    """The 20 dev questions and G03, G05 (text in memory only; assertions name ids)."""
    rows = [(q["id"], q["question"]) for q in run_eval.load_questions("dev")]
    rows += [(q["id"], q["question"]) for q in run_exit_check.load_exit_questions()
             if q["source"] == "new" and q["id"] in ("G03", "G05")]
    return rows


# 7. Section validator --------------------------------------------------------------------------


def test_corpus_sections_from_the_store():
    assert len(CORPUS["nta2025"]["sections"]) == 84 and len(CORPUS["ntaa2025"]["sections"]) == 65
    assert CORPUS["nta2025"]["schedules"] == {1, 3, 11, 13}
    assert POLICY.headings[("nta2025", 13)] == "Employment income"


@pytest.mark.parametrize("question,reason,text", [
    ("What does section 250 of the Nigeria Tax Act say?", "no_such_section",
     "The Nigeria Tax Act 2025 has no section 250. It has 203 sections."),
    ("Explain section 160 of the NTAA.", "no_such_section",
     "The Nigeria Tax Administration Act 2025 has no section 160. It has 148 sections."),
    ("What is in section 310?", "no_such_section",
     "Neither the Nigeria Tax Act 2025 (203 sections) nor the Nigeria Tax Administration Act 2025 (148 sections) "
     "has a section 310."),
    ("What does the Fifteenth Schedule of the NTA cover?", "no_such_section",
     "The Nigeria Tax Act 2025 has no Fifteenth Schedule. It has 14 Schedules."),
    ("What does section 13 of the Nigeria Tax Act say?", "section_outside_v1",
     'Section 13 of the Nigeria Tax Act 2025 ("Employment income") is outside the corpus this tool covers (v1).'),
    ("Summarise section 125 of the Nigeria Tax Act.", "section_outside_v1",
     'Section 125 of the Nigeria Tax Act 2025 ("Manner of denoting duty") is outside the corpus this tool covers (v1).'),
])
def test_references_that_all_fail_are_refused(question, reason, text):
    result = check(question)
    assert (result.refusal, result.refusal_text) == (reason, text)


def test_149_to_203_with_no_act_named_is_the_nigeria_tax_act():
    result = check("What does section 186 provide?")
    assert result.refusal is None and [(r.number, r.act, r.verdict) for r in result.references] == [(186, None, "ok")]
    assert check("What does section 166 provide?").refusal_text.startswith("Section 166 of the Nigeria Tax Act 2025 (")


def test_no_act_named_and_both_acts_have_the_section_names_both():
    text = check("What does section 125 provide?").refusal_text
    assert "Section 125 of the Nigeria Tax Act 2025" in text and "Section 125 of the Nigeria Tax Administration Act 2025" in text


def test_tax_administration_act_without_nigeria_names_the_administration_act():
    result = check("Explain section 160 of the Tax Administration Act.")
    assert result.refusal == "no_such_section"
    assert result.refusal_text == "The Nigeria Tax Administration Act 2025 has no section 160. It has 148 sections."
    assert [r.act for r in result.references] == ["ntaa2025"]


def test_another_act_is_a_note_and_never_a_refusal():
    result = check("Does section 24 of CITA still apply after the new law?")
    assert result.refusal is None and result.flags == ["act_note"]
    assert [n.text for n in result.notes] == [
        "The Companies Income Tax Act is not in the corpus this tool covers. Section numbers in the answer refer to "
        "the Nigeria Tax Act 2025 or the Nigeria Tax Administration Act 2025."]
    # Kene, 2026-11-03: a Nigeria Tax Act of another year keeps the other-Act note, not a refusal.
    year = check("What does the Nigeria Tax Act 2027 say about VAT?")
    assert year.refusal is None and year.notes[0].text.startswith("The Nigeria Tax Act 2027 is not in the corpus")


def test_mixed_references_are_answered_with_a_note():
    result = check("Compare sections 56 and 250 of the NTA.")
    assert result.refusal is None and result.flags == ["reference_note"]
    assert [n.text for n in result.notes] == ["The Nigeria Tax Act 2025 has no section 250. It has 203 sections."]


@pytest.mark.parametrize("question", ["", "   ", "12345 ?", "x" * 2001])
def test_invalid_questions(question):
    assert check(question).refusal == "invalid_question"


def test_no_dev_or_in_scope_question_is_refused_or_noted_at_pre_check():
    flagged = [qid for qid, q in in_scope() if check(q).refusal or check(q).notes]
    assert not flagged, flagged


# 8. Relevance floor ----------------------------------------------------------------------------


def items(scores, reasons=("vector",)):
    return [Retrieved(f"c{i}", i, s, 1 - s, list(reasons)) for i, s in enumerate(scores, 1)]


def test_floor():
    assert guardrails.relevance_floor(items([0.35, 0.3]), 0.40).refuse
    assert guardrails.relevance_floor(items([0.45]), 0.40).status == "pass"
    lookup = items([0.2], ("section lookup: nta2025 s56",)) + items([0.1])
    assert guardrails.relevance_floor(lookup, 0.40).status == "exempt_lookup"
    assert guardrails.relevance_floor(items([0.1]), None).status == "not_calibrated"


def test_tau_is_read_from_the_policy_file(tmp_path):
    assert POLICY.tau == 0.40  # set by calibration on 2026-11-03 (D-35)
    text = (ROOT / "data" / "curated" / "guardrails_policy.yml").read_text(encoding="utf-8")
    path = tmp_path / "policy.yml"
    path.write_text(text.replace("  tau: 0.40", "  tau: null"), encoding="utf-8")
    assert guardrails.load_guardrails_policy(path).tau is None  # an empty tau turns the floor off


class CountingRetriever:
    def __init__(self, score):
        self.score, self.calls = score, 0

    def retrieve(self, question, k=5, filters=None, query_vector=None):
        self.calls += 1
        return [Retrieved("nta2025:s56", 1, self.score, 1 - self.score, ["vector"])]


class CountingLLM:
    model = llm.MODEL

    def __init__(self):
        self.requests = []

    def create(self, params):
        self.requests.append(params)
        raise AssertionError("no model call expected")


def test_floor_refuses_after_retrieval_and_before_any_model_call(tmp_path):
    assert POLICY.tau == 0.40
    retriever, model, verifier = CountingRetriever(0.30), CountingLLM(), CountingLLM()
    answer = gen.generate("How do I bake bread?", retriever=retriever, llm=model, verifier=verifier, store=STORE,
                          log_dir=None, guardrails_policy=POLICY)
    assert (answer.status, answer.refusal_reason) == ("refused", "low_relevance")
    assert retriever.calls == 1 and model.requests == [] and verifier.requests == []


def test_pre_check_refusal_makes_no_retrieval_or_model_call(tmp_path):
    retriever, model, verifier = CountingRetriever(0.9), CountingLLM(), CountingLLM()
    answer = gen.generate("What does section 250 of the Nigeria Tax Act say?", retriever=retriever, llm=model,
                          verifier=verifier, store=STORE, log_dir=tmp_path)
    assert (answer.status, answer.refusal_reason) == ("refused", "no_such_section")
    assert retriever.calls == 0 and model.requests == [] and verifier.requests == []
    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8"))
    assert record["refusal"] == {"status": "refused", "reason": "no_such_section", "stage": "pre_check"}
    assert record["retrieval"] is None and record["refusal_flag"] is True


# 9. Scope notes --------------------------------------------------------------------------------


@pytest.mark.parametrize("topic,question", [
    ("personal_income_tax", "How is PAYE worked out for my staff?"),
    ("stamp_duties", "Do I pay stamp duty on a lease?"),
    ("petroleum_taxes", "How are oil and gas profits taxed?"),
    ("state_local_taxes", "Which state levies apply to my shop?"),
    ("surcharge", "Is there a surcharge on fuel?"),
    ("economic_development_incentives", "Can my factory get pioneer status?"),
    ("subsidiary_legislation", "What do the latest NRS guidelines say?"),
    ("customs_and_excise", "What import duty applies to phones?"),
    ("pre_2026_periods", "How was my company taxed in 2024?"),
    ("pre_2026_periods", "What rate applied for the 2023 year of assessment?"),
    ("pre_2026_periods", "Which rules covered profits earned before January 2026?"),
    # D-44: month-and-year dates
    ("pre_2026_periods", "Was a VAT return lodged in October 2024 on time?"),
    ("pre_2026_periods", "What applied to a sale made on 15 June 2023?"),
    ("pre_2026_periods", "Do the Dec. 2025 accounts follow the new rules?"),
])
def test_scope_notes_fire_on_their_patterns(topic, question):
    result = check(question)
    assert result.refusal is None and f"scope:{topic}" in [n.ref for n in result.notes]
    assert result.notes[-1].text.endswith(POLICY.scope_note_suffix) and "scope_note" in result.flags


@pytest.mark.parametrize("question", ["What does the Nigeria Tax Act 2025 say about VAT?",
                                      "Is section 9 of the Nigeria Tax Act, 2025 in force?",
                                      "What is due in 2026?",
                                      "Is a return due by 31 March 2026?"])
def test_pre_2026_note_ignores_the_acts_own_year_and_later_years(question):
    assert "scope:pre_2026_periods" not in [n.ref for n in check(question).notes]


def test_scope_notes_fire_on_no_in_scope_question():
    fired = [(qid, n.ref) for qid, q in in_scope() for n in check(q).notes if n.ref.startswith("scope:")]
    assert not fired, fired


ADVERSARIAL_SCOPE_LABELS = ("personal_income_tax", "stamp_duties", "petroleum_taxes", "state_local_taxes", "surcharge",
                            "economic_development_incentives", "subsidiary_legislation", "pre_2026_periods")


def test_note_labels_match_the_adversarial_set():
    """Labels used in the adversarial set's required_notes (overseer fix 5, labels only)."""
    assert set(ADVERSARIAL_SCOPE_LABELS) <= set(POLICY.scope_topics)
    assert set(POLICY.scope_topics) - set(ADVERSARIAL_SCOPE_LABELS) == {"customs_and_excise"}  # documented extra
    assert [n.ref for n in check("Does section 24 of CITA still apply?").notes] == ["act:companies_income_tax_act"]
    assert [n.ref for n in check("Is PITA relevant to my shop?").notes][:1] == ["act:personal_income_tax_act"]
    assert [n.ref for n in check("What does the Nigeria Tax Act 2027 say?").notes] == ["act:nigeria_tax_act_2027"]
    mixed = check("Compare sections 56 and 250 of the NTA, and section 13.")
    assert [n.ref for n in mixed.notes] == ["reference:no_such_section", "reference:section_outside_v1"]
    refused = check("What does section 250 of the Nigeria Tax Act say?")
    assert guardrails.note_labels(refused.notes, refused.refusal) == ["reference:no_such_section"]
    assert refused.as_log()["labels"] == ["reference:no_such_section"]


# 10. DQ-12 -------------------------------------------------------------------------------------


DQ12 = load_policy()


@pytest.mark.parametrize("question", [
    "For annual returns, from when are the procedures in the administration statute binding?",  # from when
    "Since when have NRS assessment procedures been governed by the 2025 administration statute?",  # since when
    "Which starting date governs the Nigeria Tax Administration Act 2025?",  # start(ing) date
    "Is the administration statute only beginning to apply to returns filed this year?",  # begin(s|ning) to apply
    "Are the NTAA record-keeping duties operative for small firms?",  # operative
    "Are the new audit procedures effective on the first day of the year?",  # effective on
    "Have the NTAA penalty provisions been effective since 2026?",  # effective since
    "When did the tax administration provisions on objections take effect?",  # when did ... take effect
])
def test_dq12_new_patterns(question):
    assert is_ntaa_commencement_question(question, DQ12)


@pytest.mark.parametrize("question,shown", [
    # commencement-day wording (D-44)
    ("On what date do the reformed tax rules become binding on traders?", True),
    ("Which date must firms comply with the new administration rules from?", True),
    ("As of what day are returns governed by the administration statute?", True),
    ("What date must a VAT return be filed?", False),
    # period trigger (D-44): the Administration Act named with a date or year before 2026
    ("Can the NTAA penalties reach a return lodged in 2024?", True),
    ("Is a late return from Oct 2025 caught by the Tax Administration Act?", True),
    ("What penalty does the Nigeria Tax Administration Act 2025 set for late returns?", False),
    ("What penalty does the Nigeria Tax Act set for a return lodged in 2024?", False),
])
def test_dq12_commencement_day_and_period_triggers(question, shown):
    assert is_ntaa_commencement_question(question, DQ12) is shown


def test_dq12_retrieval_trigger():
    question = "What year do the tax administration rules cover?"  # no pattern matches
    assert not is_ntaa_commencement_question(question, DQ12)
    assert is_ntaa_commencement_question(question, DQ12, retrieved=["ntaa2025:s2"])
    assert is_ntaa_commencement_question(question, DQ12, retrieved=["nta2025:s203"])
    assert not is_ntaa_commencement_question("What does the administration law cover?", DQ12,
                                             retrieved=["ntaa2025:s2"])  # no time word
    assert not is_ntaa_commencement_question("From when does the Nigeria Tax Act apply?", DQ12,
                                             retrieved=["nta2025:s203"])  # names only the Nigeria Tax Act


# 12. Rendering ---------------------------------------------------------------------------------


def test_refusal_texts_are_pinned():
    assert guardrails.INVALID_TEXT == ("The question could not be read. Please ask about the Nigeria Tax Act 2025 or "
                                       "the Nigeria Tax Administration Act 2025.")
    assert guardrails.LOW_RELEVANCE_TEXT == ("No provision in the corpus matches this question closely enough to "
                                             "answer it.")
    assert gen.UNSUPPORTED_ANSWER_TEXT == ("The system could not check an answer to this question against the "
                                           "provisions it cited, so no answer is shown.")
    assert gen.VERIFICATION_UNAVAILABLE_TEXT == ("The answer could not be checked, so it is not shown. Please try "
                                                 "again later.")
    assert POLICY.other_act_note == ("The {name} is not in the corpus this tool covers. Section numbers in the answer "
                                     "refer to the Nigeria Tax Act 2025 or the Nigeria Tax Administration Act 2025.")


def test_refusal_renders_text_then_notes_then_disclaimer():
    result = check("Explain section 160 of the NTAA and stamp duty.")
    rendered = gen.render("refused", [], [], [], {}, [], notes=result.notes, refusal_text=result.refusal_text)
    assert rendered == "\n\n".join([result.refusal_text, "Notes\n- " + result.notes[0].text, gen.DISCLAIMER])


def test_answer_renders_notes_first_and_removal_lines_after_the_claims():
    chunks = STORE.chunks(["nta2025:s56"])
    claim = gen.Claim("Tax is levied on total profits.", ["P1"], ["nta2025:s56"])
    notes = check("Does section 24 of CITA still apply?").notes
    rendered = gen.render("answered", [claim], [], ["nta2025:s56"], chunks, [], notes=notes,
                          removal_lines=["1 statement was removed because the cited provisions did not support it."])
    parts = rendered.split("\n\n")
    assert parts[0].startswith("Notes\n- The Companies Income Tax Act") and parts[1].startswith(claim.text)
    assert parts[2] == "1 statement was removed because the cited provisions did not support it."
    assert parts[3].startswith("Sources\n") and rendered.endswith(gen.DISCLAIMER)


@pytest.mark.parametrize("status", ["answered", "not_covered", "refused", "error"])
def test_notes_and_question_disclosures_render_whatever_the_status(status):
    """D-44: a note the question triggers is shown on every status."""
    notes = check("How was my company taxed in 2024?").notes
    dq12 = Disclosure("note", "DQ-12", "question", "question", DQ12.dq12_text)
    chunks = STORE.chunks(["nta2025:s56"])
    claims = [gen.Claim("Tax is levied on total profits.", ["P1"], ["nta2025:s56"])] if status == "answered" else []
    cited = ["nta2025:s56"] if claims else []
    rendered = gen.render(status, claims, [], cited, chunks, [dq12], notes=notes,
                          refusal_text="x" if status in ("refused", "error") else None)
    assert "Notes\n- " + notes[0].text in rendered and DQ12.dq12_text in rendered
    assert rendered.endswith(gen.DISCLAIMER)
    if status in ("refused", "error"):
        assert rendered.startswith("x\n\nNotes\n")


@pytest.mark.parametrize("status,text", [("refused", "x"), ("error", None), ("not_covered", None)])
def test_disclaimer_on_every_status(status, text):
    assert gen.render(status, [], [], [], {}, [], refusal_text=text).endswith(gen.DISCLAIMER)


# 14. Runner guards -----------------------------------------------------------------------------


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_holdout_guard(tmp_path):
    config, marker, result = tmp_path / "config_g2.json", tmp_path / "marker.json", tmp_path / "result.json"
    live = {"tau": 0.4}
    sha = write(config, json.dumps(live))
    kw = {"config_path": config, "marker": marker, "result": result, "live": live}
    assert run_adversarial.check_holdout_guard(config_sha256=sha, **kw) == live
    with pytest.raises(run_adversarial.GuardError, match="no frozen configuration"):
        run_adversarial.check_holdout_guard(config_sha256=None, **kw)
    with pytest.raises(run_adversarial.GuardError, match="does not match"):
        run_adversarial.check_holdout_guard(config_sha256="0" * 64, **kw)
    with pytest.raises(run_adversarial.GuardError, match="live configuration differs"):
        run_adversarial.check_holdout_guard(config_sha256=sha, **{**kw, "live": {"tau": 0.5}})
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="runs once"):
        run_adversarial.check_holdout_guard(config_sha256=sha, **kw)


def test_holdout_guard_on_the_real_files_matches_the_frozen_config(tmp_path):
    """D-49 (frozen 2026-11-04): the recorded hash is the on-disk config_g2.json, the live configuration
    equals it, it cannot be frozen again, and the guard refuses once a held-out marker exists. Only the
    guard is called; nothing from the held-out part is read."""
    assert run_adversarial.CONFIG_G2_SHA256 == "6faad20a1864fabfb8b812c612e4bc5ce5cbfdaa2060152fe98ca05f5eb965d9"
    assert hashlib.sha256(run_adversarial.CONFIG_G2.read_bytes()).hexdigest() == run_adversarial.CONFIG_G2_SHA256
    frozen = json.loads(run_adversarial.CONFIG_G2.read_text(encoding="utf-8"))
    assert frozen == run_adversarial.current_config()
    with pytest.raises(run_adversarial.GuardError, match="frozen once"):
        run_adversarial.freeze_config()
    marker = tmp_path / "marker.json"
    marker.write_text("{}", encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="runs once"):
        run_adversarial.check_holdout_guard(marker=marker, result=tmp_path / "result.json")


def test_adversarial_loader_keeps_one_split_and_checks_both_hashes(tmp_path):
    # Rows keep split null: the split file is authoritative (check_adversarial.py).
    rows = [{"id": "ADV-T", "split": None, "question": "t"}, {"id": "ADV-H", "split": None, "question": "h"}]
    data, split = tmp_path / "adv.jsonl", tmp_path / "split.json"
    data_sha = write(data, "".join(json.dumps(r) + "\n" for r in rows))
    split_sha = write(split, json.dumps({"set_sha256": data_sha, "tune": ["ADV-T"], "holdout": ["ADV-H"]}))
    kept = run_adversarial.load_adversarial("tune", data, data_sha, split, split_sha)
    assert [(r["id"], r["split"]) for r in kept] == [("ADV-T", "tune")]
    for bad in ((None, split_sha), ("0" * 64, split_sha), (data_sha, "0" * 64)):
        with pytest.raises(run_adversarial.GuardError):
            run_adversarial.load_adversarial("tune", data, bad[0], split, bad[1])
    other = write(split, json.dumps({"set_sha256": data_sha, "tune": ["ADV-X"], "holdout": ["ADV-H"]}))
    with pytest.raises(run_adversarial.GuardError, match="do not match"):
        run_adversarial.load_adversarial("tune", data, data_sha, split, other)
    stale = write(split, json.dumps({"set_sha256": "0" * 64, "tune": ["ADV-T"], "holdout": ["ADV-H"]}))
    with pytest.raises(run_adversarial.GuardError, match="not made for this adversarial set"):
        run_adversarial.load_adversarial("tune", data, data_sha, split, stale)


def test_adversarial_hashes_are_pinned_and_the_loader_keeps_only_the_tune_part():
    """D-36 hashes (frozen 2026-11-03). The tune part is read only through the runner's loader;
    the assertions report counts and ids, never question text."""
    assert run_adversarial.ADVERSARIAL_SHA256 == "2e3541c88db45e53fa50ec591e7b65d108e7bb79955608ac3f1c07632ff592dc"
    assert run_adversarial.SPLIT_SHA256 == "0143e246fd23e98569001bcdafd5b771d5c4fa628a305a37c36d74937227f6fc"
    tune = run_adversarial.load_adversarial("tune")
    assert len(tune) == 48 and all(row["split"] == "tune" for row in tune)
    assert len({row["id"] for row in tune}) == 48


def test_freeze_config_runs_once(tmp_path):
    path = tmp_path / "config_g2.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="frozen once"):
        run_adversarial.freeze_config(path)


def test_calibration_guards(tmp_path):
    data = tmp_path / "cal.jsonl"
    rows = [{"id": "R", "kind": "real"}, {"id": "A", "kind": "mutation"}, {"id": "B", "kind": "mutation"}]
    sha = write(data, "".join(json.dumps(r) + "\n" for r in rows))
    review = tmp_path / "review.json"
    signed = {"sha256": sha, "reviewed_by": "overseer", "on": "2026-11-03"}
    with pytest.raises(run_calibration.GuardError, match="sha256"):
        run_calibration.load_calibration(data, "0" * 64, review)
    with pytest.raises(run_calibration.GuardError, match="missing"):
        run_calibration.load_calibration(data, sha, review)
    write(review, json.dumps({**signed, "sha256": "0" * 64}))
    with pytest.raises(run_calibration.GuardError, match="does not record"):
        run_calibration.load_calibration(data, sha, review)
    write(review, json.dumps({**signed, "mutations": {"A": "pass", "B": "pending overseer check"}}))
    with pytest.raises(run_calibration.GuardError, match="not yet passed.*B"):
        run_calibration.load_calibration(data, sha, review)
    write(review, json.dumps({**signed, "mutations": {"A": "pass"}, "excluded": ["B"]}))
    assert [r["id"] for r in run_calibration.load_calibration(data, sha, review)] == ["R", "A"]


def test_review_file_records_the_overseer_review_of_every_mutation():
    """Overseer review, 2026-11-03: all 60 mutations pass (the 9 hand-written ones on recheck), so
    the calibration runner's guard passes. No verifier call is made here."""
    review = json.loads(run_calibration.REVIEW.read_text(encoding="utf-8"))
    assert review["sha256"] == run_calibration.CALIBRATION_SHA256
    assert (review["reviewed_by"], review["on"], review["excluded"]) == ("overseer", "2026-11-03", [])
    builder = load_module("build_calibration_set", ROOT / "eval" / "guardrails" / "build_calibration_set.py")
    assert set(builder.OVERRIDES) <= set(review["mutations"]) and len(review["mutations"]) == 60
    assert set(review["mutations"].values()) == {"pass"}
    rows = run_calibration.load_calibration()
    assert len(rows) == 195 and sum(r["kind"] == "mutation" for r in rows) == 60


def test_accepted_false_removal():
    """Kene, 2026-11-03 (option a): CAL-R1-G05-3 is accepted as a false removal of the rule check
    (pinned in test_verification.py). It counts against the 5% limit (at most 6 of 131); the rule
    check is not loosened for it."""
    rows = [json.loads(line) for line in run_calibration.CALIBRATION.read_text(encoding="utf-8").splitlines()]
    row = next(r for r in rows if r["id"] == "CAL-R1-G05-3")
    assert row["human_verdict"] == "supported" and "Chapter Four" in row["text"]


def test_calibration_file_is_the_frozen_one():
    rows = [json.loads(line) for line in run_calibration.CALIBRATION.read_text(encoding="utf-8").splitlines()]
    assert hashlib.sha256(run_calibration.CALIBRATION.read_bytes()).hexdigest() == run_calibration.CALIBRATION_SHA256
    real = [r for r in rows if r["kind"] == "real"]
    mutations = [r for r in rows if r["kind"] == "mutation"]
    assert len(real) == 135 and len(mutations) == 60
    assert {t: sum(r["mutation_type"] == t for r in mutations) for t in run_calibration.MUTATION_TYPES} == \
        dict.fromkeys(run_calibration.MUTATION_TYPES, 10)
    assert len({r["base_id"] for r in mutations}) == 60 and all(r["expected"] == "not_supported" for r in mutations)
    verdicts = {}
    for r in real:
        verdicts[r["human_verdict"]] = verdicts.get(r["human_verdict"], 0) + 1
    assert verdicts == {"supported": 131, "unsupported": 2, "not_a_claim": 2}
    assert {r["id"] for r in real if r["human_verdict"] == "unsupported"} == {"CAL-R1-Q018-4", "CAL-R1-Q043-5"}


# D-29 and held-out guards ----------------------------------------------------------------------


def _artefacts():
    paths = [ROOT / "data" / "curated" / "guardrails_policy.yml", ROOT / "data" / "curated" / "disclosure_policy.yml"]
    paths += sorted((ROOT / "docs" / "phase06_guardrails").glob("*.md"))
    paths += sorted((ROOT / "python" / "generation").glob("*.py"))
    paths += [p for p in sorted((ROOT / "eval" / "guardrails").rglob("*.*"))
              if p.name not in ("adversarial_v1.jsonl", "split_v1.json")]  # the writing session checks its own file
    paths += sorted((ROOT / "tests").glob("test_*.py"))
    return [p for p in paths if p.is_file() and p.suffix in {".py", ".md", ".yml", ".json", ".jsonl", ".csv"}]


def test_no_test_question_text_in_phase6_artefacts():
    questions = run_eval.load_questions("test")
    assert len(questions) == 30
    leaks = []
    for path in _artefacts():
        text = path.read_text(encoding="utf-8").lower()
        leaks += [(path.name, q["id"]) for q in questions if q["question"].lower() in text]
    assert not leaks, leaks


@pytest.mark.skipif(os.getenv("TAXY_OVERSEER_HOLDOUT_CHECK") != "1",
                    reason="overseer-only check: set TAXY_OVERSEER_HOLDOUT_CHECK=1 (Kene, 2026-11-03)")
def test_no_held_out_question_text_in_tests_or_fixtures():
    """Design section 10. Reads the held-out rows (in memory, ids only reported), so it runs only in
    the overseer's session when the flag is set (D-40: the build session never sees held-out
    questions)."""
    held_out = run_adversarial.load_adversarial("holdout")
    leaks = []
    for path in _artefacts():
        text = path.read_text(encoding="utf-8").lower()
        leaks += [(path.name, q["id"]) for q in held_out if q["question"].lower() in text]
    assert not leaks, leaks
