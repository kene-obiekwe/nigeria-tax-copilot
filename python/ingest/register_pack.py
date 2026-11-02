"""Page crops for the register review (group A: substantive and missing_content rows).

    .venv\\Scripts\\python python\\ingest\\register_pack.py

Writes PNGs to docs/phase02_chunking/register_pack/ (git-ignored): for each group A row, one
crop of the cited page and one of the comparison page, each centred on the reading. The crop
is found from the reading's first words (pdfplumber words for the text PDFs, the re-OCR boxes
for the NRS scan); when they cannot be found the whole page is rendered.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from ingest.register_check import FILES, RAW, OCR, words  # noqa: E402

REGISTER = ROOT / "data" / "curated" / "discrepancy_register.csv"
OUT = ROOT / "docs" / "phase02_chunking" / "register_pack"
GROUP_A = {"substantive", "missing_content"}
SCALE = 2.0      # render scale for the text PDFs (144 dpi)
BAND = 260       # points of page height shown around the reading
SCAN_HEIGHT = 1600  # pixel height the NRS scan pages are rendered at


def locate_text_pdf(path: Path, page: int, reading: str) -> tuple[float, float] | None:
    """(top, bottom) in points of the first few reading words on the page."""
    target = words(reading)[:6]
    if not target:
        return None
    with pdfplumber.open(str(path)) as pdf:
        pg = pdf.pages[page - 1]
        ws = pg.extract_words()
        norm = [(words(w["text"]) or [""])[0] for w in ws]
        for i in range(len(ws)):
            if norm[i] == target[0] and [n for n in norm[i:i + len(target) + 3] if n][:len(target)] == target:
                return ws[i]["top"], ws[min(i + 40, len(ws) - 1)]["bottom"]
    return None


def locate_scan(page: int, reading: str) -> tuple[float, float, float] | None:
    """(top, bottom, page height) in OCR pixels of the reading's first words on the scan."""
    path = OCR / f"p{page:03d}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    key = "".join(words(reading))[:30]
    if not key:
        return None
    boxes = sorted(data["boxes"], key=lambda b: (b["cy"], b["x0"]))
    for i, b in enumerate(boxes):
        line = "".join(words(" ".join(x["text"] for x in boxes[i:i + 3])))
        if key[:15] and key[:15] in line:
            return b["cy"] - 60, b["cy"] + 700, data["height"]
    return None


def render(file: str, page: int, reading: str, out: Path) -> None:
    pdf = pdfium.PdfDocument(str(RAW / file))
    pg = pdf[page - 1]
    w, h = pg.get_size()
    if file.endswith("_nrs.pdf"):
        loc = locate_scan(page, reading)
        frac = (max(0, loc[0] / loc[2]), min(1, loc[1] / loc[2])) if loc else (0, 1)
    else:
        loc = locate_text_pdf(RAW / file, page, reading)
        frac = (max(0, (loc[0] - 30) / h), min(1, (loc[0] + BAND) / h)) if loc else (0, 1)
    # the scan's pages are small in points (147 x 202), so it is scaled to a fixed pixel height
    scale = SCALE if not file.endswith("_nrs.pdf") else SCAN_HEIGHT / h
    img = pg.render(scale=scale).to_pil()
    top, bottom = int(frac[0] * img.height), int(frac[1] * img.height)
    bottom = min(img.height, max(bottom, top + 200))
    img.crop((0, top, img.width, bottom)).save(out)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    rows = [r for r in csv.DictReader(REGISTER.open(encoding="utf-8")) if r["type"] in GROUP_A]
    n = 0
    for r in rows:
        for side, reading, pages in (("cited", r["cited_reading"], r["cited_pages"]),
                                     (r["comparison_text"], r["comparison_reading"], r["comparison_pages"])):
            if not pages.split():
                continue
            page = int(pages.split()[0])
            name = f"{r['register_id']}_{'cited' if side == 'cited' else side}_p{page:03d}.png"
            render(FILES[(r["act_id"], side)], page, re.split(r" … ", reading)[0], OUT / name)
            n += 1
    print(f"{n} crops for {len(rows)} group A rows -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
