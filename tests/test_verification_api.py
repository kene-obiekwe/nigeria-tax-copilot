"""Verification against the live API (Phase 6; guardrails design, section 10, API tests 1 to 5).
Marked `api`: skipped with a stated reason when LLM_API_KEY is absent, and failing, not skipping,
when the database is down. Output is not deterministic, so the tests check verdict classes, never
wording. Real Phase 5 claims are named by calibration id; no calibration mutation is run (the
calibration set runs only after the overseer's review). About seven Haiku calls per run."""
import json
import os

import pytest
from dotenv import load_dotenv

from check_pgvector import connect
from generation import generate as gen, llm, verify
from retrieval.retrieve import Retriever

ROOT = gen.ROOT
load_dotenv(ROOT / ".env")
pytestmark = [pytest.mark.api,
              pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="LLM_API_KEY is not set in .env")]

STORE = gen.FileCorpusStore()
REAL = {r["id"]: r for r in (json.loads(line) for line in (ROOT / "eval" / "guardrails" / "verifier_calibration_v1.jsonl")
                             .read_text(encoding="utf-8").splitlines()) if r["kind"] == "real"}
SUPPORTED = "CAL-R2-Q018-2"     # human verdict: supported (not a mutation base)
OVERSTATED = "CAL-R1-Q043-5"    # human verdict: unsupported (overstated condition, weakness (f))


@pytest.fixture(scope="module")
def verifier():
    return llm.AnthropicLLMClient(model=llm.VERIFIER_MODEL)


def run_claim(claim_id, verifier, text=None, **kw):
    row = REAL[claim_id]
    chunks = STORE.chunks([c for _, c in row["cited"]])
    return verify.check_claim(1, text or row["text"], [(label, chunks[c]) for label, c in row["cited"]], verifier, **kw)


@pytest.fixture(scope="module")
def supported_twice(verifier):
    return run_claim(SUPPORTED, verifier), run_claim(SUPPORTED, verifier)


def test_a_supported_claim_verifies_with_its_evidence_found(supported_twice):
    first, _ = supported_twice
    assert REAL[SUPPORTED]["human_verdict"] == "supported"
    assert first.verdict == "supported", (first.verdict, first.reason, first.sub_reason, first.error)
    assert any(e["found"] for e in first.evidence)


def test_the_same_claim_with_its_figure_changed_is_not_supported(verifier):
    row = REAL[SUPPORTED]
    assert "7.5%" in row["text"]
    changed = run_claim(SUPPORTED, verifier, text=row["text"].replace("7.5%", "10%"), always_call=True)
    assert changed.verdict != "supported" and changed.rule.missing == ["pct:10"]
    assert changed.llm_verdict in ("unsupported", "contradicted"), changed.llm_verdict


def test_the_overstated_condition_claim_is_not_supported(verifier):
    assert REAL[OVERSTATED]["human_verdict"] == "unsupported"
    result = run_claim(OVERSTATED, verifier)
    assert result.verdict != "supported", (result.verdict, result.reason)


class Counting:
    model = llm.MODEL

    def __init__(self):
        self.calls = 0

    def create(self, params):
        self.calls += 1
        raise AssertionError("no model call expected")


def test_no_such_section_is_refused_before_any_model_or_embedding_call():
    model, check_model = Counting(), Counting()
    with connect() as conn:
        retriever = Retriever(conn, gen.retrieval_config())
        calls = []
        original = retriever.retrieve
        retriever.retrieve = lambda *a, **k: calls.append(1) or original(*a, **k)
        answer = gen.generate("What does section 250 of the Nigeria Tax Act say?", retriever=retriever, llm=model,
                              verifier=check_model, store=gen.DbCorpusStore(conn), log_dir=None)
    assert (answer.status, answer.refusal_reason) == ("refused", "no_such_section")
    assert calls == [] and model.calls == 0 and check_model.calls == 0


def test_usage_and_cost_recorded_and_the_cache_read_on_the_second_call(supported_twice):
    first, second = supported_twice
    for result in (first, second):
        assert result.usage["output_tokens"] > 0 and result.cost_usd > 0
        assert result.response["model"].startswith("claude-haiku-5-5")
    assert second.usage["cache_read_input_tokens"] > 0, second.usage


# Gap classifier, gap-prompt/3 (D-66, D-67). Gap texts are from tuning runs 2 to 4 (adversarial_v1 tune part
# and dev split), except the two wording probes, which are written for this test. A question "dev:<id>" is read
# from the dev split. One Haiku call each.
GAP_FIXTURES = [
    # ADV-083, run 4: says what an absent provision sets (the rate), which section 51 itself leaves to regulations.
    ("ADV-083", "Our parent company is in the Netherlands. When our Nigerian company pays it a dividend, is any tax "
     "taken off?",
     "The rate of tax deducted at source on dividends, and the rules for deduction, are in section 51 of the Nigeria "
     "Tax Administration Act, 2025, which is not among the passages.", "legal_content"),
    # ADV-028, run 4: names the absent provision only by the passage's link to it.
    ("ADV-028", "What is the withholding tax on dividends paid by a company in Senegal?",
     "The rate of withholding tax on dividends is not stated in the passages; section 51 of the Nigeria Tax "
     "Administration Act 2025, which is referred to, is not among them.", "missing_information"),
    # Probe: a link that also says what the absent provision is for.
    ("probe-for", "Our parent company is in the Netherlands. When our Nigerian company pays it a dividend, is any "
     "tax taken off?",
     "Section 51 of the Nigeria Tax Administration Act, 2025, which sets the rate of tax deducted at source, is not "
     "among the passages.", "legal_content"),
    # Probe: a bare "subject to" link that names no subject matter.
    ("probe-link", "We paid company tax late. What is added to it?",
     "Section 67 of the Nigeria Tax Administration Act 2025, to which section 65(1) is subject, is not among the "
     "passages.", "missing_information"),
    # ADV-083, tune run 2: says what an absent provision sets (its rate and method).
    ("ADV-083-run2", "Our parent company is in the Netherlands. When our Nigerian company pays it a dividend, is any "
     "tax taken off?",
     "The rate and method of the tax deducted at source under section 51 of the Nigeria Tax Administration Act, 2025 "
     "are not in the passages.", "legal_content"),
    # Q025, tune run 3: names what the passages do not define, and no provision.
    ("Q025-run3", "dev:Q025", 'The passages do not define "professional services".', "missing_information"),
]


def gap_question(question: str) -> str:
    if not question.startswith("dev:"):
        return question
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
    run_eval = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("run_eval", run_eval)
    spec.loader.exec_module(run_eval)
    return {q["id"]: q["question"] for q in run_eval.load_questions("dev")}[question.removeprefix("dev:")]


@pytest.mark.parametrize("case_id, question, gap, expected", GAP_FIXTURES, ids=[f[0] for f in GAP_FIXTURES])
def test_gap_prompt_3_classes(verifier, case_id, question, gap, expected):
    result = verify.check_gaps(gap_question(question), [(gap, "model")], verifier, ())
    assert result.error is None, result.error
    assert result.request["prompt_version"] == "gap-prompt/3"
    assert result.gaps[0].gap_class == expected, (case_id, result.gaps[0].gap_class)
