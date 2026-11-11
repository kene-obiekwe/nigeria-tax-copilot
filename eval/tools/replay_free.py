"""Free regression replays of the no-tool path (Phase 7; tools design, section 8, items 2 to 4).

    .venv\\Scripts\\python eval\\tools\\replay_free.py [--base cd0b9da]

No database, no embedding and no model call. Item 1 (the byte-identical request with tools off) and
item 5 (the full suite) are pytest tests.

2. **Pre-check replay.** `pre_check` of the base commit (the code before `check_section` was made
   public, read with `git show`) and of the working tree give the same `as_log()`, refusal text and
   note texts on every visible question, both loading the corrected heading file (D-126), so the
   replay tests the refactor alone. Then the heading correction itself: the base code with the
   base commit's heading file against the working tree with the corrected file; the only
   differences allowed are corrected heading strings inside `section_outside_v1` texts.
3. **Render replay.** `render()` with no calculation lines gives byte-identical text for every stored
   `gen-log/2` record of tuning run 17 (logs/generation/, git-ignored; skipped with the reason if
   absent). Refusal texts of pre-check refusals are rebuilt with the base heading file, which the
   run used, so only `render()` is tested.
4. **Rule-check replay.** Every kept claim of tuning run 17 gets the same rule-check result (passed,
   tokens, missing) under the current code (M7 baseline: 436 claims).

Visible questions: the 305 of earlier phases (adversarial_v1 both parts, v2 and v3, the 20 dev
questions, G01 to G05 and the 88 note-recall probes; `check_foreign_law.visible_questions`), plus
adversarial_v4 (48, tuning data since D-115). The retrieval test split is never read (D-29).
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "eval" / "guardrails"))
sys.path.insert(0, str(ROOT / "eval" / "retrieval"))

import check_foreign_law  # noqa: E402
import run_adversarial  # noqa: E402
from generation import generate as gen, guardrails, verify  # noqa: E402
from generation.disclosure import Disclosure  # noqa: E402

BASE = "cd0b9da"  # HEAD when the Phase 7 build started
RUN17 = ROOT / "eval" / "guardrails" / "results" / "tune_run17.json"
LOG_GLOB = str(ROOT / "logs" / "generation" / "*.jsonl")


def git_show(rev: str, path: str, out: Path) -> Path:
    out.write_bytes(subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, check=True,
                                   capture_output=True).stdout)
    return out


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


def questions() -> list[dict]:
    rows = [dict(r, group="305") for r in check_foreign_law.visible_questions()]
    rows += [{"id": r["id"], "question": r["question"], "group": "v4"}
             for r in run_adversarial.load_adversarial("v4", "holdout")]
    return rows


def _outcome(check) -> dict:
    return {"log": check.as_log(), "refusal_text": check.refusal_text, "notes": [n.text for n in check.notes]}


def precheck_replay(base_guardrails, base_headings: Path, corpus: dict) -> dict:
    rows = questions()
    policy_args = {"path": guardrails.POLICY, "section_map": guardrails.SECTION_MAP}
    base_now = base_guardrails.load_guardrails_policy(heading_corrections=guardrails.HEADING_CORRECTIONS, **policy_args)
    base_old = base_guardrails.load_guardrails_policy(heading_corrections=base_headings, **policy_args)
    current = guardrails.load_guardrails_policy(heading_corrections=guardrails.HEADING_CORRECTIONS, **policy_args)
    corrected = {k: (base_old.headings[k], v) for k, v in current.headings.items() if base_old.headings[k] != v}

    refactor_diffs, heading_diffs, unexplained = [], [], []
    for r in rows:
        new = _outcome(guardrails.pre_check(r["question"], current, corpus))
        if _outcome(base_guardrails.pre_check(r["question"], base_now, corpus)) != new:
            refactor_diffs.append(r["id"])
        old = _outcome(base_guardrails.pre_check(r["question"], base_old, corpus))
        if old != new:
            heading_diffs.append(r["id"])
            # Undo the corrected headings in the new texts: what is left must equal the old outcome.
            undone = json.loads(json.dumps(new))
            for derived, heading in corrected.values():
                quoted_new, quoted_old = f'("{heading}")', f'("{derived}")'
                if undone["refusal_text"]:
                    undone["refusal_text"] = undone["refusal_text"].replace(quoted_new, quoted_old)
                undone["notes"] = [t.replace(quoted_new, quoted_old) for t in undone["notes"]]
            reason = new["log"]["refusal"]
            notes_ok = all(ref == "reference:section_outside_v1" or a == b
                           for ref, a, b in zip(new["log"]["notes"], new["notes"], old["notes"]))
            if undone != old or not (reason == "section_outside_v1" or notes_ok):
                unexplained.append(r["id"])
    return {"questions": len(rows), "by_group": {g: sum(r["group"] == g for r in rows) for g in ("305", "v4")},
            "refactor_differences": refactor_diffs,
            "heading_rows_corrected": len(corrected),
            "heading_differences": heading_diffs, "unexplained_differences": unexplained}


def run17_records() -> tuple[list[dict], dict[str, dict]]:
    cases = json.loads(RUN17.read_text(encoding="utf-8"))["cases"]
    wanted = {c["record_id"] for c in cases}
    records = {}
    for path in sorted(glob.glob(LOG_GLOB)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if rec["record_id"] in wanted:
                        records[rec["record_id"]] = rec
    return cases, records


def _render_inputs(rec: dict, store, base_policy, corpus) -> dict:
    v = rec["verification"]
    refusal = rec["refusal"]
    if v is not None:
        status = v["summary"]["status"]
        kept = [c for c in v["claims"] if c["action"] == "kept"]
        claims = [gen.Claim(c["text"], c["labels"], c["kept_chunk_ids"]) for c in kept] if status == "answered" else []
        gaps = [g["text"] for g in (v["gaps"] or {"gaps": []})["gaps"] if g["kept"]]
        judged = [c for c in v["claims"] if c["action"] != verify.MOVED_NOT_RENDERED]
        failed = sum(c["verdict"] in ("unsupported", "contradicted") for c in judged)
        unverified = sum(c["verdict"] == "unverified" for c in judged)
        lines = []
        if status == "answered":
            if failed:
                lines.append(verify._plural(verify.FAILED_LINE, failed))
            if unverified:
                lines.append(verify._plural(verify.UNVERIFIED_LINE, unverified))
    else:
        status = refusal["status"] if refusal else rec["parsed"]["status"]
        claims, gaps, lines = [], rec["parsed"]["gaps"] if status == "not_covered" else [], []
    cited = gen.cited_in_order(claims)
    refusal_text = None
    if refusal:
        reason = refusal["reason"]
        check = guardrails.pre_check(rec["question"], base_policy, corpus) if refusal["stage"] == "pre_check" else None
        refusal_text = gen._refusal_text(reason, check)
    return {"status": status, "claims": claims, "gaps": gaps, "cited": cited, "chunks": store.chunks(cited),
            "disclosures": [Disclosure(**d) for d in rec["disclosures"]],
            "notes": [Disclosure(**n) for n in rec["notes"]], "refusal_text": refusal_text, "removal_lines": lines}


def render_replay(store, base_policy, corpus) -> dict:
    cases, records = run17_records()
    if not records:
        return {"skipped": "no tuning run 17 records in logs/generation/ (the log is git-ignored)"}
    diffs = []
    for case in cases:
        rec = records.get(case["record_id"])
        if rec is None:
            diffs.append({"id": case["id"], "problem": "record missing"})
            continue
        x = _render_inputs(rec, store, base_policy, corpus)
        text = gen.render(x["status"], x["claims"], x["gaps"], x["cited"], x["chunks"], x["disclosures"],
                          notes=x["notes"], refusal_text=x["refusal_text"], removal_lines=x["removal_lines"])
        if text != rec["rendered"]:
            diffs.append({"id": case["id"], "problem": "rendered text differs"})
    return {"records": len(records), "cases": len(cases), "identical": len(cases) - len(diffs), "differences": diffs}


def rule_check_replay(store) -> dict:
    cases, records = run17_records()
    claims = changed = 0
    ids = []
    for case in cases:
        rec = records.get(case["record_id"])
        if rec is None or rec["verification"] is None:
            continue
        for c in rec["verification"]["claims"]:
            if c["action"] != "kept":
                continue
            claims += 1
            chunks = store.chunks(c["chunk_ids"])
            now = verify.rule_check(c["text"], [chunks[x] for x in c["chunk_ids"]]).as_log()
            if any(now[k] != c["rule"][k] for k in ("passed", "tokens", "missing", "reason", "hint")):
                changed += 1
                ids.append(f"{case['id']} claim {c['no']}")
    return {"kept_claims": claims, "same": claims - changed, "changed": ids}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args(argv)
    store = gen.FileCorpusStore()
    corpus = store.sections()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base_guardrails = load_module("guardrails_base", git_show(args.base, "python/generation/guardrails.py",
                                                                  tmp / "guardrails_base.py"))
        base_headings = git_show(args.base, "data/curated/heading_corrections.yml", tmp / "heading_corrections_base.yml")
        base_policy = guardrails.load_guardrails_policy(heading_corrections=base_headings)
        out = {"base": args.base,
               "base_heading_corrections_sha256": guardrails._sha256(base_headings),
               "heading_corrections_sha256": guardrails._sha256(guardrails.HEADING_CORRECTIONS),
               "precheck": precheck_replay(base_guardrails, base_headings, corpus),
               "render": render_replay(store, base_policy, corpus),
               "rule_check": rule_check_replay(store)}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    p, r, k = out["precheck"], out["render"], out["rule_check"]
    ok = (not p["refactor_differences"] and not p["unexplained_differences"]
          and (r.get("skipped") or not r["differences"]) and not k["changed"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
