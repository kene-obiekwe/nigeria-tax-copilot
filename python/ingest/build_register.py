"""Discrepancy register (chunking design, section 6): candidate rows, then the reviewed register.

    .venv\\Scripts\\python python\\ingest\\build_register.py

1. Candidates. The cited text of every in-scope unit (exactly the chunk text) is compared with
   each comparison print: the National Assembly text for the Nigeria Tax Act; the Gombe and the
   National Assembly prints for the Nigeria Tax Administration Act. Sections are aligned by
   data/derived/section_map.csv. Only letters, digits and "%" are compared, word by word; a
   difference that disappears when spaces are removed ("amonth" / "a month", "conver- sion") is
   not recorded. Each difference is placed in the cited chunk and subsection it falls in and
   given a suggested type (auto_class) from the design's triggers.
   Writes data/derived/register_candidates.csv.
2. Register. data/curated/register_decisions.yml holds the review decision for every candidate
   (final type, readings, legal-effect note, or the reason it was dropped) and the rows that
   come from the data-quality log rather than from a text difference (DR-000, numbering
   anomalies, structure). Every candidate must have a decision. Reviewed rows keep the
   register_id stored with their decision (D-18); a new row takes the next free number. Writes
   data/curated/discrepancy_register.csv.
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from ingest import chunker, section_map  # noqa: E402
from ingest.extract import join_lines  # noqa: E402
from ingest.parse_structure import DEFINITION, SourceLine, definition_starts, parse_cached  # noqa: E402
from ingest.scope import NTA_IN_SCOPE, NTA_SCHEDULES_IN_SCOPE, NTAA_IN_SCOPE  # noqa: E402

CHUNKS = ROOT / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
CANDIDATES = ROOT / "data" / "derived" / "register_candidates.csv"
DECISIONS = ROOT / "data" / "curated" / "register_decisions.yml"
REGISTER = chunker.REGISTER
CANDIDATE_FIELDS = ["key", "act_id", "cited_unit", "gazette_section", "subsection", "comparison_text",
                    "comparison_section", "auto_class", "cited_diff", "comparison_diff", "cited_reading",
                    "comparison_reading", "similarity", "cited_pages", "comparison_pages"]
REGISTER_FIELDS = ["register_id", "act_id", "cited_unit", "gazette_section", "comparison_text",
                   "comparison_section", "type", "cited_reading", "comparison_reading", "similarity",
                   "auto_class", "cited_pages", "comparison_pages", "refs", "reviewed_by", "reviewed_on",
                   "notes"]
TYPES = {"substantive", "wording", "numbering", "missing_content"}
COMPARISONS = {"nta2025": [("nass", section_map.NTA_NASS)],
               "ntaa2025": [("gombe", section_map.NTAA_GOMBE), ("nass", section_map.NTAA_NASS)]}
CITED_PRINT = {"nta2025": section_map.NTA_GAZETTE, "ntaa2025": section_map.NTAA_NRS}

CONTEXT = 6          # words of context either side of a difference in a reading
JOIN_GAP = 3         # differences separated by at most this many equal words are one difference
MISSING_MIN = 8      # a one-sided difference of at least this many words suggests missing_content
REWRITE_MIN = 25     # a two-sided difference this long suggests substantive
TRIGGER_WORDS = set("""
shall may must not no nor only nothing never except unless without including excluding exclusive
and or
january february march april may june july august september october november december
minister president service commission tribunal court courts authority authorities board governor
state states federal government company companies individual individuals person persons taxpayer
taxpayers bank banks partnership trustee trustees
""".split())
WORD = re.compile(r"[A-Za-z0-9%]+")
LABEL_AT_START = re.compile(r"^(?:\d{1,3}\s?\.?\s?(?:[—–-]\s?)?)?\((\d{1,2}[A-Z]?)\)\s")


# --- token streams --------------------------------------------------------------------------------

@dataclass
class Stream:
    """A text with its words. Each word carries (normalised form, start, end, line index)."""
    text: str
    pages: list[tuple[int, str | None]]                 # per line of `text`
    units: list[str] = field(default_factory=list)      # per line: cited chunk id ("" for comparison)
    labels: list[str] = field(default_factory=list)     # per line: subsection label in effect
    words: list[tuple[str, int, int, int]] = field(default_factory=list)

    def __post_init__(self):
        starts = [0] + [m.end() for m in re.finditer("\n", self.text)]
        line = 0
        for m in WORD.finditer(self.text):
            while line + 1 < len(starts) and starts[line + 1] <= m.start():
                line += 1
            self.words.append((m.group(0).lower(), m.start(), m.end(), line))

    def norm(self) -> list[str]:
        return [w[0] for w in self.words]


def labels_for(lines: list[str], first: str = "") -> list[str]:
    out, current = [], first
    for ln in lines:
        m = LABEL_AT_START.match(ln)
        if m:
            current = f"({m.group(1)})"
        out.append(current)
    return out


def cited_stream(chunks: list[dict], ref: list[SourceLine]) -> Stream:
    """The cited text: the chunk texts in order, each line tagged with its chunk and page."""
    lines, units, pages = [], [], []
    for c in chunks:
        own = c["text"].split("\n")
        lines += own
        units += [c["chunk_id"]] * len(own)
        on = c["source"]["pages"]
        if len(on) == 1:  # most chunks sit on one page
            label = c["page_labels"][0] if c["page_labels"] else None
            pages += [(on[0], label)] * len(own)
        else:  # align only against the cited lines on the chunk's own pages
            pages += chunker.map_line_pages(own, [ln for ln in ref if ln.page in on] or ref)
    first = chunks[0]["subsections"][0] if chunks and chunks[0]["subsections"] else ""
    return Stream("\n".join(lines), pages, units, labels_for(lines, first))


def joined_pages(joined: list[str], lines: list[SourceLine]) -> list[tuple[int, str | None]]:
    """Page of each line of join_lines output. Joining only merges consecutive source lines, so
    each joined line takes the page of the first source line it consumes, counted by letters."""
    out, k = [], 0
    for jl in joined:
        need = len(chunker.letters(jl))
        start = min(k, len(lines) - 1)
        got = 0
        while k < len(lines) and (got < need or k == start):
            got += len(chunker.letters(lines[k].text))
            k += 1
        out.append((lines[start].page, lines[start].page_label))
    return out


def comparison_stream(lines: list[SourceLine], vocab) -> Stream:
    text = join_lines([ln.text for ln in lines], vocab)
    joined = text.split("\n")
    return Stream(text, joined_pages(joined, lines), [""] * len(joined), labels_for(joined))


def drop_leading_number(stream: Stream, number: int) -> None:
    if stream.words and stream.words[0][0] == str(number):
        stream.words.pop(0)


# --- differences ------------------------------------------------------------------------------------

@dataclass
class Diff:
    i1: int
    i2: int
    j1: int
    j2: int


def differences(a: list[str], b: list[str]) -> list[Diff]:
    """Word-level differences, with near neighbours joined and spacing-only ones removed."""
    groups: list[Diff] = []
    last_equal = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            last_equal = i2 - i1
            continue
        if groups and last_equal <= JOIN_GAP:
            groups[-1].i2, groups[-1].j2 = i2, j2
        else:
            groups.append(Diff(i1, i2, j1, j2))
    return [d for d in groups if "".join(a[d.i1:d.i2]) != "".join(b[d.j1:d.j2])]


def is_offset(a: list[str], b: list[str]) -> bool:
    """Every differing word is a section number that the National Assembly text gives one lower,
    from section 89 on (DR-000)."""
    nums = [x for x in a + b if not x.isdigit()]
    return (not nums and len(a) == len(b) > 0
            and all(int(x) == int(y) + 1 and int(x) >= 90 for x, y in zip(a, b)))


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
NUMBER_WORDS.update({"twentyfive": "25", "thirty": "30"})
STYLE = [  # drafting-style variants of the National Assembly print, applied to both sides
    (r"\blaws? of the federation of nigerian? ", "lfn "),
    (r"\bof this (?:section|act)\b", ""),
    (r"\b(\d+) ?(?:per ?cent|percent)\b", r"\1%"),
    (r"\b(\d+)(?:st|nd|rd|th)\b", r"\1"),
    (r"\b2025\b", ""),
]


def style_only(a: list[str], b: list[str]) -> bool:
    """True when the two sides differ only by a drafting-style variant: "of this section" or
    "of this Act" added, ", 2025" after an Act's name, "Laws of the Federation of Nigeria" or
    "LFN", "30 per cent" or "30%", number words or digits, "31st" or "31", "₦100,000.00" or
    "₦100,000", or a lone "and" / "or" joining list items."""
    if not a or not b:
        if set(a or b) <= {"and", "or"} and len(a or b) == 1:
            return True

    def canon(words):
        # "₦100,000.00" against "₦100,000": the kobo figure is style
        text = " " + " ".join(NUMBER_WORDS.get(w, w) for w in words if w != "00") + " "
        for pat, rep in STYLE:
            text = re.sub(pat, rep, text)
        return text.split()
    return canon(a) == canon(b)


def auto_class(a: list[str], b: list[str]) -> str:
    if style_only(a, b):
        return "wording"
    if not a or not b:
        if len(a or b) >= MISSING_MIN:
            return "missing_content"
    elif min(len(a), len(b)) >= REWRITE_MIN:
        return "substantive"
    changed = set(a) ^ set(b) or set(a) | set(b)
    if any(re.search(r"\d|%", w) for w in changed) or changed & TRIGGER_WORDS:
        return "substantive"
    return "wording"


def reading(stream: Stream, i1: int, i2: int, words, vocab, unit: str | None = None) -> str:
    """Verbatim extract around words i1..i2 (with context), kept inside one cited chunk."""
    n = len(stream.words)
    lo, hi = max(0, i1 - CONTEXT), min(n, max(i2, i1) + CONTEXT)
    if unit is not None:
        while lo < i1 and stream.units[stream.words[lo][3]] != unit:
            lo += 1
        while hi > max(i2, i1) and stream.units[stream.words[hi - 1][3]] != unit:
            hi -= 1
    if lo >= hi:
        return ""
    text = stream.text[stream.words[lo][1]:stream.words[hi - 1][2]]
    return " ".join(text.split())


def page_span(stream: Stream, i1: int, i2: int) -> str:
    idx = range(i1, i2) if i2 > i1 else [min(i1, len(stream.words) - 1)]
    pages = sorted({stream.pages[stream.words[k][3]][0] for k in idx if 0 <= k < len(stream.words)})
    return " ".join(str(p) for p in pages)


def unit_at(stream: Stream, i1: int, i2: int) -> tuple[str, str]:
    """(cited chunk id, subsection label) of a difference; an insertion takes the word before."""
    k = i1 if i2 > i1 else max(0, i1 - 1)
    k = min(k, len(stream.words) - 1)
    line = stream.words[k][3]
    return stream.units[line], stream.labels[line]


def similarity(a: list[str], b: list[str]) -> str:
    return f"{difflib.SequenceMatcher(None, ''.join(a), ''.join(b), autojunk=False).ratio():.3f}"


def compare(act_id: str, section: str | None, comp: str, comp_section: str | None,
            cited: Stream, other: Stream, out: list[dict], offsets: list[dict], vocab) -> None:
    a, b = cited.norm(), other.norm()
    sim = similarity(a, b)
    for d in differences(a, b):
        da, db = a[d.i1:d.i2], b[d.j1:d.j2]
        unit, label = unit_at(cited, d.i1, d.i2)
        row = {
            "act_id": act_id, "cited_unit": unit, "gazette_section": section or "",
            "subsection": label, "comparison_text": comp, "comparison_section": comp_section or "",
            "auto_class": auto_class(da, db), "cited_diff": " ".join(da), "comparison_diff": " ".join(db),
            "cited_reading": reading(cited, d.i1, d.i2, a, vocab, unit),
            "comparison_reading": reading(other, d.j1, d.j2, b, vocab),
            "similarity": sim, "cited_pages": page_span(cited, d.i1, d.i2),
            "comparison_pages": page_span(other, d.j1, d.j2),
        }
        if act_id == "nta2025" and is_offset(da, db):
            offsets.append(row)
        else:
            out.append(row)


# --- per unit -------------------------------------------------------------------------------------

def load_chunks() -> list[dict]:
    return [json.loads(line) for line in CHUNKS.read_text(encoding="utf-8").splitlines()]


def term_key(chunk_id: str) -> str:
    return re.sub(r"\([a-z]+\)(?:-\([a-z]+\))?$", "", chunk_id.split(":def:")[1])


def compare_definitions(act_id, n, comp, comp_n, group, ref, other_sec, vocab, out, offsets):
    """Interpretation sections: definitions are matched by term, so a change of order between
    prints is not a difference; the lead-in is compared on its own."""
    by_term: dict[str, list[dict]] = defaultdict(list)
    for c in group:
        by_term[term_key(c["chunk_id"])].append(c)
    # lead-in: the lines of the first chunk before its definition
    first = group[0]
    lines = first["text"].split("\n")
    start = next(i for i, ln in enumerate(lines)
                 if any(k == 0 for k, _ in definition_starts(lines[i:i + 2])))
    lead = dict(first, text="\n".join(lines[:start]))
    by_term[term_key(first["chunk_id"])][0] = dict(first, text="\n".join(lines[start:]))
    o_start = other_sec.definitions[0].lines[0] if other_sec.definitions else None
    o_lead = [ln for ln in other_sec.lines[:other_sec.lines.index(o_start)]] if o_start else other_sec.lines
    if lead["text"].strip():
        cs, os_ = cited_stream([lead], ref), comparison_stream(o_lead, vocab)
        drop_leading_number(cs, n)
        drop_leading_number(os_, comp_n)
        compare(act_id, str(n), comp, str(comp_n), cs, os_, out, offsets, vocab)
    others: dict[str, list] = defaultdict(list)
    order = []
    for u in other_sec.definitions:
        k = chunker.slug(u.label)
        others[k].append(u)
        order.append(k)
    # a term spelt differently in the comparison print ("authority officer" for "authorised
    # officer") is the same definition when the terms are close and neither has an exact match
    for k in by_term:
        if others.get(k):
            continue
        spare = [o for o in others if others[o] and o not in by_term]
        best = max(spare, key=lambda o: difflib.SequenceMatcher(None, k, o).ratio(), default=None)
        if best and difflib.SequenceMatcher(None, k, best).ratio() >= 0.8:
            others[k] = [others[best].pop(0)]
    matched = set()
    for k, chunks in by_term.items():
        if not others.get(k):
            text = " ".join(chunks[0]["text"].split())
            out.append({"act_id": act_id, "cited_unit": chunks[0]["chunk_id"], "gazette_section": str(n),
                        "subsection": "", "comparison_text": comp, "comparison_section": str(comp_n),
                        "auto_class": "missing_content", "cited_diff": f"definition {k}",
                        "comparison_diff": "", "cited_reading": " ".join(text.split()[:20]),
                        "comparison_reading": "", "similarity": "0.000",
                        "cited_pages": " ".join(str(p) for p in chunks[0]["source"]["pages"]),
                        "comparison_pages": ""})
            continue
        u = others[k].pop(0)
        matched.add(id(u))
        compare(act_id, str(n), comp, str(comp_n), cited_stream(chunks, ref),
                comparison_stream(u.lines, vocab), out, offsets, vocab)
    # definitions only in the comparison print: placed on the cited definition before them
    previous = group[0]["chunk_id"]
    for u in other_sec.definitions:
        k = chunker.slug(u.label)
        if k in by_term:
            previous = by_term[k][0]["chunk_id"]
        if id(u) in matched:
            continue
        text = " ".join(join_lines([ln.text for ln in u.lines], vocab).split())
        out.append({"act_id": act_id, "cited_unit": previous, "gazette_section": str(n), "subsection": "",
                    "comparison_text": comp, "comparison_section": str(comp_n),
                    "auto_class": "missing_content", "cited_diff": "",
                    "comparison_diff": f"definition {k}", "cited_reading": "",
                    "comparison_reading": " ".join(text.split()[:20]), "similarity": "0.000",
                    "cited_pages": "", "comparison_pages": " ".join(str(p) for p in sorted({ln.page for ln in u.lines}))})


SCHEDULE_REF = re.compile(r"^(?:Sections?\s|and\s\d|\d[\d(), ]*(?:and\s\d+)?$|\(\w\),?$)")


def schedule_body(lines: list[str]) -> int:
    """Index of the first line after the 'Sections …' reference block at the head of a schedule."""
    k = 0
    while k < len(lines) and SCHEDULE_REF.match(lines[k].strip()):
        k += 1
    return k


def compare_schedule(ordinal, chunks, gaz, nass, out, offsets):
    cited_chunks = [c for c in chunks if c["unit_type"] != "schedule_table"]
    tables = [c for c in chunks if c["unit_type"] == "schedule_table"]
    g_sched, n_sched = gaz.schedule(ordinal), nass.schedule(ordinal)
    g_lines, n_lines = g_sched.lines, n_sched.lines
    if ordinal == "First":
        g_lines, _ = chunker._first_schedule_part(g_lines, "I")
        n_lines, _ = chunker._first_schedule_part(n_lines, "I")
    configs = [t for t in chunker.table_config() if t["schedule"] == ordinal]
    n_table_lines = []
    for t in configs:
        region = _region(n_lines, t)
        n_lines = [ln for ln in n_lines if ln not in region]
        # the table body starts after its column-heading line (title lines above it are headings)
        hdr = next((i for i, ln in enumerate(region) if chunker.letters(ln.text).startswith(
            chunker.letters(t["header_starts_with"]))), 0)
        n_table_lines += region[hdr:]
    if cited_chunks:
        first = cited_chunks[0]["text"].split("\n")
        cut = schedule_body(first)
        cited_chunks = [dict(cited_chunks[0], text="\n".join(first[cut:]))] + cited_chunks[1:]
        # the comparison starts where the cited text starts (headings above it are in the header)
        key = chunker.letters(first[cut])[:20]
        start = next((i for i, ln in enumerate(n_lines) if chunker.letters(ln.text).startswith(key)), None)
        if start is None:
            start = schedule_body([ln.text for ln in n_lines])
        cs = cited_stream(cited_chunks, g_lines)
        os_ = comparison_stream(n_lines[start:], nass.vocab)
        compare("nta2025", None, "nass", None, cs, os_, out, offsets, nass.vocab)
    for t, c in zip(configs, tables):
        rows, _, _ = chunker.extract_table(t)
        cited_words = [w.lower() for r in rows for cell in r for w in WORD.findall(cell)
                       if cell != t["empty_cell"]]
        comp_words = [w.lower() for ln in n_table_lines[1:] for w in WORD.findall(ln.text)
                      if not chunker.letters(ln.text).startswith(chunker.letters(t["header_starts_with"]))]
        only_c, only_n = _multiset_minus(cited_words, comp_words), _multiset_minus(comp_words, cited_words)
        if only_c or only_n:
            out.append({"act_id": "nta2025", "cited_unit": c["chunk_id"], "gazette_section": "",
                        "subsection": "", "comparison_text": "nass", "comparison_section": "",
                        "auto_class": "missing_content" if len(only_n) >= MISSING_MIN else "substantive",
                        "cited_diff": " ".join(only_c), "comparison_diff": " ".join(only_n),
                        "cited_reading": "", "comparison_reading": "",
                        "similarity": similarity(cited_words, comp_words),
                        "cited_pages": " ".join(str(p) for p in c["source"]["pages"]),
                        "comparison_pages": " ".join(str(p) for p in sorted({ln.page for ln in n_table_lines}))})


def _region(lines, t):
    start = next((i for i, ln in enumerate(lines)
                  if chunker.letters(ln.text).startswith(chunker.letters(t["region_starts_with"]))), None)
    if start is None:
        return []
    end = len(lines)
    if t["ends_before"]:
        end = next((i for i, ln in enumerate(lines) if i > start and
                    chunker.letters(ln.text).startswith(chunker.letters(t["ends_before"]))), len(lines))
    return lines[start:end]


def _multiset_minus(a: list[str], b: list[str]) -> list[str]:
    rest = list(b)
    out = []
    for w in a:
        if w in rest:
            rest.remove(w)
        else:
            out.append(w)
    return out


def candidates() -> tuple[list[dict], list[dict]]:
    chunks = load_chunks()
    by_sec: dict[tuple[str, int], list[dict]] = defaultdict(list)
    by_sched: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        if c["section"] is not None:
            by_sec[(c["act_id"], int(c["section"]))].append(c)
        else:
            by_sched[c["schedule"]["number"]].append(c)
    out, offsets = [], []
    for act_id, numbers in (("nta2025", NTA_IN_SCOPE), ("ntaa2025", NTAA_IN_SCOPE)):
        cited_print = parse_cached(CITED_PRINT[act_id])
        for n in numbers:
            group = by_sec[(act_id, n)]
            ref = cited_print.section(n).lines
            for comp, file in COMPARISONS[act_id]:
                other = parse_cached(file)
                comp_n = section_map.nta_na_section(n) if act_id == "nta2025" else n
                other_sec = other.section(comp_n)
                if all(c["unit_type"] == "definition" for c in group):
                    compare_definitions(act_id, n, comp, comp_n, group, ref, other_sec, other.vocab,
                                        out, offsets)
                    continue
                cs, os_ = cited_stream(group, ref), comparison_stream(other_sec.lines, other.vocab)
                drop_leading_number(cs, n)
                drop_leading_number(os_, comp_n)
                compare(act_id, str(n), comp, str(comp_n), cs, os_, out, offsets, other.vocab)
        if act_id == "nta2025":
            gaz, nass = cited_print, parse_cached(section_map.NTA_NASS)
            for ordinal in NTA_SCHEDULES_IN_SCOPE:
                compare_schedule(ordinal, by_sched[ordinal], gaz, nass, out, offsets)
    out = merge_wording(out)
    order = {c["chunk_id"]: i for i, c in enumerate(chunks)}
    comp_order = {"gombe": 0, "nass": 1}
    out.sort(key=lambda r: (order[r["cited_unit"]], comp_order[r["comparison_text"]]))
    seen: dict[tuple, int] = defaultdict(int)
    for r in out:
        base = (r["cited_unit"], r["comparison_text"])
        seen[base] += 1
        r["key"] = f"{r['cited_unit']}|{r['comparison_text']}|{seen[base]}"
    return out, offsets


def merge_wording(rows: list[dict]) -> list[dict]:
    """Wording differences in the same cited chunk, subsection and comparison print become one row."""
    out, index = [], {}
    for r in rows:
        if r["auto_class"] != "wording":
            out.append(r)
            continue
        k = (r["cited_unit"], r["subsection"], r["comparison_text"])
        if k not in index:
            index[k] = r
            out.append(r)
            continue
        m = index[k]
        for f in ("cited_diff", "comparison_diff", "cited_reading", "comparison_reading"):
            m[f] = " … ".join(x for x in (m[f], r[f]) if x)
        for f in ("cited_pages", "comparison_pages"):
            m[f] = " ".join(sorted(set((m[f] + " " + r[f]).split()), key=int))
    return out


# --- register ---------------------------------------------------------------------------------------

def load_decisions() -> dict:
    if not DECISIONS.is_file():
        return {"review": {}, "rows": {}, "manual_rows": []}
    return yaml.safe_load(DECISIONS.read_text(encoding="utf-8"))


def register(cands: list[dict], decisions: dict) -> list[dict]:
    """Apply the review decisions. Every candidate must have a decision whose `diff` still matches."""
    review = decisions.get("review") or {}
    by_key = decisions.get("rows") or {}
    missing = [c["key"] for c in cands if c["key"] not in by_key]
    if missing:
        raise SystemExit(f"{len(missing)} candidate(s) without a review decision, first: {missing[:3]}")
    stale = [k for k in by_key if k not in {c["key"] for c in cands}]
    if stale:
        raise SystemExit(f"decision(s) for candidates that no longer exist: {stale[:3]}")
    chunk_order = {c["chunk_id"]: i for i, c in enumerate(load_chunks())}
    rows, merges, by_cand = [], [], {}
    for c in cands:
        d = by_key[c["key"]]
        expect = f"{c['cited_diff']} => {c['comparison_diff']}"
        if d.get("diff") != expect:
            raise SystemExit(f"{c['key']}: candidate changed since review ({expect!r} != {d.get('diff')!r})")
        if d.get("drop"):
            continue
        if d.get("merge_into"):
            merges.append((d["merge_into"], c))
            continue
        if d["type"] not in TYPES:
            raise SystemExit(f"{c['key']}: unknown type {d['type']!r}")
        rows.append({
            "act_id": c["act_id"], "cited_unit": d.get("cited_unit", c["cited_unit"]),
            "gazette_section": c["gazette_section"], "comparison_text": c["comparison_text"],
            "comparison_section": c["comparison_section"], "type": d["type"],
            "cited_reading": d.get("cited_reading", c["cited_reading"]),
            "comparison_reading": d.get("comparison_reading", c["comparison_reading"]),
            "similarity": c["similarity"], "auto_class": c["auto_class"], "cited_pages": c["cited_pages"],
            "comparison_pages": c["comparison_pages"], "refs": d.get("refs", ""),
            "notes": d.get("note", ""), "_order": (chunk_order.get(d.get("cited_unit", c["cited_unit"]), -1),
                                                    c["comparison_text"]),
            "_id": d.get("register_id", ""), "key": c["key"],
        })
        by_cand[c["key"]] = rows[-1]
    # a candidate merged into another row adds its readings and pages to that row
    for target, c in merges:
        if target not in by_cand:
            raise SystemExit(f"{c['key']}: merge_into {target!r} is not a kept row")
        r, d = by_cand[target], by_key[target]
        for f in ("cited_reading", "comparison_reading"):
            if f not in d:  # readings written by the reviewer already cover the merged rows
                r[f] = " … ".join(x for x in (r[f], c[f]) if x)
        for f in ("cited_pages", "comparison_pages"):
            r[f] = " ".join(sorted(set((r[f] + " " + c[f]).split()), key=int))
    for i, m in enumerate(decisions.get("manual_rows") or []):
        row = {f: str(m.get(f, "")) for f in REGISTER_FIELDS if f not in ("register_id", "reviewed_by", "reviewed_on")}
        row["notes"] = m.get("note", "")
        row["_order"] = (chunk_order.get(m.get("cited_unit", ""), -1), m.get("comparison_text", ""))
        row["_id"], row["key"] = m.get("register_id", ""), f"manual_rows[{i}]"
        rows.append(row)
    # ids are pinned in the decisions after review (D-18); a row added later takes the next free number
    pinned = [r["_id"] for r in rows if r["_id"]]
    dupes = sorted({p for p in pinned if pinned.count(p) > 1})
    if dupes:
        raise SystemExit(f"register_id used by more than one decision: {dupes[:3]}")
    free = max((int(p[3:]) for p in pinned), default=-1) + 1
    for r in sorted((r for r in rows if not r["_id"]), key=lambda r: r["_order"]):
        r["_id"], free = f"DR-{free:03d}", free + 1
    rows.sort(key=lambda r: r["_id"])
    for r in rows:
        r.pop("_order")
        r["register_id"] = r.pop("_id")
        r["reviewed_by"] = review.get("by") or ""
        r["reviewed_on"] = str(review.get("on") or "")
    return rows


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    cands, offsets = candidates()
    write_csv(CANDIDATES, CANDIDATE_FIELDS, cands)
    by = defaultdict(int)
    for c in cands:
        by[(c["act_id"], c["comparison_text"], c["auto_class"])] += 1
    print(f"{len(cands)} candidates -> {CANDIDATES.relative_to(ROOT)} "
          f"({len(offsets)} section-number differences covered by the offset rule, not listed)")
    for k in sorted(by):
        print("  " + "/".join(k) + f": {by[k]}")
    if "--candidates-only" in sys.argv:
        return 0
    rows = register(cands, load_decisions())
    write_csv(REGISTER, REGISTER_FIELDS, rows)
    final = defaultdict(int)
    for r in rows:
        final[(r["act_id"], r["type"])] += 1
    print(f"{len(rows)} register rows -> {REGISTER.relative_to(ROOT)}")
    for k in sorted(final):
        print("  " + "/".join(k) + f": {final[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
