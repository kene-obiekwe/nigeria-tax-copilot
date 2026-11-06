"""Offline gap re-check of the live gap prompt (D-66, D-67).

    .venv\\Scripts\\python eval\\guardrails\\recheck_gaps.py --out gap_recheck_2.json

Re-classifies, with the live gap classifier (Claude Haiku 5.5 at the verifier's effort, and the gap
prompt in python/generation/prompt.py), every gap that was kept (`missing_information` under
`gap-prompt/1`) in tuning runs 2 to 4 and the three held-out repeats of G2 decision run 1. Answers are
not regenerated: each case's kept gaps go through the live gap check with the case's question (one
call per case up to re-check 2; one call per gap from re-check 3, D-68). `calls` counts API calls. Gaps that were
removed under `gap-prompt/1` are not re-checked, since a gap already removed cannot be shown.

The v1 held-out part is tuning data since D-52; no adversarial_v2 file or result is read. Dev
question text is read from the dev split and is not written to the output. The run stops before
the next call once the summed cost passes the cap (default $0.60).

Writes eval/guardrails/results/<--out>, never overwritten (gap_recheck_1.json is the gap-prompt/2
record, gap_recheck_2.json the gap-prompt/3 record), and prints every gap whose
class changes, with its run, case id and text.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from dotenv import load_dotenv  # noqa: E402

from generation.guardrails import load_guardrails_policy  # noqa: E402
from generation.llm import VERIFIER_MODEL, AnthropicLLMClient  # noqa: E402
from generation.prompt import GAP_PROMPT_VERSION, GAP_SYSTEM_PROMPT  # noqa: E402
from generation.verify import check_gaps  # noqa: E402

spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
sys.modules.setdefault("run_eval", run_eval)
spec.loader.exec_module(run_eval)

RESULTS = HERE / "results"
TUNE_RUNS = (2, 3, 4)


def runs():
    """(run label, case rows) for tuning runs 2 to 4 and held-out repeats 1 to 3."""
    for n in TUNE_RUNS:
        yield f"tune run {n}", json.loads((RESULTS / f"tune_run{n}.json").read_text(encoding="utf-8"))["cases"]
    holdout = json.loads((RESULTS / "holdout_final.json").read_text(encoding="utf-8"))
    for run in holdout["runs"]:
        yield f"held-out repeat {run['repeat']}", run["cases"]


def kept_gaps(row: dict) -> list[str]:
    v = row.get("verification")
    return [g["text"] for g in (v or {}).get("gaps") or [] if g["kept"] and g["class"] == "missing_information"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cap", type=float, default=0.60, help="stop once the summed cost passes this (USD)")
    parser.add_argument("--out", required=True, help="record name under eval/guardrails/results/: gap_recheck_<N>.json")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"gap_recheck_\d+\.json", args.out):
        print("refused: --out must be gap_recheck_<N>.json", file=sys.stderr)
        return 2
    OUT = RESULTS / args.out
    if OUT.exists():
        print(f"refused: {OUT.name} already exists", file=sys.stderr)
        return 2
    load_dotenv(ROOT / ".env")
    dev = {q["id"]: q["question"] for q in run_eval.load_questions("dev")}
    patterns = load_guardrails_policy().disputed_gap_patterns
    client = AnthropicLLMClient(model=VERIFIER_MODEL)
    rows, cost, calls, stopped = [], 0.0, 0, False
    for label, cases in runs():
        for case in cases:
            gaps = kept_gaps(case)
            if not gaps:
                continue
            if cost > args.cap:
                stopped = True
                break
            question = dev[case["id"]] if case["source"] == "dev" else case["question"]
            result = check_gaps(question, [(g, "model") for g in gaps], client, patterns)
            calls += (result.request or {}).get("calls", 1)
            cost += result.cost_usd
            for g in result.gaps:
                rows.append({"run": label, "id": case["id"], "text": g.text, "old_class": "missing_information",
                             "new_class": g.gap_class, "reason": g.reason, "error": result.error})
        if stopped:
            break
    flips = [r for r in rows if r["new_class"] != "missing_information"]
    record = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "gap_prompt_version": GAP_PROMPT_VERSION,
              "gap_prompt_sha256": hashlib.sha256(GAP_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
              "model": VERIFIER_MODEL, "calls": calls, "cost_usd": round(cost, 6), "stopped_at_cap": stopped,
              "gaps_checked": len(rows), "gaps_flipped": len(flips), "review": "pending", "gaps": rows}
    with OUT.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    for r in flips:
        print(f"{r['run']} | {r['id']} | {r['new_class'] or r['reason']} | {r['text']}")
    print(f"{len(rows)} kept gaps re-checked in {calls} calls, {len(flips)} flipped, ${cost:.4f}"
          + (" (stopped at the cap)" if stopped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
