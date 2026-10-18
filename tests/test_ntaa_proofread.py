"""Proofread NRS texts for the NTAA sections that differ from the Gombe print (2b-4), and a guard
against lines the re-OCR missed on in-scope pages."""
import csv
import re
from functools import lru_cache
from pathlib import Path

import pdfplumber
import pytest

from ingest.parse_structure import parse

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
NRS_SCAN = RAW / "nigeria_tax_administration_act_2025_gazette_nrs.pdf"
GOMBE = RAW / "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf"
ALIGNMENT = ROOT / "data" / "derived" / "ntaa_alignment.csv"
OCR_DIR = ROOT / "data" / "derived" / "ocr" / "ntaa_nrs"
PROOF_DIR = ROOT / "data" / "curated" / "ntaa_nrs_proofread"
INDEX = PROOF_DIR / "index.csv"
INDEX_FIELDS = ["section", "status", "reviewed_by", "reviewed_on", "nrs_pages", "heading", "notes"]

# Words the guard may ignore, with the reason. Each entry is (NRS page, word).
MISSED_LINE_ALLOWLIST = {
    # An old-layer misread of "through" in s121. Kept as specified in the 2b-4 task; it is not
    # triggered by pdfplumber's reading of the old layer, which gives "through".
    (58, "though"),
}


def alignment_rows():
    with ALIGNMENT.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def index_rows():
    with INDEX.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert set(INDEX_FIELDS) <= set(reader.fieldnames)
        return {int(r["section"]): r for r in reader}


@lru_cache(maxsize=None)
def gombe():
    return parse(GOMBE)


def different_sections():
    return [int(r["section"]) for r in alignment_rows() if r["status"] == "different"]


def test_every_different_section_has_a_proofread_file_and_index_row():
    index = index_rows()
    for n in different_sections():
        assert (PROOF_DIR / f"s{n:03d}.txt").is_file(), f"s{n}: no proofread file"
        assert n in index, f"s{n}: no row in index.csv"


@pytest.mark.parametrize("n", different_sections())
def test_proofread_section_has_been_reviewed(n):
    row = index_rows()[n]
    assert row["status"] == "reviewed", f"s{n}: status is {row['status']!r}; waiting for Kene's review"
    assert row["reviewed_by"].strip(), f"s{n}: reviewed_by is empty"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["reviewed_on"]), f"s{n}: reviewed_on is not a date"


@pytest.mark.parametrize("n", different_sections())
def test_proofread_file_is_utf8_lf_and_starts_with_its_number(n):
    data = (PROOF_DIR / f"s{n:03d}.txt").read_bytes()
    assert b"\r" not in data
    text = data.decode("utf-8")
    assert re.match(rf"{n}\.\s", text), f"s{n}: file does not start with '{n}.'"


def labels_in(text: str) -> list[str]:
    """Subsection labels at line start (including one after the section number), as printed."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(?:\d{1,3}\.\s?(?:[—–-]\s?)?)?\((\d{1,2}[A-Z]?)\)\s", line)
        if m:
            out.append(f"({m.group(1)})")
    return out


EXPECTED_LABELS = {  # as printed in the NRS print (checked against the page images)
    11: ["(1)", "(2)", "(3)", "(4)", "(5)", "(6)"],
    29: [],
    34: ["(1)", "(2)", "(3)", "(4)", "(5)", "(6)"],
    39: ["(1)", "(2)", "(3)"],
    41: ["(1)", "(2)", "(3)", "(4)", "(5)", "(6)", "(7)", "(8)"],
    147: [],
}


@pytest.mark.parametrize("n", sorted(EXPECTED_LABELS))
def test_proofread_subsection_labels_in_order(n):
    text = (PROOF_DIR / f"s{n:03d}.txt").read_text(encoding="utf-8")
    assert labels_in(text) == EXPECTED_LABELS[n]


def test_expected_labels_cover_every_different_section():
    assert sorted(EXPECTED_LABELS) == sorted(different_sections())


def test_proofread_s41_has_no_deposit_clause():
    text = (PROOF_DIR / "s041.txt").read_text(encoding="utf-8")
    assert "20%" not in text and "disputed amount" not in text
    assert text.rstrip().endswith("Appeal shall be to the Supreme Court.")


def test_proofread_s147_small_business_reads_as_printed():
    text = (PROOF_DIR / "s147.txt").read_text(encoding="utf-8")
    assert '"small business" means a business that earns gross turnover of\n₦100,000,000 or less' in text


def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def test_no_line_missed_by_the_reocr_on_in_scope_pages():
    """Every word of 6+ letters in the scan's original text layer that also occurs in the Gombe
    print must appear in the re-OCR of that page or a neighbouring page."""
    if not NRS_SCAN.is_file():
        pytest.fail(f"{NRS_SCAN.name} is missing; run .venv\\Scripts\\python python\\ingest\\fetch_raw.py")
    pages = sorted({int(p) for r in alignment_rows() for p in r["nrs_pages"].split()})
    gombe_words = {w for s in gombe().sections for ln in s.lines for w in re.findall(r"[a-z]+", ln.text.lower())}

    def ocr(page):
        path = OCR_DIR / f"p{page:03d}.txt"
        return letters(path.read_text(encoding="utf-8")) if path.is_file() else ""

    missed = []
    with pdfplumber.open(str(NRS_SCAN)) as pdf:
        for page in pages:
            layer = (pdf.pages[page - 1].extract_text() or "").lower()
            words = {w for w in re.findall(r"[a-z]+", layer) if len(w) >= 6 and w in gombe_words}
            nearby = "|".join(ocr(p) for p in (page - 1, page, page + 1))
            missed += [(page, w) for w in sorted(words) if w not in nearby and (page, w) not in MISSED_LINE_ALLOWLIST]
    assert missed == [], f"words in the old text layer but not in the re-OCR: {missed}"
