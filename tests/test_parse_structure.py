import re
from functools import lru_cache
from pathlib import Path

import pytest

from ingest.extract import join_lines
from ingest.parse_structure import FILE_PROFILES, parse

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
NTA_GAZETTE = "nigeria_tax_act_2025_gazette_lirs.pdf"
NTA_NASS = "nigeria_tax_act_2025_nass.pdf"
NTAA_GOMBE = "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf"
NTAA_NASS = "nigeria_tax_administration_act_2025_nass.pdf"
NTAA_NRS = "nigeria_tax_administration_act_2025_gazette_nrs.pdf"  # parsed from committed OCR output


@lru_cache(maxsize=None)
def parsed(name):
    return parse(RAW / name)


@pytest.mark.parametrize("name, expected", [
    (NTA_GAZETTE, 203), (NTA_NASS, 202), (NTAA_GOMBE, 148), (NTAA_NASS, 148), (NTAA_NRS, 148),
])
def test_section_counts_in_order(name, expected):
    act = parsed(name)
    assert [s.number for s in act.sections] == list(range(1, expected + 1))
    assert not any(a.startswith("found ") for a in act.anomalies)


@pytest.mark.parametrize("name", list(FILE_PROFILES))
def test_arrangement_of_sections_fully_read(name):
    act = parsed(name)
    assert all(s.toc_title for s in act.sections)


# Anomalies are pinned so that any new parser behaviour has to be looked at and explained in
# docs/phase02_chunking/build_notes.md.
EXPECTED_ANOMALIES = {
    NTA_GAZETTE: [
        "33 page(s) set sideways (landscape tables) not parsed: 172-202, 214-215",
        "s57: subsection (4) follows (2) (page 42)",
        "s140: no marginal-note heading level with the section (page 85); heading taken from the arrangement of sections",
        "schedule heading(s) not found: Ninth, Tenth (their pages are absorbed by the preceding schedule or skipped as sideways pages)",
    ],
    NTA_NASS: [
        "schedule heading(s) not found: Ninth, Tenth (their pages are absorbed by the preceding schedule or skipped as sideways pages)",
    ],
    NTAA_GOMBE: [
        "1 page(s) set sideways (landscape tables) not parsed: 89",
        "s34: no marginal-note heading level with the section (page 25); heading taken from the arrangement of sections",
        "s79: heading 'Power of the President to designate relevant authority' does not match the arrangement of sections 'Taxation of digital and virtual transactions or assets'",
    ],
    NTAA_NASS: [
        "s66: no marginal-note heading level with the section (page 43); heading taken from the arrangement of sections",
        "s67: no marginal-note heading level with the section (page 43); heading taken from the arrangement of sections",
        "s95: no marginal-note heading level with the section (page 54); heading taken from the arrangement of sections",
        "s145: heading 'Supemacy of this Act' does not match the arrangement of sections 'Conflict with other laws'",
    ],
    NTAA_NRS: [
        # OCR missed the line holding s12(4) and s62(4) (both out of scope); see build notes.
        "s12: subsection (5) follows (3) (page 13)",
        "s62: subsection (5) follows (3) (page 39)",
        "s34: no marginal-note heading level with the section (page 23); heading taken from the arrangement of sections",
        "s79: heading 'Power of the President to designate relevant authority' does not match the arrangement of sections 'Taxation of digital and virtual transactions or assets'",
    ],
}


@pytest.mark.parametrize("name", list(FILE_PROFILES))
def test_anomalies_are_exactly_the_known_ones(name):
    assert sorted(parsed(name).anomalies) == sorted(EXPECTED_ANOMALIES[name])


# --- golden text from the cited Nigeria Tax Act (design section 1, normalised) ----------------

def test_s56_golden():
    act = parsed(NTA_GAZETTE)
    s = act.section(56)
    assert s.heading == "Rates of tax for companies"
    assert s.chapter == ("2", "TAXATION OF INCOME OF PERSONS")
    assert s.part == ("IX", "RATES OF TAX")
    assert s.pages == [42]
    assert s.lines[0].page_label == "A 426"
    assert s.text(act.vocab) == (
        "56. Tax shall be levied, for each year of assessment in respect of total\n"
        "profits of every company, in the case of –\n"
        "(a) a small company, at 0%; and\n"
        "(b) any other company, at the rate of 30 per cent from the commencement of this Act."
    )
    assert [p.label for p in s.paragraphs] == ["(a)", "(b)"]


def test_s148_golden():
    act = parsed(NTA_GAZETTE)
    s = act.section(148)
    assert s.heading == "Rate of VAT"
    assert s.chapter[0] == "6" and s.chapter[1] == "VALUE ADDED TAX"
    assert s.pages == [88]
    assert s.text(act.vocab) == (
        "148. Subject to the provisions of Part IV of Chapter Eight of this Act,\n"
        "VAT shall be charged on the value of all taxable supplies at the rate of 7.5%."
    )


def test_s202_small_company_definition_golden():
    act = parsed(NTA_GAZETTE)
    s = act.section(202)
    assert s.heading == "General interpretation"
    definition = next(d for d in s.definitions if d.label == "small company")
    assert join_lines([ln.text for ln in definition.lines], act.vocab) == (
        "“small company” means a company that earns gross turnover of\n"
        "₦50,000,000 or less per annum with total fixed assets not exceeding\n"
        "₦250,000,000, provided that any business providing professional services shall\n"
        "not be classified as a small company;"
    ).replace("“", '"').replace("”", '"')
    assert [ln.page for ln in definition.lines] == [126, 126, 126, 126]


def test_s57_labels_kept_as_printed():
    # DQ-07: the gazette prints (1), (2), (4) with no (3).
    assert parsed(NTA_GAZETTE).section(57).subsection_labels == ["(1)", "(2)", "(4)"]


def test_national_assembly_section_148_without_full_stop():
    s = parsed(NTA_NASS).section(148)
    assert s.lines[0].text.startswith("148—(1)")
    assert s.subsection_labels[0] == "(1)"


def test_numbering_offset_between_gazette_and_national_assembly():
    gaz, nass = parsed(NTA_GAZETTE), parsed(NTA_NASS)
    assert gaz.section(89).heading == "Petroleum royalty"
    assert nass.section(88).toc_title.startswith("Fiscal stabilisation")
    assert gaz.section(148).heading == nass.section(147).heading == "Rate of VAT"


# --- integrity checks over every file ---------------------------------------------------------

MARGIN_BLEED = re.compile(r"^[A-Z][a-z]+(?: [a-z]+){0,4} \d{1,3}\s?\.\s?[—–-]?\s?(?:\(1\)|[A-Z])")


def test_bleed_pattern_catches_merged_margin_text():
    assert MARGIN_BLEED.match("Rates of tax 56. Tax shall be levied, for each year")
    assert MARGIN_BLEED.match("Manner of 124.—(1) Duties payable on any instrument")
    assert not MARGIN_BLEED.match("56. Tax shall be levied, for each year")


@pytest.mark.parametrize("name", list(FILE_PROFILES))
def test_no_marginal_note_bleed(name):
    act = parsed(name)
    bad = [ln.text for s in act.sections for ln in s.lines if MARGIN_BLEED.match(ln.text)]
    assert bad == []
    if name == NTA_GAZETTE:
        assert not any(ln.text.startswith("Rates of tax") for s in act.sections for ln in s.lines)


@pytest.mark.parametrize("name", list(FILE_PROFILES))
def test_no_unconverted_naira(name):
    act = parsed(name)
    lines = [ln.text for s in act.sections for ln in s.lines]
    lines += [ln.text for sch in act.schedules for ln in sch.lines]
    assert [t for t in lines if re.search(r"(?<![A-Za-z0-9])N\s?\d", t)] == []


@pytest.mark.parametrize("name", list(FILE_PROFILES))
def test_every_section_has_a_heading(name):
    act = parsed(name)
    assert all(s.heading for s in act.sections)


def test_in_scope_schedules_found_in_cited_nta():
    act = parsed(NTA_GAZETTE)
    titles = {
        "First": "CAPITAL ALLOWANCE",
        "Third": "DEDUCTIBLE INTEREST",
        "Eleventh": "ITEMS ON WHICH TAX IS SUSPENDED UNDER THIS ACT",
        "Thirteenth": "EXEMPTION FOR AGRICULTURAL BUSINESSES",
    }
    for ordinal, title in titles.items():
        schedule = act.schedule(ordinal)
        assert schedule is not None, ordinal
        assert any(title in ln.text for ln in schedule.lines), ordinal
    assert act.schedule("Eleventh").pages == [203]
    assert act.schedule("Thirteenth").pages == [209]


def test_thirteenth_schedule_prints_no_products():
    # DQ-06: the cited copy has the column heading but no product text.
    lines = [ln.text for ln in parsed(NTA_GAZETTE).schedule("Thirteenth").lines]
    assert "SUB-SECTOR ACTIVITY EXEMPTED PRODUCTS" in lines
    assert not any("milk" in t.lower() or "chocolate" in t.lower() for t in lines)


def test_gombe_ntaa_s41_has_the_deposit_subsection():
    act = parsed(NTAA_GOMBE)
    s = act.section(41)
    assert s.subsection_labels == [f"({i})" for i in range(1, 10)]
    deposit = next(u for u in s.subsections if u.label == "(8)")
    assert "pay 20% of the" in join_lines([ln.text for ln in deposit.lines], act.vocab)


def test_wrapped_subsection_reference_is_not_a_subsection():
    # NTAA s65 contains a line starting "(1)(b) and (c) shall be compound interest".
    s = parsed(NTAA_GOMBE).section(65)
    assert s.subsection_labels == ["(1)", "(2)", "(3)", "(4)"]


def test_cross_references_are_captured():
    refs = parsed(NTA_GAZETTE).section(59).cross_refs
    assert "Fourteenth Schedule" in refs
    assert any(r.startswith("subsection (3)") for r in refs)
