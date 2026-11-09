"""The foreign-law pre-check (D-110; G2 fix design 4, sections 2.3, 2.4 and 8) on every question it
may meet in G2 tuning.

    .venv\\Scripts\\python eval\\guardrails\\check_foreign_law.py

Visible questions (305): adversarial_v1 both parts, v2 and v3 (spent, tuning data), the 20 dev
questions, G01 to G05 and the 88 note-recall probes. For these it prints the ids it fires on, the
count of expected-answer questions (`answer_supported` or `flag`) it fires on, and the in-scope
questions of the D-84 rule among them. Design 4 measured 14 of 305, 0 of 158 and 0.

Test split rules (D-29): the 30 test questions are read in memory only. This prints counts only: the
questions, those naming a foreign place, and those the check fires on (design 4: 30, 0 and 0). No
text, id or gold unit of a test question is printed or written.

The check reads only question text and the guardrails policy: no database, no embedding and no model
call. adversarial_v4 is never loaded (its held-out part is refused before its marker exists).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "eval" / "retrieval"))

import run_adversarial  # noqa: E402
import run_eval  # noqa: E402
from generation.guardrails import foreign_law_check, load_guardrails_policy  # noqa: E402

PROBES = HERE / "note_recall_probes.jsonl"
SPENT_PARTS = (("v1", ("tune", "holdout")), ("v2", ("holdout",)), ("v3", ("holdout",)))
EXPECTED_ANSWER = ("answer_supported", "flag")


def visible_questions() -> list[dict]:
    """The 305 visible questions: id, question, expected, and whether it is one of the 54 in-scope."""
    rows = []
    for name, parts in SPENT_PARTS:
        for part in parts:
            for r in run_adversarial.load_adversarial(name, part):
                rows.append({"id": r["id"], "question": r["question"], "expected": r["expected"],
                             "in_scope": r["category"] == "C"})
    for r in run_adversarial.in_scope_questions(True):
        rows.append({"id": r["id"], "question": r["question"], "expected": r["expected"],
                     "in_scope": r["expected"] == "answer_supported"})
    for line in PROBES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append({"id": r["id"], "question": r["question"], "expected": "probe", "in_scope": False})
    return rows


def measure() -> dict:
    rules = load_guardrails_policy().foreign_law
    if rules is None:
        raise SystemExit("the guardrails policy has no foreign_law block")
    rows = visible_questions()
    fired = [r for r in rows if foreign_law_check(r["question"], rules).fired]
    test = [q["question"] for q in run_eval.load_questions("test")]
    return {
        "visible": len(rows),
        "fired_ids": [r["id"] for r in fired],
        "fired_expected": sorted({r["expected"] for r in fired}),
        "expected_answer": sum(r["expected"] in EXPECTED_ANSWER for r in rows),
        "expected_answer_fired": [r["id"] for r in fired if r["expected"] in EXPECTED_ANSWER],
        "in_scope": sum(r["in_scope"] for r in rows),
        "in_scope_fired": [r["id"] for r in fired if r["in_scope"]],
        "test": {"questions": len(test),
                 "foreign_place": sum(bool(rules.foreign_place.search(q)) for q in test),
                 "fired": sum(foreign_law_check(q, rules).fired for q in test)},
    }


def main() -> int:
    m = measure()
    print(f"Visible questions: {m['visible']}; the check fires on {len(m['fired_ids'])}: {', '.join(m['fired_ids'])}")
    print(f"Expected of the questions it fires on: {', '.join(m['fired_expected']) or 'none'}")
    print(f"Expected-answer questions: {m['expected_answer']}; fired: {len(m['expected_answer_fired'])}"
          + (f" ({', '.join(m['expected_answer_fired'])})" if m["expected_answer_fired"] else ""))
    print(f"In-scope questions (D-84): {m['in_scope']}; fired: {len(m['in_scope_fired'])}")
    t = m["test"]
    print(f"Test split (counts only): {t['questions']} questions, {t['foreign_place']} naming a foreign place, "
          f"{t['fired']} fired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
