"""Gate G2 runner: adversarial tuning runs and the one held-out decision run (D-37, D-38, D-40;
guardrails design, sections 5 and 6).

    .venv\\Scripts\\python eval\\guardrails\\run_adversarial.py --split tune --run 1
    .venv\\Scripts\\python eval\\guardrails\\run_adversarial.py --freeze-config
    .venv\\Scripts\\python eval\\guardrails\\run_adversarial.py --holdout

Tuning runs: the tune part of eval/guardrails/adversarial_v1.jsonl plus the 20 dev questions and
G01 to G05, through the full `generate()` flow, once. Results go to
eval/guardrails/results/tune_run<N>.json (never overwritten) and a review sheet to the git-ignored
docs/phase06_guardrails/review_pack/.

The adversarial file and its split are written by a separate session (D-40). This runner reads
them only through `load_adversarial`, which checks both files against their frozen sha256 values
and keeps the rows of the requested split, discarding the others as the file is read (as
`run_eval.load_questions` does for the test split). The sha256 values pinned below are the ones
recorded in D-36; a file that does not match them is refused.

`--freeze-config` writes eval/guardrails/config_g2.json from the live configuration (prompt
versions and hashes, verifier model and effort, normaliser, tau, policy and retrieval hashes) once
tuning has passed twice in a row (design section 6.1).

`--holdout` is the G2 decision run. Before any work it refuses when a held-out marker or result
already exists (the held-out part runs once), when CONFIG_G2_SHA256 is not recorded or
config_g2.json does not match it, or when the live configuration differs from config_g2.json. It
then writes the marker and runs the held-out questions plus the 22 in-scope questions (dev, G03,
G05) 3 times. No test-split question is ever loaded (D-29).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from generation.disclosure import POLICY as DISCLOSURE_POLICY, load_policy  # noqa: E402
from generation.generate import DISCLAIMER, RETRIEVAL_CONFIG, DbCorpusStore, generate, retrieval_config  # noqa: E402
from generation.guardrails import POLICY as GUARDRAILS_POLICY, load_guardrails_policy, note_labels  # noqa: E402
from generation.llm import EFFORT, MODEL, VERIFIER_EFFORT, VERIFIER_MODEL, AnthropicLLMClient  # noqa: E402
from generation.prompt import (GAP_PROMPT_VERSION, GAP_SYSTEM_PROMPT, PROMPT_VERSION, SYSTEM_PROMPT,  # noqa: E402
                               VERIFIER_PROMPT_VERSION, VERIFIER_SCHEMA_VERSION, VERIFIER_SYSTEM_PROMPT)
from generation.verify import NORMALISER_VERSION  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402

spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "retrieval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
sys.modules.setdefault("run_eval", run_eval)
spec.loader.exec_module(run_eval)
spec = importlib.util.spec_from_file_location("run_exit_check", ROOT / "eval" / "generation" / "run_exit_check.py")
run_exit_check = importlib.util.module_from_spec(spec)
sys.modules.setdefault("run_exit_check", run_exit_check)
spec.loader.exec_module(run_exit_check)

ADVERSARIAL = HERE / "adversarial_v1.jsonl"
ADVERSARIAL_SHA256: str | None = "2e3541c88db45e53fa50ec591e7b65d108e7bb79955608ac3f1c07632ff592dc"  # D-36, frozen 2026-11-03
SPLIT = HERE / "split_v1.json"
SPLIT_SHA256: str | None = "0143e246fd23e98569001bcdafd5b771d5c4fa628a305a37c36d74937227f6fc"  # D-36
CONFIG_G2 = HERE / "config_g2.json"
CONFIG_G2_SHA256: str | None = "6faad20a1864fabfb8b812c612e4bc5ce5cbfdaa2060152fe98ca05f5eb965d9"  # D-49, frozen 2026-11-04
RESULTS_DIR = HERE / "results"
HOLDOUT_MARKER = RESULTS_DIR / "holdout_started.json"
HOLDOUT_RESULT = RESULTS_DIR / "holdout_final.json"
PACK_DIR = ROOT / "docs" / "phase06_guardrails" / "review_pack"
SPLITS = ("tune", "holdout")
REPEATS = 3
IN_SCOPE_EXIT = ("G03", "G05")


class GuardError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_adversarial(split: str, path: Path = ADVERSARIAL, expected_sha256: str | None = ADVERSARIAL_SHA256,
                     split_path: Path = SPLIT, split_sha256: str | None = SPLIT_SHA256) -> list[dict]:
    """The adversarial rows of one split only. Rows of the other split are discarded unread.

    The split file is authoritative: rows keep their own `split` field null (check_adversarial.py),
    so rows are chosen by the split file's id list and returned with `split` set.
    """
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    if not expected_sha256 or not split_sha256:
        raise GuardError("no frozen sha256 is recorded for the adversarial set or its split yet")
    if not path.exists() or sha256_file(path) != expected_sha256:
        raise GuardError(f"{path.name} does not match its frozen sha256")
    if not split_path.exists() or sha256_file(split_path) != split_sha256:
        raise GuardError(f"{split_path.name} does not match its frozen sha256")
    split_file = json.loads(split_path.read_text(encoding="utf-8"))
    if split_file.get("set_sha256") != expected_sha256:
        raise GuardError(f"{split_path.name} was not made for this adversarial set")
    ids = set(split_file[split])
    kept = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                if row["id"] in ids:
                    kept.append(dict(row, split=split))
    if len(kept) != len(ids) or {r["id"] for r in kept} != ids:
        raise GuardError(f"the {split} rows do not match {split_path.name}")
    return kept


def in_scope_questions(include_out_of_scope_exit: bool) -> list[dict]:
    """The 20 dev questions (text from the dev split only) and G03, G05 (plus G01, G02, G04 for
    tuning runs), from the Phase 5 exit-check file."""
    rows = []
    for q in run_eval.load_questions("dev"):
        rows.append({"id": q["id"], "source": "dev", "question": q["question"], "expected": "answer_supported"})
    for q in run_exit_check.load_exit_questions():
        if q["source"] == "new" and (include_out_of_scope_exit or q["id"] in IN_SCOPE_EXIT):
            rows.append({"id": q["id"], "source": "exit_check", "question": q["question"],
                         "expected": "answer_supported" if q["id"] in IN_SCOPE_EXIT else "refuse"})
    return rows


def current_config() -> dict:
    """The live guardrails configuration, as config_g2.json records it."""
    policy = load_guardrails_policy()
    return {
        "generation": {"model": MODEL, "effort": EFFORT, "prompt_version": PROMPT_VERSION,
                       "system_prompt_sha256": sha256_text(SYSTEM_PROMPT)},
        "verifier": {"model": VERIFIER_MODEL, "effort": VERIFIER_EFFORT, "prompt_version": VERIFIER_PROMPT_VERSION,
                     "schema_version": VERIFIER_SCHEMA_VERSION,
                     "system_prompt_sha256": sha256_text(VERIFIER_SYSTEM_PROMPT),
                     "gap_prompt_version": GAP_PROMPT_VERSION, "gap_prompt_sha256": sha256_text(GAP_SYSTEM_PROMPT)},
        "normaliser": NORMALISER_VERSION,
        "tau": policy.tau,
        "guardrails_policy_sha256": sha256_file(GUARDRAILS_POLICY),
        "disclosure_policy_sha256": sha256_file(DISCLOSURE_POLICY),
        "retrieval_config_sha256": sha256_file(RETRIEVAL_CONFIG),
    }


def check_holdout_guard(*, config_path: Path = CONFIG_G2, config_sha256: str | None = CONFIG_G2_SHA256,
                        marker: Path = HOLDOUT_MARKER, result: Path = HOLDOUT_RESULT, live: dict | None = None) -> dict:
    """Return the frozen configuration, or raise GuardError before any work is done."""
    if marker.exists() or result.exists():
        raise GuardError("the held-out part has already been run; it runs once")
    if not config_sha256:
        raise GuardError("no frozen configuration hash is recorded yet")
    if not config_path.exists() or sha256_file(config_path) != config_sha256:
        raise GuardError(f"{config_path.name} does not match the recorded sha256 {config_sha256}")
    frozen = json.loads(config_path.read_text(encoding="utf-8"))
    if frozen != (live if live is not None else current_config()):
        raise GuardError(f"the live configuration differs from {config_path.name}")
    return frozen


def freeze_config(path: Path = CONFIG_G2) -> str:
    if path.exists():
        raise GuardError(f"{path.name} already exists; it is frozen once")
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(current_config(), fh, indent=1)
        fh.write("\n")
    return sha256_file(path)


def answer_row(case: dict, answer) -> dict:
    notes = note_labels(answer.notes, answer.refusal_reason, answer.disclosures)  # required_notes labels
    row = {k: case[k] for k in ("id", "source", "expected") if k in case}
    for key in ("category", "expected_reason", "required_notes", "must_not", "rationale"):
        if key in case:
            row[key] = case[key]
    if case["source"] != "dev":
        row["question"] = case["question"]
    v = answer.verification
    row.update({
        "status": answer.status, "refusal_reason": answer.refusal_reason, "flags": answer.flags,
        "notes_shown": notes,
        "claims": [{"no": i, "text": c.text, "chunk_ids": c.chunk_ids} for i, c in enumerate(answer.claims, 1)],
        "gaps": answer.gaps,
        "passages": [{"label": p.label, "rank": p.rank, "chunk_id": p.chunk_id, "score": p.score}
                     for p in answer.passages],
        "verification": None if v is None else {
            "summary": v.summary,
            "claims": [{"no": c.no, "text": c.text, "verdict": c.verdict, "reason": c.reason,
                        "sub_reason": c.sub_reason, "action": c.action, "missing": c.rule.missing,
                        "kept_chunk_ids": c.kept_chunk_ids} for c in v.checks],
            "gaps": None if v.gap_result is None else [g.as_log() for g in v.gap_result.gaps]},
        "rendered": answer.rendered,
        "cost_usd": round(answer.total_cost_usd, 6), "record_id": answer.record_id, "error": answer.error,
    })
    row["auto"] = {  # mechanical pre-checks for the reviewers; the verdicts are theirs (section 6.2)
        "required_notes_shown": all(n in notes for n in case.get("required_notes", [])),
        "refused_or_declined": answer.status in ("refused", "not_covered"),
        "disclaimer": answer.rendered.endswith(DISCLAIMER),
    }
    return row


def review_sheet(title: str, rows: list[dict], texts: dict[str, str], chunk_text: dict[str, dict]) -> str:
    out = [f"# {title}: review sheet", "",
           "Generated by eval/guardrails/run_adversarial.py (git-ignored). Check each case against the pass "
           "rule in guardrails design section 6.2; the verifier's verdicts are evidence, not the verdict.", ""]
    for row in rows:
        out += [f"## {row['id']} ({row.get('category', row['source'])}, expected {row['expected']})", "",
                f"**Question:** {texts[row['id']]}", "",
                f"**Status:** {row['status']}" + (f" ({row['refusal_reason']})" if row["refusal_reason"] else ""),
                f"**Flags:** {', '.join(row['flags']) or 'none'}; **notes shown:** {', '.join(row['notes_shown']) or 'none'}",
                "", "````text", row["rendered"], "````", ""]
        if row["verification"]:
            out += ["| No. | Claim | Verdict | Action |", "|---|---|---|---|"]
            out += [f"| {c['no']} | {c['text']} | {c['verdict']} {c['reason'] or ''} {c['sub_reason'] or ''} | "
                    f"{c['action']} |" for c in row["verification"]["claims"]]
            out.append("")
        for p in row["passages"]:
            out += [f"**{p['label']}** `{p['chunk_id']}` (score {p['score']:.3f})", "", "````text",
                    chunk_text[p["chunk_id"]]["text"], "````", ""]
    return "\n".join(out)


def run_cases(cases: list[dict], repeats: int) -> tuple[list[list[dict]], dict]:
    config = retrieval_config()
    cache = run_eval.QueryCache(config["model"])
    policy, gpolicy = load_policy(), load_guardrails_policy()
    llm, verifier = AnthropicLLMClient(model=MODEL), AnthropicLLMClient(model=VERIFIER_MODEL)
    runs = []
    with connect() as conn:
        retriever, store = Retriever(conn, config), DbCorpusStore(conn)
        for r in range(1, repeats + 1):
            rows = []
            for case in cases:
                answer = generate(case["question"], retriever=retriever, llm=llm, verifier=verifier, store=store,
                                  query_vector=cache.embed(case["question"]), policy=policy,
                                  guardrails_policy=gpolicy)
                rows.append(answer_row(case, answer))
                print(f"repeat {r} {case['id']}: {answer.status}"
                      + (f" ({answer.refusal_reason})" if answer.refusal_reason else "")
                      + f", {len(answer.claims)} claims, ${answer.total_cost_usd:.4f}")
            runs.append(rows)
        chunk_text = store.chunks(sorted({p["chunk_id"] for rows in runs for row in rows for p in row["passages"]}))
    return runs, chunk_text


def write_result(path: Path, record: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def run_tune(run_no: int) -> dict:
    out_path = RESULTS_DIR / f"tune_run{run_no}.json"
    if out_path.exists():
        raise SystemExit(f"{out_path.name} already exists; use the next run number")
    cases = [dict(row, source="adversarial") for row in load_adversarial("tune")] + in_scope_questions(True)
    runs, chunk_text = run_cases(cases, 1)
    record = {"run": run_no, "split": "tune", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "config": current_config(), "adversarial_sha256": ADVERSARIAL_SHA256, "review": "pending",
              "cost_usd": round(sum(r["cost_usd"] for r in runs[0]), 6), "cases": runs[0]}
    write_result(out_path, record)
    pack = PACK_DIR / f"tune_run{run_no}"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "review_sheet.md").write_text(
        review_sheet(f"G2 tuning run {run_no}", runs[0], {c["id"]: c["question"] for c in cases}, chunk_text),
        encoding="utf-8")
    return record


def run_holdout() -> dict:
    frozen = check_holdout_guard()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with HOLDOUT_MARKER.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump({"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "config_g2_sha256": CONFIG_G2_SHA256}, fh)
        fh.write("\n")
    cases = [dict(row, source="adversarial") for row in load_adversarial("holdout")] + in_scope_questions(False)
    runs, chunk_text = run_cases(cases, REPEATS)
    record = {"split": "holdout", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "config": frozen, "config_g2_sha256": CONFIG_G2_SHA256, "adversarial_sha256": ADVERSARIAL_SHA256,
              "repeats": REPEATS, "review": "pending",
              "cost_usd": round(sum(r["cost_usd"] for rows in runs for r in rows), 6),
              "runs": [{"repeat": i, "cases": rows} for i, rows in enumerate(runs, 1)]}
    write_result(HOLDOUT_RESULT, record)
    texts = {c["id"]: c["question"] for c in cases}
    for i, rows in enumerate(runs, 1):
        pack = PACK_DIR / f"holdout_repeat{i}"
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "review_sheet.md").write_text(review_sheet(f"G2 held-out run, repeat {i}", rows, texts, chunk_text),
                                              encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--split", choices=("tune",), help="a tuning run")
    mode.add_argument("--holdout", action="store_true", help="the G2 decision run (once, frozen configuration)")
    mode.add_argument("--freeze-config", action="store_true", help="write config_g2.json (once)")
    parser.add_argument("--run", type=int, help="tuning run number (never overwritten)")
    args = parser.parse_args(argv)
    try:
        if args.freeze_config:
            print(f"{CONFIG_G2.name} written, sha256 {freeze_config()}")
        elif args.holdout:
            record = run_holdout()
            print(f"held-out run done: {REPEATS} repeats, ${record['cost_usd']:.4f}")
        else:
            if args.run is None:
                parser.error("--split tune needs --run")
            record = run_tune(args.run)
            print(f"tuning run {record['run']}: {len(record['cases'])} cases, ${record['cost_usd']:.4f}")
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
