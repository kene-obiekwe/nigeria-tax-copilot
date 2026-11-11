"""Refusal path (Phase 6; guardrails design, sections 4 and 10, groups 7 to 10, 12 and 14): the
section validator, other-Act and scope notes, the relevance floor, the DQ-12 trigger, the pinned
texts and the runner guards, plus the D-29 and held-out text guards. No key and no database are
needed. Questions written here are new wording for the tests; dev questions are named by id only."""
import csv
import dataclasses
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


# D-60 (DQ-18): heading corrections and the refusal-text dedupe -----------------------------------

HEADING_FIXES = {("ntaa2025", 37): "Making of assessments of tax in upstream petroleum operations",
                 ("ntaa2025", 61): "Power to distrain",
                 ("nta2025", 113): "Chargeable tax petroleum operations",
                 ("ntaa2025", 24): "Returns for surcharge",          # DQ-19 page check, 2026-11-11
                 ("ntaa2025", 148): "Citation and commencement"}


LETTERS_EQUAL_FIXES = 44  # DQ-19, D-126: spacing-only corrections of out-of-corpus NTAA headings


def _adds_spaces_only(derived: str, heading: str) -> bool:
    """True when `heading` is `derived` with spaces inserted and no other character changed."""
    i = 0
    for ch in heading:
        if i < len(derived) and ch == derived[i]:
            i += 1
        elif ch != " ":
            return False
    return i == len(derived)


def test_heading_corrections_replace_exactly_the_three_wrong_headings():
    """D-60 (DQ-18): the three page-checked rows. D-126 (DQ-19): every other row is a letters-equal
    row that only adds spaces to an OCR-joined heading of an out-of-corpus NTAA section."""
    rows = guardrails.load_heading_corrections()
    page_checked = [r for r in rows if r.get("method") != "letters_equal"]
    letters_equal = [r for r in rows if r.get("method") == "letters_equal"]
    assert {(r["act_id"], r["section"]): r["heading"] for r in page_checked} == HEADING_FIXES
    assert all(isinstance(r["pdf_page"], int) and (ROOT / r["source"]).name.endswith(".pdf") for r in page_checked)
    assert len(letters_equal) == LETTERS_EQUAL_FIXES
    corpus_sections = CORPUS["ntaa2025"]["sections"]
    comparison = {}
    with guardrails.SECTION_MAP.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["act_id"] == "ntaa2025":
                comparison[(int(row["cited_section"]), row["comparison_print"])] = row["comparison_heading"].strip()
    for r in letters_equal:
        assert (r["act_id"], r["print"]) == ("ntaa2025", "nrs") and str(r["section"]) not in corpus_sections
        assert _adds_spaces_only(r["derived"], r["heading"]) and r["derived"] != r["heading"]
        assert r["matches"] and all(comparison[(r["section"], p)] == r["heading"] for p in r["matches"])
    fixes = {(r["act_id"], r["section"]): r["heading"] for r in rows}
    derived = guardrails.load_section_headings(corrections=None)
    for key, heading in fixes.items():
        assert POLICY.headings[key] == heading and derived[key] != heading
    assert {k: v for k, v in POLICY.headings.items() if k not in fixes} == \
        {k: v for k, v in derived.items() if k not in fixes}


def test_letters_equal_heading_names_the_section_in_a_refusal():
    """D-126: exit question E11's lookup error names Section 67 by its corrected heading."""
    result = check("What does section 67 of the NTAA say?")
    assert result.refusal_text == ('Section 67 of the Nigeria Tax Administration Act 2025 ("Recovery of tax") '
                                   "is outside the corpus this tool covers (v1).")


def test_corrected_heading_names_the_section_in_a_refusal():
    result = check("Which powers does section 61 of the NTAA give the Service?")
    assert result.refusal_text == ('Section 61 of the Nigeria Tax Administration Act 2025 ("Power to distrain") '
                                   "is outside the corpus this tool covers (v1).")


def test_a_stale_or_wrong_print_heading_correction_fails_to_load(tmp_path):
    stale = tmp_path / "stale.yml"
    stale.write_text("corrections:\n  - {act_id: ntaa2025, print: nrs, section: 61, derived: 'Power to distraint', "
                     "heading: 'Power to distrain'}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expects the derived heading"):
        guardrails.load_section_headings(corrections=stale)
    other_print = tmp_path / "print.yml"
    other_print.write_text("corrections:\n  - {act_id: nta2025, print: nass, section: 113, derived: x, heading: y}\n",
                           encoding="utf-8")
    with pytest.raises(ValueError, match="cited print"):
        guardrails.load_section_headings(corrections=other_print)


def test_section_named_with_and_without_an_act_is_stated_once():
    """D-60: one sentence, naming the Act (the unnamed reference alone would name both Acts)."""
    result = check("Is section 125 still in force? What does section 125 of the Nigeria Tax Act say?")
    assert [(r.number, r.act) for r in result.references] == [(125, None), (125, "nta2025")]
    assert result.refusal_text == ('Section 125 of the Nigeria Tax Act 2025 ("Manner of denoting duty") is outside '
                                   "the corpus this tool covers (v1).")
    missing = check("Is section 290 still law? What does section 290 of the Nigeria Tax Act require?")
    assert missing.refusal_text == "The Nigeria Tax Act 2025 has no section 290. It has 203 sections."


def test_dedupe_keeps_an_unnamed_reference_with_another_number():
    result = check("Is section 310 still law? Does section 290 of the Nigeria Tax Act match it?")
    assert result.refusal_text == ("Neither the Nigeria Tax Act 2025 (203 sections) nor the Nigeria Tax "
                                   "Administration Act 2025 (148 sections) has a section 310. The Nigeria Tax Act "
                                   "2025 has no section 290. It has 203 sections.")


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


# D-56 note families (G2 fix design, section 4.5). Questions are the build session's own words. ------


@pytest.mark.parametrize("question", [
    "Do the share certificates need stamping?",                      # stamping
    "Our receipts are stamped by the bank. Is that enough?",         # stamped
    "Where do I buy stamps for a tenancy agreement?",                # stamps
    "Can the office stamp an unsigned deed?",                        # stamp
    "Is a guarantee letter treated as a dutiable instrument?",       # dutiable instrument
])
def test_stamp_family(question):
    assert "scope:stamp_duties" in [n.ref for n in check(question).notes]


@pytest.mark.parametrize("question", [
    "Does the local government tax my kiosk?",                       # local government on its own
    "What can a city council charge a restaurant?",                  # city council
    "Is the municipal council allowed to bill my pharmacy?",         # municipal council
    "My LGA sent a demand notice. Is it valid?",                     # LGA
    "Does the state government take a share of my profits?",        # state government
    "Should I register with the state board for my workers?",        # state board
    "What does state inland revenue charge a hair salon?",           # state inland revenue
    "Does KWIRS assess sole traders?",                               # [A-Z]{1,4}IRS
    "Is the signage permit a federal tax?",                          # signage ... permit
    "Who sets the levy on hawking near the station?",                # levy ... hawking
    "Are street trading tickets a tax under this law?",              # street trading ... tickets
    "Is the fee for my billboard deductible?",                       # fee ... billboard
])
def test_state_and_local_family(question):
    assert "scope:state_local_taxes" in [n.ref for n in check(question).notes]


@pytest.mark.parametrize("question", [
    "Is the FIRS the same body as the new revenue service?",         # FIRS is excluded
    "What is the market value rule for gains on shares?",            # market value, no fee word
    "Do company councils pay tax on fees?",                          # council without local/area/...
    "Is a government grant taxable income?",                         # government without local/state
])
def test_state_and_local_family_near_misses(question):
    assert "scope:state_local_taxes" not in [n.ref for n in check(question).notes]


@pytest.mark.parametrize("question", [
    "Since which month have the administration provisions bound employers?",   # since which month
    "As of what year do the reformed filing duties count?",                   # as of what year
    "By which date must traders follow the new administration statute?",      # by which date
    "What year did the new audit procedures start?",                          # what year ... start
    "Which month do the revised penalty rules come into effect?",             # which month ... come into effect
    "Have the administration rules become binding on cooperatives?",          # become binding
    "Is the administration statute operative yet?",                           # operative yet
    "Are the reformed objection steps already in force?",                     # already in force
    "When will the revised assessment rules be live?",                        # when will ... live
    "When is the new filing duty enforceable against partnerships?",          # when is ... enforceable
    "Do the tax administration penalties cover a late filing from 2023?",     # tax administration + period
])
def test_dq12_start_of_law_family(question):
    assert is_ntaa_commencement_question(question, DQ12)


@pytest.mark.parametrize("question", [
    "Since which year has the Nigeria Tax Act been binding on companies?",     # names only the Nigeria Tax Act
    "What year of assessment does a dividend belong to?",                     # no start verb
    "Is effective management in Nigeria enough for residence?",               # effective management
    "Is a signed lease binding on the landlord?",                             # binding, no start-of-law form
    "When must a company pay its instalments?",                               # when, no start verb
])
def test_dq12_start_of_law_family_near_misses(question):
    assert not is_ntaa_commencement_question(question, DQ12)


PROBES = json.loads((ROOT / "tests" / "data" / "note_probes.json").read_text(encoding="utf-8"))
_TRIGGER = ("scope:", "act:")


def probe_labels(question):
    labels = [n.ref for n in check(question).notes]
    if is_ntaa_commencement_question(question, DQ12):
        labels.append("DQ-12")
    return labels


def test_note_probes_fire():
    """The 18 probes that should fire (G2 fix design, section 4.3), as fixtures."""
    assert len(PROBES["fire"]) == 18
    missed = [p["id"] for p in PROBES["fire"] if not set(p["expect"]) <= set(probe_labels(p["question"]))]
    assert not missed, missed


def test_note_probe_near_misses_do_not_fire():
    """D-57: 0 of the 16 negative probes get a scope, other-Act or DQ-12 note."""
    assert len(PROBES["near_miss"]) == 16
    fired = [(p["id"], probe_labels(p["question"])) for p in PROBES["near_miss"]]
    fired = [(i, ls) for i, ls in fired if any(l.startswith(_TRIGGER) or l == "DQ-12" for l in ls)]
    assert not fired, fired


def test_no_dev_question_gets_a_scope_or_dq12_note_under_the_new_families():
    """D-57: 0 of the 20 dev questions (the retrieval trigger is covered by the note measure)."""
    fired = [(qid, probe_labels(q)) for qid, q in in_scope() if qid.startswith("Q")]
    fired = [(i, ls) for i, ls in fired if any(l.startswith("scope:") or l == "DQ-12" for l in ls)]
    assert not fired, fired


def test_notes_render_on_a_relevance_floor_refusal():
    """Section 4.5, item 3: a fake retriever below tau; the scope note and DQ-12 are rendered on the
    low_relevance refusal and listed in note_labels, with no model call."""
    question = "Since which month has the NTAA applied, and must our invoices be stamped?"
    retriever, model, verifier = CountingRetriever(0.30), CountingLLM(), CountingLLM()
    answer = gen.generate(question, retriever=retriever, llm=model, verifier=verifier, store=STORE, log_dir=None,
                          guardrails_policy=POLICY)
    assert (answer.status, answer.refusal_reason) == ("refused", "low_relevance")
    assert model.requests == [] and verifier.requests == []
    labels = guardrails.note_labels(answer.notes, answer.refusal_reason, answer.disclosures)
    assert "scope:stamp_duties" in labels and "DQ-12" in labels
    stamp = next(n for n in answer.notes if n.ref == "scope:stamp_duties")
    assert answer.rendered.startswith(guardrails.LOW_RELEVANCE_TEXT + "\n\nNotes\n")
    assert stamp.text in answer.rendered and DQ12.dq12_text in answer.rendered
    assert answer.rendered.endswith(gen.DISCLAIMER)


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


def fixture_set(tmp_path, rows, split=True, name="fx", config_records_set=True):
    """A temporary adversarial set (D-58 tests use fixture sets only, never adversarial_v2)."""
    data = tmp_path / f"{name}.jsonl"
    data_sha = write(data, "".join(json.dumps(r) + "\n" for r in rows))
    split_path = split_sha = None
    if split:
        split_path = tmp_path / f"{name}_split.json"
        split_sha = write(split_path, json.dumps({"set_sha256": data_sha,
                                                  "tune": [r["id"] for r in rows if r["id"].endswith("T")],
                                                  "holdout": [r["id"] for r in rows if r["id"].endswith("H")]}))
    return run_adversarial.AdversarialSet(
        name=name, questions=data, questions_sha256=data_sha, split=split_path, split_sha256=split_sha,
        config=tmp_path / f"config_{name}.json", config_sha256=None, marker=tmp_path / name / "marker.json",
        result=tmp_path / name / "result.json", pack_prefix=f"{name}_holdout_repeat",
        config_records_set=config_records_set)


ROWS = [{"id": "ADV-1T", "split": None, "question": "t"}, {"id": "ADV-2H", "split": None, "question": "h"}]


def test_v1_holdout_and_freeze_refuse_forever():
    """D-58 test 1: the v1 marker exists, so --holdout --set v1 refuses; config_g2.json exists, so
    --freeze-config --set v1 refuses. Only the guards are called."""
    v1 = run_adversarial.SETS["v1"]
    assert v1.marker == run_adversarial.RESULTS_DIR / "holdout_started.json" and v1.marker.exists()
    assert v1.config == run_adversarial.HERE / "config_g2.json"
    with pytest.raises(run_adversarial.GuardError, match="runs once"):
        run_adversarial.check_holdout_guard("v1")
    with pytest.raises(run_adversarial.GuardError, match="frozen once"):
        run_adversarial.freeze_config("v1")
    assert run_adversarial.main(["--holdout", "--set", "v1"]) == 2
    assert run_adversarial.main(["--freeze-config", "--set", "v1"]) == 2


def test_v1_frozen_config_is_kept_as_the_record():
    """D-49 record: the recorded hash is the on-disk config_g2.json, made for the D-21 retrieval
    configuration. Since D-54 to D-56 the live configuration differs from it, so v1 stays a record."""
    v1 = run_adversarial.SETS["v1"]
    assert v1.config_sha256 == "6faad20a1864fabfb8b812c612e4bc5ce5cbfdaa2060152fe98ca05f5eb965d9"
    assert hashlib.sha256(v1.config.read_bytes()).hexdigest() == v1.config_sha256
    frozen = json.loads(v1.config.read_text(encoding="utf-8"))
    assert "adversarial_set" not in frozen and frozen["retrieval_config_sha256"] == hashlib.sha256(
        (ROOT / "eval" / "retrieval" / "config_final.json").read_bytes()).hexdigest()
    assert frozen != run_adversarial.current_config("v1")


V2_SHA256 = "350905729640400eb77eeba6d32e6284ea5a7b06110bb6d46ce3fbd306cf35b4"  # D-58, overseer, 2026-11-05


V2_CONFIG_SHA256 = "ad55eff74c723afd588bb71de6ca8042641ce6460ddbb00ba3f3e113e1899032"  # D-71, 2026-11-06
V3_SHA256 = "c293176417fc4ea04dc891f48a33fd85485ac55bbfcdeb6edf05828d51f116d7"  # D-75, overseer, 2026-11-07
V4_SHA256 = "83c76466b1b6ad7054f8add0ea0d4e353cdac020ea0e381371cbc103e40e1dcb"  # D-106, writer, 2026-11-09


def test_v2_entry_is_pinned_frozen_and_spent(tmp_path):
    """D-58, D-71, D-73: the v2 entry has its own files, its questions sha256 and its frozen
    configuration sha256 pinned, and no split file. Its decision run has been made (marker and
    result exist), so a second run and a second freeze refuse, and the held-out part loads as
    tuning data. The frozen file stays as the record; the live configuration has moved on since
    (D-77 to D-81), so a fresh freeze differs. Only guards, the configuration file and the spent
    rows (by count) are read."""
    v2 = run_adversarial.SETS["v2"]
    assert (v2.questions.name, v2.split, v2.config.name) == ("adversarial_v2.jsonl", None, "config_g2_v2.json")
    assert v2.marker == run_adversarial.RESULTS_DIR / "v2" / "holdout_started.json"
    assert v2.result == run_adversarial.RESULTS_DIR / "v2" / "holdout_final.json"
    assert v2.pack_prefix == "v2_holdout_repeat" and v2.config_records_set
    assert (v2.questions_sha256, v2.split_sha256, v2.config_sha256) == (V2_SHA256, None, V2_CONFIG_SHA256)
    assert hashlib.sha256(v2.config.read_bytes()).hexdigest() == V2_CONFIG_SHA256
    assert v2.marker.exists() and v2.result.exists()
    frozen = json.loads(v2.config.read_text(encoding="utf-8"))
    assert frozen["adversarial_set"] == {"version": "v2", "sha256": V2_SHA256}
    assert frozen["generation"]["prompt_version"] == "gen-prompt/7"
    assert (frozen["verifier"]["gap_prompt_version"], frozen["verifier"]["per_gap"]) == ("gap-prompt/3", True)
    assert (frozen["normaliser"], frozen["tau"]) == ("norm/5", 0.4)
    assert "tagger" not in frozen and frozen != run_adversarial.current_config("v2")
    assert len(run_adversarial.load_adversarial("v2", "holdout")) == 48  # spent: tuning data (D-73)
    for call, message in ((lambda: run_adversarial.check_holdout_guard("v2"), "runs once"),
                          (lambda: run_adversarial.load_adversarial("v2", "tune"), "no split file"),
                          (lambda: run_adversarial.freeze_config("v2"), "frozen once")):
        with pytest.raises(run_adversarial.GuardError, match=message):
            call()
    assert run_adversarial.main(["--freeze-config", "--set", "v2"]) == 2
    assert run_adversarial.main(["--holdout", "--set", "v2"]) == 2
    copy = dataclasses.replace(v2, config=tmp_path / "config_g2_v2.json")
    assert run_adversarial.freeze_config(copy) != V2_CONFIG_SHA256


def test_fixture_set_guard_and_marker(tmp_path, monkeypatch):
    """D-58 test 2: refuses with no hashes; refuses a configuration made for another set; writes its
    marker first; refuses a second run; freezes once."""
    fx = fixture_set(tmp_path, ROWS)

    def live(s=None):
        config = {"tau": 0.4}
        if s is not None:
            s = run_adversarial.get_set(s)
            config["adversarial_set"] = {"version": s.name, "sha256": s.questions_sha256}
        return config

    monkeypatch.setattr(run_adversarial, "current_config", live)
    with pytest.raises(run_adversarial.GuardError, match="no frozen sha256"):
        run_adversarial.check_holdout_guard(dataclasses.replace(fx, questions_sha256=None))
    with pytest.raises(run_adversarial.GuardError, match="no frozen sha256"):
        run_adversarial.freeze_config(dataclasses.replace(fx, split_sha256=None))
    assert not fx.config.exists()
    with pytest.raises(run_adversarial.GuardError, match="no frozen configuration hash"):
        run_adversarial.check_holdout_guard(fx)
    sha = run_adversarial.freeze_config(fx)
    frozen = json.loads(fx.config.read_text(encoding="utf-8"))
    assert frozen["adversarial_set"] == {"version": "fx", "sha256": fx.questions_sha256}
    with pytest.raises(run_adversarial.GuardError, match="frozen once"):
        run_adversarial.freeze_config(fx)
    fx = dataclasses.replace(fx, config_sha256=sha)
    assert run_adversarial.check_holdout_guard(fx) == frozen
    with pytest.raises(run_adversarial.GuardError, match="does not match the recorded sha256"):
        run_adversarial.check_holdout_guard(dataclasses.replace(fx, config_sha256="0" * 64))
    with pytest.raises(run_adversarial.GuardError, match="live configuration differs"):
        run_adversarial.check_holdout_guard(fx, live={"tau": 0.5})
    # The same frozen file presented for another set is refused.
    with pytest.raises(run_adversarial.GuardError, match="made for another adversarial set"):
        run_adversarial.check_holdout_guard(dataclasses.replace(fx, name="other"))
    # The decision run writes its marker before any work: stop it at the first case.
    calls = []

    def stop(cases, repeats):
        calls.append((fx.marker.exists(), [c["id"] for c in cases], repeats))
        raise RuntimeError("stop")

    monkeypatch.setattr(run_adversarial, "run_cases", stop)
    monkeypatch.setattr(run_adversarial, "in_scope_questions", lambda include: [])
    with pytest.raises(RuntimeError, match="stop"):
        run_adversarial.run_holdout(fx)
    assert calls == [(True, ["ADV-2H"], 3)]
    assert json.loads(fx.marker.read_text(encoding="utf-8"))["set"] == "fx"
    with pytest.raises(run_adversarial.GuardError, match="runs once"):
        run_adversarial.check_holdout_guard(fx)
    fx.marker.unlink()
    fx.result.write_text("{}", encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="runs once"):
        run_adversarial.check_holdout_guard(fx)


def test_held_out_part_loads_for_tuning_only_once_its_marker_exists(tmp_path):
    """D-58 test 3 (D-52 in code)."""
    fx = fixture_set(tmp_path, ROWS)
    assert [(r["id"], r["split"], r["set"]) for r in run_adversarial.load_adversarial(fx, "tune")] == \
        [("ADV-1T", "tune", "fx")]
    with pytest.raises(run_adversarial.GuardError, match="has not been run"):
        run_adversarial.load_adversarial(fx, "holdout")
    with pytest.raises(run_adversarial.GuardError, match="has not been run"):
        run_adversarial.tune_cases(fx, ["tune", "holdout"])
    fx.marker.parent.mkdir(parents=True)
    fx.marker.write_text("{}", encoding="utf-8")
    assert [r["id"] for r in run_adversarial.load_adversarial(fx, "holdout")] == ["ADV-2H"]


def test_set_with_no_split_file_is_all_held_out(tmp_path, monkeypatch):
    """D-58 test 3: with no split file every row is held out, --split tune refuses, and the rows load
    for tuning only after the set's marker exists."""
    fx = fixture_set(tmp_path, ROWS, split=False)
    monkeypatch.setitem(run_adversarial.SETS, "fx", fx)
    monkeypatch.setattr(run_adversarial, "next_tune_run", lambda results_dir=None: 9)
    monkeypatch.setattr(run_adversarial, "run_cases", lambda *a: pytest.fail("ran cases"))
    with pytest.raises(run_adversarial.GuardError, match="no split file"):
        run_adversarial.load_adversarial(fx, "tune")
    with pytest.raises(run_adversarial.GuardError, match="has not been run"):
        run_adversarial.load_adversarial(fx, "holdout")
    assert run_adversarial.main(["--split", "tune", "--set", "fx", "--run", "9"]) == 2
    assert run_adversarial.main(["--split", "tune", "--set", "fx", "--parts", "holdout", "--run", "9"]) == 2
    fx.marker.parent.mkdir(parents=True)
    fx.marker.write_text("{}", encoding="utf-8")
    rows = run_adversarial.load_adversarial(fx, "holdout")
    assert [(r["id"], r["split"]) for r in rows] == [("ADV-1T", "holdout"), ("ADV-2H", "holdout")]
    with pytest.raises(run_adversarial.GuardError, match="no split file"):
        run_adversarial.load_adversarial(fx, "tune")


@pytest.mark.parametrize("argv", [["--holdout"], ["--freeze-config"], ["--split", "tune", "--run", "4"],
                                  ["--holdout", "--set", "v9"]])
def test_set_is_required(argv, capsys):
    """D-58 test 4: no default set; a missing or unknown --set is a usage error."""
    with pytest.raises(SystemExit) as exc:
        run_adversarial.main(argv)
    assert exc.value.code == 2 and "--set" in capsys.readouterr().err


def test_tuning_runs_continue_the_numbers(tmp_path, monkeypatch):
    """Tuning runs continue from the last tune_run<N>.json (18 after runs 1 to 17; runs 5, 9 and 13 void,
    C-06, C-07, C-08; D-104 allows no further tuning run, D-114) and refuse any other number before
    loading anything."""
    assert run_adversarial.next_tune_run() == 18
    assert run_adversarial.next_tune_run(tmp_path) == 1
    (tmp_path / "tune_run7.json").write_text("{}", encoding="utf-8")
    assert run_adversarial.next_tune_run(tmp_path) == 8
    monkeypatch.setattr(run_adversarial, "tune_cases", lambda *a: pytest.fail("loaded before the number check"))
    for n in (17, 19):
        with pytest.raises(run_adversarial.GuardError, match="next tuning run is number 18"):
            run_adversarial.run_tune("v1", ["tune"], n)
    assert run_adversarial.parse_parts("tune,holdout") == ["tune", "holdout"]
    with pytest.raises(run_adversarial.GuardError, match="--parts"):
        run_adversarial.parse_parts("tune,test")


def test_tuning_report_counts_d61_per_set():
    """D-61: false refusals on the in-scope questions (dev, G03, G05, controls), required notes
    missed and notes not required, per set and part, by case id; controls are also listed apart."""
    def row(id_, source, status, *, expected="answer_supported", category=None, split=None, set_=None,
            required=(), shown=()):
        r = {"id": id_, "source": source, "status": status, "expected": expected, "category": category,
             "required_notes": list(required), "notes_shown": list(shown)}
        return r | ({"split": split, "set": set_} if source == "adversarial" else {})
    rows = [
        row("Q001", "dev", "answered"),
        row("Q002", "dev", "refused", shown=["DQ-12"]),
        row("G03", "exit_check", "not_covered"),
        row("G01", "exit_check", "refused", expected="refuse"),
        row("ADV-1", "adversarial", "answered", category="C", split="tune", set_="v1",
            shown=["scope:stamp_duties", "DR-150"]),
        row("ADV-2", "adversarial", "error", category="C", split="holdout", set_="v1"),
        row("ADV-3", "adversarial", "refused", expected="refuse", category="A", split="tune", set_="v1",
            required=["scope:state_local_taxes", "DQ-12"], shown=["DQ-12", "act:value_added_tax_act"]),
        row("ADV-4", "adversarial", "refused", category="B", split="tune", set_="v2"),
    ]
    report = run_adversarial.tuning_report(rows)
    assert report["in_scope_questions"] == 5
    assert report["false_refusals"] == ["Q002", "G03", "ADV-2"]
    assert report["over_refusals"] == ["ADV-4"]
    assert report["required_notes_missed"] == 1 and report["notes_not_required"] == 3
    by_set = report["by_set"]
    assert set(by_set) == {"dev", "G01-G05", "v1 tune", "v1 holdout", "v2 tune", "controls"}
    assert (by_set["dev"]["cases"], by_set["dev"]["in_scope"], by_set["dev"]["false_refusals"]) == (2, 2, ["Q002"])
    assert by_set["dev"]["notes_not_required"] == {"Q002": ["DQ-12"]}
    assert by_set["G01-G05"]["false_refusals"] == ["G03"] and by_set["G01-G05"]["in_scope"] == 1
    assert by_set["v1 tune"]["required_notes_missed"] == {"ADV-3": ["scope:state_local_taxes"]}
    assert by_set["v1 tune"]["notes_not_required"] == {"ADV-1": ["scope:stamp_duties"],
                                                      "ADV-3": ["act:value_added_tax_act"]}
    assert by_set["controls"]["cases"] == 2 and by_set["controls"]["false_refusals"] == ["ADV-2"]
    text = run_adversarial.report_markdown(report)
    assert "falsely refused: 3 (Q002, G03, ADV-2)" in text and "| v2 tune | 1 | 0 | 0 | 0 | 0 |" in text


# D-69, D-70: stop at the first API error; one re-run after a void decision run -------------------

SPEND_LIMIT = llm.APIError("BadRequestError", status_code=400, api_error_type="invalid_request_error",
                           message="You have reached your specified API usage limits.")


class CountingClient:
    """A fake model client: raises `fail_on` at the given call number (1-based), else answers."""

    def __init__(self, fail_at=None, error=SPEND_LIMIT, model="fake"):
        self.calls, self.fail_at, self.error, self.model = 0, fail_at, error, model

    def create(self, params):
        self.calls += 1
        if self.calls == self.fail_at:
            raise self.error
        return "ok"


def test_first_api_error_stops_both_clients():
    """D-69: the first APIError is recorded once; after it neither the generator nor the verifier
    client reaches the API. A non-API GenerationError does not stop the run."""
    stop = run_adversarial.APIStop()
    gen_inner, ver_inner = CountingClient(fail_at=2), CountingClient()
    gen_client = run_adversarial.StopOnAPIError(gen_inner, stop)
    ver_client = run_adversarial.StopOnAPIError(ver_inner, stop)
    assert gen_client.create({}) == "ok" and ver_client.create({}) == "ok" and not stop.stopped
    with pytest.raises(llm.APIError):
        gen_client.create({})
    assert stop.error is SPEND_LIMIT
    for client in (gen_client, ver_client):
        with pytest.raises(llm.GenerationError, match="stopped at an earlier API error"):
            client.create({})
    assert (gen_inner.calls, ver_inner.calls) == (2, 1)
    other = run_adversarial.APIStop()
    plain = run_adversarial.StopOnAPIError(CountingClient(fail_at=1, error=llm.GenerationError("x")), other)
    with pytest.raises(llm.GenerationError):
        plain.create({})
    assert not other.stopped


def test_run_loop_stops_after_the_case_with_the_first_api_error():
    """D-69: the loop keeps the rows answered so far, including the case during which the error
    happened, and records where it stopped; a run with no error is complete."""
    cases = [{"id": f"C{i}"} for i in range(1, 4)]
    stop, seen = run_adversarial.APIStop(), []

    def answer_case(case, r):
        seen.append((r, case["id"]))
        if (r, case["id"]) == (2, "C2"):
            stop.record(SPEND_LIMIT)
        return {"id": case["id"], "repeat": r}

    runs, stopped = run_adversarial.run_loop(cases, 3, answer_case, stop)
    assert seen == [(1, "C1"), (1, "C2"), (1, "C3"), (2, "C1"), (2, "C2")]
    assert [len(rows) for rows in runs] == [3, 2]
    assert stopped == {"repeat": 2, "case": "C2", "cases_written": 5, "cases_planned": 9,
                       "error": SPEND_LIMIT.as_log()}
    runs, stopped = run_adversarial.run_loop(cases, 1, lambda case, r: {"id": case["id"]}, run_adversarial.APIStop())
    assert stopped is None and [r["id"] for r in runs[0]] == ["C1", "C2", "C3"]


def stopped_record(case="ADV-1T", repeat=1):
    return {"repeat": repeat, "case": case, "cases_written": 1, "cases_planned": 2, "error": SPEND_LIMIT.as_log()}


def sheet_row(id_):
    return {"id": id_, "source": "dev", "expected": "answer_supported", "status": "answered",
            "refusal_reason": None, "flags": [], "notes_shown": [], "rendered": "r", "verification": None,
            "passages": [], "cost_usd": 0.01, "required_notes": []}


def test_stopped_tuning_run_is_written_void_and_exits_3(tmp_path, monkeypatch, capsys):
    """D-69: a tuning run that hits an API error writes what it has, marked stopped_on_api_error and
    void, with no D-61 counts and no review sheet, and exits non-zero."""
    monkeypatch.setattr(run_adversarial, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run_adversarial, "PACK_DIR", tmp_path / "pack")
    monkeypatch.setattr(run_adversarial, "next_tune_run", lambda results_dir=None: 9)
    monkeypatch.setattr(run_adversarial, "tune_cases", lambda s, parts, extra=(): [{"id": "Q001", "question": "q"}] * 2)
    monkeypatch.setattr(run_adversarial, "current_config", lambda s=None: {"tau": 0.4})
    monkeypatch.setattr(run_adversarial, "run_cases",
                        lambda cases, repeats: ([[sheet_row("Q001")]], {}, stopped_record("Q001")))
    assert run_adversarial.main(["--split", "tune", "--set", "v1", "--run", "9"]) == 3
    record = json.loads((tmp_path / "tune_run9.json").read_text(encoding="utf-8"))
    assert record["review"] == "void" and record["d61"] is None and len(record["cases"]) == 1
    assert record["stopped_on_api_error"]["error"]["message"] == "You have reached your specified API usage limits."
    assert not (tmp_path / "pack").exists()
    err = capsys.readouterr().err
    assert "STOPPED at the first API error" in err and "BadRequestError (HTTP 400)" in err and "void" in err


def frozen_fixture(tmp_path, monkeypatch):
    """A fixture set with a frozen configuration, registered in SETS, its in-scope questions empty."""
    fx = fixture_set(tmp_path, ROWS)
    monkeypatch.setattr(run_adversarial, "current_config",
                        lambda s=None: {"tau": 0.4} | ({"adversarial_set": {
                            "version": run_adversarial.get_set(s).name,
                            "sha256": run_adversarial.get_set(s).questions_sha256}} if s is not None else {}))
    fx = dataclasses.replace(fx, config_sha256=run_adversarial.freeze_config(fx))
    monkeypatch.setitem(run_adversarial.SETS, "fx", fx)
    monkeypatch.setattr(run_adversarial, "in_scope_questions", lambda include: [])
    monkeypatch.setattr(run_adversarial, "PACK_DIR", tmp_path / "pack")
    return fx


def test_void_decision_run_allows_exactly_one_rerun(tmp_path, monkeypatch):
    """D-70: a decision run stopped by an API error is written void (no review sheets) and exits 3;
    the plain command then refuses; --rerun-after-void runs once with its own marker and result; a
    second re-run is refused."""
    fx = frozen_fixture(tmp_path, monkeypatch)
    outcomes = iter([([[sheet_row("ADV-2H")]], {}, stopped_record("ADV-2H")),
                     ([[sheet_row("ADV-2H")]] * 3, {}, None)])
    monkeypatch.setattr(run_adversarial, "run_cases", lambda cases, repeats: next(outcomes))
    with pytest.raises(run_adversarial.GuardError, match="no complete record of a first run"):
        run_adversarial.check_holdout_guard(fx, rerun=True)
    assert run_adversarial.main(["--holdout", "--set", "fx"]) == 3
    first = json.loads(fx.result.read_text(encoding="utf-8"))
    assert first["review"] == "void" and first["stopped_on_api_error"]["case"] == "ADV-2H"
    assert first["rerun_after_void"] is False and not (tmp_path / "pack").exists()
    assert run_adversarial.main(["--holdout", "--set", "fx"]) == 2  # runs once
    assert run_adversarial.main(["--holdout", "--set", "fx", "--rerun-after-void"]) == 0
    assert json.loads(fx.rerun_marker.read_text(encoding="utf-8"))["rerun_after_void"] is True
    second = json.loads(fx.rerun_result.read_text(encoding="utf-8"))
    assert second["review"] == "pending" and second["stopped_on_api_error"] is None
    assert second["rerun_after_void"] is True and len(second["runs"]) == 3
    assert (tmp_path / "pack" / "fx_holdout_repeat1_rerun" / "review_sheet.md").exists()
    assert json.loads(fx.result.read_text(encoding="utf-8")) == first  # the void record is kept
    with pytest.raises(run_adversarial.GuardError, match="has already been made"):
        run_adversarial.check_holdout_guard(fx, rerun=True)
    assert run_adversarial.main(["--holdout", "--set", "fx", "--rerun-after-void"]) == 2


def test_rerun_is_refused_unless_the_first_run_was_void(tmp_path, monkeypatch, capsys):
    """D-70: no re-run after a complete first run, after a run with no record, or for a void re-run
    (the one re-run is spent); the flag needs --holdout. The live-configuration checks still apply."""
    fx = frozen_fixture(tmp_path, monkeypatch)
    fx.marker.parent.mkdir(parents=True)
    fx.marker.write_text("{}", encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="no complete record"):  # crashed, not void
        run_adversarial.check_holdout_guard(fx, rerun=True)
    fx.result.write_text(json.dumps({"review": "pending", "stopped_on_api_error": None}), encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="not marked stopped_on_api_error"):
        run_adversarial.check_holdout_guard(fx, rerun=True)
    fx.result.write_text(json.dumps({"stopped_on_api_error": stopped_record()}), encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="live configuration differs"):
        run_adversarial.check_holdout_guard(fx, rerun=True, live={"tau": 0.5})
    assert run_adversarial.check_holdout_guard(fx, rerun=True)["tau"] == 0.4
    fx.rerun_result.write_text(json.dumps({"stopped_on_api_error": stopped_record()}), encoding="utf-8")
    with pytest.raises(run_adversarial.GuardError, match="has already been made"):
        run_adversarial.check_holdout_guard(fx, rerun=True)
    with pytest.raises(SystemExit) as exc:
        run_adversarial.main(["--split", "tune", "--set", "v1", "--run", "8", "--rerun-after-void"])
    assert exc.value.code == 2 and "--holdout only" in capsys.readouterr().err
    v2 = run_adversarial.SETS["v2"]
    assert v2.rerun_marker == run_adversarial.RESULTS_DIR / "v2" / "holdout_started_rerun.json"
    assert v2.rerun_result == run_adversarial.RESULTS_DIR / "v2" / "holdout_final_rerun.json"


def test_frozen_configuration_records_the_gap_check_mode(monkeypatch):
    """D-68, D-69: per_gap is part of the live configuration, so a frozen file catches a revert."""
    from generation import verify
    assert verify.GAP_PER_GAP is True
    live = run_adversarial.current_config("v2")
    assert live["verifier"]["per_gap"] is True
    monkeypatch.setattr(run_adversarial, "GAP_PER_GAP", False)
    assert run_adversarial.current_config("v2") != live


def test_adversarial_loader_checks_both_hashes(tmp_path):
    fx = fixture_set(tmp_path, ROWS)
    for bad in ({"questions_sha256": None}, {"questions_sha256": "0" * 64}, {"split_sha256": "0" * 64}):
        with pytest.raises(run_adversarial.GuardError):
            run_adversarial.load_adversarial(dataclasses.replace(fx, **bad), "tune")
    other = write(fx.split, json.dumps({"set_sha256": fx.questions_sha256, "tune": ["ADV-X"], "holdout": ["ADV-2H"]}))
    with pytest.raises(run_adversarial.GuardError, match="do not match"):
        run_adversarial.load_adversarial(dataclasses.replace(fx, split_sha256=other), "tune")
    stale = write(fx.split, json.dumps({"set_sha256": "0" * 64, "tune": ["ADV-1T"], "holdout": ["ADV-2H"]}))
    with pytest.raises(run_adversarial.GuardError, match="not made for this adversarial set"):
        run_adversarial.load_adversarial(dataclasses.replace(fx, split_sha256=stale), "tune")


def test_adversarial_hashes_are_pinned_and_v1_loads_both_parts():
    """D-36 hashes (frozen 2026-11-03). v1's held-out part is spent (D-52) and loads for tuning, since
    its marker exists. The assertions report counts and ids, never question text."""
    v1 = run_adversarial.SETS["v1"]
    assert v1.questions_sha256 == "2e3541c88db45e53fa50ec591e7b65d108e7bb79955608ac3f1c07632ff592dc"
    assert v1.split_sha256 == "0143e246fd23e98569001bcdafd5b771d5c4fa628a305a37c36d74937227f6fc"
    tune, holdout = run_adversarial.load_adversarial("v1", "tune"), run_adversarial.load_adversarial("v1", "holdout")
    assert len(tune) == 48 and all(row["split"] == "tune" for row in tune)
    assert len(holdout) == 48 and all(row["split"] == "holdout" for row in holdout)
    assert len({row["id"] for row in tune + holdout}) == 96


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
    # Every policy file (D-83), including the retrieval policy (D-105)
    paths = sorted((ROOT / "data" / "curated").glob("*.yml"))
    paths += sorted((ROOT / "docs" / "phase06_guardrails").glob("*.md"))
    paths += sorted((ROOT / "python" / "generation").glob("*.py"))  # includes prompt.py (gen-prompt/8)
    paths += sorted((ROOT / "python" / "retrieval").glob("*.py"))  # the commencement and definition rules (D-105)
    # Question sets are not artefacts. Anything named v4 is written by the adversarial_v4 session
    # and is never opened here (D-40, D-105, D-106); v2 and v3 are spent (D-73, D-106), so their
    # results are checked too.
    paths += [p for p in sorted((ROOT / "eval" / "guardrails").rglob("*.*"))
              if not p.name.startswith("adversarial_v") and p.name != "split_v1.json" and "v4" not in p.name
              and "v4" not in p.relative_to(ROOT / "eval" / "guardrails").parts]
    paths += sorted((ROOT / "tests").glob("test_*.py")) + sorted((ROOT / "tests" / "data").glob("*.json"))
    return [p for p in paths if p.is_file() and p.suffix in {".py", ".md", ".yml", ".json", ".jsonl", ".csv"}]


# D-127: decision run 4 ran (D-115); only its published results may be read as artefacts.
PUBLISHED_V4_RESULTS = {"docs/phase06_guardrails/g2_results_v4.md", "docs/phase06_guardrails/g2_results_v4.csv"}


def test_artefacts_never_include_a_question_set_or_v4_file():
    names = [p.relative_to(ROOT).as_posix() for p in _artefacts()]
    assert not [n for n in names if n.startswith("eval/guardrails/adversarial_v")
                or ("v4" in n and n not in PUBLISHED_V4_RESULTS)], names
    assert "python/generation/prompt.py" in names and "data/curated/disclosure_policy.yml" in names
    assert "data/curated/retrieval_policy.yml" in names and "python/retrieval/commencement.py" in names
    assert "eval/guardrails/note_recall_probes.jsonl" in names


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
    """Design section 10, on the v4 set (D-105, D-106; v1, v2 and v3 are spent, D-62, D-73, D-106),
    over the tests, fixtures, phase 6 docs, python/generation (prompt.py included), python/retrieval
    and data/curated/*.yml (the retrieval policy included). Reads adversarial_v4.jsonl directly,
    because load_adversarial refuses a held-out part before its marker exists; checks the bytes
    against the pinned sha256 first, keeps the rows in memory and reports ids only. It runs only in
    the overseer's session when the flag is set (D-40: the build session never sees held-out
    questions)."""
    v4 = run_adversarial.SETS["v4"]
    data = v4.questions.read_bytes()
    assert hashlib.sha256(data).hexdigest() == v4.questions_sha256 == V4_SHA256
    held_out = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    leaks = []
    for path in _artefacts():
        text = path.read_text(encoding="utf-8").lower()
        leaks += [(path.name, q["id"]) for q in held_out if q["question"].lower() in text]
    assert not leaks, leaks
