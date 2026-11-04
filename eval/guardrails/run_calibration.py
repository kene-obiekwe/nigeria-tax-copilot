"""Verifier calibration runner (D-36; guardrails design, section 3.8).

    .venv\\Scripts\\python eval\\guardrails\\run_calibration.py --run 1 [--passes 2] [--effort medium]

Runs every claim in the frozen calibration set (eval/guardrails/verifier_calibration_v1.jsonl)
through the verifier exactly as the generation flow does, except that the LLM call is always made,
even when the rule check fails, so the rule check and the LLM check are measured separately.
Writes eval/guardrails/results/calibration_run<N>.json with every verdict and the measures of
design section 3.8, and never overwrites a run.

Guards, checked before any call:

- the calibration file matches CALIBRATION_SHA256 (frozen before the first verifier run);
- the overseer's mutation review exists (verifier_calibration_v1_review.json), names the same
  sha256, and marks every mutation it does not exclude as "pass". Rows it excludes are skipped.
  The calibration file itself is never edited.

Chunk text comes from the committed chunk files (the same text the database holds). No test-split
question is used (D-29): the set holds Phase 5 claims (10 dev questions, G01 to G05) only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from generation.generate import FileCorpusStore  # noqa: E402
from generation.llm import VERIFIER_EFFORT, VERIFIER_MAX_TOKENS, VERIFIER_MODEL, AnthropicLLMClient  # noqa: E402
from generation.prompt import VERIFIER_PROMPT_VERSION, VERIFIER_SCHEMA_VERSION, VERIFIER_SYSTEM_PROMPT  # noqa: E402
from generation.verify import NORMALISER_VERSION, check_claim  # noqa: E402

CALIBRATION = HERE / "verifier_calibration_v1.jsonl"
CALIBRATION_SHA256 = "7b929677724a9b6df6505bc986d3d67ac05bf60eeb9f3a03c6539b3bc391ec37"  # rebuilt 2026-11-03 after the overseer review
REVIEW = HERE / "verifier_calibration_v1_review.json"
RESULTS_DIR = HERE / "results"
MUTATION_TYPES = ("figure", "hardening", "bridging", "subject_swap", "negation", "citation_move")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class GuardError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_calibration(path: Path = CALIBRATION, expected_sha256: str = CALIBRATION_SHA256,
                     review_path: Path = REVIEW) -> list[dict]:
    """The calibration rows to run, after both guards. Excluded rows are dropped."""
    if not path.exists() or sha256_file(path) != expected_sha256:
        raise GuardError(f"{path.name} does not match its frozen sha256")
    if not review_path.exists():
        raise GuardError(f"{review_path.name} is missing: the overseer reviews every mutation first")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("sha256") != expected_sha256 or not review.get("reviewed_by") or not review.get("on"):
        raise GuardError(f"{review_path.name} does not record a review of this file")
    excluded = set(review.get("excluded", []))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = review.get("mutations", {})
    unreviewed = sorted(row["id"] for row in rows if row["kind"] == "mutation" and row["id"] not in excluded
                        and results.get(row["id"]) != "pass")
    if unreviewed:
        raise GuardError(f"{len(unreviewed)} mutation(s) not yet passed by the overseer: {', '.join(unreviewed)}")
    return [row for row in rows if row["id"] not in excluded]


def measures(rows: list[dict], verdicts: dict[str, str]) -> dict:
    """Design section 3.8 measures for one pass. `verdicts` maps row id to final verdict."""
    mutated = [r for r in rows if r["kind"] == "mutation"]
    real_unsupported = [r for r in rows if r["kind"] == "real" and r["human_verdict"] == "unsupported"]
    real_supported = [r for r in rows if r["kind"] == "real" and r["human_verdict"] == "supported"]
    real_not_claim = [r for r in rows if r["kind"] == "real" and r["human_verdict"] == "not_a_claim"]
    caught = [r for r in mutated if verdicts[r["id"]] != "supported"]
    per_type = {t: {"n": sum(r["mutation_type"] == t for r in mutated),
                    "caught": sum(r["mutation_type"] == t for r in caught)} for t in MUTATION_TYPES}
    false_removals = [r["id"] for r in real_supported if verdicts[r["id"]] != "supported"]
    return {
        "mutations": {"n": len(mutated), "caught": len(caught), "per_type": per_type,
                      "missed": [r["id"] for r in mutated if verdicts[r["id"]] == "supported"]},
        "real_unsupported": {"n": len(real_unsupported),
                             "caught": sum(verdicts[r["id"]] != "supported" for r in real_unsupported)},
        "real_not_a_claim": {"n": len(real_not_claim),
                             "as_not_a_claim": sum(verdicts[r["id"]] == "not_a_claim" for r in real_not_claim)},
        "false_removals": {"n_supported": len(real_supported), "removed": len(false_removals),
                           "ids": false_removals},
        "targets_met": {
            "mutations_95pct": len(caught) >= 57 and all(v["caught"] >= 9 for v in per_type.values()),
            "real_unsupported_both": all(verdicts[r["id"]] != "supported" for r in real_unsupported),
            "false_removals_max_5pct": len(false_removals) <= 0.05 * len(real_supported),
        },
    }


def run(run_no: int, passes: int, effort: str, workers: int) -> dict:
    out_path = RESULTS_DIR / f"calibration_run{run_no}.json"
    if out_path.exists():
        raise SystemExit(f"{out_path.name} already exists; use the next run number")
    rows = load_calibration()
    store = FileCorpusStore()
    verifier = AnthropicLLMClient(model=VERIFIER_MODEL)

    def one(row):
        chunks = store.chunks([chunk_id for _, chunk_id in row["cited"]])
        cited = [(label, chunks[chunk_id]) for label, chunk_id in row["cited"]]
        return check_claim(0, row["text"], cited, verifier, effort=effort, max_tokens=VERIFIER_MAX_TOKENS,
                           always_call=True)

    passes_out = []
    for p in range(1, passes + 1):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            checks = list(pool.map(one, rows))
        verdicts = {row["id"]: c.verdict for row, c in zip(rows, checks)}
        usage = {k: sum(c.usage.get(k, 0) for c in checks) for k in USAGE_KEYS}
        passes_out.append({
            "pass": p, "measures": measures(rows, verdicts), "usage": usage,
            "cost_usd": round(sum(c.cost_usd for c in checks), 6),
            "latency_ms": {"median": sorted(c.latency_ms for c in checks)[len(checks) // 2],
                           "max": max(c.latency_ms for c in checks)},
            "verdict_counts": dict(Counter(verdicts.values())),
            "rows": [{"id": row["id"], "verdict": c.verdict, "reason": c.reason, "sub_reason": c.sub_reason,
                      "llm_verdict": c.llm_verdict, "reasoning": c.reasoning, "second": c.second,
                      "rule": c.rule.as_log(), "unsupported_part": c.unsupported_part,
                      "evidence": c.evidence, "citations": c.citations, "error": c.error,
                      "usage": c.usage, "cost_usd": round(c.cost_usd, 6), "latency_ms": c.latency_ms}
                     for row, c in zip(rows, checks)],
        })
        m = passes_out[-1]["measures"]
        print(f"pass {p}: mutations caught {m['mutations']['caught']}/{m['mutations']['n']}, "
              f"real unsupported caught {m['real_unsupported']['caught']}/{m['real_unsupported']['n']}, "
              f"false removals {m['false_removals']['removed']}/{m['false_removals']['n_supported']}, "
              f"${passes_out[-1]['cost_usd']:.4f}")
    stability = None
    if passes > 1:
        first = {r["id"]: r["verdict"] for r in passes_out[0]["rows"]}
        changed = sorted({r["id"] for later in passes_out[1:] for r in later["rows"] if r["verdict"] != first[r["id"]]})
        stability = {"changed": len(changed), "n": len(rows), "share": round(len(changed) / len(rows), 4),
                     "ids": changed}
    record = {"run": run_no, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "calibration_file": CALIBRATION.relative_to(ROOT).as_posix(), "calibration_sha256": CALIBRATION_SHA256,
              "verifier_model": verifier.model, "effort": effort, "normaliser": NORMALISER_VERSION,
              "prompt_version": VERIFIER_PROMPT_VERSION, "schema_version": VERIFIER_SCHEMA_VERSION,
              "system_prompt_sha256": hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
              "rows_run": len(rows), "passes": passes_out, "stability": stability}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=int, required=True, help="run number (never overwritten)")
    parser.add_argument("--passes", type=int, default=1, help="passes over the set (2 measures stability)")
    parser.add_argument("--effort", default=VERIFIER_EFFORT, choices=("low", "medium", "high"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        run(args.run, args.passes, args.effort, args.workers)
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
