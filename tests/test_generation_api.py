"""Grounded generation against the live APIs (Phase 5). Marked `api`: skipped with a stated reason
when LLM_API_KEY or EMBEDDING_API_KEY is absent, and failing, not skipping, when the database is
down. Output is not deterministic, so the tests check structure, never wording. Two model calls
per run (a few cents), both logged under logs/generation/."""
import importlib.util
import os
import sys

import pytest
from dotenv import load_dotenv

from embed import clients
from generation.generate import generate, retrieval_config

ROOT = clients.ROOT
load_dotenv(ROOT / ".env")
pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="LLM_API_KEY is not set in .env"),
    pytest.mark.skipif(not os.getenv("EMBEDDING_API_KEY"), reason="EMBEDDING_API_KEY is not set in .env"),
]

spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
sys.modules.setdefault("run_eval", run_eval)
spec.loader.exec_module(run_eval)

OUT_OF_CORPUS = "What is the company income tax rate in Kenya?"


@pytest.fixture(scope="module")
def answers():
    """Two back-to-back calls: dev question Q003, then an out-of-corpus question."""
    dev = {q["id"]: q["question"] for q in run_eval.load_questions("dev")}
    cache = run_eval.QueryCache(retrieval_config()["model"])
    first = generate(dev["Q003"], query_vector=cache.embed(dev["Q003"]))
    second = generate(OUT_OF_CORPUS, query_vector=cache.embed(OUT_OF_CORPUS))
    return first, second


def test_dev_question_is_answered_with_retrieved_citations(answers):
    first, _ = answers
    assert first.status == "answered", (first.status, first.error)
    assert first.claims
    retrieved = {p.chunk_id for p in first.passages}
    assert all(set(c.chunk_ids) <= retrieved and c.chunk_ids for c in first.claims)
    assert first.rendered.rstrip().endswith("18 October 2026).")


def test_out_of_corpus_question_is_declined(answers):
    _, second = answers
    assert second.status == "not_covered", (second.status, second.error)
    assert second.claims == []


def test_system_prompt_cache_is_read_on_the_second_call(answers):
    first, second = answers
    assert first.usage["cache_creation_input_tokens"] + first.usage["cache_read_input_tokens"] > 0
    assert second.usage["cache_read_input_tokens"] > 0
