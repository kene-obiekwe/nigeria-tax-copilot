"""Phase 7 regression sample (D-116, D-124; tools design, Section 8). A check, not a gate.

    .venv\\Scripts\\python eval\\tools\\run_regression_sample.py --status      # free
    .venv\\Scripts\\python eval\\tools\\run_regression_sample.py --run         # PAID, once, cap $0.30

Runs the 12 named cases once with tools on, and compares each with tuning run 17
(eval/guardrails/results/tune_run17.json): status and refusal reason, kept and removed claim counts,
notes shown, whether a tool was called and which, and cost. The configuration is the live one
(`config_p7.json` is not frozen; this runner never freezes it): the record holds it, with the sha256 of
the code and data files used.

**Cost stop.** The run stops before a case when the spend so far plus the dearest case so far (at least
$0.045) would pass $0.30. **API errors (D-128).** The generation, verifier and tagger clients share one
stop: the first API error stops the run after that case, and no later call reaches the API. The record
is then void (`stopped_on_api_error`, review "void", no comparison) and the runner exits with code 3. A
void record may be re-run once, `--run --rerun-after-void`, into `regression_sample_rerun.json`. The
result files are written once (mode "x").

Questions: the six dev questions come from the dev split only (the retrieval test split is never
opened, D-29); the six adversarial ids are tuning data (D-115).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "eval" / "guardrails"))
sys.path.insert(0, str(ROOT / "eval" / "retrieval"))

import run_adversarial  # noqa: E402
import run_eval  # noqa: E402
from generation import generate as gen  # noqa: E402
from tools import taxcalc  # noqa: E402

_spec = importlib.util.spec_from_file_location("tools_run_exit_check", HERE / "run_exit_check.py")
rec = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("tools_run_exit_check", rec)
_spec.loader.exec_module(rec)

BASELINE = ROOT / "eval" / "guardrails" / "results" / "tune_run17.json"
RESULTS_DIR = HERE / "results"
RESULT = RESULTS_DIR / "regression_sample.json"
RERUN_RESULT = RESULTS_DIR / "regression_sample_rerun.json"
DECISION = "D-124"
COST_CAP_USD = 0.30
MIN_CASE_ESTIMATE_USD = 0.045
DEV_IDS = ["Q003", "Q014", "Q025", "Q034", "Q043", "Q047"]
ADV_IDS = ["ADV-017", "ADV-019", "ADV-001", "ADV-009", "ADV-025", "ADV-048"]  # 2 declines, 4 pre-check refusals
CASE_IDS = DEV_IDS + ADV_IDS
CODE_FILES = ["python/generation/generate.py", "python/generation/prompt.py", "python/generation/guardrails.py",
              "python/generation/verify.py", "python/generation/llm.py", "python/generation/note_tagger.py",
              "python/generation/disclosure.py", "python/tools/taxcalc.py", "python/tools/lookup.py",
              "data/curated/calc_rates.yml", "data/curated/heading_corrections.yml",
              "eval/guardrails/config_g2_v4.json", "eval/tools/run_regression_sample.py"]


class GuardError(RuntimeError):
    """A run the rules forbid. Raised before any model call."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes() -> dict:
    out = {p: sha256_file(ROOT / p) for p in CODE_FILES}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                                    check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        head, dirty = None, None
    return {"git_head": head, "tree_dirty": dirty, "files": out}


def load_cases() -> list[dict]:
    """The 12 cases in the stated order. Dev text from the dev split only; the adversarial rows from
    adversarial v1 (tune and spent held-out parts)."""
    dev = {q["id"]: q for q in run_eval.load_questions("dev")}
    adv = {r["id"]: r for part in ("tune", "holdout") for r in run_adversarial.load_adversarial("v1", part)}
    cases = []
    for cid in CASE_IDS:
        if cid in dev:
            cases.append({"id": cid, "source": "dev", "question": dev[cid]["question"], "expected": "answer_supported"})
        elif cid in adv:
            cases.append(dict(adv[cid], source="adversarial"))
        else:
            raise GuardError(f"case {cid} not found")
    return cases


def load_baseline(path: Path = BASELINE) -> dict[str, dict]:
    rows = {c["id"]: c for c in json.loads(path.read_text(encoding="utf-8"))["cases"]}
    missing = [i for i in CASE_IDS if i not in rows]
    if missing:
        raise GuardError(f"tuning run 17 has no row for {missing}")
    return rows


def kept_removed(verification) -> tuple[int, int]:
    """Kept and removed claim counts from a baseline `verification` block or a live VerificationResult."""
    if verification is None:
        return 0, 0
    summary = verification["summary"] if isinstance(verification, dict) else verification.summary
    return summary["claims_kept"], summary["claims_removed"]


def tool_calls(answer) -> list[str]:
    return [u["name"] + (f":{(u['input'] or {}).get('scenario')}" if u["name"] == "calculate_liability" else "")
            for u in (answer.tools or {}).get("tool_uses", [])]


def compare(base: dict, now: dict) -> dict:
    """Per-case comparison of a baseline row (tuning run 17) with this run's row."""
    base_kept, base_removed = kept_removed(base["verification"])
    base_notes, now_notes = base["notes_shown"], now["notes_shown"]
    out = {
        "status": {"run17": base["status"], "now": now["status"]},
        "refusal_reason": {"run17": base["refusal_reason"], "now": now["refusal_reason"]},
        "claims_kept": {"run17": base_kept, "now": now["claims_kept"]},
        "claims_removed": {"run17": base_removed, "now": now["claims_removed"]},
        "notes_shown": {"run17": base_notes, "now": now_notes,
                        "added": [n for n in now_notes if n not in base_notes],
                        "dropped": [n for n in base_notes if n not in now_notes]},
        "tool_called": {"run17": False, "now": bool(now["tool_calls"]), "which": now["tool_calls"]},
        "cost_usd": {"run17": base["cost_usd"], "now": now["cost_usd"],
                     "delta": round(now["cost_usd"] - base["cost_usd"], 6)},
    }
    out["differs"] = [k for k in ("status", "refusal_reason", "claims_kept", "claims_removed")
                      if out[k]["run17"] != out[k]["now"]]
    if out["notes_shown"]["added"] or out["notes_shown"]["dropped"]:
        out["differs"].append("notes_shown")
    if now["tool_calls"]:
        out["differs"].append("tool_called")
    return out


def live_row(case: dict, answer) -> dict:
    row = run_adversarial.answer_row(case, answer)
    kept, removed = kept_removed(answer.verification)
    row |= {"claims_kept": kept, "claims_removed": removed, "tool_calls": tool_calls(answer),
            "computations": answer.computations,
            "tool_uses": [{k: u[k] for k in ("turn", "name", "input", "is_error", "limit_hit", "calculation_id")}
                          for u in (answer.tools or {}).get("tool_uses", [])]}
    return row


def projected_over_cap(spent: float, costs: list[float], cap: float = COST_CAP_USD) -> bool:
    return spent + max([MIN_CASE_ESTIMATE_USD, *costs]) > cap


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def check_run_allowed(*, rerun: bool = False, results_dir: Path | None = None) -> None:
    first = (results_dir or RESULTS_DIR) / RESULT.name
    second = (results_dir or RESULTS_DIR) / RERUN_RESULT.name
    if rerun:
        if second.exists():
            raise GuardError("the one re-run after a void run has already been made")
        if not rec.is_void_record(rec.read_record(first)):
            raise GuardError("the first record is not a complete void record, so it cannot be re-run (D-128)")
    elif first.exists():
        raise GuardError("the regression sample has already been run; it is never repeated "
                         "(after a void run, use --rerun-after-void once)")


def default_answer(question: str, config, clients: dict):
    return gen.generate(question, config=config, llm=clients["llm"], verifier=clients["verifier"],
                        tagger=clients["tagger"])


def run(*, rerun: bool = False, clients: dict | None = None, answer_fn=None, store=None,
        results_dir: Path | None = None, baseline: dict | None = None, cases: list[dict] | None = None,
        live_config: dict | None = None) -> dict:
    """The paid run (tests pass fake clients, `answer_fn`, `store`, a results folder and the rest)."""
    results_dir = results_dir or RESULTS_DIR
    check_run_allowed(rerun=rerun, results_dir=results_dir)
    cases = cases if cases is not None else load_cases()
    baseline = baseline if baseline is not None else load_baseline()
    live_config = live_config if live_config is not None else rec.current_config()
    rates = taxcalc.load_rates()
    config = gen.GenerationConfig(tools=gen.ToolsConfig(enabled=True, rates=rates))  # config_p7.json is not frozen
    stop = run_adversarial.APIStop()
    raw = clients if clients is not None else rec.default_clients()
    wrapped = {name: run_adversarial.StopOnAPIError(client, stop) for name, client in raw.items()}
    answer_fn = answer_fn or default_answer
    store = store or gen.FileCorpusStore()
    started = datetime.now(timezone.utc)
    rows, costs, spent, cost_stop, api_stop = [], [], 0.0, None, None
    for case in cases:
        if projected_over_cap(spent, costs):
            cost_stop = {"before": case["id"], "spent_usd": round(spent, 6), "cap_usd": COST_CAP_USD}
            break
        answer = answer_fn(case["question"], config, wrapped)
        row = live_row(case, answer)
        row["comparison"] = compare(baseline[case["id"]], row)
        rows.append(row)
        costs.append(answer.total_cost_usd)
        spent += answer.total_cost_usd
        print(f"{case['id']}: {answer.status}" + (f" ({answer.refusal_reason})" if answer.refusal_reason else "")
              + f", tools {row['tool_calls'] or 'none'}, ${answer.total_cost_usd:.4f}"
              + (f"; STOPPED at API error: {stop.error}" if stop.stopped else ""))
        if stop.stopped:
            api_stop = {"case": case["id"], "cases_written": len(rows), "cases_planned": len(cases),
                        "error": stop.error.as_log()}
            break
    result = {"decision": DECISION, "rerun_after_void": rerun, "check_not_gate": True,
              "baseline": {"file": BASELINE.relative_to(ROOT).as_posix(),
                           "sha256": sha256_file(BASELINE) if BASELINE.exists() else None},
              "live_config": live_config, "config_p7_frozen": rec.CONFIGS[1].exists(),
              "code": code_hashes(), "case_ids": CASE_IDS,
              "started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
              "cost_usd": round(spent, 6), "cap_usd": COST_CAP_USD}
    if api_stop is not None:  # void (D-128): no comparison
        for r in rows:
            r.pop("comparison")
        result |= {"review": "void", "stopped_on_api_error": api_stop, "stopped_on_cost": cost_stop, "cases": rows}
    else:
        result |= {"review": "pending", "stopped_on_api_error": None, "stopped_on_cost": cost_stop, "cases": rows,
                   "differences": {r["id"]: r["comparison"]["differs"] for r in rows if r["comparison"]["differs"]}}
    write_record(results_dir / (RERUN_RESULT.name if rerun else RESULT.name), result)
    return result


def main(argv=None, *, run_fn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="free: report whether the sample may run")
    group.add_argument("--run", action="store_true", help="PAID: run the 12 cases once")
    ap.add_argument("--rerun-after-void", action="store_true", help="PAID: with --run, the one re-run after a void run")
    args = ap.parse_args(argv)
    if args.rerun_after_void and not args.run:
        ap.error("--rerun-after-void goes with --run only")
    try:
        if args.status:
            for rerun in (False, True):
                try:
                    check_run_allowed(rerun=rerun)
                    print(f"{'rerun' if rerun else 'run'}: allowed")
                except GuardError as exc:
                    print(f"{'rerun' if rerun else 'run'}: refused ({exc})")
            return 0
        result = (run_fn or run)(rerun=args.rerun_after_void)
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if stop := result["stopped_on_api_error"]:
        error = stop["error"]
        print(f"STOPPED at an API error during {stop['case']} ({stop['cases_written']} of {stop['cases_planned']} "
              f"cases written): {error['error_type']} {error['status_code']} {error['api_error_type']}: "
              f"{error['message']}. The record is void (D-128); one re-run is allowed with --rerun-after-void.",
              file=sys.stderr)
        return 3
    print(f"regression sample: {len(result['cases'])} cases, cost ${result['cost_usd']:.4f} of ${COST_CAP_USD:.2f}"
          + (f"; stopped on cost before {result['stopped_on_cost']['before']}" if result["stopped_on_cost"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
