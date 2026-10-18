import re
from pathlib import Path

import pytest

from ingest.extract import extract_pages, hyphen_vocabulary, join_lines, normalise_line

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
NTA_GAZETTE = RAW / "nigeria_tax_act_2025_gazette_lirs.pdf"


@pytest.mark.parametrize("raw, expected", [
    ("N 50,000,000 or less", "₦50,000,000 or less"),
    ("not exceeding N250,000,000,", "not exceeding ₦250,000,000,"),
    ("Act No. 8, 2019", "Act No. 8, 2019"),  # "No." is not a naira amount
    ("PART IX � RATES OF TAX", "PART IX – RATES OF TAX"),
    ("（1）  Where   a", "(1) Where a"),  # NFKC folds full-width brackets; spaces collapse
    ("  (a) a small company, at 0%; and ", "(a) a small company, at 0%; and"),
])
def test_normalise_line(raw, expected):
    assert normalise_line(raw) == expected


def test_join_lines_rejoins_typesetting_hyphens_only():
    vocab = hyphen_vocabulary(["a non-resident company", "commencement of this Act"])
    assert join_lines(["from the commence-", "ment of this Act."], vocab) == "from the commencement of this Act."
    assert join_lines(["a non-", "resident person"], vocab) == "a non-resident person"
    assert join_lines(["first line;", "second line"], vocab) == "first line;\nsecond line"


@pytest.fixture(scope="module")
def page42():
    (page,) = extract_pages(NTA_GAZETTE, only=[42])
    return page


def test_page_label_and_header(page42):
    assert page42.index == 42
    assert page42.label == "A 426"
    assert page42.header.startswith("A 426")


def test_margin_notes_are_split_from_body(page42):
    margin = [line.text for line in page42.margin]
    assert margin[:3] == ["Rates of tax", "for compa-", "nies"]
    body = [line.text for line in page42.body]
    assert "56. Tax shall be levied, for each year of assessment in respect of total" in body
    assert not any(t.startswith(("Rates of tax", "Effective Tax")) for t in body)


def test_small_caps_heading_stays_in_body(page42):
    assert "PART IX – RATES OF TAX" in [line.text for line in page42.body]


def test_no_unconverted_naira_on_definition_page():
    (page,) = extract_pages(NTA_GAZETTE, only=[126])
    text = page.body_text
    assert "₦50,000,000 or less per annum" in text
    assert not re.search(r"(?<![A-Za-z0-9])N\s?\d", text)


def test_sideways_table_pages_are_flagged():
    flags = {p.index: p.rotated for p in extract_pages(NTA_GAZETTE, only=[42, 172, 203])}
    assert flags == {42: False, 172: True, 203: False}
