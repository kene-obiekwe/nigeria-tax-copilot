"""v1 chunk build (2b-5): coverage, order, determinism, token caps, golden text, known anomalies,
the section map and the table word check. The build runs once per test session, in process,
on the shared parse cache."""
import csv
import hashlib
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pytest

from ingest import build_chunks, chunker, section_map
from ingest.parse_structure import parse_cached, parse_section_text
from ingest.scope import NTA_IN_SCOPE, NTA_SCHEDULES_IN_SCOPE, NTAA_IN_SCOPE, nta_in_scope_from_doc

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).with_name("data") / "golden_chunks.json"
BGE_SHA256 = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"

FIELDS = {
    "chunk_id", "citation", "act_id", "act_title", "unit_type", "chapter", "part", "schedule", "section",
    "subsections", "na_section", "heading", "heading_source", "context_header", "text", "text_source",
    "source", "page_labels", "cross_refs", "discrepancy", "corpus_snapshot", "edition_date",
    "tokens_cl100k", "tokens_with_header", "tokens_bge_with_header", "text_sha256",
}
UNIT_TYPES = {"section", "subsection_group", "paragraph_group", "definition", "schedule_paragraph",
              "schedule_table"}
HEADING_SOURCES = {"margin", "arrangement", "schedule_title", "gombe_margin", "gombe_arrangement",
                   "gombe_margin_nrs_arrangement", "proofread_index", "nrs_ocr"}


@lru_cache(maxsize=None)
def built():
    return build_chunks.build()


def chunks():
    return built()[2]


def by_section():
    groups = defaultdict(list)
    for c in chunks():
        if c["section"] is not None:
            groups[(c["act_id"], int(c["section"]))].append(c)
    return groups


def source_section(act_id, n):
    """The section the chunker reads: gazette NTA; Gombe or proofread NTAA (see chunker.build)."""
    if act_id == "nta2025":
        return parse_cached(chunker.NTA_GAZETTE).section(n)
    if chunker.alignment_status()[n] == "identical":
        return parse_cached(section_map.NTAA_GOMBE).section(n)
    text = (chunker.PROOF_DIR / f"s{n:03d}.txt").read_text(encoding="utf-8")
    return parse_section_text(text, n)[0]


# --- inputs pinned --------------------------------------------------------------------------------

def test_tokenizer_asset_is_pinned():
    assert hashlib.sha256(chunker.BGE_TOKENIZER.read_bytes()).hexdigest() == BGE_SHA256


def test_nta_scope_list_matches_corpus_definition():
    doc = ROOT / "docs" / "phase01_corpus" / "v1_corpus_definition.md"
    assert nta_in_scope_from_doc(doc) == (NTA_IN_SCOPE, NTA_SCHEDULES_IN_SCOPE)
    assert len(NTA_IN_SCOPE) == len(set(NTA_IN_SCOPE)) == 84


def test_source_hash_matches_manifest():
    with chunker.MANIFEST.open(newline="", encoding="utf-8") as fh:
        manifest = {r["file"]: r["sha256"] for r in csv.DictReader(fh)}
    for c in chunks():
        assert c["source"]["sha256"] == manifest[c["source"]["file"]], c["chunk_id"]
    gazette = ROOT / "data" / "raw" / chunker.NTA_GAZETTE
    assert hashlib.sha256(gazette.read_bytes()).hexdigest() == manifest["raw/" + chunker.NTA_GAZETTE]


# --- determinism ----------------------------------------------------------------------------------

def test_committed_chunks_equal_a_fresh_build():
    jsonl, manifest, _ = built()
    assert build_chunks.CHUNKS.read_bytes() == jsonl
    committed = json.loads(build_chunks.BUILD_MANIFEST.read_bytes())
    fresh = json.loads(manifest)
    committed.pop("python"), fresh.pop("python")  # patch release of the interpreter only
    assert committed == fresh
    assert fresh["jsonl_sha256"] == hashlib.sha256(jsonl).hexdigest()


def test_jsonl_has_no_wall_clock_time():
    jsonl, manifest, _ = built()
    assert not re.search(rb"20\d\d-\d\d-\d\dT\d\d:\d\d", jsonl + manifest)


# --- schema, ids, order ---------------------------------------------------------------------------

def test_every_chunk_has_the_schema_fields():
    for c in chunks():
        assert set(c) == FIELDS, c["chunk_id"]
        assert c["unit_type"] in UNIT_TYPES
        assert c["heading_source"] in HEADING_SOURCES
        assert c["text_sha256"] == hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
        assert c["corpus_snapshot"] == "2026-10-18"
        assert c["text"].strip() and c["context_header"].startswith(chunker.ACTS[c["act_id"]]["short"])


def test_chunk_ids_are_unique():
    ids = [c["chunk_id"] for c in chunks()]
    assert len(ids) == len(set(ids))
    assert all(i.startswith(c["act_id"] + ":") for i, c in zip(ids, chunks()))


def test_chunks_are_in_document_order():
    acts = ["nta2025", "ntaa2025"]
    ordinals = list(chunker.ORDINAL_NUMBER)

    def key(c):
        if c["section"] is not None:
            return (acts.index(c["act_id"]), 0, int(c["section"]))
        return (acts.index(c["act_id"]), 1, ordinals.index(c["schedule"]["number"]))

    keys = [key(c) for c in chunks()]
    assert keys == sorted(keys)
    for a, b in zip(chunks(), chunks()[1:]):
        if key(a) == key(b):
            assert a["source"]["pages"][0] <= b["source"]["pages"][0], (a["chunk_id"], b["chunk_id"])


# --- coverage -------------------------------------------------------------------------------------

def test_every_in_scope_section_and_schedule_is_present_and_nothing_else():
    sections = by_section()
    assert sorted(n for a, n in sections if a == "nta2025") == NTA_IN_SCOPE
    assert sorted(n for a, n in sections if a == "ntaa2025") == NTAA_IN_SCOPE
    schedules = {c["schedule"]["number"] for c in chunks() if c["schedule"]}
    assert schedules == set(NTA_SCHEDULES_IN_SCOPE)
    assert all(c["act_id"] == "nta2025" for c in chunks() if c["schedule"])


@pytest.mark.parametrize("act_id, n", [("nta2025", n) for n in NTA_IN_SCOPE] + [("ntaa2025", n) for n in NTAA_IN_SCOPE])
def test_chunk_texts_reassemble_the_section(act_id, n):
    """The letters of a section's chunks, in order, are the letters of the source section: no
    line is lost, repeated or moved between chunks."""
    expected = chunker.letters("".join(ln.text for ln in source_section(act_id, n).lines))
    got = chunker.letters("".join(c["text"] for c in by_section()[(act_id, n)]))
    assert got == expected


def test_every_subsection_label_is_in_exactly_one_chunk():
    for (act_id, n), group in by_section().items():
        for label in source_section(act_id, n).subsection_labels:
            holders = [c for c in group if c["unit_type"] == "section" or label in c["subsections"]]
            assert holders, f"{act_id} s{n}{label}: in no chunk"
            if len(holders) > 1:  # a subsection too long for one chunk is split at its paragraphs
                assert all(c["unit_type"] == "paragraph_group" for c in holders), f"{act_id} s{n}{label}"


@pytest.mark.parametrize("act_id, n, count", [("nta2025", 189, 22), ("nta2025", 202, 120), ("ntaa2025", 147, 55)])
def test_interpretation_sections_give_one_chunk_per_definition(act_id, n, count):
    group = by_section()[(act_id, n)]
    assert all(c["unit_type"] == "definition" for c in group)
    terms = {re.sub(r"\([a-z]+\)(-\([a-z]+\))?$", "", c["chunk_id"].split(":def:")[1]) for c in group}
    assert len(terms) == len(source_section(act_id, n).definitions) == count


def test_first_schedule_is_part_one_only_and_complete():
    first = [c for c in chunks() if c["schedule"] and c["schedule"]["number"] == "First"]
    assert all(c["schedule"]["part"] == "I" for c in first)
    assert not any(re.search(r"^PART II\b", c["text"], re.M) for c in first)
    paras = set()
    for c in first:
        m = re.search(r":para(\d+)(?:-(\d+))?", c["chunk_id"])
        if m:
            paras.update(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    assert paras == set(range(1, 24))


def test_ntaa_chunks_cite_nrs_pages_of_their_section():
    with chunker.ALIGNMENT.open(newline="", encoding="utf-8") as fh:
        nrs_pages = {int(r["section"]): {int(p) for p in r["nrs_pages"].split()} for r in csv.DictReader(fh)}
    for c in chunks():
        if c["act_id"] == "ntaa2025":
            assert c["source"]["file"] == "raw/" + section_map.NTAA_NRS
            assert set(c["source"]["pages"]) <= nrs_pages[int(c["section"])], c["chunk_id"]
            assert c["na_section"] == c["section"]
            expected = "gombe_verified_identical" if chunker.alignment_status()[int(c["section"])] == "identical" \
                else "nrs_proofread"
            assert c["text_source"] == expected


def test_nta_na_section_follows_the_offset():
    for c in chunks():
        if c["act_id"] == "nta2025" and c["section"]:
            assert c["na_section"] == str(section_map.nta_na_section(int(c["section"])))
        if c["schedule"]:
            assert c["na_section"] is None


# --- tokens ---------------------------------------------------------------------------------------

def test_every_chunk_fits_both_token_caps():
    for c in chunks():
        full = c["context_header"] + "\n" + c["text"]
        assert c["tokens_with_header"] == chunker.cl100k_tokens(full) <= chunker.CAP, c["chunk_id"]
        assert c["tokens_cl100k"] == chunker.cl100k_tokens(c["text"])
        assert c["tokens_bge_with_header"] == chunker.bge_tokens(full) <= 512, c["chunk_id"]


# --- golden text checked against the page images --------------------------------------------------

def golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["chunks"]


def test_golden_file_spread():
    g = golden()
    assert len(g) == len({x["chunk_id"] for x in g}) == 30
    kinds = {x["text_source"] for x in g} | {x["unit_type"] for x in g}
    assert {"pdf_text", "gombe_verified_identical", "nrs_proofread", "section", "subsection_group",
            "paragraph_group", "definition", "schedule_paragraph", "schedule_table"} <= kinds


@pytest.mark.parametrize("entry", golden(), ids=lambda e: e["chunk_id"])
def test_golden_chunk(entry):
    c = next((c for c in chunks() if c["chunk_id"] == entry["chunk_id"]), None)
    assert c is not None, f"{entry['chunk_id']} not built"
    for field in ("citation", "unit_type", "text_source", "page_labels", "heading", "text"):
        assert c[field] == entry[field], field
    assert c["source"]["pages"] == entry["pages"]


# --- known anomalies ------------------------------------------------------------------------------

def chunk(chunk_id):
    return next(c for c in chunks() if c["chunk_id"] == chunk_id)


def test_s57_labels_kept_as_printed():
    # DQ-07: the gazette prints (1), (2), (4) with no (3).
    text = chunk("nta2025:s57")["text"]
    assert re.findall(r"^(?:57\. )?\((\d)\)", text, re.M) == ["1", "2", "4"]


def test_thirteenth_schedule_products_none_printed():
    # DQ-06: the products column is empty in the cited copy.
    rows = [ln for ln in chunk("nta2025:sch13:table1")["text"].splitlines() if ln.startswith("Sub-sector:")]
    assert len(rows) == 6
    assert all(ln.endswith("| Exempted products: (none printed)") for ln in rows)


def test_ntaa_s41_has_no_deposit_clause():
    group = by_section()[("ntaa2025", 41)]
    assert {c["text_source"] for c in group} == {"nrs_proofread"}
    assert not any("20%" in c["text"] or "disputed amount" in c["text"] for c in group)
    assert [lab for c in group for lab in c["subsections"]] == [f"({i})" for i in range(1, 9)]


def test_ntaa_s147_small_business_threshold():
    c = chunk("ntaa2025:s147:def:small-business")
    assert c["text_source"] == "nrs_proofread"
    assert c["text"].startswith('"small business" means a business that earns gross turnover of\n₦100,000,000 or less')


MARGIN_BLEED = re.compile(r"^[A-Z][a-z]+(?: [a-z]+){0,4} \d{1,3}\s?\.\s?[—–-]?\s?(?:\(1\)|[A-Z])")


def test_no_marginal_note_bleed():
    bad = [(c["chunk_id"], ln) for c in chunks() for ln in c["text"].splitlines() if MARGIN_BLEED.match(ln)]
    assert bad == []


def test_no_unconverted_naira():
    bad = [(c["chunk_id"], m.group(0)) for c in chunks()
           for m in re.finditer(r"(?<![A-Za-z0-9])N\s?\d", c["text"])]
    assert bad == []


def test_definitions_do_not_swallow_the_next_definition():
    # A line inside a definition chunk that opens a quoted term would be a missed definition. The
    # first definition carries the section lead-in, so its second line is the term itself.
    for c in chunks():
        if c["unit_type"] == "definition":
            lines = c["text"].splitlines()
            later = lines[2:] if chunker.SECTION_START.match(lines[0]) else lines[1:]
            assert not [ln for ln in later if re.match(r'^["“]', ln)], c["chunk_id"]


def test_cross_references():
    targets = lambda cid: {r["target"] for r in chunk(cid)["cross_refs"]}
    assert "s142" in targets("ntaa2025:s29")
    assert {"s27", "s28"} <= targets("nta2025:sch1:pt1:para2-3")
    for c in chunks():
        for r in c["cross_refs"]:
            assert re.search(r"[\d(]|Schedule|Chapter|Part", r["raw"]), (c["chunk_id"], r["raw"])


def test_discrepancy_flags_wait_for_the_register():
    if chunker.REGISTER.is_file():
        pytest.skip("register present; its integrity is tested with step 2b-6")
    assert all(c["discrepancy"] == {"flag": False, "register_ids": []} for c in chunks())


# --- tables ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("t", chunker.table_config(), ids=lambda t: f"{t['schedule']}-{t['id']}")
def test_table_rows_use_every_printed_word_once(t):
    rows, header, _ = chunker.extract_table(t)  # raises if the word multisets differ
    assert all(len(r) == len(t["columns"]) for r in rows)


def test_table_word_check_catches_a_lost_word():
    from ingest.parse_structure import SourceLine
    lines = [SourceLine("Crop Growing of", 1, None, 0.0, 0.0)]
    chunker.check_table_words([[["Crop"], ["Growing", "of"]]], lines, "ok")
    with pytest.raises(ValueError, match="lose or repeat"):
        chunker.check_table_words([[["Crop"], ["Growing"]]], lines, "lost")
    with pytest.raises(ValueError, match="lose or repeat"):
        chunker.check_table_words([[["Crop"], ["Growing", "of", "of"]]], lines, "repeated")


# --- section map ----------------------------------------------------------------------------------

def read_section_map():
    with section_map.OUT.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# Title pairs below 0.8 similarity, each looked at: the sections match (renumbering or rewording
# between prints, or OCR spacing); see build notes 2b-5.
LOW_SIMILARITY = {
    ("nta2025", "26", "26"), ("nta2025", "105", "104"), ("nta2025", "114", "113"), ("nta2025", "125", "124"),
    ("nta2025", "126", "125"), ("nta2025", "127", "126"), ("nta2025", "150", "149"), ("nta2025", "157", "156"),
    ("ntaa2025:gombe", "131", "131"), ("ntaa2025:gombe", "148", "148"),
    ("ntaa2025:nass", "7", "7"), ("ntaa2025:nass", "8", "8"), ("ntaa2025:nass", "79", "79"),
    ("ntaa2025:nass", "81", "81"), ("ntaa2025:nass", "145", "145"), ("ntaa2025:nass", "148", "148"),
}


def test_section_map_is_reproducible(tmp_path):
    out = tmp_path / "section_map.csv"
    section_map.write(section_map.build(), out)
    assert out.read_bytes() == section_map.OUT.read_bytes()


def test_section_map_offset():
    rows = {(r["act_id"], r["comparison_print"], r["cited_section"]): r for r in read_section_map()}
    assert rows[("nta2025", "nass", "89")]["comparison_section"] == ""
    assert rows[("nta2025", "nass", "148")]["comparison_section"] == "147"
    assert rows[("nta2025", "nass", "88")]["comparison_section"] == "88"
    assert len([r for r in read_section_map() if r["act_id"] == "nta2025"]) == 203
    assert len([r for r in read_section_map() if r["act_id"] == "ntaa2025"]) == 2 * 148


def test_section_map_titles_agree_or_are_listed():
    low = set()
    for r in read_section_map():
        if r["title_similarity"] and float(r["title_similarity"]) < 0.8:
            act = r["act_id"] if r["act_id"] == "nta2025" else f"{r['act_id']}:{r['comparison_print']}"
            low.add((act, r["cited_section"], r["comparison_section"]))
    assert low == LOW_SIMILARITY
