"""Align the NRS print (cited, from OCR) with the Gombe print, section by section (design section 2).

For every in-scope NTAA section the two texts are compared after normalisation: NFKC, lower
case, letters, digits and "%" only, with OCR confusions in subsection labels mapped first.
Equality is strict; there is no similarity threshold. Nothing is proofread here (step 2b-4).

Runs in the main venv and reads only committed OCR output:

    .venv\\Scripts\\python python\\ingest\\align_ntaa.py

Writes data/derived/ntaa_alignment.csv.
"""
from __future__ import annotations

import csv
import difflib
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from ingest.parse_structure import parse_cached  # noqa: E402
from ingest.scope import NTAA_IN_SCOPE  # noqa: E402

RAW = ROOT / "data" / "raw"
NRS = "nigeria_tax_administration_act_2025_gazette_nrs.pdf"
GOMBE = "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf"
OUT = ROOT / "data" / "derived" / "ntaa_alignment.csv"
FIELDS = ["section", "status", "similarity", "diff_summary", "nrs_pages", "gombe_pages"]

# Mappings applied to BOTH texts before the strict comparison, so they cannot hide a real
# difference: they only make two prints that read the same compare equal.
# (design section 2, step 3) "(l)" or "(I)" anywhere, and "(i)" before a capital, read as "(1)".
LABEL_L = re.compile(r"\(\s*[lI]\s*\)")
LABEL_I = re.compile(r"\(\s*i\s*\)(?=\s*[A-Z])")
# (added in 2b-3) the naira sign: the OCR keeps it as a letter "N" when the space before it is
# lost ("ofN5,000,000"); the text PDFs give "₦". Neither is a letter of the text.
NAIRA = re.compile(r"[₦N](?=\s?\d)")
# (added in 2b-3) "o" for "0" next to a digit or comma inside a figure ("N1,0oo.oo0"). It is
# applied after whitespace is removed, because the OCR drops spaces ("to10" for "to 10") and
# the mapping must treat both texts the same. Applied repeatedly so runs are all mapped.
LETTER_O_IN_FIGURE = re.compile(r"(?<=[\d,])o|o(?=[\d,])")


def comparable(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = NAIRA.sub("", LABEL_I.sub("(1)", LABEL_L.sub("(1)", text)))
    text = re.sub(r"[^a-z0-9%,]", "", text.lower())
    while LETTER_O_IN_FIGURE.search(text):
        text = LETTER_O_IN_FIGURE.sub("0", text)
    return text.replace(",", "")


def diff_summary(a: str, b: str, limit: int = 3) -> str:
    """Up to `limit` differing spans, as 'gombe[...] / nrs[...]'."""
    spans = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            spans.append(f"gombe[{a[i1:i2][:40]}] / nrs[{b[j1:j2][:40]}]")
    more = f" (+{len(spans) - limit} more)" if len(spans) > limit else ""
    return "; ".join(spans[:limit]) + more


def align() -> list[dict]:
    nrs, gombe = parse_cached(NRS), parse_cached(GOMBE)
    rows = []
    for number in NTAA_IN_SCOPE:
        n_sec, g_sec = nrs.section(number), gombe.section(number)
        a, b = comparable(g_sec.text(gombe.vocab)), comparable(n_sec.text(nrs.vocab))
        same = a == b
        rows.append({
            "section": number,
            "status": "identical" if same else "different",
            "similarity": f"{difflib.SequenceMatcher(None, a, b, autojunk=False).ratio():.4f}",
            "diff_summary": "" if same else diff_summary(a, b),
            "nrs_pages": " ".join(str(p) for p in n_sec.pages),
            "gombe_pages": " ".join(str(p) for p in g_sec.pages),
        })
    return rows


def main() -> int:
    rows = align()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    different = [r["section"] for r in rows if r["status"] == "different"]
    print(f"{len(rows)} in-scope sections: {len(rows) - len(different)} identical, "
          f"{len(different)} different")
    print("different:", ", ".join(str(s) for s in different))
    return 0


if __name__ == "__main__":
    sys.exit(main())
