"""Check register readings against the printed pages, independently of the parser.

Each reading (split at " … ") must occur on its cited or comparison pages (one page either side),
read straight from the PDF with pdfplumber, or for the NRS scan from the committed re-OCR page
text. Matching is word by word, in order: the page text interleaves marginal notes with the body
and breaks words at line ends, so each reading word must follow the previous one within a few
page words, and may match two page words joined ("commence" "ment"). The naira sign is ignored
(pdfplumber gives it as "N" before the figure). For the scan, up to a tenth of the words may be
OCR misreads. Readings that are not verbatim (structure rows, table cells joined row by row) are
skipped.
"""
from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OCR = ROOT / "data" / "derived" / "ocr" / "ntaa_nrs"
FILES = {
    ("nta2025", "cited"): "nigeria_tax_act_2025_gazette_lirs.pdf",
    ("nta2025", "nass"): "nigeria_tax_act_2025_nass.pdf",
    ("ntaa2025", "cited"): "nigeria_tax_administration_act_2025_gazette_nrs.pdf",
    ("ntaa2025", "gombe"): "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf",
    ("ntaa2025", "nass"): "nigeria_tax_administration_act_2025_nass.pdf",
}
NOT_VERBATIM = re.compile(r"\((?:cells as printed|no counterpart|none printed)|^s\d+ sits|printed before s\d+")
GAP = 8  # page words allowed between two consecutive reading words (marginal notes, labels)


def words(text: str) -> list[str]:
    text = re.sub(r"[N₦](?=\s?\d)", " ", text)
    return re.findall(r"[a-z0-9]+", text.lower())


# The running head ("A 400  2025 No. 7  Nigeria Tax Act, 2025"), which would otherwise sit
# between the last words of one page and the first of the next.
RUNNING_HEAD = re.compile(r"2025\s?No\.?\s?[57]\b|^\s*A\s?\d{3}\s*$", re.I)


def _body(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not RUNNING_HEAD.search(ln))


@lru_cache(maxsize=None)
def _pdf(file: str):
    return pdfplumber.open(str(RAW / file))  # kept open for the process; read-only


@lru_cache(maxsize=None)
def page_words(file: str, page: int) -> tuple[str, ...]:
    if file.endswith("_nrs.pdf"):
        path = OCR / f"p{page:03d}.txt"
        return tuple(words(_body(path.read_text(encoding="utf-8")))) if path.is_file() else ()
    pdf = _pdf(file)
    if not 1 <= page <= len(pdf.pages):
        return ()
    return tuple(words(_body(pdf.pages[page - 1].extract_text() or "")))


def _match_from(reading: list[str], page: tuple[str, ...], start: int, misses_allowed: int) -> bool:
    k, misses = start, 0
    for w in reading:
        hit = None
        for j in range(k, min(len(page), k + GAP + 1)):
            if page[j] == w:
                hit = j + 1
                break
            if j + 1 < len(page) and page[j] + page[j + 1] == w:
                hit = j + 2
                break
            # a reading word split differently from the page ("amonth" / "a" "month")
            if w.startswith(page[j]) and j + 1 < len(page) and w == page[j] + page[j + 1][: len(w) - len(page[j])]:
                hit = j + 2
                break
        if hit is None:
            misses += 1
            if misses > misses_allowed:
                return False
            continue
        k = hit
    return True


def _scan_match(part: str, page_letters: str) -> bool:
    """The re-OCR runs words together ("theNigeriaTaxAct"), so a scan reading is matched on its
    letters and digits: at least 90% of them must align within a window of the page."""
    k = "".join(words(part))
    if not k:
        return True
    if k in page_letters:
        return True
    sm = difflib.SequenceMatcher(None, k, page_letters, autojunk=False)
    m = sm.find_longest_match(0, len(k), 0, len(page_letters))
    lo = max(0, m.b - m.a - 20)
    window = page_letters[lo:lo + len(k) + 40]
    blocks = difflib.SequenceMatcher(None, k, window, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks) >= 0.9 * len(k)


def found(reading: str, file: str, pages: list[int]) -> bool:
    if not reading or NOT_VERBATIM.search(reading):
        return True
    span = sorted({p + d for p in pages for d in (-1, 0, 1)})
    page = tuple(w for p in span for w in page_words(file, p))
    for part in reading.split(" … "):
        if file.endswith("_nrs.pdf"):
            if not _scan_match(part, "".join(page)):
                return False
            continue
        rw = words(part)
        if not rw:
            continue
        starts = [i for i, w in enumerate(page) if w == rw[0] or rw[0].startswith(w)]
        if not any(_match_from(rw, page, s, 0) for s in starts):
            return False
    return True


def check_readings(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        for side, field, pages_field in (("cited", "cited_reading", "cited_pages"),
                                         (r["comparison_text"], "comparison_reading", "comparison_pages")):
            pages = [int(p) for p in r[pages_field].split()]
            if not pages:
                continue
            file = FILES[(r["act_id"], side)]
            if not found(r[field], file, pages):
                out.append(f"{r['register_id']} {field} not on {file} pages {pages}: {r[field][:80]!r}")
    return out
