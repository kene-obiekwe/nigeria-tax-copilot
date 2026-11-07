"""Offline replay of the gap reference check (D-79; G2 fix design 2, section 3.2).

    .venv\\Scripts\\python eval\\guardrails\\replay_gap_references.py

Replays `verify.unconnected_references` over every gap kept in tuning runs 6 and 7 and the three
repeats of G2 decision run 2 (adversarial_v2, spent and tuning data since D-73), with the passages
each case was shown, read from the committed chunk file. No model call and no database. Prints, per
run, the kept gaps, those naming a section or Schedule, and every gap the check removes (case id,
unconnected references and gap text), then checks the removals against the eight in design 3.2.
Exit code 0 when they match, 1 otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from generation.generate import FileCorpusStore  # noqa: E402
from generation.verify import unconnected_references  # noqa: E402
from retrieval import references  # noqa: E402

RESULTS = HERE / "results"
RUNS = (("tune 6", RESULTS / "tune_run6.json"), ("tune 7", RESULTS / "tune_run7.json"),
        ("v2 repeat 1", RESULTS / "v2" / "holdout_final.json", 1),
        ("v2 repeat 2", RESULTS / "v2" / "holdout_final.json", 2),
        ("v2 repeat 3", RESULTS / "v2" / "holdout_final.json", 3))
# Design 3.2, last column: (run, case id, unconnected references).
EXPECTED = [
    ("tune 6", "ADV-008", ["section 290 (ntaa2025)"]),
    ("tune 6", "ADV-048", ["section 57A (nta2025)"]),
    ("tune 7", "ADV-008", ["section 290 (ntaa2025)"]),
    ("tune 7", "ADV-048", ["section 57A (nta2025)"]),
    ("v2 repeat 1", "ADV2-024", ["section 101A (ntaa2025)"]),
    ("v2 repeat 1", "G03", ["section 5"]),
    ("v2 repeat 2", "ADV2-024", ["section 101A (ntaa2025)"]),
    ("v2 repeat 3", "ADV2-024", ["section 101A (ntaa2025)"]),
]


def run_cases(entry) -> list[dict]:
    data = json.loads(entry[1].read_text(encoding="utf-8"))
    if len(entry) == 2:
        return data["cases"]
    return next(r["cases"] for r in data["runs"] if r["repeat"] == entry[2])


def replay(store: FileCorpusStore | None = None) -> tuple[list[dict], list[tuple[str, str, list[str], str]]]:
    """(per-run counts, removals as (run, case id, unconnected references, gap text))."""
    store = store or FileCorpusStore()
    other_acts = references.load_other_acts()
    counts, removals = [], []
    for entry in RUNS:
        name = entry[0]
        kept = naming = removed = 0
        for case in run_cases(entry):
            passages = list(store.chunks([p["chunk_id"] for p in case.get("passages") or []]).values())
            for gap in case.get("gaps") or []:
                kept += 1
                if references.parse_references(gap, other_acts):
                    naming += 1
                bad = unconnected_references(gap, passages, other_acts)
                if bad:
                    removed += 1
                    removals.append((name, case["id"], bad, gap))
        counts.append({"run": name, "kept_gaps": kept, "naming_a_reference": naming, "removed": removed})
    return counts, removals


def main() -> int:
    counts, removals = replay()
    print("| Run | Kept gaps | Naming a section or Schedule | Removed |")
    print("|---|---|---|---|")
    for c in counts:
        print(f"| {c['run']} | {c['kept_gaps']} | {c['naming_a_reference']} | {c['removed']} |")
    print(f"| All | {sum(c['kept_gaps'] for c in counts)} | {sum(c['naming_a_reference'] for c in counts)} | "
          f"{sum(c['removed'] for c in counts)} |\n")
    for run, case_id, bad, gap in removals:
        print(f"- {run} {case_id}: {', '.join(bad)}: {gap}")
    got = [(run, case_id, bad) for run, case_id, bad, _ in removals]
    ok = got == EXPECTED
    print(f"\n{'MATCH' if ok else 'DIFFERENT'}: {len(got)} removals against the {len(EXPECTED)} in design 3.2")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
