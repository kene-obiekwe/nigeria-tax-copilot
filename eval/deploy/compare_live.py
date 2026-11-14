r"""Live rows against their Phase 8 case or Phase 9 record, in three classes (deployment design Section 9.2; free).

    # 1. the five questions to ask on the live URL (seed 20261114) and the two refusal questions, word for word
    .venv\Scripts\python eval\deploy\compare_live.py draw

    # 2. after asking them, compare each live row (the reference shown on the page) with its case
    .venv\Scripts\python eval\deploy\with_hosted.py review eval\deploy\compare_live.py check --commit <sha> ^
        --case Q-aaaaaaaa=Q031 --case Q-bbbbbbbb=Q002 ... --refusal Q-cccccccc=Q-e88f33ea --refusal Q-dddddddd=Q-67c8ea25 ^
        --parity-passed

Run as the reviewer (`with_hosted.py review` sets `REVIEW_DATABASE_URL`). The Phase 9 baseline rows are read from
the local container (the `POSTGRES_*` settings of `.env`). Nothing is printed except field names, chunk ids and
the pass or fail of each check: no question, no answer, no connection string, no password.

The draw (`draw`): the cases of subset T of Phase 8 that were answered in both runs with identical passage lists,
in id order; `random.Random(20261114).sample(ids, 5)`; then the two Phase 9 refusals (Q-e88f33ea, no_such_section;
Q-67c8ea25, foreign_law). Using spent questions here is a check of the deployment, not a measurement.

The three classes, for every live row:

1. **Must match exactly** (zero differences): `config_sha256`, `retrieval_config_sha256`,
   `guardrails_policy_sha256`; the generator, verifier and tagger models; every prompt, schema and tool-definition
   label; the `versions` object; the `corpus` hashes (the load id differs between databases and is left out); the
   pre-check result; for a refusal, the status, reason, stage and `refusal_text`. The baseline of an answered case
   is a local answered row (`--baseline-ref`, default the first Phase 9 live-check row), because the Phase 8
   results hold no such fields; the baseline of a refusal is its own Phase 9 row.
2. **Must match, one stated exception**: passages P1 to P8, chunk ids in label order, equal to the Phase 8 case. A
   difference is accepted only with `--parity-passed` (design Section 9.1 passed for every question, so hosted and
   local agree on the cached vector and a difference can only come from the fresh query embedding); every such
   difference is listed either way.
3. **Must hold, not compared**: status `answered`; every shown claim `supported` with at least one kept
   citation; every cited chunk in the hosted corpus with `chunk_text_sha256` equal to the sha of the committed
   chunk text; a `shown` delivery whose `shown_sha256` equals `rendered_sha256`; the case's gold unit among the
   cited chunks; the latest hosted `app_starts` row ok with the deployed commit.

Answer wording is not compared (non-deterministic paid calls, D-171). Exit 0: every row passes. 1: a difference or
a failed check. 2: a usage error or a lookup that found no row.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

SEED = 20261114
DRAW_COUNT = 5
E2E_RUNS = ("eval/e2e/results/e2e_run1.json", "eval/e2e/results/e2e_run2.json")
QUESTIONS = "eval/retrieval/questions_v1.jsonl"
LABELS = ("eval/e2e/labels_test_v1.jsonl", "eval/e2e/labels_dev_v1.jsonl")
LIVE_CHECK = "eval/interface/results/live_check_v1.json"
REFUSAL_REFERENCES = ("Q-e88f33ea", "Q-67c8ea25")
DEFAULT_BASELINE_REF = "Q-9681c177"
RETRIEVAL_LABELS = tuple(f"P{n}" for n in range(1, 9))  # P1 to P8
EXACT_FIELDS = ("config_sha256", "retrieval_config_sha256", "guardrails_policy_sha256", "generator_model",
                "verifier_model", "tagger_model", "prompt_version", "schema_version", "tool_defs_version",
                "verifier_prompt_version", "gap_prompt_version")
REFUSAL_FIELDS = ("status", "refusal_reason", "refusal_stage", "refusal_text", "guardrails_policy_sha256",
                  "tagger_model")


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def read_jsonl(relative: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / relative).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------- the draw ----------

def case_of(run: dict, case_id: str) -> dict:
    return next(c for c in run["cases"] if c["id"] == case_id)


def eligible_cases(run1: dict, run2: dict) -> list[str]:
    """Subset T cases answered in both runs with identical passage lists (label and chunk id), in id order."""
    out = []
    for case in sorted((c for c in run1["cases"] if c["subset"] == "T"), key=lambda c: c["id"]):
        other = case_of(run2, case["id"])
        same = [(p["label"], p["chunk_id"]) for p in case["passages"]] == [(p["label"], p["chunk_id"])
                                                                           for p in other["passages"]]
        if case["status"] == "answered" and other["status"] == "answered" and same:
            out.append(case["id"])
    return out


def draw_cases(run1: dict, run2: dict, seed: int = SEED, count: int = DRAW_COUNT) -> list[str]:
    return random.Random(seed).sample(eligible_cases(run1, run2), count)


# ---------- the three classes (pure functions over plain dicts) ----------

def corpus_hashes(corpus: dict | None) -> dict | None:
    """The corpus object without the load id, which differs between the two databases."""
    return None if corpus is None else {k: v for k, v in corpus.items() if k != "load_id"}


def class1_answered(live: dict, baseline: dict, case: dict) -> list[str]:
    """Differences between a live answered row and the baseline row (fixed fields) and the Phase 8 case (flags)."""
    out = [f"{field}: differs" for field in EXACT_FIELDS if live.get(field) != baseline.get(field)]
    if live.get("versions") != baseline.get("versions"):
        out.append("versions: differs")
    if corpus_hashes(live.get("corpus")) != corpus_hashes(baseline.get("corpus")):
        out.append("corpus: hashes differ")
    pre = live.get("pre_check") or {}
    if pre.get("refusal") is not None:
        out.append("pre_check: refused an answered case")
    if list(live.get("flags") or []) != list(case.get("flags") or []):
        out.append("flags: differ from the Phase 8 case")
    return out


def class1_refusal(live: dict, baseline: dict) -> list[str]:
    out = [f"{field}: differs" for field in REFUSAL_FIELDS if live.get(field) != baseline.get(field)]
    for field in ("versions", "pre_check"):
        if live.get(field) != baseline.get(field):
            out.append(f"{field}: differs")
    if corpus_hashes(live.get("corpus")) != corpus_hashes(baseline.get("corpus")):
        out.append("corpus: hashes differ")
    return out


def retrieval_ids(passages: list[dict]) -> list[tuple[str, str]]:
    """(label, chunk id) of P1 to P8 in label order."""
    by_label = {p["label"]: p["chunk_id"] for p in passages if p["label"] in RETRIEVAL_LABELS}
    return [(label, by_label[label]) for label in RETRIEVAL_LABELS if label in by_label]


def class2(live_passages: list[dict], case: dict, *, parity_passed: bool) -> tuple[list[str], list[str]]:
    """(failures, accepted differences). Every difference is listed; it is accepted only after the parity check."""
    expected = retrieval_ids([{"label": p["label"], "chunk_id": p["chunk_id"]} for p in case["passages"]])
    live = retrieval_ids(live_passages)
    if live == expected:
        return [], []
    labels = sorted({label for label, _ in live} | {label for label, _ in expected})
    expected_by, live_by = dict(expected), dict(live)
    notes = [f"{label}: live {live_by.get(label, 'absent')}, Phase 8 {expected_by.get(label, 'absent')}"
             for label in labels if live_by.get(label) != expected_by.get(label)]
    return ([], notes) if parity_passed else (notes, [])


def class3(data: dict, *, local_sha: dict[str, str], gold_units: list[str], matches, start_problems: list[str]) -> list[str]:
    """What must hold of an answered live row. `data` is `audit.report.fetch_query`'s dict."""
    query, out = data["query"], []
    if query["status"] != "answered":
        out.append(f"status is {query['status']}, expected answered")
    shown = [c for c in data["claims"] if c["shown"]]
    if not shown:
        out.append("no shown claim")
    for claim in shown:
        if claim["verdict"] != "supported":
            out.append(f"claim {claim['claim_no']}: verdict {claim['verdict']}, expected supported")
        if not claim["kept_chunk_ids"]:
            out.append(f"claim {claim['claim_no']}: no kept citation")
    cited = [p for p in data["passages"] if p["cited"]]
    corpus = data.get("corpus_text")
    for passage in cited:
        chunk_id = passage["chunk_id"]
        if local_sha.get(chunk_id) != passage["chunk_text_sha256"]:
            out.append(f"{chunk_id}: chunk_text_sha256 differs from the committed chunk text")
        if corpus is None:
            out.append(f"{chunk_id}: the corpus is not readable by the reviewer")
        elif chunk_id not in corpus:
            out.append(f"{chunk_id}: not in the hosted corpus")
        elif corpus[chunk_id]["text_sha256"] != local_sha.get(chunk_id):
            out.append(f"{chunk_id}: hosted text sha differs from the committed chunk text")
    if not any(matches(p["chunk_id"], unit) for p in cited for unit in gold_units):
        out.append("no cited chunk matches the gold unit")
    if not any(d["outcome"] == "shown" and d["shown_sha256"] == query["rendered_sha256"] for d in data["deliveries"]):
        out.append("no shown delivery whose shown_sha256 equals rendered_sha256")
    out += [f"app start: {p}" for p in start_problems]
    return out


# ---------- the run ----------

@dataclass
class RowResult:
    reference: str
    kind: str                     # case id or "refusal"
    failures: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)


def fetch(conn, reference: str) -> dict:
    from audit.report import fetch_query, resolve_reference  # noqa: PLC0415

    return fetch_query(conn, resolve_reference(conn, reference))


def check_answered(live: dict, baseline: dict, case: dict, *, local_sha, gold_units, matches, start_problems,
                   parity_passed: bool) -> RowResult:
    failures = class1_answered(live["query"], baseline["query"], case)
    fail2, accepted = class2(live["passages"], case, parity_passed=parity_passed)
    failures += [f"passages {d}" for d in fail2]
    failures += class3(live, local_sha=local_sha, gold_units=gold_units, matches=matches, start_problems=start_problems)
    return RowResult(live["query"].get("query_id", "?")[:8], case["id"], failures, accepted)


def check_refusal(live: dict, baseline: dict, start_problems: list[str]) -> RowResult:
    failures = class1_refusal(live["query"], baseline["query"])
    failures += [f"app start: {p}" for p in start_problems]
    return RowResult(live["query"].get("query_id", "?")[:8], "refusal", failures)


def parse_pairs(items: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for item in items:
        live, sep, base = item.partition("=")
        if not sep or not live or not base:
            raise ValueError(f"expected LIVE_REFERENCE=BASELINE, got {item!r}")
        pairs.append((live, base))
    return pairs


def cmd_draw() -> int:
    run1, run2 = (read_json(p) for p in E2E_RUNS)
    questions = {q["id"]: q["question"] for q in read_jsonl(QUESTIONS)}
    ids = draw_cases(run1, run2)
    print(f"seed {SEED}; {len(eligible_cases(run1, run2))} eligible subset T cases; the drawn cases, in order:")
    for n, case_id in enumerate(ids, 1):
        print(f"\n{n}. {case_id}\n{questions[case_id]}")
    live_check = {r["reference"]: r for r in read_json(LIVE_CHECK)["results"]}
    for n, ref in enumerate(REFUSAL_REFERENCES, len(ids) + 1):
        record = live_check[ref]
        print(f"\n{n}. {ref} ({record['topic']})\n{record['question']}")
    return 0


def cmd_check(args) -> int:
    from audit.report import connect_review  # noqa: PLC0415
    from check_pgvector import connect  # noqa: PLC0415
    from embed.load import CHUNKS, read_chunks  # noqa: PLC0415
    from retrieval.smoke_query import matches  # noqa: PLC0415

    cases, refusals = parse_pairs(args.case), parse_pairs(args.refusal)
    if not cases and not refusals:
        print("nothing to check: give --case and/or --refusal")
        return 2
    run1, run2 = (read_json(p) for p in E2E_RUNS)
    labels = {row["id"]: row for path in LABELS for row in read_jsonl(path)}
    local_sha = {c["chunk_id"]: c["text_sha256"] for c in read_chunks(CHUNKS)}
    check_live_start = load_script("check_live_start_for_compare", "eval/deploy/check_live_start.py")
    live_check = {r["reference"]: r for r in read_json(LIVE_CHECK)["results"]}
    results: list[RowResult] = []
    try:
        hosted, read_only = connect_review()
        with hosted, connect() as local:
            if not read_only:
                print("REVIEW_DATABASE_URL is not set: run this through with_hosted.py review")
                return 2
            start = check_live_start.latest_hosted_start(hosted)
            start_problems = (["no hosted start row"] if start is None
                              else check_live_start.problems_of(start, args.commit))
            baseline = fetch(local, args.baseline_ref) if cases else None
            for live_ref, case_id in cases:
                case = case_of(run1, case_id)
                results.append(check_answered(fetch(hosted, live_ref), baseline, case, local_sha=local_sha,
                                              gold_units=labels[case_id]["gold_units"], matches=matches,
                                              start_problems=start_problems, parity_passed=args.parity_passed))
                results[-1].reference = live_ref
            for live_ref, base_ref in refusals:
                if base_ref not in live_check:
                    print(f"{base_ref} is not a Phase 9 record")
                    return 2
                results.append(check_refusal(fetch(hosted, live_ref), fetch(local, base_ref), start_problems))
                results[-1].reference = live_ref
    except LookupError as exc:
        print(f"lookup failed: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - the type only: a message can carry a host or a user name
        print(f"compare_live.py: failed ({type(exc).__name__})")
        return 2
    for r in results:
        status = "PASS" if not r.failures else "FAIL"
        print(f"{r.reference} ({r.kind}): {status}")
        for line in r.failures:
            print(f"  FAIL {line}")
        for line in r.accepted:
            print(f"  accepted (parity passed): {line}")
    print("PASS: every live row meets its three classes" if all(not r.failures for r in results)
          else "FAIL: see the lines above")
    return 0 if all(not r.failures for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("draw", help="print the questions to ask on the live URL")
    check = sub.add_parser("check", help="compare live rows with their cases")
    check.add_argument("--commit", required=True, help="the deployed commit (full hash or at least 7 characters)")
    check.add_argument("--case", action="append", default=[], metavar="LIVE=CASE",
                       help="a live reference and its Phase 8 case id, for example Q-1a2b3c4d=Q031")
    check.add_argument("--refusal", action="append", default=[], metavar="LIVE=PHASE9",
                       help="a live reference and its Phase 9 reference, for example Q-1a2b3c4d=Q-e88f33ea")
    check.add_argument("--baseline-ref", default=DEFAULT_BASELINE_REF,
                       help="the local answered row that holds the fixed fields (default %(default)s)")
    check.add_argument("--parity-passed", action="store_true",
                       help="design Section 9.1 passed: a passage difference is accepted and listed")
    args = parser.parse_args(argv)
    if args.command == "draw":
        return cmd_draw()
    try:
        parse_pairs(args.case + args.refusal)
    except ValueError as exc:
        print(exc)
        return 2
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
