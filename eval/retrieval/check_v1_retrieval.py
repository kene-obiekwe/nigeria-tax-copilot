"""v1 retrieval check for the D-54 revision (G2 fix design, sections 1, 2.2 and 2.5, item 4).

    .venv\\Scripts\\python eval\\retrieval\\check_v1_retrieval.py

Runs the D-21 configuration (`config_final.json`, 5 passages) and the D-54 configuration
(`config_v2.json`, 6 passages) over the in-scope adversarial_v1 cases and G03, G05, and reports,
for each, whether a gold unit is among the passages shown. Pass for `config_v2.json`: all five needed
units shown, and 34 of 34 in-scope cases with a gold unit shown.

**Cases (34).** The 16 controls, the 8 A8 cases, ADV-005, ADV-006, ADV-008, ADV-041, the 4 A7 cases
expected to be answered, plus G03 and G05. Both parts of adversarial_v1 are read only through
`run_adversarial.load_adversarial` (D-52: the spent held-out part is tuning data).

**Gold units.** For a case that passed, the chunks its supported, kept claims cited in tuning runs 1
to 3 and the three held-out repeats (eval/guardrails/results/). For the six cases with no such claim
or with a provision that was not retrieved, the provision named in the G2 review or the case's
rationale (`NAMED`). The five cases in `NEEDED` must have every listed unit shown.

Also reported: on how many of the 96 v1 cases the first five passages differ from D-21. Prints ids,
ranks and chunk ids only, never question text. Query vectors come from the git-ignored query cache;
a question not yet cached costs one embedding call. Writes nothing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402
from retrieval.smoke_query import matches  # noqa: E402


def _load(name: str, path: Path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


run_eval = _load("run_eval", HERE / "run_eval.py")
run_exit_check = _load("run_exit_check", ROOT / "eval" / "generation" / "run_exit_check.py")
run_adversarial = _load("run_adversarial", ROOT / "eval" / "guardrails" / "run_adversarial.py")

CONFIGS = {"D-21": (HERE / "config_final.json", 5), "D-54": (HERE / "config_v2.json", 6)}
RESULT_FILES = [ROOT / "eval" / "guardrails" / "results" / f"tune_run{n}.json" for n in (1, 2, 3)]
HOLDOUT_FILE = ROOT / "eval" / "guardrails" / "results" / "holdout_final.json"
EXTRA_IN_SCOPE = ("ADV-005", "ADV-006", "ADV-008", "ADV-041")
# Provisions named in the G2 review (g2_results.md) or the case's rationale, for cases with no
# supported claim or whose answering provision was not retrieved (G2 fix design, section 1).
NAMED = {
    "ADV-005": ["nta2025:s56"],
    "ADV-050": ["nta2025:s148"],
    "ADV-064": ["ntaa2025:s51(1)-(7)"],
    "ADV-081": ["nta2025:s196"],
    "ADV-082": ["nta2025:s148", "nta2025:s196"],
    "ADV-086": ["ntaa2025:s3(1)"],
}
NEEDED = {
    "ADV-081": ["nta2025:s196"],
    "ADV-082": ["nta2025:s148", "nta2025:s196"],
    "ADV-086": ["ntaa2025:s3(1)"],
    "ADV-064": ["ntaa2025:s51(1)-(7)"],
    "ADV-090": ["nta2025:s203"],
}
EXPECTED_CASES = 34


def in_scope_cases() -> tuple[list[dict], list[dict]]:
    """(the 34 in-scope cases, all 96 v1 cases), with id, source and question."""
    rows = run_adversarial.load_adversarial("v1", "tune") + run_adversarial.load_adversarial("v1", "holdout")
    every = [{"id": r["id"], "question": r["question"], "source": "v1"} for r in rows]
    chosen = [{"id": r["id"], "question": r["question"], "source": "v1"} for r in rows
              if r["category"] in ("C", "A8") or r["id"] in EXTRA_IN_SCOPE
              or (r["category"] == "A7" and r["expected"] == "answer_supported")]
    chosen += [{"id": q["id"], "question": q["question"], "source": "exit_check"}
               for q in run_exit_check.load_exit_questions() if q["id"] in run_adversarial.IN_SCOPE_EXIT]
    return chosen, every


def gold_units() -> dict[str, set[str]]:
    """Chunks cited by supported, kept claims in the saved tuning and held-out results, plus NAMED."""
    gold: dict[str, set[str]] = {}
    case_lists = [json.loads(p.read_text(encoding="utf-8"))["cases"] for p in RESULT_FILES]
    case_lists += [run["cases"] for run in json.loads(HOLDOUT_FILE.read_text(encoding="utf-8"))["runs"]]
    for cases in case_lists:
        for case in cases:
            for claim in (case["verification"] or {}).get("claims", []):
                if claim["verdict"] == "supported" and claim["action"] == "kept":
                    gold.setdefault(case["id"], set()).update(claim["kept_chunk_ids"])
    for case_id, units in NAMED.items():
        gold.setdefault(case_id, set()).update(units)
    return gold


def shown(ids: list[str], unit: str) -> int | None:
    return next((rank for rank, chunk_id in enumerate(ids, start=1) if matches(chunk_id, unit)), None)


def main() -> int:
    cases, every = in_scope_cases()
    gold = gold_units()
    if len(cases) != EXPECTED_CASES:
        print(f"expected {EXPECTED_CASES} in-scope cases, found {len(cases)}", file=sys.stderr)
        return 2
    missing_gold = [c["id"] for c in cases if not gold.get(c["id"])]
    if missing_gold:
        print(f"no gold unit for {missing_gold}", file=sys.stderr)
        return 2
    model = json.loads(CONFIGS["D-21"][0].read_text(encoding="utf-8"))["model"]
    cache = run_eval.QueryCache(model)
    lists: dict[str, dict[str, list[str]]] = {}
    with connect() as conn:
        for name, (path, n) in CONFIGS.items():
            retriever = Retriever(conn, json.loads(path.read_text(encoding="utf-8")))
            lists[name] = {c["id"]: [r.chunk_id for r in retriever.retrieve(c["question"], 5,
                                                                               query_vector=cache.embed(c["question"]))][:n]
                           for c in {c["id"]: c for c in cases + every}.values()}

    print(f"{'case':<9} {'D-21 (5)':<9} {'D-54 (6)':<9} gold")
    hits = {name: 0 for name in CONFIGS}
    for c in cases:
        cells = []
        for name in CONFIGS:
            ranks = [r for r in (shown(lists[name][c["id"]], u) for u in sorted(gold[c["id"]])) if r]
            cells.append(f"P{min(ranks)}" if ranks else "miss")
            hits[name] += bool(ranks)
        print(f"{c['id']:<9} {cells[0]:<9} {cells[1]:<9} {', '.join(sorted(gold[c['id']]))}")
    print()
    for name in CONFIGS:
        print(f"{name}: {hits[name]} of {len(cases)} in-scope cases with a gold unit shown")
    print()
    needed_ok = True
    for case_id, units in NEEDED.items():
        cells = []
        for name in CONFIGS:
            ranks = [shown(lists[name][case_id], u) for u in units]
            cells.append(" ".join(f"{u.split(':', 1)[1]}@{'P' + str(r) if r else 'miss'}" for u, r in zip(units, ranks)))
            if name == "D-54":
                needed_ok &= all(ranks)
        print(f"needed {case_id}: D-21 {cells[0]}; D-54 {cells[1]}")
    changed = [c["id"] for c in every if lists["D-21"][c["id"]][:5] != lists["D-54"][c["id"]][:5]]
    print(f"\nfirst five changed on {len(changed)} of {len(every)} v1 cases: {', '.join(changed) or 'none'}")
    print(f"query embeddings: {cache.hits} from cache, {cache.misses} embedded, {cache.tokens_used} tokens")
    passed = needed_ok and hits["D-54"] == len(cases)
    print(f"\nPASS: all five needed units shown and {hits['D-54']} of {len(cases)}" if passed else
          f"\nFAIL: needed units shown {needed_ok}; {hits['D-54']} of {len(cases)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
