"""Phase 4 labelled retrieval question set (eval/retrieval/questions_v1.jsonl).

Checks the schema, coverage, the seeded stratified split, that every gold unit and evidence
quote exists in the chunk file, that no question copies five or more consecutive words from its
gold chunks, that none of the eight Phase 3 smoke questions is reused, and that the file still
matches the sha256 frozen in docs/phase04_retrieval/question_set.md. Needs no database or API.
"""
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

import pytest

from retrieval import smoke_query

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "retrieval" / "questions_v1.jsonl"
CHUNKS = ROOT / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
DOC = ROOT / "docs" / "phase04_retrieval" / "question_set.md"

SEED = 20261102
N_QUESTIONS, N_DEV, N_TEST = 50, 20, 30
MAX_SHARED_RUN = 4  # a shared run of 5 or more consecutive words fails
MAX_EVIDENCE_WORDS = 24
CATEGORIES = ["cit_rates_levy", "deductions_gains", "vat", "exemptions_incentives", "definitions",
              "schedules", "ntaa_registration_returns", "ntaa_assessment_objection",
              "ntaa_payment_penalties", "section_reference"]
FIELDS = ["id", "question", "category", "gold_units", "primary_unit", "evidence", "register_ids", "split"]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


RECORDS = load_jsonl(QUESTIONS)
CHUNK_LIST = load_jsonl(CHUNKS)


def matches(chunk_id: str, unit: str) -> bool:
    """The hit rule: a chunk matches a gold unit if it is that chunk or one of its parts."""
    return chunk_id == unit or chunk_id.startswith(unit + "(")


def gold_chunks(units: list[str]) -> list[dict]:
    return [c for c in CHUNK_LIST if any(matches(c["chunk_id"], u) for u in units)]


def squash(text: str) -> str:
    return " ".join(text.split())


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))


def defined_terms() -> set[tuple[str, ...]]:
    """Multi-word defined terms, from the definition chunk ids (single words are not ignored)."""
    terms = set()
    for c in CHUNK_LIST:
        if ":def:" in c["chunk_id"]:
            term = tuple(c["chunk_id"].split(":def:")[1].split("(")[0].split("-"))
            if len(term) > 1:
                terms.add(term)
    return terms


DEFINED_TERMS = defined_terms()


def shared_runs(question: str, chunk_texts: list[str], n: int = MAX_SHARED_RUN + 1) -> list[str]:
    """Runs of n question words also found consecutively in a chunk, skipping runs that touch a
    defined term."""
    q = words(question)
    in_term = [False] * len(q)
    for term in DEFINED_TERMS:
        for i in range(len(q) - len(term) + 1):
            if tuple(q[i:i + len(term)]) == term:
                in_term[i:i + len(term)] = [True] * len(term)
    chunk_grams = set()
    for text in chunk_texts:
        w = words(text)
        chunk_grams.update(tuple(w[i:i + n]) for i in range(len(w) - n + 1))
    return [" ".join(q[i:i + n]) for i in range(len(q) - n + 1)
            if not any(in_term[i:i + n]) and tuple(q[i:i + n]) in chunk_grams]


def expected_split(records: list[dict]) -> dict[str, str]:
    """Dev quota per category by largest remainder (ties by category name), then one
    random.Random(SEED) shuffles each category's sorted ids, categories in sorted order."""
    counts = Counter(r["category"] for r in records)
    exact = {c: counts[c] * N_DEV / len(records) for c in counts}
    quota = {c: int(exact[c]) for c in counts}
    for c in sorted(counts, key=lambda c: (-(exact[c] - quota[c]), c))[:N_DEV - sum(quota.values())]:
        quota[c] += 1
    rng = random.Random(SEED)
    split = {}
    for c in sorted(counts):
        ids = sorted(r["id"] for r in records if r["category"] == c)
        rng.shuffle(ids)
        split.update({qid: "dev" if k < quota[c] else "test" for k, qid in enumerate(ids)})
    return split


def test_schema_and_ids():
    assert len(RECORDS) == N_QUESTIONS
    assert [r["id"] for r in RECORDS] == [f"Q{i:03d}" for i in range(1, N_QUESTIONS + 1)]
    for r in RECORDS:
        assert list(r) == FIELDS, r["id"]
        assert isinstance(r["question"], str) and r["question"].strip(), r["id"]
        assert r["category"] in CATEGORIES, r["id"]
        assert r["gold_units"] and len(set(r["gold_units"])) == len(r["gold_units"]), r["id"]
        assert r["primary_unit"] in r["gold_units"], r["id"]
        assert r["split"] in ("dev", "test"), r["id"]
        assert isinstance(r["register_ids"], list), r["id"]
    assert len({squash(r["question"]).lower() for r in RECORDS}) == N_QUESTIONS


def test_coverage():
    counts = Counter(r["category"] for r in RECORDS)
    assert set(counts) == set(CATEGORIES) and min(counts.values()) >= 3, counts
    assert counts["section_reference"] == 5
    acts = Counter(r["primary_unit"].split(":")[0] for r in RECORDS if r["category"] != "section_reference")
    assert 30 <= acts["nta2025"] <= 34 and 11 <= acts["ntaa2025"] <= 15, acts
    flagged = [r["id"] for r in RECORDS
               if any(c["discrepancy"]["flag"] for c in gold_chunks([r["primary_unit"]]))]
    assert len(flagged) >= 5, flagged
    needs_definition = [r["id"] for r in RECORDS if any(":def:" in u for u in r["gold_units"])]
    assert len(needs_definition) >= 3, needs_definition


def test_split_counts_and_stratification():
    assert Counter(r["split"] for r in RECORDS) == {"dev": N_DEV, "test": N_TEST}
    assert {r["id"]: r["split"] for r in RECORDS} == expected_split(RECORDS)
    for c in CATEGORIES:
        in_cat = [r for r in RECORDS if r["category"] == c]
        dev = sum(r["split"] == "dev" for r in in_cat)
        assert abs(dev - len(in_cat) * N_DEV / N_QUESTIONS) < 1, (c, dev, len(in_cat))


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_gold_units_exist_and_register_ids_match(record):
    for unit in record["gold_units"]:
        assert gold_chunks([unit]), f"{record['id']}: {unit} matches no chunk"
    expected = sorted({dr for c in gold_chunks(record["gold_units"]) for dr in c["discrepancy"]["register_ids"]},
                      key=lambda dr: int(dr.split("-")[1]))
    assert record["register_ids"] == expected


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_evidence_is_verbatim_in_primary_chunk(record):
    evidence = squash(record["evidence"])
    assert 0 < len(evidence.split()) <= MAX_EVIDENCE_WORDS, record["id"]
    assert any(evidence in squash(c["text"]) for c in gold_chunks([record["primary_unit"]])), record["id"]


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_no_five_word_overlap_with_gold_chunks(record):
    texts = [c["text"] for c in gold_chunks(record["gold_units"])]
    assert shared_runs(record["question"], texts) == []


def test_overlap_check_catches_copied_wording():
    text = ("the books shall be kept for a period not less than six years and the owner shall "
            "notify the relevant tax authority of the change")
    assert shared_runs("Are books KEPT for a period, not less than six years?", [text]) == [
        "kept for a period not", "for a period not less", "a period not less than",
        "period not less than six", "not less than six years"]
    # "relevant tax authority" is a defined term, so a run through it does not count
    assert not shared_runs("Who should notify the relevant tax authority of it?", [text])
    assert not shared_runs("How long must I keep my books?", [text])


def test_smoke_questions_not_reused():
    smoke = {squash(q).lower() for q, _ in smoke_query.QUESTIONS}
    assert len(smoke) == 8
    assert not smoke & {squash(r["question"]).lower() for r in RECORDS}


def test_sha256_matches_frozen_value_in_doc():
    recorded = re.search(r"sha256 `([0-9a-f]{64})`", DOC.read_text(encoding="utf-8"))
    assert recorded, "frozen sha256 not found in question_set.md"
    assert hashlib.sha256(QUESTIONS.read_bytes()).hexdigest() == recorded.group(1)
