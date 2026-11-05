"""Offline evidence re-check for norm/5 (D-55; G2 fix design, sections 3.2 and 3.4).

    .venv\\Scripts\\python eval\\guardrails\\recheck_evidence.py

Re-checks every stored evidence quote against the chunk it names, under `norm/4` (D-48, pinned
below as it was) and under the live normaliser (`norm/5`), with no API call. A final verdict changes
where the claim's LLM verdict was `supported`, its rule check passed, no quote was found under
`norm/4`, and a quote is found under `norm/5`.

Runs: calibration runs 7 and 8 (both passes; quotes in eval/guardrails/results/), tuning runs 1 to 3
and the three held-out repeats of G2 decision run 1 (quotes in the local, git-ignored gen-log,
logs/generation/, found by each case's record id). Chunk text and context headers come from the
committed chunk file. Prints ids and counts; quotes are printed only for verdicts that change.

Pass (section 3.4): exactly three final verdicts change (Q041 claim 2 in held-out repeats 2 and 3,
ADV-087 claim 1 in repeat 3), and no quote found under `norm/4` is lost.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from generation.generate import FileCorpusStore  # noqa: E402
from generation.verify import MIN_QUOTE_CHARS, NORMALISER_VERSION, _clean, evidence_found  # noqa: E402

RESULTS = HERE / "results"
CALIBRATION = HERE / "verifier_calibration_v1.jsonl"
LOG_DIR = ROOT / "logs" / "generation"
EXPECTED_CHANGES = {("held-out repeat 2", "Q041", 2), ("held-out repeat 3", "Q041", 2),
                    ("held-out repeat 3", "ADV-087", 1)}


def found_norm4(quote: str, text: str) -> bool:
    """The norm/4 evidence check (D-48), as it was before D-55: chunk text only."""
    def form(s: str) -> str:
        return re.sub(r"\s+", " ", _clean(s)).strip().casefold()
    q = form(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in form(text)


def calibration_claims(run_no: int):
    """(run label, claim id, claim log, {label: chunk id}) for both passes of a calibration run."""
    cited = {}
    for line in CALIBRATION.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            cited[row["id"]] = dict(row["cited"])
    record = json.loads((RESULTS / f"calibration_run{run_no}.json").read_text(encoding="utf-8"))
    for p in record["passes"]:
        for row in p["rows"]:
            yield f"calibration run {run_no}, pass {p['pass']}", (row["id"], None), row, cited[row["id"]]


def load_gen_log() -> dict[str, dict]:
    records = {}
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                records[record["record_id"]] = record
    return records


def logged_claims(label: str, cases: list[dict], log: dict[str, dict]):
    for case in cases:
        if case.get("record_id") is None or case.get("verification") is None:
            continue
        record = log.get(case["record_id"])
        if record is None:
            raise SystemExit(f"{label}: gen-log record for {case['id']} not found in {LOG_DIR}")
        for claim in (record["verification"] or {}).get("claims", []):
            yield label, (case["id"], claim["no"]), claim, dict(zip(claim["labels"], claim["chunk_ids"]))


def all_claims():
    for n in (7, 8):
        yield from calibration_claims(n)
    log = load_gen_log()
    for n in (1, 2, 3):
        record = json.loads((RESULTS / f"tune_run{n}.json").read_text(encoding="utf-8"))
        yield from logged_claims(f"tune run {n}", record["cases"], log)
    holdout = json.loads((RESULTS / "holdout_final.json").read_text(encoding="utf-8"))
    for run in holdout["runs"]:
        yield from logged_claims(f"held-out repeat {run['repeat']}", run["cases"], log)


def main() -> int:
    store = FileCorpusStore()
    chunks = store.chunks(list(store._chunks))
    table: dict[str, dict] = {}
    changes, lost = [], []
    for run, (case_id, no), claim, cited in all_claims():
        t = table.setdefault(run, {"quotes": 0, "not_found_norm4": 0, "new_dash": 0, "new_header": 0, "lost": 0})
        any4 = any5 = False
        quotes5 = []
        for e in claim.get("evidence") or []:
            t["quotes"] += 1
            chunk = chunks.get(cited.get(e["label"], ""))
            f4 = chunk is not None and found_norm4(e["quote"], chunk["text"])
            f5_text = chunk is not None and evidence_found(e["quote"], chunk["text"])
            f5 = chunk is not None and evidence_found(e["quote"], chunk["text"], chunk["context_header"])
            any4 |= f4
            any5 |= f5
            if not f4:
                t["not_found_norm4"] += 1
                if f5_text:
                    t["new_dash"] += 1
                elif f5:
                    t["new_header"] += 1
            elif not f5:
                t["lost"] += 1
                lost.append((run, case_id, no, e["label"]))
            if f5 and not f4:
                quotes5.append(e["quote"])
        rule_passed = (claim.get("rule") or {}).get("passed", False)
        if claim.get("llm_verdict") == "supported" and rule_passed and not any4 and any5:
            before = claim["verdict"] + (f"/{claim['sub_reason']}" if claim.get("sub_reason") else "")
            changes.append((run, case_id, no, before, quotes5[0]))

    print(f"Offline evidence re-check, norm/4 against {NORMALISER_VERSION}\n")
    print(f"| Run | Quotes | Not found under norm/4 | Newly found: dash rule | Newly found: header | Lost |")
    print("|---|---|---|---|---|---|")
    for run, t in table.items():
        print(f"| {run} | {t['quotes']} | {t['not_found_norm4']} | {t['new_dash']} | {t['new_header']} | {t['lost']} |")
    print("\nFinal verdict changes:")
    for run, case_id, no, before, quote in changes:
        label = f"{case_id} claim {no}" if no is not None else case_id
        print(f"- {run}: {label}: {before} -> supported; quote {quote[:90]!r}")
    if not changes:
        print("- none")
    got = {(run, case_id, no) for run, case_id, no, _, _ in changes}
    passed = got == EXPECTED_CHANGES and not lost
    print(f"\nquotes lost: {len(lost)}")
    print(f"{'PASS' if passed else 'FAIL'}: {len(changes)} verdict changes "
          f"({'as expected' if got == EXPECTED_CHANGES else 'expected ' + str(sorted(EXPECTED_CHANGES))}), "
          f"{len(lost)} quotes lost")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
