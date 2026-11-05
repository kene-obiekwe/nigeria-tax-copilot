"""Gate G2 runner: adversarial tuning runs and the once-only held-out decision runs (D-37, D-38, D-40,
D-52, D-58, D-61; guardrails design, sections 5 and 6; G2 fix design, sections 5 and 7).

    .venv\\Scripts\\python eval\\guardrails\\run_adversarial.py --split tune --set v1 --parts tune,holdout --run 4
    .venv\\Scripts\\python eval\\guardrails\\run_adversarial.py --freeze-config --set v2
    .venv\\Scripts\\python eval\\guardrails\\run_adversarial.py --holdout --set v2

**Set registry (D-58).** Each adversarial set version has its own entry in `SETS`: question file and
sha256, an optional split file and sha256, frozen configuration file and its recorded sha256,
held-out marker, held-out result and review-pack folder. The v1 entry keeps the paths and hashes of
the first decision run, so its marker refuses `--holdout` forever and its configuration refuses
`--freeze-config` forever. A set whose hashes are not recorded is refused. `--set` is required for
every command, so a decision run always names its set.

**Loading (D-40, D-52).** The adversarial files are written by a separate session (D-40). This runner
reads them only through `load_adversarial(set, part)`, which checks the files against their recorded
sha256 values and keeps the rows of the requested part, discarding the others as the file is read.
Where a set has a split file, the split file is authoritative (C-04). A set with no split file is
all held out: its `tune` part is refused. **A held-out part loads only once that set's marker
exists**, that is, once its decision run has started: the decision run writes the marker before it
loads anything, and after that the spent part is tuning data (D-52).

**Tuning runs (D-61).** `--split tune --set <set> --parts <parts> --run N` runs the chosen parts of a
set plus the 20 dev questions and G01 to G05 through the full `generate()` flow, once. Run numbers
continue the existing `tune_run<N>.json` files (the next number only; never overwritten). The result
records the set, its sha256 and the parts, plus the D-61 counts per part: false refusals on the
in-scope questions, required notes missed and notes not required. A review sheet goes to the
git-ignored docs/phase06_guardrails/review_pack/.

**Freezing.** `--freeze-config --set <set>` writes the set's configuration file once (`open("x")`)
from the live configuration: prompt versions and hashes, verifier model and effort, normaliser, tau,
policy hashes, the retrieval configuration's sha256 and, for every set after v1, the set's version
and sha256.

**Decision run.** `--holdout --set <set>` refuses, before any work, when the set's marker or result
exists, when the set's hashes or its configuration hash are not recorded, when the configuration file
does not match its hash or was made for another set, or when the live configuration differs from it.
It then writes the marker and runs the set's held-out rows plus the 22 in-scope questions (dev, G03,
G05) 3 times. No retrieval test-split question is ever loaded (D-29).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from generation.disclosure import POLICY as DISCLOSURE_POLICY, load_policy  # noqa: E402
from generation.generate import DISCLAIMER, RETRIEVAL_CONFIG, DbCorpusStore, generate, retrieval_config  # noqa: E402
from generation.guardrails import (HEADING_CORRECTIONS, POLICY as GUARDRAILS_POLICY, load_guardrails_policy,  # noqa: E402
                                   note_labels)
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

RESULTS_DIR = HERE / "results"
PACK_DIR = ROOT / "docs" / "phase06_guardrails" / "review_pack"
PARTS = ("tune", "holdout")
REPEATS = 3
IN_SCOPE_EXIT = ("G03", "G05")


class GuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdversarialSet:
    """One adversarial set version and its once-only files (D-58)."""
    name: str
    questions: Path
    questions_sha256: str | None
    split: Path | None             # None: no split file, every row is held out
    split_sha256: str | None
    config: Path
    config_sha256: str | None
    marker: Path
    result: Path
    pack_prefix: str               # review pack folder: <pack_prefix><repeat>
    config_records_set: bool = True  # False only for v1, frozen before the registry (D-49)


SETS: dict[str, AdversarialSet] = {
    "v1": AdversarialSet(
        name="v1",
        questions=HERE / "adversarial_v1.jsonl",
        questions_sha256="2e3541c88db45e53fa50ec591e7b65d108e7bb79955608ac3f1c07632ff592dc",  # D-36, 2026-11-03
        split=HERE / "split_v1.json",
        split_sha256="0143e246fd23e98569001bcdafd5b771d5c4fa628a305a37c36d74937227f6fc",  # D-36
        config=HERE / "config_g2.json",
        config_sha256="6faad20a1864fabfb8b812c612e4bc5ce5cbfdaa2060152fe98ca05f5eb965d9",  # D-49, 2026-11-04
        marker=RESULTS_DIR / "holdout_started.json",
        result=RESULTS_DIR / "holdout_final.json",
        pack_prefix="holdout_repeat",
        config_records_set=False),
    # adversarial_v2 (D-52, D-58): 48 held-out cases, no split file. The sha256 values are pinned by
    # the build session from the overseer's prompt once the file is frozen; until then every v2
    # command is refused. This runner never opens the v2 file before then.
    "v2": AdversarialSet(
        name="v2",
        questions=HERE / "adversarial_v2.jsonl",
        questions_sha256="350905729640400eb77eeba6d32e6284ea5a7b06110bb6d46ce3fbd306cf35b4",  # D-58, 2026-11-05
        split=None,
        split_sha256=None,
        config=HERE / "config_g2_v2.json",
        config_sha256=None,
        marker=RESULTS_DIR / "v2" / "holdout_started.json",
        result=RESULTS_DIR / "v2" / "holdout_final.json",
        pack_prefix="v2_holdout_repeat"),
}

# Kept for the v1 record and older callers.
ADVERSARIAL, ADVERSARIAL_SHA256 = SETS["v1"].questions, SETS["v1"].questions_sha256
SPLIT, SPLIT_SHA256 = SETS["v1"].split, SETS["v1"].split_sha256
CONFIG_G2, CONFIG_G2_SHA256 = SETS["v1"].config, SETS["v1"].config_sha256
HOLDOUT_MARKER, HOLDOUT_RESULT = SETS["v1"].marker, SETS["v1"].result


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_set(adv_set: str | AdversarialSet) -> AdversarialSet:
    if isinstance(adv_set, AdversarialSet):
        return adv_set
    if adv_set not in SETS:
        raise GuardError(f"unknown adversarial set {adv_set!r}; known: {', '.join(SETS)}")
    return SETS[adv_set]


def _check_set_hashes(s: AdversarialSet) -> None:
    """Refuse a set whose hashes are not recorded, before any of its files is opened."""
    if not s.questions_sha256 or (s.split is not None and not s.split_sha256):
        raise GuardError(f"no frozen sha256 is recorded for adversarial set {s.name} or its split yet")


def load_adversarial(adv_set: str | AdversarialSet, part: str) -> list[dict]:
    """The rows of one part of an adversarial set. Rows of other parts are discarded unread.

    A held-out part loads only once the set's marker exists (D-52, D-58). A set with no split file
    has every row held out, so its tune part is refused.
    """
    s = get_set(adv_set)
    if part not in PARTS:
        raise ValueError(f"unknown part {part!r}")
    _check_set_hashes(s)
    if part == "tune" and s.split is None:
        raise GuardError(f"adversarial set {s.name} has no split file: every row is held out, so it has no tune part")
    if part == "holdout" and not s.marker.exists():
        raise GuardError(f"the held-out part of adversarial set {s.name} has not been run; it cannot be loaded "
                         "until its decision run has started")
    if not s.questions.exists() or sha256_file(s.questions) != s.questions_sha256:
        raise GuardError(f"{s.questions.name} does not match its frozen sha256")
    ids = None
    if s.split is not None:
        if not s.split.exists() or sha256_file(s.split) != s.split_sha256:
            raise GuardError(f"{s.split.name} does not match its frozen sha256")
        split_file = json.loads(s.split.read_text(encoding="utf-8"))
        if split_file.get("set_sha256") != s.questions_sha256:
            raise GuardError(f"{s.split.name} was not made for this adversarial set")
        ids = set(split_file[part])
    kept = []
    with s.questions.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                if ids is None or row["id"] in ids:
                    kept.append(dict(row, split=part, set=s.name))
    if ids is not None and (len(kept) != len(ids) or {r["id"] for r in kept} != ids):
        raise GuardError(f"the {part} rows do not match {s.split.name}")
    if ids is None and len({r["id"] for r in kept}) != len(kept):
        raise GuardError(f"{s.questions.name} has duplicate ids")
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


def current_config(adv_set: str | AdversarialSet | None = None) -> dict:
    """The live guardrails configuration, as a set's frozen configuration file records it. Every set
    after v1 also records its own version and sha256 (D-58)."""
    policy = load_guardrails_policy()
    config = {
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
        "heading_corrections_sha256": sha256_file(HEADING_CORRECTIONS),  # D-60
        "retrieval_config_sha256": sha256_file(RETRIEVAL_CONFIG),
    }
    if adv_set is not None:
        s = get_set(adv_set)
        if s.config_records_set:
            config["adversarial_set"] = {"version": s.name, "sha256": s.questions_sha256}
    return config


def check_holdout_guard(adv_set: str | AdversarialSet, *, live: dict | None = None) -> dict:
    """Return the set's frozen configuration, or raise GuardError before any work is done."""
    s = get_set(adv_set)
    if s.marker.exists() or s.result.exists():
        raise GuardError(f"the held-out part of adversarial set {s.name} has already been run; it runs once")
    _check_set_hashes(s)
    if not s.config_sha256:
        raise GuardError(f"no frozen configuration hash is recorded for adversarial set {s.name} yet")
    if not s.config.exists() or sha256_file(s.config) != s.config_sha256:
        raise GuardError(f"{s.config.name} does not match the recorded sha256 {s.config_sha256}")
    frozen = json.loads(s.config.read_text(encoding="utf-8"))
    if s.config_records_set and frozen.get("adversarial_set") != {"version": s.name, "sha256": s.questions_sha256}:
        raise GuardError(f"{s.config.name} was made for another adversarial set")
    if frozen != (live if live is not None else current_config(s)):
        raise GuardError(f"the live configuration differs from {s.config.name}")
    return frozen


def freeze_config(adv_set: str | AdversarialSet) -> str:
    s = get_set(adv_set)
    if s.config.exists():
        raise GuardError(f"{s.config.name} already exists; it is frozen once")
    _check_set_hashes(s)
    config = current_config(s)
    with s.config.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(config, fh, indent=1)
        fh.write("\n")
    return sha256_file(s.config)


def answer_row(case: dict, answer) -> dict:
    notes = note_labels(answer.notes, answer.refusal_reason, answer.disclosures)  # required_notes labels
    row = {k: case[k] for k in ("id", "source", "expected") if k in case}
    for key in ("set", "split", "category", "expected_reason", "required_notes", "must_not", "rationale"):
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


# D-61 counts -------------------------------------------------------------------------------------

# Notes counted as "not required": the trigger notes (scope, other Act, DQ-12). Reference labels
# belong to refusals and failed references, and register ids follow the cited chunks, so neither is
# a trigger note (G2 fix design, section 4.3).
_TRIGGER_NOTE = re.compile(r"^(?:scope:|act:|DQ-12$)")
FALSE_REFUSAL_STATUSES = ("refused", "not_covered", "error")


def report_group(row: dict) -> str:
    """The set a case is counted in: dev, G01-G05, or the adversarial set and part."""
    if row["source"] == "dev":
        return "dev"
    if row["source"] == "exit_check":
        return "G01-G05"
    return f"{row.get('set', 'v1')} {row.get('split', '?')}"


def is_in_scope(row: dict) -> bool:
    """The in-scope questions for false refusals (D-61): dev, G03, G05 and the controls."""
    return row["source"] == "dev" or row["id"] in IN_SCOPE_EXIT or row.get("category") == "C"


def notes_not_required(row: dict) -> list[str]:
    required = set(row.get("required_notes") or [])
    return [n for n in row["notes_shown"] if _TRIGGER_NOTE.match(n) and n not in required]


def required_notes_missed(row: dict) -> list[str]:
    return [n for n in row.get("required_notes") or [] if n not in row["notes_shown"]]


def tuning_report(rows: list[dict]) -> dict:
    """D-61 counts, per set and part, with case ids (never question text). False refusals use the
    status alone; whether a refusal is false, and whether a note is a false trigger, stay the
    reviewers' judgement."""
    groups: dict[str, dict] = {}
    for row in rows:
        for name in [report_group(row)] + (["controls"] if row.get("category") == "C" else []):
            g = groups.setdefault(name, {"cases": 0, "in_scope": 0, "false_refusals": [],
                                         "required_notes_missed": {}, "notes_not_required": {}})
            g["cases"] += 1
            if is_in_scope(row):
                g["in_scope"] += 1
                if row["status"] in FALSE_REFUSAL_STATUSES:
                    g["false_refusals"].append(row["id"])
            if missed := required_notes_missed(row):
                g["required_notes_missed"][row["id"]] = missed
            if extra := notes_not_required(row):
                g["notes_not_required"][row["id"]] = extra
    in_scope = [r for r in rows if is_in_scope(r)]
    over = [r["id"] for r in rows if not is_in_scope(r) and r["expected"] == "answer_supported"
            and r["status"] in FALSE_REFUSAL_STATUSES]
    return {
        "in_scope_questions": len(in_scope),
        "false_refusals": [r["id"] for r in in_scope if r["status"] in FALSE_REFUSAL_STATUSES],
        "over_refusals": over,
        "required_notes_missed": sum(len(g["required_notes_missed"]) for k, g in groups.items() if k != "controls"),
        "notes_not_required": sum(len(g["notes_not_required"]) for k, g in groups.items() if k != "controls"),
        "by_set": groups,
    }


def report_markdown(report: dict) -> str:
    out = ["## D-61 counts", "",
           f"In-scope questions: {report['in_scope_questions']}; falsely refused: "
           f"{len(report['false_refusals'])} ({', '.join(report['false_refusals']) or 'none'}); "
           f"over-refusals (reported, not failed): {', '.join(report['over_refusals']) or 'none'}.", "",
           "| Set | Cases | In scope | False refusals | Required notes missed | Notes not required |",
           "|---|---|---|---|---|---|"]
    for name, g in report["by_set"].items():
        missed = "; ".join(f"{k} {', '.join(v)}" for k, v in g["required_notes_missed"].items()) or "0"
        extra = "; ".join(f"{k} {', '.join(v)}" for k, v in g["notes_not_required"].items()) or "0"
        out.append(f"| {name} | {g['cases']} | {g['in_scope']} | {', '.join(g['false_refusals']) or '0'} | "
                   f"{missed} | {extra} |")
    return "\n".join(out) + "\n"


# Running -----------------------------------------------------------------------------------------


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def next_tune_run(results_dir: Path = RESULTS_DIR) -> int:
    numbers = [int(m.group(1)) for p in results_dir.glob("tune_run*.json")
               if (m := re.fullmatch(r"tune_run(\d+)\.json", p.name))]
    return max(numbers, default=0) + 1


def parse_parts(text: str) -> list[str]:
    parts = list(dict.fromkeys(p.strip() for p in text.split(",") if p.strip()))
    if not parts or any(p not in PARTS for p in parts):
        raise GuardError(f"--parts takes a comma-separated list of {', '.join(PARTS)}")
    return parts


def tune_cases(adv_set: str | AdversarialSet, parts: list[str]) -> list[dict]:
    """Every case of a tuning run. Loads (and so checks) every part before any work is done."""
    cases = []
    for part in parts:
        cases += [dict(row, source="adversarial") for row in load_adversarial(adv_set, part)]
    return cases + in_scope_questions(True)


def run_tune(adv_set: str | AdversarialSet, parts: list[str], run_no: int) -> dict:
    s = get_set(adv_set)
    expected = next_tune_run()
    if run_no != expected:
        raise GuardError(f"the next tuning run is number {expected}; run numbers continue and are never reused")
    out_path = RESULTS_DIR / f"tune_run{run_no}.json"
    if out_path.exists():
        raise GuardError(f"{out_path.name} already exists; use the next run number")
    cases = tune_cases(s, parts)
    runs, chunk_text = run_cases(cases, 1)
    report = tuning_report(runs[0])
    record = {"run": run_no, "split": "tune", "set": s.name, "parts": parts,
              "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "config": current_config(s), "adversarial_sha256": s.questions_sha256, "review": "pending",
              "cost_usd": round(sum(r["cost_usd"] for r in runs[0]), 6), "d61": report, "cases": runs[0]}
    write_result(out_path, record)
    pack = PACK_DIR / f"tune_run{run_no}"
    pack.mkdir(parents=True, exist_ok=True)
    sheet = review_sheet(f"G2 tuning run {run_no}", runs[0], {c["id"]: c["question"] for c in cases}, chunk_text)
    (pack / "review_sheet.md").write_text(report_markdown(report) + "\n" + sheet, encoding="utf-8")
    return record


def run_holdout(adv_set: str | AdversarialSet) -> dict:
    s = get_set(adv_set)
    frozen = check_holdout_guard(s)
    s.marker.parent.mkdir(parents=True, exist_ok=True)
    with s.marker.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump({"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "set": s.name,
                   "config_sha256": s.config_sha256}, fh)
        fh.write("\n")
    cases = [dict(row, source="adversarial") for row in load_adversarial(s, "holdout")] + in_scope_questions(False)
    runs, chunk_text = run_cases(cases, REPEATS)
    record = {"split": "holdout", "set": s.name, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "config": frozen, "config_sha256": s.config_sha256, "adversarial_sha256": s.questions_sha256,
              "repeats": REPEATS, "review": "pending",
              "cost_usd": round(sum(r["cost_usd"] for rows in runs for r in rows), 6),
              "runs": [{"repeat": i, "cases": rows} for i, rows in enumerate(runs, 1)]}
    write_result(s.result, record)
    texts = {c["id"]: c["question"] for c in cases}
    for i, rows in enumerate(runs, 1):
        pack = PACK_DIR / f"{s.pack_prefix}{i}"
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "review_sheet.md").write_text(review_sheet(f"G2 held-out run ({s.name}), repeat {i}", rows, texts,
                                                           chunk_text), encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--split", choices=("tune",), help="a tuning run")
    mode.add_argument("--holdout", action="store_true", help="a G2 decision run (once per set, frozen configuration)")
    mode.add_argument("--freeze-config", action="store_true", help="write the set's frozen configuration (once)")
    parser.add_argument("--set", required=True, choices=sorted(SETS), help="adversarial set version (required)")
    parser.add_argument("--parts", default="tune", help="tuning run: comma-separated parts (tune, holdout)")
    parser.add_argument("--run", type=int, help="tuning run number (the next number; never overwritten)")
    args = parser.parse_args(argv)
    try:
        if args.freeze_config:
            print(f"{SETS[args.set].config.name} written, sha256 {freeze_config(args.set)}")
        elif args.holdout:
            record = run_holdout(args.set)
            print(f"held-out run ({args.set}) done: {REPEATS} repeats, ${record['cost_usd']:.4f}")
        else:
            if args.run is None:
                parser.error("--split tune needs --run")
            record = run_tune(args.set, parse_parts(args.parts), args.run)
            print(f"tuning run {record['run']}: {len(record['cases'])} cases, ${record['cost_usd']:.4f}")
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
