"""First five and first six passages: config_v2.json against config_v3.json (D-54, D-100; G2 fix
design 3, sections 6 and 8).

    .venv\\Scripts\\python eval\\retrieval\\check_first_six.py

config_v3.json only appends attachments after P6, so on every dev and test question the first six
chunk ids must be those config_v2.json gives, and the first five those gate G1 was measured on
(D-21: the test split's 30 of 30 still describes them). For each split this prints counts only:
questions, first five identical, first six identical, a definition attached, the commencement
trigger firing (and its chunk attached), and questions with a percent number after a list number
(D-101).

Test split rules (D-29): the questions are compared by chunk ids only. No question text, gold unit
or hit-rate is read or printed, and nothing is written. Query vectors come from the cache only: a
question with no cached vector stops the check, so no embedding call is ever made.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(HERE))

import run_eval  # noqa: E402
from check_pgvector import connect  # noqa: E402
from embed.clients import CLIENTS  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402

CONFIG_V2 = HERE / "config_v2.json"
CONFIG_V3 = HERE / "config_v3.json"
# A list number followed by a percent sign, "per cent" or "percent" ("section 56, 30%", "section 148,
# 7.5%"): the pattern the D-101 rule changes, counted on the question text (G2 fix design 3, section 6).
PERCENT_AFTER_LIST = re.compile(r"(?:,|\band\b|\bor\b|\bto\b)\s*\d+(?:\.\d+)?\s*(?:%|per\s*cent|percent)",
                                re.IGNORECASE)


class NotCached(RuntimeError):
    pass


def cached_vectors(model: str):
    cache = run_eval.QueryCache(model)
    client_cls = CLIENTS.get(model)

    def vector(question: str) -> list[float]:
        digest = cache.key(client_cls, question)
        if digest not in cache.records:
            raise NotCached("a question has no cached query vector; this check never embeds")
        return cache.records[digest]
    return vector


def compare(conn, questions: list[dict], v2: dict, v3: dict, vector) -> dict:
    """Counts only, by chunk ids."""
    old, new = Retriever(conn, v2), Retriever(conn, v3)
    counts = {"questions": 0, "first_five_identical": 0, "first_six_identical": 0, "definition_attached": 0,
              "commencement_trigger": 0, "commencement_attached": 0, "percent_after_list_number": 0}
    for q in questions:
        text = q["question"]
        vec = vector(text)
        a = [r.chunk_id for r in old.retrieve(text, 5, query_vector=vec)]
        b = new.retrieve(text, 5, query_vector=vec)
        ids = [r.chunk_id for r in b]
        reasons = [r.reasons[0] for r in b[len(a):] if r.reasons]
        counts["questions"] += 1
        counts["first_five_identical"] += ids[:5] == a[:5]
        counts["first_six_identical"] += ids[:6] == a[:6] and len(a) == 6
        counts["definition_attached"] += any(r.startswith("definition attachment") for r in reasons)
        counts["commencement_trigger"] += new.commencement.target(text) is not None
        counts["commencement_attached"] += any(r.startswith("commencement lookup") for r in reasons)
        counts["percent_after_list_number"] += bool(PERCENT_AFTER_LIST.search(text))
    return counts


def main() -> int:
    v2 = json.loads(CONFIG_V2.read_text(encoding="utf-8"))
    v3 = json.loads(CONFIG_V3.read_text(encoding="utf-8"))
    if v2["model"] != v3["model"]:
        print("refused: the two configurations use different embedding models", file=sys.stderr)
        return 2
    vector = cached_vectors(v3["model"])
    try:
        with connect() as conn:
            for split in run_eval.SPLITS:
                c = compare(conn, run_eval.load_questions(split), v2, v3, vector)
                print(f"{split}: {c['questions']} questions; first five identical {c['first_five_identical']}; "
                      f"first six identical {c['first_six_identical']}; definition attached {c['definition_attached']}; "
                      f"commencement trigger fires {c['commencement_trigger']} "
                      f"(attached {c['commencement_attached']}); "
                      f"percent after a list number {c['percent_after_list_number']}")
    except NotCached as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 3
    print("query embeddings: 0 embedded (cache only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
