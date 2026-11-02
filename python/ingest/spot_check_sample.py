"""Exit-gate spot-check sample and review pack (design section 9, Phase 2b-7a).

    .venv\\Scripts\\python python\\ingest\\spot_check_sample.py [--seed 20261018]

Draws 20 chunks from data/derived/chunks/v1_chunks.jsonl, stratified:
  - 10 NTA chunks outside schedules (at most 3 definitions),
  - 6 NTAA chunks (at most 2 definitions, at least 2 with a verified text_source),
  - 4 NTA schedule chunks (at least 1 schedule_table),
with at least 2 discrepancy-flagged chunks in each stratum. The draw is deterministic: a
random.Random(seed) over chunk ids in sorted order.

Writes docs/phase02_chunking/spot_check/ (git-ignored, regenerated): per-chunk JSON, page PNGs
(whole page plus top and bottom halves) and review_sheet.md; and the committed, blank
docs/phase02_chunking/spot_check_results.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHUNKS = ROOT / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
REGISTER = ROOT / "data" / "curated" / "discrepancy_register.csv"
DATA = ROOT / "data"
PACK = ROOT / "docs" / "phase02_chunking" / "spot_check"
RESULTS = ROOT / "docs" / "phase02_chunking" / "spot_check_results.csv"

DEFAULT_SEED = 20261018
MIN_FLAGGED = 2
VERIFIED_SOURCES = {"nrs_proofread", "gombe_verified_identical"}
TEXT_SCALE = 3.0        # render scale for the text PDFs (216 dpi)
SCAN_WIDTH = 2117       # pixel width the NRS scan pages are rendered at, as in the proofread pack

# name, size, max definitions, minimum flagged, selector
STRATA = ("nta_sections", "ntaa_sections", "nta_schedules")
SIZES = {"nta_sections": 10, "ntaa_sections": 6, "nta_schedules": 4}
MAX_DEFINITIONS = {"nta_sections": 3, "ntaa_sections": 2, "nta_schedules": 0}

CHECKS = [
    ("check1_number", "The section, subsection or schedule number is correct in the cited file."),
    ("check2_text", "The text matches the page verbatim after the normalisation rules: nothing "
                    "missing, nothing extra, no marginal-note bleed."),
    ("check3_heading", "The heading (marginal note) is correct."),
    ("check4_pages", "The page numbers are correct."),
    ("check5_chapter_part", "Chapter and Part are correct."),
    ("check6_discrepancy", "The discrepancy flag is consistent with the register."),
    ("check7_cross_refs", "Cross-references are resolved correctly."),
]
CSV_COLUMNS = ["chunk_id", "seed", "stratum"] + [c for c, _ in CHECKS] + ["reviewer", "date", "note"]


class SampleError(RuntimeError):
    pass


def load_chunks() -> list[dict]:
    with CHUNKS.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def stratum_of(c: dict) -> str | None:
    if c["act_id"] == "ntaa2025":
        return "ntaa_sections" if not c["schedule"] else None
    if c["act_id"] == "nta2025":
        return "nta_schedules" if c["schedule"] else "nta_sections"
    return None


def draw_stratum(name: str, pool: list[dict], rng: random.Random) -> list[dict]:
    """Required picks first (table, verified source, flagged), then a random fill."""
    size, max_def = SIZES[name], MAX_DEFINITIONS[name]
    chosen: list[dict] = []

    def n_def() -> int:
        return sum(c["unit_type"] == "definition" for c in chosen)

    def candidates(pred=lambda c: True) -> list[dict]:
        out = []
        for c in pool:
            if c in chosen or not pred(c):
                continue
            if c["unit_type"] == "definition" and n_def() >= max_def:
                continue
            out.append(c)
        return out

    def pick(pred=lambda c: True, why: str = "") -> None:
        cands = candidates(pred)
        if not cands:
            raise SampleError(f"stratum {name}: no candidate left for {why or 'fill'}")
        chosen.append(rng.choice(cands))

    flagged = lambda c: c["discrepancy"]["flag"]  # noqa: E731
    if name == "nta_schedules":
        pick(lambda c: c["unit_type"] == "schedule_table", "a schedule_table chunk")
    if name == "ntaa_sections":
        while sum(c["text_source"] in VERIFIED_SOURCES for c in chosen) < 2:
            pick(lambda c: c["text_source"] in VERIFIED_SOURCES, "a verified-source chunk")
    while sum(flagged(c) for c in chosen) < MIN_FLAGGED:
        pick(flagged, "a discrepancy-flagged chunk")
    while len(chosen) < size:
        pick(why="fill")
    if len(chosen) != size:
        raise SampleError(f"stratum {name}: drew {len(chosen)} chunks, expected {size}")
    return sorted(chosen, key=lambda c: c["chunk_id"])


def draw_sample(chunks: list[dict], seed: int) -> list[tuple[str, dict]]:
    """[(stratum, chunk)] in stratum order, then chunk id. Same seed and chunks, same result."""
    ordered = sorted(chunks, key=lambda c: c["chunk_id"])
    rng = random.Random(seed)
    out: list[tuple[str, dict]] = []
    for name in STRATA:
        pool = [c for c in ordered if stratum_of(c) == name]
        if len(pool) < SIZES[name]:
            raise SampleError(f"stratum {name}: only {len(pool)} chunks in the file")
        out += [(name, c) for c in draw_stratum(name, pool, rng)]
    return out


# ---------------------------------------------------------------- pack

def ensure_raw(rel: str) -> Path:
    path = DATA / rel
    if not path.is_file():
        print(f"{rel} missing; running fetch_raw.py")
        subprocess.run([sys.executable, str(ROOT / "python" / "ingest" / "fetch_raw.py")], check=True)
    if not path.is_file():
        raise SampleError(f"cited file still missing after fetch: {rel}")
    return path


def render_pages(chunk: dict, out_dir: Path, docs: dict) -> list[str]:
    import pypdfium2 as pdfium

    rel = chunk["source"]["file"]
    if rel not in docs:
        docs[rel] = pdfium.PdfDocument(str(ensure_raw(rel)))
    pdf = docs[rel]
    stem = "nrs" if rel.endswith("_nrs.pdf") else "lirs" if "lirs" in rel else Path(rel).stem
    names: list[str] = []
    for page in chunk["source"]["pages"]:
        pg = pdf[page - 1]
        width = pg.get_size()[0]
        scale = SCAN_WIDTH / width if rel.endswith("_nrs.pdf") else TEXT_SCALE
        full = pg.render(scale=scale).to_pil()
        mid = full.height // 2
        whole = full.resize((full.width // 2, full.height // 2))
        for suffix, img in (("", whole), ("_top", full.crop((0, 0, full.width, mid))),
                            ("_bottom", full.crop((0, mid, full.width, full.height)))):
            name = f"{stem}_p{page:03d}{suffix}.png"
            img.save(out_dir / name)
            names.append(name)
    return names


def slug(chunk_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", chunk_id).strip("_")


def load_register() -> dict[str, dict]:
    with REGISTER.open(encoding="utf-8", newline="") as f:
        return {r["register_id"]: r for r in csv.DictReader(f)}


def resolve_ref(ref: dict, chunks: list[dict]) -> str:
    target = ref.get("target")
    if not target:
        return "unresolved"
    act = ref.get("act_id") or ""
    m = re.match(r"^s(\d+[A-Za-z]?)(\(\w+\))?", target)
    if m:
        in_section = [c for c in chunks if c["act_id"] == act and c["section"] == m.group(1)]
        hit = None
        if m.group(2):
            hit = next((c for c in in_section if m.group(2) in c["subsections"]), None)
        hit = hit or next(iter(in_section), None)
        if hit:
            return f"{act} {target}: {hit['citation']} ({hit['heading']})"
    return f"{act} {target}"


def label(x: dict | None, key: str = "title") -> str:
    return "none" if not x else " ".join(str(v) for v in (x.get("number"), x.get(key)) if v)


def block(i: int, stratum: str, c: dict, pngs: list[str], pack_dir: str, register: dict, chunks: list[dict]) -> str:
    sch = c["schedule"]
    place = (f"Schedule: {sch['number']} {sch['title']}"
             + (f", Part {sch['part']}" if sch.get("part") else "")
             + (f", paragraph {sch['paragraph']}" if sch.get("paragraph") else "")) if sch else \
        f"Chapter: {label(c['chapter'])}; Part: {label(c['part'])}"
    lines = [f"## {i}. {c['chunk_id']}", "",
             f"- Stratum: {stratum}",
             f"- Unit type: {c['unit_type']}; text source: {c['text_source']}",
             f"- Cited file: {c['source']['file']}; PDF pages: {c['source']['pages']}; "
             f"page labels: {c['page_labels']}",
             f"- Page images (in {pack_dir}): " + ", ".join(pngs),
             f"- Chunk JSON: {slug(c['chunk_id'])}.json",
             f"- {place}",
             f"- Heading: {c['heading']} (source: {c['heading_source']})",
             f"- Citation: {c['citation']}", "", "Text:", "", "```text", c["text"], "```", "",
             f"Discrepancy flag: {c['discrepancy']['flag']}", ""]
    ids = c["discrepancy"]["register_ids"]
    if ids:
        lines += ["Register rows:", ""]
        for rid in ids:
            r = register.get(rid)
            lines.append(f"- {rid} ({r['type']}): {r['notes']}" if r else f"- {rid}: not found in the register")
        lines.append("")
    else:
        lines += ["Register rows: none", ""]
    if c["cross_refs"]:
        lines += ["Cross-references:", ""]
        lines += [f"- \"{r['raw']}\" resolves to {resolve_ref(r, chunks)}" for r in c["cross_refs"]]
        lines.append("")
    else:
        lines += ["Cross-references: none", ""]
    lines += ["Checks (Pass or Fail, with a note):", ""]
    lines += [f"{n}. {text}  Pass / Fail: ____" for n, (_, text) in enumerate(CHECKS, 1)]
    lines += ["", "Note: ", "", "---", ""]
    return "\n".join(lines)


def write_pack(sample: list[tuple[str, dict]], chunks: list[dict], seed: int) -> None:
    if PACK.exists():
        shutil.rmtree(PACK)
    PACK.mkdir(parents=True)
    register = load_register()
    docs: dict = {}
    parts = [f"# Spot-check review sheet (seed {seed})", "",
             "One block per sampled chunk. Compare the chunk with the page images, mark each of the "
             "seven checks Pass or Fail, and record the result in `../spot_check_results.csv`. "
             "Pass rule and method are in `../spot_check.md`.", "", "---", ""]
    for i, (stratum, c) in enumerate(sample, 1):
        (PACK / f"{slug(c['chunk_id'])}.json").write_text(
            json.dumps(c, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        pngs = render_pages(c, PACK, docs)
        parts.append(block(i, stratum, c, pngs, PACK.relative_to(ROOT).as_posix(), register, chunks))
    (PACK / "review_sheet.md").write_text("\n".join(parts), encoding="utf-8")


def results_hold_review() -> bool:
    """True when the results CSV exists and any check, reviewer, date or note field is filled."""
    if not RESULTS.is_file():
        return False
    with RESULTS.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    filled = [c for c, _ in CHECKS] + ["reviewer", "date", "note"]
    return any((r.get(k) or "").strip() for r in rows for k in filled)


def write_results(sample: list[tuple[str, dict]], seed: int) -> bool:
    """Write the blank template; never overwrite a file that holds review data."""
    if results_hold_review():
        return False
    with RESULTS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        for stratum, c in sample:
            w.writerow({"chunk_id": c["chunk_id"], "seed": seed, "stratum": stratum})
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    chunks = load_chunks()
    try:
        sample = draw_sample(chunks, args.seed)
        write_pack(sample, chunks, args.seed)
    except SampleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    written = write_results(sample, args.seed)
    if not written:
        print(f"{RESULTS.relative_to(ROOT)} holds review data; left untouched")
    for stratum, c in sample:
        print(f"{stratum:14} {c['chunk_id']:44} {c['unit_type']:18} flag={c['discrepancy']['flag']}")
    print(f"{len(sample)} chunks, seed {args.seed} -> {PACK.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
