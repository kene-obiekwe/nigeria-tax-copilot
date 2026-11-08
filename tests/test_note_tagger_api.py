"""The note tagger against the live API (D-77; G2 fix design 2, section 2.7, item 3). Marked `api`:
skipped with a stated reason when LLM_API_KEY is absent. Six fixed questions, written by the build
session (D-41), each through the live majority tagger (D-88: three Haiku 5.5 calls at effort low and
a 2-of-3 vote; about $0.0015 in all). Output is not deterministic, so the test checks validation and
the clear-cut labels only."""
import os

import pytest
from dotenv import load_dotenv

from generation import llm, note_tagger, prompt

load_dotenv(llm.ROOT / ".env")
pytestmark = [pytest.mark.api,
              pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="LLM_API_KEY is not set in .env")]

QUESTIONS = [
    # (question, label that must be chosen, or None for a question that raises no note topic)
    ("Does the tenancy agreement for my bakery need to carry a stamp?", "stamp_duties"),
    ("When was the administration statute of 2025 first binding on taxpayers?", "ntaa_commencement"),
    ("Do the petrol purchases for my delivery vans carry a surcharge?", "surcharge"),
    ("How much company tax does a small firm pay under the 2025 Act?", None),
    ("How is tax worked out on the monthly wage I pay our cook?", "personal_income_tax"),
    ("Were our 2023 sales assessed under the old rules or the new ones?", "pre_2026_periods"),
]


@pytest.fixture(scope="module")
def results():
    client = llm.AnthropicLLMClient(model=note_tagger.TAGGER_MODEL)
    return [note_tagger.tag_question(q, client) for q, _ in QUESTIONS]


def test_every_call_validates(results):
    for result in results:
        assert result.status == "ok", result.reason
        assert set(result.labels) <= set(prompt.TAG_LABELS) and len(result.labels) <= note_tagger.MAX_LABELS
        assert (result.votes, result.threshold, len(result.calls)) == (3, 2, 3)
        for call in result.calls:
            assert call.status == "ok", call.reason
            assert call.response["model"].startswith("claude-haiku-5-5") and call.reasoning
    assert sum(r.cost_usd for r in results) < 0.01


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_clear_cut_labels(results, index):
    expected = QUESTIONS[index][1]
    labels = results[index].labels
    assert (expected in labels) if expected else labels == []
