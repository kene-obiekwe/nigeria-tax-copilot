"""Discrepancy register (2b-6, design section 10): integrity, the known items, reproducibility from
the candidates and the review decisions, and every reading against its printed pages."""
import csv
import json
import re
from functools import lru_cache

import pytest

from ingest import build_register, register_check

TYPES = {"substantive", "wording", "numbering", "missing_content"}
GROUP_A = {"substantive", "missing_content"}

# Readings the word-by-word page check cannot match, each checked against the page image instead:
# a marginal note printed inside a word (DR-075, DR-117) and weak OCR of NRS page 11 (DR-192, DR-194).
CHECKED_ON_IMAGES = {
    ("DR-075", "cited_reading"), ("DR-075", "comparison_reading"), ("DR-117", "comparison_reading"),
    ("DR-192", "cited_reading"), ("DR-194", "cited_reading"),
}


@lru_cache(maxsize=None)
def register():
    with build_register.REGISTER.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == build_register.REGISTER_FIELDS
        return tuple(reader)


@lru_cache(maxsize=None)
def chunks():
    return tuple(json.loads(line) for line in build_register.CHUNKS.read_text(encoding="utf-8").splitlines())


def chunk_ids():
    return {c["chunk_id"] for c in chunks()}


def row(register_id):
    return next(r for r in register() if r["register_id"] == register_id)


def collapse(text):
    return " ".join(text.split())


# --- integrity ------------------------------------------------------------------------------------

def test_ids_are_unique_in_order_and_types_known():
    ids = [r["register_id"] for r in register()]
    assert all(re.fullmatch(r"DR-\d{3}", i) for i in ids)
    assert ids == sorted(set(ids))
    assert {r["type"] for r in register()} <= TYPES
    assert {r["comparison_text"] for r in register()} <= {"nass", "gombe"}


def test_every_register_id_on_a_chunk_exists():
    ids = {r["register_id"] for r in register()}
    for c in chunks():
        assert set(c["discrepancy"]["register_ids"]) <= ids, c["chunk_id"]


def test_every_row_other_than_numbering_points_to_an_existing_chunk():
    for r in register():
        if r["type"] != "numbering" or r["cited_unit"]:
            assert r["cited_unit"] in chunk_ids(), r["register_id"]
    assert row("DR-000")["cited_unit"] == ""  # the offset rule row names no chunk


def test_flags_follow_the_register():
    by_unit = {}
    for r in register():
        by_unit.setdefault(r["cited_unit"], []).append(r["register_id"])
    for c in chunks():
        expected = sorted(by_unit.get(c["chunk_id"], []))
        assert c["discrepancy"] == {"flag": bool(expected), "register_ids": expected}, c["chunk_id"]


def test_every_row_has_been_reviewed():
    unreviewed = [r["register_id"] for r in register() if not r["reviewed_by"].strip()]
    assert not unreviewed, f"{len(unreviewed)} rows wait for Kene's review (register_review.md)"
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["reviewed_on"]) for r in register())


def test_reviewed_ids_are_pinned_in_the_decisions():
    # D-18: once reviewed, every kept row carries its register_id, so ids do not move on a rebuild
    decisions = build_register.load_decisions()
    kept = [d for d in decisions["rows"].values() if not d.get("drop") and not d.get("merge_into")]
    pinned = [d.get("register_id") for d in kept + decisions["manual_rows"]]
    assert all(pinned) and sorted(pinned) == [r["register_id"] for r in register()]


def test_group_a_rows_have_a_legal_effect_note():
    assert all(r["notes"].strip() for r in register() if r["type"] in GROUP_A)


def test_substantive_cited_reading_occurs_in_the_cited_chunk():
    texts = {c["chunk_id"]: collapse(c["text"]) for c in chunks()}
    for r in register():
        if r["type"] == "substantive":
            for part in r["cited_reading"].split(" … "):
                assert collapse(part) in texts[r["cited_unit"]], (r["register_id"], part[:60])


# --- known items (task 1) -------------------------------------------------------------------------

KNOWN = [
    # (register_id, ref, type, cited_unit, comparison print, text in cited reading, text in comparison reading)
    ("DR-000", "DR-000", "numbering", "", "nass", "89.", "no counterpart"),
    ("DR-050", "DQ-01", "missing_content", "nta2025:s56", "nass", "", "reduced to 25%"),
    ("DR-150", "DQ-01", "substantive", "nta2025:s202:def:small-company", "nass", "₦50,000,000", "₦100,000,000"),
    ("DR-151", "DQ-01", "missing_content", "nta2025:s202:def:small-company", "nass", "professional services", ""),
    ("DR-178", "DQ-06", "missing_content", "nta2025:sch13:table1", "nass", "(none printed)", "All crops"),
    ("DR-053", "DQ-07", "substantive", "nta2025:s57", "nass", "₦20,000,000,000", "₦50,000,000,000"),
    ("DR-055", "DQ-07", "numbering", "nta2025:s57", "nass", "(4) For the purposes", "(3)"),
    ("DR-040", "2b-2 s27", "numbering", "nta2025:s27(5)-(6)", "nass", "", "(6) The amount of loss"),
    ("DR-067", "2b-2 s156", "missing_content", "nta2025:s156", "nass", "chargeable to VAT at 0%", ""),
    ("DR-068", "2b-2 s156", "numbering", "nta2025:s156", "nass", "(6)", "(5)"),
    ("DR-115", "2b-2 s198", "numbering", "nta2025:s198", "nass", "(3)", "(e)"),
    ("DR-206", "P1", "wording", "ntaa2025:s29", "gombe", "in amount to", "in a month amount to"),
    ("DR-220", "P3", "substantive", "ntaa2025:s39", "gombe", "or the Nigerian Naira", ""),
    ("DR-221", "P4", "substantive", "ntaa2025:s39", "gombe", "tax and payments", "relating to tax shall"),
    ("DR-229", "P5", "missing_content", "ntaa2025:s41(5)-(8)", "gombe", "", "20% of the disputed amount"),
    ("DR-267", "P6", "substantive", "ntaa2025:s147:def:small-business", "gombe", "₦100,000,000", "₦50,000,000"),
    ("DR-268", "P7", "wording", "ntaa2025:s147:def:small-business", "gombe", "with a total fixed assets", "with total fixed"),
    ("DR-190", "DQ-13", "numbering", "ntaa2025:s10", "gombe", "Chapter One", "CHAPTER TWO"),
]


@pytest.mark.parametrize("rid, ref, typ, unit, comp, in_cited, in_comp", KNOWN, ids=[k[0] for k in KNOWN])
def test_known_item_is_in_the_register(rid, ref, typ, unit, comp, in_cited, in_comp):
    r = row(rid)
    assert ref in r["refs"]
    assert (r["type"], r["cited_unit"], r["comparison_text"]) == (typ, unit, comp)
    assert in_cited in r["cited_reading"] and in_comp in r["comparison_reading"]


def test_known_items_left_out_on_purpose():
    # s13 is out of scope (no chunk); DQ-12 concerns NTAA s148, out of scope; P2 is spacing only.
    units = {r["cited_unit"] for r in register()}
    assert not any(u.startswith(("nta2025:s13", "ntaa2025:s148")) for u in units)
    assert not any("P2" in r["refs"] for r in register())


# --- reproducible from the candidates and the decisions -------------------------------------------

@lru_cache(maxsize=None)
def rebuilt():
    cands, _ = build_register.candidates()
    return cands, build_register.register(cands, build_register.load_decisions())


def read(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def test_candidates_are_reproducible():
    cands, _ = rebuilt()
    assert [{k: c[k] for k in build_register.CANDIDATE_FIELDS} for c in cands] == read(build_register.CANDIDATES)


def test_register_is_reproducible_from_the_decisions():
    _, rows = rebuilt()
    assert [{k: str(r[k]) for k in build_register.REGISTER_FIELDS} for r in rows] == [dict(r) for r in register()]


# --- readings against the printed pages -----------------------------------------------------------

def test_readings_occur_on_their_pages():
    misses = {tuple(m.split()[:2]) for m in register_check.check_readings(list(register()))}
    assert misses == CHECKED_ON_IMAGES
