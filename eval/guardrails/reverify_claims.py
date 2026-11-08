"""Offline re-verification of saved tuning-run claims under the live verifier (D-90; G2 fix design 2,
section 15).

    .venv\\Scripts\\python eval\\guardrails\\reverify_claims.py --name link_rule --runs 8 10 11
    .venv\\Scripts\\python eval\\guardrails\\reverify_claims.py --name link_rule_adv085 --runs 11 \\
        --case ADV-085 --claim 3 --calls 3

Re-checks the claims that tuning runs already produced, so a verifier prompt change can be measured
without generating new answers. For every case of each named run (eval/guardrails/results/
tune_run<N>.json), the full answer record is read from the git-ignored generation log
(logs/generation/*.jsonl, by record_id): each claim's text, the labels it was shown under and the
chunks it cites, and its saved verdict. Chunk text comes from the committed chunk file. Each claim
then goes through `verify.check_claim`, exactly as `verify_answer` sends it in the generation flow
(the cited passages under their original labels, the rule check, the hint note, the evidence check
and the second call on `contradicted`). The rebuilt user message is compared with the logged one
and the result is recorded per claim.

No generation, gap check, tagger or retrieval call is made. Prints the run, case id, claim number
and both verdicts of every claim, and the claim text only where the verdict changes. Writes
eval/guardrails/results/reverify_<name>.json and never overwrites it.

Stops before the first call if the estimate (the claims' saved verifier cost) is above --max-cost,
and stops between batches if the running cost reaches it. Any failed call stops the run, with no
retry; the record is written with what was done and the error.
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
from generation.prompt import (VERIFIER_PROMPT_VERSION, VERIFIER_SCHEMA_VERSION, VERIFIER_SYSTEM_PROMPT,  # noqa: E402
                               build_verifier_message)
from generation.verify import NORMALISER_VERSION, check_claim, hint_note, rule_check  # noqa: E402

RESULTS = HERE / "results"
LOGS = ROOT / "logs" / "generation"
BATCH = 4


def load_records(record_ids: set[str], log_dir: Path = LOGS) -> dict[str, dict]:
    """The full answer records with these ids, from every generation log file."""
    found: dict[str, dict] = {}
    for path in sorted(log_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("record_id") in record_ids:
                    found[record["record_id"]] = record
    return found


def saved_claims(runs: list[int], case_id: str | None = None, claim_no: int | None = None) -> list[dict]:
    """Every verified claim of the named tuning runs, with what the generation flow sent the verifier."""
    items, wanted = [], []
    for run in runs:
        data = json.loads((RESULTS / f"tune_run{run}.json").read_text(encoding="utf-8"))
        for case in data["cases"]:
            if case.get("verification") and (case_id is None or case["id"] == case_id):
                wanted.append((run, case))
    records = load_records({case["record_id"] for _, case in wanted})
    missing = sorted({f"run {run} {case['id']}" for run, case in wanted if case["record_id"] not in records})
    if missing:
        raise SystemExit(f"generation log records not found: {', '.join(missing)}")
    for run, case in wanted:
        record = records[case["record_id"]]
        for claim in record["verification"]["claims"]:
            if claim_no is not None and claim["no"] != claim_no:
                continue
            items.append({"run": run, "case_id": case["id"], "record_id": case["record_id"], "no": claim["no"],
                          "text": claim["text"], "labels": claim["labels"], "chunk_ids": claim["chunk_ids"],
                          "saved_verdict": claim["verdict"], "saved_reason": claim["reason"],
                          "saved_prompt_version": (claim.get("request") or {}).get("prompt_version"),
                          "saved_cost_usd": claim.get("cost_usd", 0.0),
                          "logged_message": ((claim.get("request") or {}).get("messages") or [{}])[0].get("content")})
    return items


def cited_passages(item: dict, store: FileCorpusStore) -> list[tuple[str, dict]]:
    chunks = store.chunks(item["chunk_ids"])
    return [(label, chunks[chunk_id]) for label, chunk_id in zip(item["labels"], item["chunk_ids"])]


def rebuilt_message(item: dict, cited: list[tuple[str, dict]]) -> str:
    """The user message check_claim sends: cited passages, statement and any hint note."""
    rule = rule_check(item["text"], [chunk for _, chunk in cited])
    return build_verifier_message(cited, item["text"], hint_note(rule.hint) if rule.hint else None)


def run(name: str, runs: list[int], case_id: str | None, claim_no: int | None, calls: int,
        max_cost: float) -> dict:
    out_path = RESULTS / f"reverify_{name}.json"
    if out_path.exists():
        raise SystemExit(f"{out_path.name} already exists; use another name")
    items = saved_claims(runs, case_id, claim_no)
    if not items:
        raise SystemExit("no claims match")
    estimate = sum(item["saved_cost_usd"] for item in items) * calls
    print(f"{len(items)} claim(s) x {calls} call(s); estimate ${estimate:.4f} (saved verifier cost), cap ${max_cost:.2f}")
    if estimate > max_cost:
        raise SystemExit("estimate is above the cap; nothing was sent")
    store = FileCorpusStore()
    verifier = AnthropicLLMClient(model=VERIFIER_MODEL)
    jobs = [(item, call) for item in items for call in range(1, calls + 1)]

    def one(job):
        item, _ = job
        return check_claim(item["no"], item["text"], cited_passages(item, store), verifier,
                           effort=VERIFIER_EFFORT, max_tokens=VERIFIER_MAX_TOKENS)

    rows, spent, stopped = [], 0.0, None
    # Separate calls on one claim run one after another; other claims run BATCH at a time.
    batch = 1 if calls > 1 else BATCH
    with ThreadPoolExecutor(max_workers=batch) as pool:
        for start in range(0, len(jobs), batch):
            if spent >= max_cost:
                stopped = f"cost cap ${max_cost:.2f} reached after {len(rows)} call(s)"
                break
            part = jobs[start:start + batch]
            checks = list(pool.map(one, part))
            for (item, call), check in zip(part, checks):
                cited = cited_passages(item, store)
                message = rebuilt_message(item, cited)
                spent += check.cost_usd
                rows.append({
                    "run": item["run"], "case_id": item["case_id"], "record_id": item["record_id"], "no": item["no"],
                    "call": call, "text": item["text"], "labels": item["labels"], "chunk_ids": item["chunk_ids"],
                    "saved_prompt_version": item["saved_prompt_version"], "saved_verdict": item["saved_verdict"],
                    "saved_reason": item["saved_reason"],
                    "message_matches_log": None if item["logged_message"] is None else message == item["logged_message"],
                    "verdict": check.verdict, "reason": check.reason, "sub_reason": check.sub_reason,
                    "llm_verdict": check.llm_verdict, "reasoning": check.reasoning, "second": check.second,
                    "unsupported_part": check.unsupported_part, "evidence": check.evidence,
                    "rule": check.rule.as_log(), "error": check.error, "usage": check.usage,
                    "cost_usd": round(check.cost_usd, 6), "latency_ms": check.latency_ms})
                changed = check.verdict != item["saved_verdict"]
                print(f"run {item['run']} {item['case_id']} claim {item['no']}"
                      + (f" call {call}" if calls > 1 else "")
                      + f": {item['saved_verdict']} -> {check.verdict}" + (" CHANGED" if changed else ""))
                if changed:
                    print(f"    {item['text']}")
            errors = [c.error for c in checks if c.error]
            if errors:
                stopped = f"call failed: {errors[0]}"
                break

    transitions = Counter(f"{r['saved_verdict']} -> {r['verdict']}" for r in rows)
    lost = [{"run": r["run"], "case_id": r["case_id"], "no": r["no"], "call": r["call"], "verdict": r["verdict"],
             "reason": r["reason"], "sub_reason": r["sub_reason"], "text": r["text"], "chunk_ids": r["chunk_ids"]}
            for r in rows if r["saved_verdict"] == "supported" and r["verdict"] != "supported"]
    gained = [{"run": r["run"], "case_id": r["case_id"], "no": r["no"], "call": r["call"],
               "saved_verdict": r["saved_verdict"], "text": r["text"]}
              for r in rows if r["saved_verdict"] != "supported" and r["verdict"] == "supported"]
    record = {
        "name": name, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": runs, "case": case_id, "claim": claim_no, "calls_per_claim": calls,
        "verifier": {"model": verifier.model, "effort": VERIFIER_EFFORT, "prompt_version": VERIFIER_PROMPT_VERSION,
                     "schema_version": VERIFIER_SCHEMA_VERSION,
                     "system_prompt_sha256": hashlib.sha256(VERIFIER_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
                     "normaliser": NORMALISER_VERSION},
        "chunk_source": "committed chunk file (FileCorpusStore)", "claims": len(items), "calls": len(rows),
        "stopped": stopped, "cost_usd": round(spent, 6),
        "messages_matching_log": sum(r["message_matches_log"] is True for r in rows),
        "messages_differing_from_log": sum(r["message_matches_log"] is False for r in rows),
        "transitions": dict(transitions), "supported_lost": lost, "supported_gained": gained, "rows": rows}
    RESULTS.mkdir(parents=True, exist_ok=True)
    with out_path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{len(rows)} call(s), ${spent:.4f}; transitions {dict(transitions)}; "
          f"supported lost {len(lost)}, gained {len(gained)}; "
          f"messages matching log {record['messages_matching_log']}, differing {record['messages_differing_from_log']}"
          + (f"; STOPPED: {stopped}" if stopped else ""))
    print(f"written {out_path.relative_to(ROOT).as_posix()}")
    return record


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="results/reverify_<name>.json (never overwritten)")
    parser.add_argument("--runs", type=int, nargs="+", required=True, help="tuning run numbers")
    parser.add_argument("--case", help="only this case id")
    parser.add_argument("--claim", type=int, help="only this claim number (with --case)")
    parser.add_argument("--calls", type=int, default=1, help="separate calls per claim")
    parser.add_argument("--max-cost", type=float, default=0.60, help="stop at this spend (USD)")
    args = parser.parse_args(argv)
    if args.claim is not None and args.case is None:
        parser.error("--claim needs --case")
    record = run(args.name, args.runs, args.case, args.claim, args.calls, args.max_cost)
    return 1 if record["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
