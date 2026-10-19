"""NTAA re-OCR output, its parse and the Gombe alignment. Uses only committed files (no OCR stack,
no git-ignored scan)."""
import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from ingest.align_ntaa import FIELDS, OUT, align
from ingest.extract import join_lines
from ingest.parse_structure import parse_cached
from ingest.scope import NTAA_IN_SCOPE, ntaa_in_scope_from_doc

ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = ROOT / "data" / "derived" / "ocr" / "ntaa_nrs"
RAW = ROOT / "data" / "raw"
NRS = "nigeria_tax_administration_act_2025_gazette_nrs.pdf"
GOMBE = "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf"


@lru_cache(maxsize=None)
def parsed(name):
    return parse_cached(name)  # shared with the other test modules


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ocr_pages_match_their_manifest():
    manifest = json.loads((OCR_DIR / "ocr_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pages"] == 89
    assert sorted(p.name for p in OCR_DIR.glob("p*.json")) == [f"p{i:03d}.json" for i in range(1, 90)]
    for key, page in manifest["per_page"].items():
        assert sha256(OCR_DIR / f"p{key}.json") == page["json_sha256"], key
        assert sha256(OCR_DIR / f"p{key}.txt") == page["txt_sha256"], key


def test_ocr_manifest_names_the_pinned_scan():
    manifest = json.loads((OCR_DIR / "ocr_manifest.json").read_text(encoding="utf-8"))
    with (ROOT / "data" / "manifest.csv").open(newline="", encoding="utf-8") as fh:
        row = next(r for r in csv.DictReader(fh) if r["file"] == "raw/" + NRS)
    assert manifest["source_sha256"] == row["sha256"]
    assert manifest["libraries"]["rapidocr-onnxruntime"] == "1.3.24"


def test_nrs_print_parses_to_148_sections_in_order():
    act = parsed(NRS)
    assert [s.number for s in act.sections] == list(range(1, 149))
    assert all(s.toc_title for s in act.sections)


def test_nrs_s41_has_no_deposit_clause_but_gombe_does():
    nrs, gombe = parsed(NRS), parsed(GOMBE)
    assert "20%" not in nrs.section(41).text(nrs.vocab)
    assert "pay 20% of the" in gombe.section(41).text(gombe.vocab)
    assert nrs.section(41).subsection_labels == [f"({i})" for i in range(1, 9)]


def test_scope_list_matches_corpus_definition():
    doc = ROOT / "docs" / "phase01_corpus" / "v1_corpus_definition.md"
    assert ntaa_in_scope_from_doc(doc) == NTAA_IN_SCOPE
    assert len(NTAA_IN_SCOPE) == len(set(NTAA_IN_SCOPE)) == 65


def read_alignment():
    with OUT.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == FIELDS
        return list(reader)


def test_alignment_covers_every_in_scope_section_once():
    rows = read_alignment()
    assert [int(r["section"]) for r in rows] == NTAA_IN_SCOPE
    assert {r["status"] for r in rows} <= {"identical", "different"}
    by_section = {int(r["section"]): r for r in rows}
    assert by_section[41]["status"] == "different"


def test_alignment_file_is_reproducible_from_committed_ocr():
    rebuilt = [{k: str(v) for k, v in row.items()} for row in align()]
    assert rebuilt == read_alignment()


def test_identical_sections_really_are_identical():
    from ingest.align_ntaa import comparable
    nrs, gombe = parsed(NRS), parsed(GOMBE)
    for row in read_alignment():
        if row["status"] == "identical":
            n = int(row["section"])
            assert comparable(nrs.section(n).text(nrs.vocab)) == comparable(gombe.section(n).text(gombe.vocab))


def test_nrs_section_10_reads_cleanly():
    act = parsed(NRS)
    s = act.section(10)
    assert s.subsection_labels == ["(1)", "(2)", "(3)", "(4)", "(5)", "(6)"]
    first = join_lines([ln.text for ln in s.subsections[0].lines], act.vocab)
    assert first.startswith("10. (1) Where a taxable person temporarily ceases to carry on a trade")
