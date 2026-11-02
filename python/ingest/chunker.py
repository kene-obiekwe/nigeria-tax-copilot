"""Chunker for the v1 corpus (chunking design, sections 4 and 5).

Text sources (D-09, D-12):
- NTA: the cited gazette, as parsed by parse_structure.py.
- NTAA: the NRS print is cited. For in-scope sections the alignment (2b-3) marks "identical",
  the Gombe text is used verbatim; for those marked "different", the proofread NRS text (2b-4).
  Either way, source.file, sha256 and pages point to the NRS print and NRS pages.

Granularity (design section 5):
1. One chunk per section when tokens_with_header <= CAP.
2. Interpretation sections: one chunk per definition.
3. Otherwise pack consecutive subsections greedily, never crossing a section; a subsection
   that alone is too big is split at its paragraphs.
4. Schedules: the whole schedule (or in-scope Part) if it fits, otherwise packed paragraphs.
   Tables (tables.yml) become their own chunks, one line per logical row.
Labels are kept as printed.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pdfplumber
import tiktoken
import yaml
from tokenizers import Tokenizer

from ingest.extract import join_lines, normalise_line
from ingest.parse_structure import (CLAUSE_END, CROSS_REF, DEFINITION, definition_starts, PARAGRAPH, SCHEDULE_PARA, SECTION_START,
                                    SUB_IN_FIRST_LINE,
                                    SUBSECTION, ParsedAct, Section,
                                    SourceLine, parse_cached, parse_section_text)
from ingest.scope import (NTA_FIRST_SCHEDULE_PARTS, NTA_IN_SCOPE, NTA_SCHEDULES_IN_SCOPE,
                          NTAA_IN_SCOPE)
from ingest.section_map import NTA_GAZETTE, NTAA_GOMBE, NTAA_NRS, nta_na_section

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.csv"
ALIGNMENT = DATA / "derived" / "ntaa_alignment.csv"
PROOF_DIR = DATA / "curated" / "ntaa_nrs_proofread"
REGISTER = DATA / "curated" / "discrepancy_register.csv"
TABLES = Path(__file__).with_name("tables.yml")
BGE_TOKENIZER = Path(__file__).parent / "assets" / "bge-small-en-v1.5.tokenizer.json"

CAP = 450  # cl100k tokens including the context header (design section 5)
CORPUS_SNAPSHOT = "2026-10-18"  # D-10
ACTS = {
    "nta2025": {"title": "Nigeria Tax Act, 2025", "short": "Nigeria Tax Act 2025", "file": NTA_GAZETTE},
    "ntaa2025": {"title": "Nigeria Tax Administration Act, 2025",
                 "short": "Nigeria Tax Administration Act 2025", "file": NTAA_NRS},
}
ORDINAL_NUMBER = {w: i for i, w in enumerate(
    "First Second Third Fourth Fifth Sixth Seventh Eighth Ninth Tenth Eleventh Twelfth Thirteenth "
    "Fourteenth".split(), 1)}
LETTERS = "abcdefghijklmnopqrstuvwxyz"


# --- tokenizers -------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _cl100k():
    return tiktoken.get_encoding("cl100k_base")


@lru_cache(maxsize=None)
def _bge():
    return Tokenizer.from_file(str(BGE_TOKENIZER))


def cl100k_tokens(text: str) -> int:
    return len(_cl100k().encode(text))


def bge_tokens(text: str) -> int:
    return len(_bge().encode(text).ids)  # includes [CLS] and [SEP]


# --- inputs -------------------------------------------------------------------------------------

def manifest_rows() -> dict[str, dict]:
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        return {Path(r["file"]).name: r for r in csv.DictReader(fh)}


def alignment_status() -> dict[int, str]:
    with ALIGNMENT.open(newline="", encoding="utf-8") as fh:
        return {int(r["section"]): r["status"] for r in csv.DictReader(fh)}


def proofread_index() -> dict[int, dict]:
    with (PROOF_DIR / "index.csv").open(newline="", encoding="utf-8") as fh:
        return {int(r["section"]): r for r in csv.DictReader(fh)}


def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --- chunk assembly -----------------------------------------------------------------------------

@dataclass
class Piece:
    """A run of lines to emit as one chunk, with its labels for the id and citation."""
    lines: list[SourceLine]
    first: str | None = None  # first label in the piece, e.g. "(1)" or "(a)"
    last: str | None = None
    subsections: tuple[str, ...] = ()


class Builder:
    def __init__(self):
        self.manifest = manifest_rows()
        self.chunks: list[dict] = []
        self.notes: list[str] = []  # build-time observations recorded in the build manifest

    # common fields ------------------------------------------------------------------------
    def emit(self, *, act_id, chunk_id, citation, unit_type, chapter, part, schedule, section,
             subsections, na_section, heading, heading_source, header, text, text_source, pages,
             page_labels):
        source_file = ACTS[act_id]["file"]
        row = self.manifest[source_file]
        chunk = {
            "chunk_id": chunk_id,
            "citation": citation,
            "act_id": act_id,
            "act_title": ACTS[act_id]["title"],
            "unit_type": unit_type,
            "chapter": chapter,
            "part": part,
            "schedule": schedule,
            "section": section,
            "subsections": list(subsections),
            "na_section": na_section,
            "heading": heading,
            "heading_source": heading_source,
            "context_header": header,
            "text": text,
            "text_source": text_source,
            "source": {"file": row["file"], "sha256": row["sha256"], "pages": sorted(set(pages))},
            "page_labels": sorted({p for p in page_labels if p}, key=_label_key),
            "cross_refs": resolve_cross_refs(text, act_id, section),
            "discrepancy": {"flag": False, "register_ids": []},
            "corpus_snapshot": CORPUS_SNAPSHOT,
            "edition_date": row["edition_or_amendment_date"][:10],
            "tokens_cl100k": cl100k_tokens(text),
            "tokens_with_header": cl100k_tokens(header + "\n" + text),
            "tokens_bge_with_header": bge_tokens(header + "\n" + text),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        self.chunks.append(chunk)
        return chunk

    # sections -------------------------------------------------------------------------------
    def section_chunks(self, act_id: str, sec: Section, vocab, *, chapter, part, heading,
                       heading_source, text_source, na_section, pages_of):
        """Chunks for one section. `pages_of(line)` gives (page, page_label) for a line."""
        sid = str(sec.number)
        header = context_header(act_id, chapter, part, f"s{sid} {heading}" if heading else f"s{sid}")
        common = dict(act_id=act_id, chapter=chapter, part=part, schedule=None, section=sid,
                      na_section=na_section, heading=heading, heading_source=heading_source,
                      header=header, text_source=text_source)
        short = ACTS[act_id]["short"]

        def text_of(lines):
            return join_lines([ln.text for ln in lines], vocab)

        def fits(lines):
            return cl100k_tokens(header + "\n" + text_of(lines)) <= CAP

        def emit_piece(piece: Piece, unit_type: str, chunk_id: str, citation: str):
            pg = [pages_of(ln) for ln in piece.lines]
            self.emit(chunk_id=chunk_id, citation=citation, unit_type=unit_type,
                      subsections=piece.subsections, text=text_of(piece.lines),
                      pages=[p for p, _ in pg], page_labels=[lab for _, lab in pg], **common)

        if fits(sec.lines):
            emit_piece(Piece(sec.lines), "section", f"{act_id}:s{sid}", f"{short}, section {sid}")
            return
        if is_interpretation(sec):
            self._definition_chunks(sec, act_id, sid, short, fits, emit_piece)
            return
        if sec.subsections:
            units = _subsection_units(sec)
            for group in _pack(units, fits):
                if len(group) == 1 and not fits(group[0].lines):
                    self._paragraph_chunks(sec, group[0], act_id, sid, short, fits, emit_piece)
                    continue
                piece = _merge(group)
                rng = _range(piece.first, piece.last)
                emit_piece(piece, "subsection_group", f"{act_id}:s{sid}{rng}",
                           f"{short}, section {sid}{_cite_range(piece.first, piece.last)}")
            return
        whole = Piece(sec.lines)
        self._paragraph_chunks(sec, whole, act_id, sid, short, fits, emit_piece)

    def _paragraph_chunks(self, sec, unit: Piece, act_id, sid, short, fits, emit_piece):
        """Split one unit (a subsection, or a section without subsections) at its paragraphs."""
        units = _paragraph_units(sec, unit)
        prefix = unit.first or ""  # the subsection label, if the unit is a subsection
        for group in _pack(units, fits):
            piece = _merge(group)
            if not fits(piece.lines):
                raise ValueError(f"{act_id} s{sid}{prefix}: paragraph {piece.first} alone exceeds {CAP} "
                                 "tokens; no finer split is designed")
            rng = _range(piece.first, piece.last)
            emit_piece(Piece(piece.lines, subsections=(prefix,) if prefix else ()), "paragraph_group",
                       f"{act_id}:s{sid}{prefix}{rng}",
                       f"{short}, section {sid}{prefix}{_cite_range(piece.first, piece.last)}")

    def _definition_chunks(self, sec, act_id, sid, short, fits, emit_piece):
        first_def_line = sec.definitions[0].lines[0]
        lead = sec.lines[:next(i for i, ln in enumerate(sec.lines) if ln is first_def_line)]
        seen: dict[str, int] = {}
        for k, d in enumerate(sec.definitions):
            lines = (lead if k == 0 else []) + d.lines
            s = slug(d.label)
            seen[s] = seen.get(s, 0) + 1
            if seen[s] > 1:
                s = f"{s}-{seen[s]}"
            base_id = f"{act_id}:s{sid}:def:{s}"
            base_cite = f'{short}, section {sid}, definition of "{d.label}"'
            if fits(lines):
                emit_piece(Piece(lines), "definition", base_id, base_cite)
                continue
            # A long definition (a list of items) is split at its lettered paragraphs.
            for group in _pack(_paragraph_units(sec, Piece(lines)), fits):
                piece = _merge(group)
                if not fits(piece.lines):
                    raise ValueError(f"{act_id} s{sid}: definition {d.label!r} paragraph {piece.first} "
                                     f"alone exceeds {CAP} tokens")
                emit_piece(piece, "definition", f"{base_id}{_range(piece.first, piece.last)}",
                           f"{base_cite}, paragraph{'s' if piece.first != piece.last else ''} "
                           f"{_cite_range(piece.first, piece.last)}")

    # schedules ------------------------------------------------------------------------------
    def schedule_chunks(self, act: ParsedAct, ordinal: str, tables: list[dict]):
        act_id = act.act_id
        sched = act.schedule(ordinal)
        number = ORDINAL_NUMBER[ordinal]
        short = ACTS[act_id]["short"]
        lines = list(sched.lines)
        part = None
        if ordinal == "First":
            lines, part = _first_schedule_part(lines, NTA_FIRST_SCHEDULE_PARTS[0])
        title = _schedule_title(sched)
        schedule_obj = {"number": ordinal, "title": title, "part": part[0] if part else None}
        sch_id = f"{act_id}:sch{number}" + (f":pt{_roman_to_int(part[0])}" if part else "")
        sch_cite = f"{short}, {ordinal} Schedule" + (f", Part {part[0]}" if part else "")
        header = context_header(act_id, None, None,
                                f"{ordinal} Schedule" + (f" > Part {part[0]} {part[1]}" if part else "")
                                + (f" > {title}" if title else ""))
        my_tables = [t for t in tables if t["schedule"] == ordinal]
        table_lines: set[int] = set()
        for t in my_tables:
            region = _table_region_lines(lines, t)
            table_lines.update(id(ln) for ln in region)
        body = [ln for ln in lines if id(ln) not in table_lines]
        vocab = act.vocab

        def text_of(ls):
            return join_lines([ln.text for ln in ls], vocab)

        def fits(ls):
            return cl100k_tokens(header + "\n" + text_of(ls)) <= CAP

        common = dict(act_id=act_id, chapter=None, part=None, schedule=schedule_obj, section=None,
                      subsections=(), na_section=None, heading=title, heading_source="schedule_title",
                      header=header, text_source="pdf_text")
        units = _schedule_paragraph_units(body) if body else []
        table_lead: list[str] = []
        if units and units[0].first is None and my_tables:
            # A schedule that is only a table: its reference and title lines open the table chunk.
            table_lead = [text_of(body)]
        elif units:
            if fits(body):
                self.emit(chunk_id=sch_id, citation=sch_cite, unit_type="schedule_paragraph",
                          text=text_of(body), pages=[ln.page for ln in body],
                          page_labels=[ln.page_label for ln in body], **common)
            else:
                for group in _pack(units, fits):
                    piece = _merge(group)
                    if not fits(piece.lines):
                        self._split_schedule_paragraph(piece, sch_id, sch_cite, fits, text_of, common)
                        continue
                    rng = f"{piece.first}" if piece.first == piece.last else f"{piece.first}-{piece.last}"
                    cite = (f"paragraph {piece.first}" if piece.first == piece.last
                            else f"paragraphs {piece.first} to {piece.last}")
                    self.emit(chunk_id=f"{sch_id}:para{rng}", citation=f"{sch_cite}, {cite}",
                              unit_type="schedule_paragraph", text=text_of(piece.lines),
                              pages=[ln.page for ln in piece.lines],
                              page_labels=[ln.page_label for ln in piece.lines], **common)
        for t in my_tables:
            rows, header_line, page_label = extract_table(t)
            row_texts = [" | ".join(f"{col}: {cell}" for col, cell in zip(t["columns"], row)) for row in rows]
            # The configured title is used only when the schedule's own title block does not open the chunk.
            lead = (table_lead or [t["title"]]) + [" | ".join(t["columns"])]
            groups = _pack_texts(row_texts, lambda ts: cl100k_tokens(header + "\n" + "\n".join(lead + ts)) <= CAP)
            for gi, (start, group) in enumerate(groups):
                suffix = "" if len(groups) == 1 else f":rows{start + 1}-{start + len(group)}"
                self.emit(chunk_id=f"{sch_id}:{t['id']}{suffix}",
                          citation=f"{sch_cite}, {t['title']}" + (f", rows {start + 1} to {start + len(group)}"
                                                                  if suffix else ""),
                          unit_type="schedule_table", text="\n".join(lead + group),
                          pages=[t["page"]], page_labels=[page_label], **common)


    def _split_schedule_paragraph(self, piece: Piece, sch_id, sch_cite, fits, text_of, common):
        """An oversize schedule paragraph: one chunk per definition when it is a definitions
        list ('1. For the purposes of this Part –' …), else packed sub-paragraphs (1), (2) …"""
        n = piece.first
        starts = definition_starts([ln.text for ln in piece.lines])
        if len(starts) >= 3:
            defs = [i for i, _ in starts]
            bounds = defs + [len(piece.lines)]
            seen: dict[str, int] = {}
            for k, (start, term) in enumerate(starts):
                lines = piece.lines[0 if k == 0 else start:bounds[k + 1]]
                s = slug(term)
                seen[s] = seen.get(s, 0) + 1
                s = s if seen[s] == 1 else f"{s}-{seen[s]}"
                groups = [Piece(lines)] if fits(lines) else [_merge(g) for g in _pack(_letter_units(lines), fits)]
                for sub in groups:
                    if not fits(sub.lines):
                        raise ValueError(f"{sch_id} paragraph {n}: definition {term!r} part {sub.first} "
                                         f"exceeds {CAP} tokens")
                    rng = _range(sub.first, sub.last)
                    self.emit(chunk_id=f"{sch_id}:para{n}:def:{s}{rng}",
                              citation=f'{sch_cite}, paragraph {n}, definition of "{term}"'
                                       + (f", paragraph{'s' if sub.first != sub.last else ''} "
                                          f"{_cite_range(sub.first, sub.last)}" if rng else ""),
                              unit_type="schedule_paragraph", text=text_of(sub.lines),
                              pages=[ln.page for ln in sub.lines],
                              page_labels=[ln.page_label for ln in sub.lines], **common)
            return
        subs = [i for i, ln in enumerate(piece.lines) if i and SUBSECTION.match(ln.text)]
        if not subs:
            raise ValueError(f"{sch_id} paragraph {n} exceeds {CAP} tokens and has no sub-paragraphs")
        bounds = [0] + subs + [len(piece.lines)]
        units = []
        for k in range(len(bounds) - 1):
            lines = piece.lines[bounds[k]:bounds[k + 1]]
            m = SUBSECTION.match(lines[0].text) or SUB_IN_FIRST_LINE.match(lines[0].text)
            label = f"({m.group(1)})" if m else None
            units.append(Piece(lines, label, label))
        for group in _pack(units, fits):
            sub = _merge(group)
            if not fits(sub.lines):
                raise ValueError(f"{sch_id} paragraph {n}{sub.first}: exceeds {CAP} tokens")
            self.emit(chunk_id=f"{sch_id}:para{n}{_range(sub.first, sub.last)}",
                      citation=f"{sch_cite}, paragraph {n}{_cite_range(sub.first, sub.last)}",
                      unit_type="schedule_paragraph", text=text_of(sub.lines),
                      pages=[ln.page for ln in sub.lines], page_labels=[ln.page_label for ln in sub.lines],
                      **common)


# --- helpers: structure ---------------------------------------------------------------------------

def is_interpretation(sec: Section) -> bool:
    """An interpretation section: three or more quoted-term definitions, opened by an
    'In this Act' style lead-in or headed 'interpretation'."""
    if len(sec.definitions) < 3:
        return False
    first = sec.lines[0].text
    return bool(re.search(r"\bIn [Tt]his (Act|Part|Chapter)|For the purposes? of this (Act|Part)", first)) or \
        "interpretation" in (sec.heading or "").lower()


def _subsection_units(sec: Section) -> list[Piece]:
    units = []
    first_sub_line = sec.subsections[0].lines[0]
    lead = sec.lines[:next(i for i, ln in enumerate(sec.lines) if ln is first_sub_line)]
    for k, sub in enumerate(sec.subsections):
        lines = (lead if k == 0 else []) + sub.lines
        units.append(Piece(lines, sub.label, sub.label, (sub.label,)))
    return units


def _paragraph_units(sec: Section, unit: Piece) -> list[Piece]:
    """Letter paragraphs inside `unit`; roman sub-paragraphs stay with their letter."""
    ids = {id(ln) for ln in unit.lines}
    starts: list[tuple[int, str]] = []  # (index in unit.lines, label)
    expected = 0
    for para in sec.paragraphs:
        if id(para.lines[0]) not in ids:
            continue
        label = para.label.strip("()")
        if expected < len(LETTERS) and label == LETTERS[expected]:
            idx = next(i for i, ln in enumerate(unit.lines) if ln is para.lines[0])
            starts.append((idx, para.label))
            expected += 1
    if not starts:
        return [Piece(unit.lines, unit.first, unit.last, unit.subsections)]
    units = []
    for k, (idx, label) in enumerate(starts):
        begin = 0 if k == 0 else idx
        end = starts[k + 1][0] if k + 1 < len(starts) else len(unit.lines)
        units.append(Piece(unit.lines[begin:end], label, label))
    return units


def _letter_units(lines: list[SourceLine]) -> list[Piece]:
    """Lettered paragraphs (a), (b) … found directly in lines (for text with no parsed paragraph
    list, such as schedule definitions). A label counts only as the next letter, after a line
    that ends a clause; the lines before (a) stay with (a)."""
    starts: list[tuple[int, str]] = []
    expected = 0
    for i, ln in enumerate(lines):
        m = PARAGRAPH.match(ln.text)
        if (m and i and expected < len(LETTERS) and m.group(1) == LETTERS[expected]
                and CLAUSE_END.search(lines[i - 1].text)):
            starts.append((i, f"({m.group(1)})"))
            expected += 1
    if not starts:
        return [Piece(lines)]
    units = []
    for k, (idx, label) in enumerate(starts):
        begin = 0 if k == 0 else idx
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        units.append(Piece(lines[begin:end], label, label))
    return units


def _pack(units: list[Piece], fits) -> list[list[Piece]]:
    groups: list[list[Piece]] = []
    for u in units:
        if groups and fits([ln for p in groups[-1] + [u] for ln in p.lines]):
            groups[-1].append(u)
        else:
            groups.append([u])
    return groups


def _pack_texts(texts: list[str], fits) -> list[tuple[int, list[str]]]:
    groups: list[tuple[int, list[str]]] = []
    for i, t in enumerate(texts):
        if groups and fits(groups[-1][1] + [t]):
            groups[-1][1].append(t)
        else:
            groups.append((i, [t]))
    return groups


def _merge(group: list[Piece]) -> Piece:
    return Piece([ln for p in group for ln in p.lines], group[0].first, group[-1].last,
                 tuple(s for p in group for s in p.subsections))


def _range(first, last) -> str:
    if first is None:
        return ""
    return first if first == last else f"{first}-{last}"


def _cite_range(first, last) -> str:
    if first is None:
        return ""
    return first if first == last else f"{first} to {last}"


def _roman_to_int(roman: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    for a, b in zip(roman, roman[1:] + " "):
        total += -values[a] if values.get(b, 0) > values[a] else values[a]
    return total


def _label_key(label: str):
    m = re.search(r"\d+", label or "")
    return (int(m.group()) if m else 0, label)


PART_LINE = re.compile(r"^PART\s+([IVXL]+)\s*[—–-]\s*(.*)$")


def _first_schedule_part(lines: list[SourceLine], wanted: str):
    """Lines of one Part of the First Schedule, from its PART heading to the next PART heading."""
    out, part, inside = [], None, False
    for k, ln in enumerate(lines):
        m = PART_LINE.match(ln.text)
        if m:
            if inside:
                break
            if m.group(1) == wanted:
                inside = True
                title = m.group(2).strip()
                # The heading wraps onto the next line in the gazette ("… UNDER" / "CHAPTER TWO OF THIS ACT").
                nxt = lines[k + 1].text if k + 1 < len(lines) else ""
                if title.endswith(("UNDER", "FOR", "OF", "THE")) and nxt.isupper():
                    title = f"{title} {nxt}"
                part = (wanted, title)
        if inside:
            out.append(ln)
    if not out:
        raise ValueError(f"First Schedule Part {wanted} not found")
    return out, part


def _schedule_title(sched) -> str | None:
    """The schedule's capitalised title line (after the 'Sections …' reference lines)."""
    for ln in sched.lines[:12]:
        t = ln.text
        if t.isupper() and len(t) > 3 and not t.startswith(("PART", "SECTION", "SUB-SECTOR")):
            return t
    return None


CROSS_HEADING = re.compile(r"^[A-Z][A-Za-z ,'()-]{2,80}$")


def _schedule_paragraph_units(lines: list[SourceLine]) -> list[Piece]:
    """Paragraphs of a schedule: numbered "1.", "2." … restarting after a PART heading. A short
    unpunctuated cross-heading just before a paragraph belongs to that paragraph."""
    starts: list[tuple[int, str]] = []
    expected = 1
    for i, ln in enumerate(lines):
        if PART_LINE.match(ln.text):
            expected = 1
            continue
        m = SCHEDULE_PARA.match(ln.text)
        if m and int(m.group(1)) == expected:
            begin = i
            if i > 0 and CROSS_HEADING.match(lines[i - 1].text) and not lines[i - 1].text.endswith(
                    (".", ";", ":", ",")) and len(lines[i - 1].text.split()) <= 8 and starts:
                begin = i - 1
            starts.append((begin, m.group(1)))
            expected += 1
    if not starts:
        return [Piece(lines)]
    units = []
    for k, (idx, label) in enumerate(starts):
        begin = 0 if k == 0 else idx
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        units.append(Piece(lines[begin:end], label, label))
    return units


# --- helpers: tables ------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def table_config() -> list[dict]:
    return yaml.safe_load(TABLES.read_text(encoding="utf-8"))["tables"]


def _table_region_lines(lines: list[SourceLine], t: dict) -> list[SourceLine]:
    """Parser lines that belong to a configured table (excluded from paragraph text)."""
    on_page = [ln for ln in lines if ln.page == t["page"]]
    start = next((i for i, ln in enumerate(on_page)
                  if letters(ln.text).startswith(letters(t["region_starts_with"]))), None)
    if start is None:
        raise ValueError(f"table {t['schedule']} {t['id']}: region start not found on page {t['page']}")
    end = len(on_page)
    if t["ends_before"]:
        end = next((i for i, ln in enumerate(on_page)
                    if i > start and letters(ln.text).startswith(letters(t["ends_before"]))), None)
        if end is None:
            raise ValueError(f"table {t['schedule']} {t['id']}: end anchor not found")
    return on_page[start:end]


@lru_cache(maxsize=None)
def _table_words(file: str, page: int) -> tuple:
    with pdfplumber.open(str(DATA / "raw" / file)) as pdf:
        pg = pdf.pages[page - 1]
        return tuple((w["text"], round(w["x0"], 1), round(w["top"], 1), round(w["bottom"], 1))
                     for w in pg.extract_words(keep_blank_chars=False, use_text_flow=False))


def extract_table(t: dict) -> tuple[list[list[str]], str, str | None]:
    """Logical rows of a configured table, checked against the words in its region.

    Columns split at the x-position of each header word; logical rows are separated by vertical
    space (see the rule in the loop).
    Returns (rows, header line text, page label).
    """
    act = parse_cached(t["file"])
    region = _table_region_lines([ln for s in act.schedules for ln in s.lines], t)
    header_line = next(ln for ln in region if letters(ln.text).startswith(letters(t["header_starts_with"])))
    body_lines = region[region.index(header_line) + 1:]
    words = _table_words(t["file"], t["page"])
    tol = 3.0  # words within 3 pt vertically are on one printed line ("Forestry" / "Plantation of")
    hdr = [w for w in words if abs(w[2] - header_line.top) <= tol]
    starts = []
    for name in t["column_starts"]:
        w = next((w for w in hdr if letters(w[0]) == letters(name)), None)
        if w is None:
            raise ValueError(f"table {t['schedule']}: header word {name!r} not found")
        starts.append(w[1])
    top = header_line.top + 4
    bottom = (max(ln.top for ln in body_lines) + 14) if body_lines else top
    cell_words = sorted((w for w in words if top < w[2] <= bottom), key=lambda w: (w[2], w[1]))
    # group words into physical lines by top
    lines: list[list[tuple]] = []
    for w in cell_words:
        if lines and abs(lines[-1][0][2] - w[2]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    pitch = sorted(b[0][2] - a[0][2] for a, b in zip(lines, lines[1:]))
    pitch = pitch[len(pitch) // 2] if pitch else 0
    rows: list[list[list[str]]] = []
    prev_top = None
    for line in lines:
        cells = [[] for _ in starts]
        for w in sorted(line, key=lambda w: w[1]):
            col = max(i for i, x in enumerate(starts) if w[1] >= x - 3) if w[1] >= starts[0] - 3 else 0
            cells[col].append(normalise_line(w[0]))
        gap = (line[0][2] - prev_top) if prev_top is not None else None
        # Rows are separated by space: more than 1.35 line pitches, or more than 1.1 when the first
        # column starts a new entry ("Dairy" follows "trees" by 1.16 pitches; "Crop" / "Production"
        # is one wrapped cell at 1.0).
        new_row = gap is None or gap > 1.35 * pitch or (bool(cells[0]) and gap > 1.1 * pitch)
        if new_row:
            rows.append([[] for _ in starts])
        for i, c in enumerate(cells):
            rows[-1][i].extend(c)
        prev_top = line[0][2]
    check_table_words(rows, body_lines, f"table {t['schedule']} {t['id']}")
    out = [[" ".join(c) if c else t["empty_cell"] for c in row] for row in rows]
    return out, header_line.text, header_line.page_label


def check_table_words(rows: list[list[list[str]]], body_lines: list[SourceLine], name: str) -> None:
    """Word-multiset check: the rebuilt cells use every word of the parser's lines for the table
    body exactly once. The two come from separate extractions (positioned words against the
    parser's text lines), so a word dropped or repeated by the row or column rules shows here."""
    used = sorted(w for row in rows for cell in row for w in cell)
    printed = sorted(w for ln in body_lines for w in ln.text.split())
    if used != printed:
        raise ValueError(f"{name}: rebuilt rows lose or repeat words")


# --- helpers: headers, cross-references, pages ---------------------------------------------------

def context_header(act_id: str, chapter, part, tail: str) -> str:
    parts = [ACTS[act_id]["short"]]
    if chapter:
        parts.append(f"Chapter {chapter['number']} {chapter['title']}".strip())
    if part:
        parts.append(f"Part {part['number']} {part['title']}".strip())
    parts.append(tail)
    return " > ".join(parts)


OTHER_ACT = {"nta2025": ("ntaa2025", re.compile(r"Nigeria Tax Administration Act")),
             "ntaa2025": ("nta2025", re.compile(r"Nigeria Tax Act"))}


def resolve_cross_refs(text: str, act_id: str, section: str | None) -> list[dict]:
    """Structured cross-references. Targets are resolved within the same Act; references to
    the other Act keep target null (its numbering in the reference is not verified)."""
    out = set()
    flat = re.sub(r"\s+", " ", text)
    for m in CROSS_REF.finditer(flat):
        raw = m.group(0).strip()
        if not re.search(r"[\d(]", raw) and re.match(r"(?i)^(?:sub)?(?:sections?|paragraphs?)\b", raw):
            continue  # a bare word ("this section shall …" reads as "section s"), not a reference
        other_id, other_pat = OTHER_ACT[act_id]
        target_act = other_id if other_pat.search(raw) else act_id
        targets: list[str | None] = []
        low = raw.lower()
        sm = re.match(r"^(sections?)\s+(.*)", low)
        if raw.endswith("Schedule") or "schedule" in low:
            om = re.search(r"(\w+) Schedule", raw)
            targets.append(f"sch{ORDINAL_NUMBER[om.group(1)]}" if om and om.group(1) in ORDINAL_NUMBER else None)
        elif sm:
            body = re.split(r"\s+of\s+", sm.group(2))[0]
            numbers = re.findall(r"(?<![\(\d])(\d{1,3})(?![\d\)])", body)
            if len(numbers) == 1:
                labels = re.findall(r"\(([0-9]{1,2}|[a-z]{1,3})\)", body)
                targets.append(f"s{numbers[0]}" + "".join(f"({x})" for x in labels))
            else:
                targets.extend(f"s{n}" for n in numbers)
        elif low.startswith("subsection") and section:
            labels = re.findall(r"\((\d{1,2})\)", raw)
            targets.extend(f"s{section}({x})" for x in labels) if labels else targets.append(None)
        elif low.startswith("chapter"):
            cm = re.match(r"^Chapter (\w+)", raw)
            targets.append(f"ch:{cm.group(1)}" if cm else None)
        else:
            targets.append(None)
        for tgt in targets or [None]:
            if target_act != act_id:
                tgt = None
            out.add((raw, target_act, tgt))
    return [{"raw": r, "act_id": a, "target": t} for r, a, t in sorted(out, key=lambda x: (x[0], x[2] or ""))]


def map_line_pages(src_lines: list[str], ref: list[SourceLine]) -> list[tuple[int, str | None]]:
    """Give each source line the page of the matching place in a reference text (letters only).

    Used to put NRS page numbers on Gombe or proofread lines: the letters of both are aligned,
    and each source line takes the page of the reference character its first letter maps to.
    """
    ref_text, ref_pages = [], []
    for ln in ref:
        lt = letters(ln.text)
        ref_text.append(lt)
        ref_pages.extend([(ln.page, ln.page_label)] * len(lt))
    ref_s = "".join(ref_text)
    src_s, starts = "", []
    for line in src_lines:
        starts.append(len(src_s))
        src_s += letters(line)
    mapping = {}
    for a, b, size in difflib.SequenceMatcher(None, src_s, ref_s, autojunk=False).get_matching_blocks():
        for k in range(size):
            mapping[a + k] = b + k
    out = []
    keys = sorted(mapping)
    for st in starts:
        k = next((x for x in keys if x >= st), keys[-1] if keys else None)
        out.append(ref_pages[mapping[k]] if k is not None and ref_pages else (ref[0].page, ref[0].page_label))
    return out


# --- the build ----------------------------------------------------------------------------------

def _chapter_obj(t):
    return {"number": t[0], "title": t[1]} if t else None


def respace(title: str, words: set[str]) -> str:
    """Restore word spaces lost by the OCR in a capitalised heading, using a word list.

    Display only (context headers): each run of letters is split into the fewest known words;
    a run that cannot be split is left as it is.
    """
    out = []
    for token in title.split():
        core = re.sub(r"[^A-Za-z]", "", token)
        low = core.lower()
        if not low or low in words:
            out.append(token)
            continue
        best: list[list[str] | None] = [[]] + [None] * len(low)
        for i in range(1, len(low) + 1):
            for j in range(max(0, i - 20), i):
                if best[j] is not None and low[j:i] in words and (best[i] is None or len(best[j]) + 1 < len(best[i])):
                    best[i] = best[j] + [low[j:i]]
        out.append(" ".join(w.upper() for w in best[-1]) if best[-1] else token)
    return " ".join(out)


def ntaa_title(nrs_title: tuple | None, gombe_title: tuple | None, words: set[str]) -> tuple | None:
    """Chapter or Part title for an NTAA context header: Gombe's when it reads the same letter for
    letter, otherwise the NRS reading with its spaces restored."""
    if nrs_title is None:
        return None
    if gombe_title and gombe_title[0] == nrs_title[0] and letters(gombe_title[1]) == letters(nrs_title[1]):
        return gombe_title
    return (nrs_title[0], respace(nrs_title[1], words))


def build() -> tuple[list[dict], list[str]]:
    b = Builder()
    tables = table_config()
    # NTA: cited gazette
    nta = parse_cached(NTA_GAZETTE)
    for n in NTA_IN_SCOPE:
        sec = nta.section(n)
        na = nta_na_section(n)
        b.section_chunks("nta2025", sec, nta.vocab, chapter=_chapter_obj(sec.chapter),
                         part=_chapter_obj(sec.part), heading=sec.heading,
                         heading_source=sec.heading_source, text_source="pdf_text",
                         na_section=str(na) if na else None,
                         pages_of=lambda ln: (ln.page, ln.page_label))
    for ordinal in NTA_SCHEDULES_IN_SCOPE:
        b.schedule_chunks(nta, ordinal, tables)
    # NTAA: NRS print cited; text from Gombe (identical) or the proofread file (different)
    nrs, gombe = parse_cached(NTAA_NRS), parse_cached(NTAA_GOMBE)
    status, proof = alignment_status(), proofread_index()
    for n in NTAA_IN_SCOPE:
        n_sec, g_sec = nrs.section(n), gombe.section(n)
        heading, heading_source = ntaa_heading(n_sec, g_sec, proof.get(n))
        # Chapter and Part numbers come from the NRS print (the Gombe print places the Chapter Two
        # heading before s10); titles from Gombe where they read the same.
        words = gombe.vocab[1]
        chapter = ntaa_title(n_sec.chapter, g_sec.chapter, words)
        part = ntaa_title(n_sec.part, g_sec.part, words)
        if status[n] == "identical":
            sec, vocab, source = g_sec, gombe.vocab, "gombe_verified_identical"
        else:
            text = (PROOF_DIR / f"s{n:03d}.txt").read_text(encoding="utf-8")
            plines = [ln for ln in text.splitlines() if ln.strip()]
            pg = map_line_pages(plines, n_sec.lines)
            sec, issues = parse_section_text(text, n, [p for p, _ in pg], [lab for _, lab in pg])
            b.notes.extend(f"ntaa s{n} proofread: {x}" for x in issues)
            vocab, source = gombe.vocab, "nrs_proofread"
        line_pages = dict(zip((id(ln) for ln in sec.lines),
                              map_line_pages([ln.text for ln in sec.lines], n_sec.lines)))
        b.section_chunks("ntaa2025", sec, vocab, chapter=_chapter_obj(chapter), part=_chapter_obj(part),
                         heading=heading, heading_source=heading_source, text_source=source,
                         na_section=str(n), pages_of=lambda ln, lp=line_pages: lp[id(ln)])
    apply_register(b.chunks)
    return b.chunks, b.notes


def ntaa_heading(n_sec: Section, g_sec: Section, proof_row: dict | None) -> tuple[str, str]:
    """NTAA display heading: the Gombe marginal note or arrangement title when it matches the NRS
    reading letter for letter (the NRS OCR loses spaces); else the proofread index heading."""
    nrs_reading = letters(n_sec.heading or "")
    nrs_toc = letters(n_sec.toc_title or "")
    if g_sec.heading and letters(g_sec.heading) == nrs_reading:
        return g_sec.heading, "gombe_margin"
    if g_sec.toc_title and letters(g_sec.toc_title) == nrs_reading:
        return g_sec.toc_title, "gombe_arrangement"
    # The NRS marginal note can be cut short by the OCR ("Paymentof"); the NRS print's own
    # arrangement of sections is a second letter-for-letter reading of the same title.
    if g_sec.heading and letters(g_sec.heading) == nrs_toc:
        return g_sec.heading, "gombe_margin_nrs_arrangement"
    if proof_row:
        return proof_row["heading"], "proofread_index"
    return n_sec.heading, "nrs_ocr"


def apply_register(chunks: list[dict]) -> None:
    """Set discrepancy flags from data/curated/discrepancy_register.csv when it exists (2b-6).

    A row applies to the chunk its cited_unit names (a full chunk_id). The rule row DR-000 names
    no chunk and flags none.
    """
    if not REGISTER.is_file():
        return
    with REGISTER.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_unit: dict[str, list[str]] = {}
    for r in rows:
        if r["cited_unit"]:
            by_unit.setdefault(r["cited_unit"], []).append(r["register_id"])
    for c in chunks:
        ids = by_unit.get(c["chunk_id"], [])
        c["discrepancy"] = {"flag": bool(ids), "register_ids": sorted(ids)}


def to_jsonl(chunks: list[dict]) -> bytes:
    return "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks).encode("utf-8")
