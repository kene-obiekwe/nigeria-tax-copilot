"""Section map between the cited texts and the comparison prints (chunking design, section 3).

NTA: gazette n = National Assembly n for 1 to 88; gazette 89 ("Petroleum royalty") has no
counterpart; gazette n = National Assembly n - 1 for 90 to 203.
NTAA: the NRS print (cited), the Gombe print and the National Assembly print all number their
sections 1 to 148, so the map is the identity.

For every pair the marginal-note heading of each side and their similarity (letters only) are
recorded, so a mis-mapping shows up as a low similarity.

    .venv\\Scripts\\python python\\ingest\\section_map.py

writes data/derived/section_map.csv.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from ingest.parse_structure import _similar, parse_cached  # noqa: E402

OUT = ROOT / "data" / "derived" / "section_map.csv"
FIELDS = ["act_id", "cited_print", "cited_section", "cited_heading", "comparison_print",
          "comparison_section", "comparison_heading", "title_similarity"]

NTA_GAZETTE = "nigeria_tax_act_2025_gazette_lirs.pdf"
NTA_NASS = "nigeria_tax_act_2025_nass.pdf"
NTAA_NRS = "nigeria_tax_administration_act_2025_gazette_nrs.pdf"
NTAA_GOMBE = "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf"
NTAA_NASS = "nigeria_tax_administration_act_2025_nass.pdf"
PRINT_IDS = {NTA_GAZETTE: "gazette", NTA_NASS: "nass", NTAA_NRS: "nrs", NTAA_GOMBE: "gombe",
             NTAA_NASS: "nass"}


def nta_na_section(gazette_section: int) -> int | None:
    """National Assembly number for a gazette NTA section (None for gazette 89)."""
    if gazette_section <= 88:
        return gazette_section
    if gazette_section == 89:
        return None
    return gazette_section - 1


def build() -> list[dict]:
    rows: list[dict] = []
    gaz, nass = parse_cached(NTA_GAZETTE), parse_cached(NTA_NASS)
    for sec in gaz.sections:
        other = nta_na_section(sec.number)
        o_sec = nass.section(other) if other else None
        rows.append(_row("nta2025", NTA_GAZETTE, sec, NTA_NASS, o_sec))
    nrs = parse_cached(NTAA_NRS)
    for comparison in (NTAA_GOMBE, NTAA_NASS):
        other_act = parse_cached(comparison)
        for sec in nrs.sections:
            rows.append(_row("ntaa2025", NTAA_NRS, sec, comparison, other_act.section(sec.number)))
    return rows


def _row(act_id, cited_file, sec, comparison_file, other) -> dict:
    return {
        "act_id": act_id,
        "cited_print": PRINT_IDS[cited_file],
        "cited_section": sec.number,
        "cited_heading": sec.heading or "",
        "comparison_print": PRINT_IDS[comparison_file],
        "comparison_section": other.number if other else "",
        "comparison_heading": (other.heading or "") if other else "",
        "title_similarity": f"{_similar(sec.heading or '', other.heading or ''):.3f}" if other else "",
    }


def write(rows: list[dict], path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    rows = build()
    write(rows)
    low = [r for r in rows if r["title_similarity"] and float(r["title_similarity"]) < 0.8]
    print(f"{len(rows)} pairs written to {OUT.relative_to(ROOT)}; {len(low)} with title similarity < 0.8")
    for r in low:
        print(f"  {r['act_id']} {r['cited_print']} s{r['cited_section']} {r['cited_heading']!r} / "
              f"{r['comparison_print']} s{r['comparison_section']} {r['comparison_heading']!r} "
              f"({r['title_similarity']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
