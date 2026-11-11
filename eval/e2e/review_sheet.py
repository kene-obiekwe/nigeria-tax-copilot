"""Phase 8 review sheet and blind calibration file (D-138; evaluation design, Sections 4.1 and 7.1).

    .venv\\Scripts\\python eval\\e2e\\review_sheet.py --calibration   # free: writes blind_calibration_v1.jsonl once
    .venv\\Scripts\\python eval\\e2e\\review_sheet.py --run 1         # free: the reviewer's sheet and template for run 1

**Blind calibration.** `blind_calibration_v1.jsonl` holds the 195 claims of
`eval/guardrails/verifier_calibration_v1.jsonl` in a seeded random order, with new ids (BC-001 ...),
the claim text and its cited chunk ids only. The fields `kind`, `expected`, `human_verdict`,
`mutation_type`, `base_id`, `base_text`, `change` and every original id are left out. The mapping from
blind id to original row is not stored anywhere: `blind_order(n, seed)` derives it from the seed, and
only the scorer (`score_e2e.py --calibration`) uses it.

**Run sheet.** For a complete run record (a void or incomplete record is refused: the build session
never reads a void run's answers), the sheet lists, for every answered T or N case, each shown claim
with the text of each chunk it cites, each kept gap, and each shown computed item with its source
chunks, with free automatic flags for care (numbers in a claim that no cited chunk has, section numbers
in a claim that no cited chunk names). The flags never decide. The sheet holds no pipeline verifier
output, so the reviewer records a verdict before seeing it. The template is the review file with every
verdict empty; the scorer refuses it until the reviewer has filled every entry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import run_e2e  # noqa: E402
import score_e2e  # noqa: E402
from generation import generate as gen  # noqa: E402
from generation.verify import extract_tokens, source_tokens  # noqa: E402

BLIND_SEED = 20261112
SOURCE = score_e2e.CALIBRATION_SOURCE
SECTION_REF = re.compile(r"\b[Ss]ections?\s+(\d+[A-Z]?)\b")


# Blind calibration -------------------------------------------------------------------------------


def blind_order(n: int, seed: int = BLIND_SEED) -> list[int]:
    """Blind position i holds original row `order[i]`."""
    order = list(range(n))
    random.Random(seed).shuffle(order)
    return order


def blind_id(position: int) -> str:
    return f"BC-{position + 1:03d}"


def blind_rows(rows: list[dict], seed: int = BLIND_SEED) -> list[dict]:
    """The reviewer's file: claim text and cited chunk ids, in the seeded order, under new ids."""
    return [{"id": blind_id(i), "claim": rows[j]["text"], "cited_chunk_ids": [c for _, c in rows[j]["cited"]]}
            for i, j in enumerate(blind_order(len(rows), seed))]


def blind_bytes(rows: list[dict], seed: int = BLIND_SEED) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in blind_rows(rows, seed)).encode("utf-8")


def read_source(path: Path = SOURCE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_calibration(path: Path | None = None, source: Path = SOURCE, seed: int = BLIND_SEED) -> str:
    """Writes the blind file once and returns its sha256."""
    path = path or run_e2e.PATHS["blind_calibration_v1"]
    data = blind_bytes(read_source(source), seed)
    with path.open("xb") as fh:  # raises FileExistsError: the file is written once
        fh.write(data)
    return hashlib.sha256(data).hexdigest()


# Run sheet ---------------------------------------------------------------------------------------


def flags_for(text: str, chunks: list[dict]) -> list[str]:
    """Free scans: numbers or section numbers of the claim that none of its cited chunks has."""
    out = []
    have = set()
    for chunk in chunks:
        have |= source_tokens(chunk)
    missing = sorted(extract_tokens(text) - have)
    if missing:
        out.append("figures in no cited chunk: " + ", ".join(missing))
    body = " ".join(f"{c['citation']} {c['context_header']} {c['text']}" for c in chunks)
    absent = sorted({n for n in SECTION_REF.findall(text) if not re.search(rf"\b{re.escape(n)}\b", body)})
    if absent:
        out.append("section numbers no cited chunk names: " + ", ".join(absent))
    return out


def reviewable(record: dict) -> list[dict]:
    """The answered T and N cases of a complete record."""
    score_e2e.check_record(record)
    return [r for r in record["cases"] if r["subset"] in score_e2e.IN_SCOPE and r["status"] == "answered"]


def build_template(record: dict) -> dict:
    """The review file with every verdict empty."""
    template: dict = {"run": record["run"], "reviewer": None, "claims": {}, "gaps": {}, "computations": {},
                      "adjudications": {}}
    for row in reviewable(record):
        for item in score_e2e.shown_items(row):
            if item["kind"] == "claim":
                template["claims"][item["key"]] = {"verdict": None, "reason": "", "support_not_cited": False,
                                                   "citations": {c: None for c in item["cites"]}}
            elif item["kind"] == "gap":
                template["gaps"][item["key"]] = {"states_law": None, "verdict": None, "reason": ""}
            else:
                template["computations"][item["key"]] = {"figure_correct": None, "reason": "",
                                                         "rate_citations": {c: None for c in item["cites"]}}
    return template


def chunk_block(chunk_id: str, chunks: dict[str, dict]) -> list[str]:
    chunk = chunks.get(chunk_id)
    if chunk is None:
        return [f"**`{chunk_id}`**: not in the corpus files", ""]
    return [f"**`{chunk_id}`** {chunk['citation']} ({chunk['context_header']})", "", "````text", chunk["text"],
            "````", ""]


def build_sheet(record: dict, store) -> str:
    out = [f"# Review sheet: end-to-end run {record['run']}", "",
           "Record a verdict for every item before looking at any pipeline verifier output (D-138). "
           "Fill `results/e2e_run%d_review.json` from the template. Flags are for care and never decide." % record["run"], ""]
    for row in reviewable(record):
        out += [f"## {row['id']} ({row['subset']}, {row['category']})", "", f"Question: {row['question']}", ""]
        for item in score_e2e.shown_items(row):
            chunks = store.chunks(item["cites"])
            if item["kind"] == "claim":
                out += [f"### {item['key']}", "", item["text"], ""]
                flags = flags_for(item["text"], list(chunks.values()))
            elif item["kind"] == "gap":
                out += [f"### {item['key']} (kept gap: does it state law?)", "", item["text"], ""]
                flags = []
            else:
                comp = item["comp"]
                out += [f"### {item['key']} (computed item)", "", f"{comp['what']}: {comp['result_text']}", "",
                        "Working: " + "; ".join(comp["working"]), "",
                        "Checks: " + ", ".join(f"{k} {'ok' if v not in (None, False) else 'FAILED'}"
                                               for k, v in (comp.get("checks") or {}).items()), ""]
                flags = []
            out += [f"- FLAG: {f}" for f in flags] + ([""] if flags else [])
            for chunk_id in item["cites"]:
                out += chunk_block(chunk_id, chunks)
    return "\n".join(out)


def write_run_sheet(run_no: int, ctx: run_e2e.Context | None = None, store=None) -> tuple[Path, Path]:
    ctx = ctx or run_e2e.Context()
    record = run_e2e.counted_run(run_no, ctx.results_dir)
    if record is None:
        raise run_e2e.GuardError(f"there is no complete record of run {run_no} (a void run is never reviewed)")
    sheet_path = ctx.results_dir / f"e2e_run{run_no}_review_sheet.md"
    template_path = ctx.results_dir / f"e2e_run{run_no}_review_template.json"
    text = build_sheet(record, store or gen.FileCorpusStore())
    template = build_template(record)
    with sheet_path.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    run_e2e.write_record(template_path, template)
    return sheet_path, template_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--calibration", action="store_true", help="write blind_calibration_v1.jsonl once")
    group.add_argument("--run", type=int, choices=run_e2e.RUN_NUMBERS, help="the review sheet and template of run N")
    args = ap.parse_args(argv)
    try:
        if args.calibration:
            digest = write_calibration()
            print(f"{run_e2e.PATHS['blind_calibration_v1'].name}: {len(read_source())} claims, sha256 {digest}")
        else:
            for path in write_run_sheet(args.run):
                print(f"wrote {path.name}")
    except (run_e2e.GuardError, FileExistsError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
