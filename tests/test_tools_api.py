"""The Phase 7 tools against the live APIs (tools design, Section 11). Marked `api`: they run only
with TAXY_RUN_API_TESTS=1 (D-108) and then need LLM_API_KEY and EMBEDDING_API_KEY in .env; the database
must be up. About $0.05 a run, cap $0.15 (D-116).

Two new questions, in no question set and not among the exit questions:
- a VAT question that needs a calculation: the tool is called, the computed item is shown with the
  expected figure, and the rate claim is kept (K4);
- a question that needs no calculation: no tool call.
Output is not deterministic, so the checks are on structure and figures, never on wording.
"""
import json
import os
import sys
from decimal import Decimal

import pytest
from dotenv import load_dotenv

from embed import clients
from generation import generate as gen
from tools import taxcalc

ROOT = clients.ROOT
load_dotenv(ROOT / ".env")
VAT_QUESTION = ("Our design studio is billing a client ₦840,000 for branding services, before VAT. "
                "How much VAT do we add to the invoice?")
VAT_EXPECTED = "63000.00"  # 7.5% of ₦840,000 (Section 148)
PLAIN_QUESTION = "What is the definition of a small company in the Nigeria Tax Act 2025?"


def config() -> gen.GenerationConfig:
    return gen.GenerationConfig(tools=gen.ToolsConfig(enabled=True, rates=taxcalc.load_rates()))


def used(answer) -> list[str]:
    return [u["name"] for u in (answer.tools or {}).get("tool_uses", [])]


@pytest.mark.parametrize("question", [VAT_QUESTION, PLAIN_QUESTION], ids=["vat", "plain"])
def test_the_api_test_questions_are_new(question):
    """Free: neither question is in a question set (the retrieval test split is never opened, D-29)."""
    sys.path.insert(0, str(ROOT / "eval" / "guardrails"))
    sys.path.insert(0, str(ROOT / "eval" / "retrieval"))
    import run_adversarial
    import run_eval
    seen = {q["question"] for q in run_eval.load_questions("dev")}
    for name in ("v1", "v2", "v3", "v4"):
        seen |= {q["question"] for q in run_adversarial.load_adversarial(name, "holdout")}
        if run_adversarial.get_set(name).split is not None:
            seen |= {q["question"] for q in run_adversarial.load_adversarial(name, "tune")}
    exit_rows = [json.loads(line) for line in
                 (ROOT / "eval" / "tools" / "exit_questions_v1.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]  # read only to compare the text; no model sees them
    seen |= {r["question"] for r in exit_rows}
    assert question not in seen


@pytest.fixture(scope="module")
def answers():
    """Both questions, once each (paid): two answers, with tools on."""
    return gen.generate(VAT_QUESTION, config=config()), gen.generate(PLAIN_QUESTION, config=config())


@pytest.mark.api
@pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="LLM_API_KEY is not set in .env")
@pytest.mark.skipif(not os.getenv("EMBEDDING_API_KEY"), reason="EMBEDDING_API_KEY is not set in .env")
def test_a_vat_question_calls_the_tool_and_shows_the_computed_figure(answers):
    answer, _ = answers
    assert answer.status == "answered", (answer.status, answer.error)
    assert "calculate_liability" in used(answer)
    shown = [c for c in answer.computations if c["shown"]]
    assert [c["result"] for c in shown] == [VAT_EXPECTED], [(c["status"], c.get("result")) for c in answer.computations]
    assert taxcalc.naira(Decimal(VAT_EXPECTED)) in answer.rendered
    assert "- Result: " in answer.rendered
    # The rate claim is kept: a kept claim cites Section 148, which confirms the 7.5% rate (K4).
    assert any("nta2025:s148" in c.chunk_ids for c in answer.claims), [c.chunk_ids for c in answer.claims]
    assert answer.rendered.rstrip().endswith("18 October 2026).")


@pytest.mark.api
@pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="LLM_API_KEY is not set in .env")
@pytest.mark.skipif(not os.getenv("EMBEDDING_API_KEY"), reason="EMBEDDING_API_KEY is not set in .env")
def test_a_question_that_needs_no_calculation_makes_no_tool_call(answers):
    _, answer = answers
    assert answer.status == "answered", (answer.status, answer.error)
    assert used(answer) == [] and answer.computations == []
