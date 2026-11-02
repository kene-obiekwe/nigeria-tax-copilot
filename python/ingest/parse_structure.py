"""Structure parser for the statute PDFs (chunking design, section 3).

A line-level state machine over the body text from extract.py, in page order:

    front_matter -> body (chapters, parts, sections) -> schedules

Section starts are accepted only as the next expected number; any other match is logged as an
anomaly and treated as text. Subsection and paragraph labels are kept exactly as printed.
Marginal notes become section headings when they sit level with the section's first line.

Per-file tolerances live in FILE_PROFILES and are listed in docs/phase02_chunking/build_notes.md.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ingest.extract import (PAGE_LABEL, Line, Page, extract_pages, hyphen_vocabulary, join_lines,
                            normalise_line)

# --- patterns ---------------------------------------------------------------------------------

DASH = r"[—–-]"
SECTION_START = re.compile(rf"^(\d{{1,3}})\s?\.\s?(?:{DASH}\s?)?(?:\.\s)?(?=\(1\)|[A-Z“\"(])")
# Tolerance (profile.dash_without_period): "148—(1)" with the full stop missing.
SECTION_START_NO_PERIOD = re.compile(rf"^(\d{{1,3}}){DASH}(?=\(1\))")
SUB_IN_FIRST_LINE = re.compile(rf"^\d{{1,3}}\s?\.?\s?(?:{DASH}\s?)?\((\d{{1,2}}[A-Z]?)\)")
SUBSECTION = re.compile(r"^\((\d{1,2}[A-Z]?)\)\s(?=[A-Z“\"(])")  # "(2) For …", not "(2) of …"
PARAGRAPH = re.compile(r"^\(([a-z]{1,2}|[ivx]{1,5})\)\s")
# A line that can precede a new unit: ends in clause punctuation (optionally followed by a
# closing quote or bracket) or in "and"/"or".
CLAUSE_END = re.compile(r"(?:[.;:,—–-][”\"')\]]*|\band|\bor)$")
# A definition opens with a quoted term at the start of a line ('"small company" means …',
# '"taxable goods and services" is as defined …', '"Nigeria" for the purpose of this Act, means …').
# The gazette often closes the term with two apostrophes ('"lender'' means') or one
# ('"goods' for the purposes'); an apostrophe inside a term ("arm's length") is not a closer.
TERM_CLOSE = r"(?:[”\"]|''|'(?=[\s,]))"
DEFINITION = re.compile(rf"^[“\"]([^”\"]{{1,80}}?){TERM_CLOSE}\s*(?=[a-z(,—–-])")
# A term that wraps: the line opens a quote and never closes it, and the next line closes it
# ('"farming machinery and implements and farming transportation' / 'equipment" means …').
WRAPPED_TERM = re.compile(r"^[“\"]([^”\"']{3,80})$")
TERM_END = re.compile(rf"^([^”\"']{{1,80}}?){TERM_CLOSE}\s*(?=[a-z(,—–-])")
ORDINALS = ("ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN").split()
CHAPTER = re.compile(rf"^CHAPTER\s+({'|'.join(ORDINALS)}|\d+)\b\s*(?:{DASH}\s*(.*))?$")
PART = re.compile(rf"^PART\s+([IVXL]+|\d+)\b\s*(?:{DASH}\s*(.*))?$")
SCHEDULE_WORDS = ("First Second Third Fourth Fifth Sixth Seventh Eighth Ninth Tenth Eleventh "
                  "Twelfth Thirteenth Fourteenth").split()
SCHEDULE = re.compile(rf"^({'|'.join(SCHEDULE_WORDS)})\s+Schedule$", re.I)
SCHEDULE_PARA = re.compile(rf"^(\d{{1,3}})\s?\.\s?(?:{DASH}\s?)?(?=\(1\)|[A-Z“\"(])")
LONG_TITLE = re.compile(r"^AN ACT TO\b")
CROSS_REF = re.compile(
    r"\b(?:sections?|subsections?|paragraphs?)\s+\(?\d*[a-z]?\)?(?:\s?\(\d{1,2}\))*(?:\s?\([a-z]{1,3}\))*"
    r"(?:\s*(?:,|and|or|to)\s*\(?\d{1,3}\)?(?:\s?\(\d{1,2}\))*(?:\s?\([a-z]{1,3}\))*)*"
    r"(?:\s+of\s+(?:this Act|the [A-Z][A-Za-z ]+Act(?:,? \d{4})?))?"
    r"|\b(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|Eleventh|Twelfth|"
    r"Thirteenth|Fourteenth)\s+Schedule(?:\s+to\s+this\s+Act)?"
    r"|\bChapter\s+(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine)(?:\s+of\s+this\s+Act)?"
    r"|\bPart\s+[IVX]+(?:\s+of\s+(?:this\s+)?Chapter\s+\w+)?"
)
HEADING_ALIGN = 12.0  # max vertical distance (pt) between a section's first line and its note
NOTE_LINE_GAP = 14.0  # margin lines closer than this belong to the same note
TOC_MATCH_MIN = 0.5  # heading vs arrangement-of-sections title similarity below this is logged


# --- per-file profiles --------------------------------------------------------------------------

@dataclass(frozen=True)
class FileProfile:
    act_id: str
    expected_sections: int
    fullsize_margin_notes: bool = False  # extract.py rule 2
    dash_without_period: bool = False  # accept "148—(1)" as a section start
    ocr_dir: str | None = None  # read committed OCR boxes (repo-relative) instead of the PDF
    note_line_gap: float = NOTE_LINE_GAP  # margin lines closer than this belong to one note
    align_tolerance: float = 3.0  # a note line this close to another section's start ends a note
    note: str = ""


FILE_PROFILES = {
    "nigeria_tax_act_2025_gazette_lirs.pdf": FileProfile("nta2025", 203),
    "nigeria_tax_act_2025_nass.pdf": FileProfile(
        "nta2025", 202, fullsize_margin_notes=True, dash_without_period=True,
        note="Some pages set marginal notes at body size (extract.py rule 2); section 148 is "
             "printed '148—(1)' without a full stop."),
    "nigeria_tax_administration_act_2025_gazette_gombe_irs.pdf": FileProfile("ntaa2025", 148),
    "nigeria_tax_administration_act_2025_nass.pdf": FileProfile(
        "ntaa2025", 148, note="Section 15 is printed '15. . Notwithstanding'; the general section "
                              "pattern allows the stray '. ', so no special tolerance is needed."),
    "nigeria_tax_administration_act_2025_gazette_nrs.pdf": FileProfile(
        "ntaa2025", 148, ocr_dir="data/derived/ocr/ntaa_nrs", note_line_gap=20.0, align_tolerance=6.0,
        note="Scan; parsed from the committed RapidOCR boxes with the OCR line fixes (OCR_FIXES). "
             "OCR line positions are looser than PDF text, so note grouping is wider."),
}

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"


# --- OCR input (the NRS scan, chunking design section 2) -----------------------------------------

OCR_PAGE_WIDTH = 612.0  # boxes are rescaled to a 612-pt page so the PDF thresholds apply
OCR_GUTTER = 0.005  # fraction of page width between the body column and a marginal note
OCR_RIGHT_NOTE_START = 0.72  # on right-margin pages, a box starting this far right is a note
# OCR-only line fixes, applied in order. Each is a recognition error seen in the NRS scan, not a
# change to the printed text; every one is listed in docs/phase02_chunking/build_notes.md.
OCR_FIXES = [
    # O1: spaces inside a label: "(1 )", "( 2)" -> "(1)", "(2)".
    (re.compile(r"\(\s+(\w{1,4})\s*\)|\((\w{1,4})\s+\)"), lambda m: f"({m.group(1) or m.group(2)})"),
    # O2: "l" or "I" read for "1", or the digit lost, in the subsection label of a section's
    # first line: "10. (l)", "94.()".
    (re.compile(r"^(\d{1,3}\s?\.\s?(?:[—–-]\s?)?)\(([lI]?)\)"), lambda m: f"{m.group(1)}(1)"),
    # O3: a digit label containing "l" or "I": "(1l)" -> "(11)", "(l2)" -> "(12)".
    (re.compile(r"^\((\d[lI]|[lI]\d)\)"), lambda m: "(" + m.group(1).replace("l", "1").replace("I", "1") + ")"),
    # O4: no space after a leading label: "(2)An application", "(a)name" -> "(2) An", "(a) name".
    (re.compile(r"^(\(\w{1,5}\))(?=[A-Za-z“\"])"), lambda m: m.group(1) + " "),
    # O5: no space after CHAPTER or PART in a heading: "CHAPTERTWO", "PARTII —"; "=" read for
    # the dash ("PARTII=REGISTRATION"); a Part heading read in title case ("Part II-Offences").
    (re.compile(r"^CHAPTER(?=[A-Z])"), lambda m: "CHAPTER "),
    (re.compile(r"^(?:PART|Part)\s?([IVXL]+)\s*[—–=-]\s*(?=[A-Z])"), lambda m: f"PART {m.group(1)} – "),
    # O6: the long title, the parser's front-matter anchor, read without spaces: "ANACTTO PROVIDE".
    (re.compile(r"^AN\s?ACT\s?TO(?=\s?[A-Z])\s?"), lambda m: "AN ACT TO "),
    # O7: "l"/"I" for 1 and "O" for 0 inside a leading number that has at least one real digit
    # and is followed by a full stop: "10l.Failure" -> "101.Failure".
    (re.compile(r"^(?=[\dlIO]{2,3}\s?\.)(?=[lIO]*\d)([\dlIO]{2,3})(?=\s?\.)"),
     lambda m: m.group(1).replace("l", "1").replace("I", "1").replace("O", "0")),
    # O8: a schedule heading read as one word: "FirstSchedule".
    (re.compile(rf"^({'|'.join(SCHEDULE_WORDS)})(?=Schedule$)", re.I), lambda m: m.group(1) + " "),
    # O10: the naira sign kept as "N" when the space before it is lost: "ofN5,000,000".
    (re.compile(r"(?<=[a-z])N(?=\d)"), lambda m: " ₦"),
]
# O9: the detector sometimes reads a marginal note and the body line beside it as one box
# ("Functions of 94.() The Revenue …", "… petroleum operations Actual"). Such boxes are split
# before the margin split: at an embedded section number when the note is on the left, or at
# the word boundary nearest the column edge when it is on the right.
MERGED_PREFIX = re.compile(r"^(.{2,45}?)\s+(\d{1,3}\s?\.\s?(?:[—–-]\s?)?(?:\(|[A-Z]))")


def _median(values: list[float]) -> float:
    values = sorted(values)
    return values[len(values) // 2]


def _ocr_fix(text: str) -> str:
    for pattern, repl in OCR_FIXES:
        text = pattern.sub(repl, text)
    return text


def _ocr_lines(boxes: list[dict], scale: float) -> list[Line]:
    """Group boxes into lines by vertical centre, left to right; coordinates in points."""
    rows: list[list[dict]] = []
    for b in sorted(boxes, key=lambda b: (b["cy"], b["x0"])):
        if rows and abs(b["cy"] - rows[-1][0]["cy"]) <= 0.5 * min(b["h"], rows[-1][0]["h"]):
            rows[-1].append(b)
        else:
            rows.append([b])
    out = []
    for row in rows:
        row.sort(key=lambda b: b["x0"])
        text = _ocr_fix(normalise_line(" ".join(b["text"] for b in row)))
        if text:
            top = min(b["cy"] - b["h"] / 2 for b in row) * scale
            out.append(Line(text=text, top=round(top, 1), x0=round(row[0]["x0"] * scale, 1)))
    return out


def _split_box(box: dict, at: int, boundary_x: float) -> tuple[dict, dict]:
    left_text, right_text = box["text"][:at].rstrip(), box["text"][at:].lstrip()
    return ({**box, "text": left_text, "x1": boundary_x}, {**box, "text": right_text, "x0": boundary_x})


def _split_merged(boxes: list[dict], side: str | None, left: float, right: float, width: int) -> list[dict]:
    """Rule O9: split boxes that read a marginal note together with its body line."""
    out = []
    edge_tol = 3 * OCR_GUTTER * width
    for b in boxes:
        text = b["text"]
        if side == "left" and b["x0"] < left - edge_tol and b["x1"] > left + edge_tol:
            m = MERGED_PREFIX.match(text)
            if m:
                note, body = _split_box(b, m.start(2), left - OCR_GUTTER * width)
                out += [note, body]
                continue
        if side == "right" and b["x1"] > right + edge_tol and b["x0"] < right - edge_tol:
            guess = round(len(text) * (right - b["x0"]) / (b["x1"] - b["x0"]))
            spaces = [i for i, ch in enumerate(text) if ch == " " and abs(i - guess) <= 6]
            if spaces:
                at = min(spaces, key=lambda i: abs(i - guess))
                body, note = _split_box(b, at, right + OCR_GUTTER * width)
                out += [body, note]
                continue
        out.append(b)
    return out


def pages_from_ocr(ocr_dir: str | Path) -> list[Page]:
    """Build Page records from committed OCR boxes (pNNN.json); no OCR library is needed."""
    pages = []
    for path in sorted(Path(ocr_dir).glob("p[0-9][0-9][0-9].json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        width, boxes = record["width"], record["boxes"]
        scale = OCR_PAGE_WIDTH / width
        if not boxes:
            pages.append(Page(index=record["page"], label=None, header=""))
            continue
        heights = sorted(b["h"] for b in boxes)
        median_h = heights[len(heights) // 2]
        top_cy = min(b["cy"] for b in boxes)
        header = [b for b in boxes if b["cy"] <= top_cy + 0.5 * median_h]
        rest = [b for b in boxes if b not in header]
        header_text = normalise_line(" ".join(b["text"] for b in sorted(header, key=lambda b: b["x0"])))
        label_match = PAGE_LABEL.search(header_text)
        side = None
        label_box = next((b for b in header if PAGE_LABEL.search(normalise_line(b["text"]))), None)
        if label_box is not None:
            side = "left" if label_box["x0"] < width / 2 else "right"
        # Column edges from the median long line: a few lines overrun the justified edge (page 23
        # has one ending at 0.84 of the width while the notes start at 0.79).
        long = [b for b in rest if b["x1"] - b["x0"] > 0.45 * width]
        left = _median([b["x0"] for b in long]) if long else 0.0
        right = _median([b["x1"] for b in long]) if long else float(width)
        gutter = OCR_GUTTER * width
        # For O9 the left edge is where continuation lines start (a low percentile), not the
        # median, which sits at the indented first lines.
        xs0 = sorted(b["x0"] for b in long)
        continuation_left = xs0[len(xs0) // 10] if xs0 else 0.0
        rest = _split_merged(rest, side, continuation_left, right, width)

        def is_margin(b):
            beyond_left = b["x1"] < left - gutter
            # Right-hand notes can start just inside the median column edge (0.76 to 0.79 of the
            # width); no body line starts that far right, so a box starting at or beyond
            # OCR_RIGHT_NOTE_START of the width is a note too.
            beyond_right = b["x0"] > right + gutter or b["x0"] >= OCR_RIGHT_NOTE_START * width
            if side == "left":
                return beyond_left
            if side == "right":
                return beyond_right
            return beyond_left or beyond_right

        pages.append(Page(
            index=record["page"],
            label=f"A {label_match.group(1)}" if label_match else None,
            header=header_text,
            body=_ocr_lines([b for b in rest if not is_margin(b)], scale),
            margin=_ocr_lines([b for b in rest if is_margin(b)], scale),
        ))
    if not pages:
        raise FileNotFoundError(f"no OCR page files (pNNN.json) in {ocr_dir}")
    return pages


# --- data model -------------------------------------------------------------------------------

@dataclass
class SourceLine:
    text: str
    page: int
    page_label: str | None
    top: float
    x0: float


@dataclass
class Unit:
    """A labelled run of lines: a subsection, a paragraph or a definition."""
    label: str
    lines: list[SourceLine] = field(default_factory=list)


@dataclass
class Section:
    number: int
    chapter: tuple[str, str] | None
    part: tuple[str, str] | None
    heading: str | None = None  # marginal note, as printed
    heading_source: str = "margin"  # "margin" or "arrangement" (fallback when no note is found)
    toc_title: str | None = None  # title in the arrangement of sections
    lines: list[SourceLine] = field(default_factory=list)
    subsections: list[Unit] = field(default_factory=list)
    paragraphs: list[Unit] = field(default_factory=list)
    definitions: list[Unit] = field(default_factory=list)
    cross_refs: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return sorted({ln.page for ln in self.lines})

    @property
    def subsection_labels(self) -> list[str]:
        return [u.label for u in self.subsections]

    def text(self, vocab=None) -> str:
        return join_lines([ln.text for ln in self.lines], vocab)


@dataclass
class Schedule:
    ordinal: str  # as printed, e.g. "Thirteenth"
    title_lines: list[str] = field(default_factory=list)
    lines: list[SourceLine] = field(default_factory=list)
    paragraphs: list[Unit] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return sorted({ln.page for ln in self.lines})


@dataclass
class ParsedAct:
    file: str
    act_id: str
    sections: list[Section]
    schedules: list[Schedule]
    anomalies: list[str]
    vocab: tuple[set[str], set[str]]

    def section(self, number: int) -> Section:
        return self.sections[number - 1]

    def schedule(self, ordinal: str) -> Schedule | None:
        return next((s for s in self.schedules if s.ordinal.lower() == ordinal.lower()), None)


# --- helpers ----------------------------------------------------------------------------------

def _flatten(pages: list[Page]) -> list[SourceLine]:
    return [SourceLine(ln.text, p.index, p.label, ln.top, ln.x0) for p in pages for ln in p.body]


def _ranges(numbers: list[int]) -> str:
    out, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            out.append(f"{start}" if start == n else f"{start}-{n}")
            start = None
    return ", ".join(out)


def _label_value(label: str) -> int:
    m = re.search(r"\d+", label)
    return int(m.group()) if m else 0


# Up to two stray glyphs may sit between the number and the title in OCR text ("1.( Objective",
# "30.1 Disclosure"); entries are accepted only in sequence, so this cannot invent an entry.
TOC_ENTRY = re.compile(r"^(\d{1,3})\s?\.\s*(?:[^A-Za-z\s\d]{1,2}\s*|[1lI]\s+)?([A-Za-z].*)$")
TOC_SKIP = re.compile(r"^(?:CHAPTER|PART|Par\s?t|Section:|ARRANGEMENT|SCHEDULES?)\b", re.I)


def _arrangement_of_sections(pages: list[Page], long_title_page: int) -> dict[int, str]:
    """Section titles from the arrangement of sections (front matter), keyed by number.

    Front-matter pages are set in small type, so their text may sit in either region; both are
    read in vertical order. Entries are accepted only in sequence; wrapped titles are joined.
    """
    toc: dict[int, str] = {}
    expected, last = 1, None
    for page in pages:
        if page.index >= long_title_page:
            break
        for ln in sorted(page.body + page.margin, key=lambda line: (line.top, line.x0)):
            m = TOC_ENTRY.match(ln.text)
            if m and int(m.group(1)) == expected:
                toc[expected] = m.group(2).strip()
                last, expected = expected, expected + 1
            elif TOC_SKIP.match(ln.text):
                last = None  # a heading; its own wrapped lines must not join a section title
            elif last and ln.text[:1].islower():
                toc[last] += " " + ln.text  # wrapped title
    return toc


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    norm = lambda s: re.sub(r"[^a-z]", "", s.lower())  # noqa: E731
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _add_section_line(sec: Section, ln: SourceLine, anomalies: list[str]) -> None:
    """Add one body line to a section, opening subsections, paragraphs and definitions."""
    text = ln.text
    # A label opens a new unit only after a line that ends a clause; otherwise it is a
    # wrapped cross-reference such as "… under subsection" / "(2) of this section".
    opens_unit = bool(CLAUSE_END.search(sec.lines[-1].text))
    sec.lines.append(ln)
    sm = SUBSECTION.match(text)
    if sm:
        label = f"({sm.group(1)})"
        last = _label_value(sec.subsections[-1].label) if sec.subsections else 0
        value = _label_value(label)
        # The next number is accepted outright; a jump (s57: (2) then (4)) only after a line
        # that ends a clause, so a wrapped cross-reference cannot open a subsection.
        if value == last + 1 or (value > last + 1 and opens_unit):
            if value != last + 1:
                anomalies.append(f"s{sec.number}: subsection {label} follows "
                                 f"{sec.subsections[-1].label if sec.subsections else 'none'} "
                                 f"(page {ln.page})")
            sec.subsections.append(Unit(label, [ln]))
            return
    if sec.subsections:
        sec.subsections[-1].lines.append(ln)
    pm = PARAGRAPH.match(text) if opens_unit else None
    if pm:
        sec.paragraphs.append(Unit(f"({pm.group(1)})", [ln]))
    elif sec.paragraphs:
        sec.paragraphs[-1].lines.append(ln)
    dm = DEFINITION.match(text)
    before = sec.lines[-3].text if len(sec.lines) >= 3 else None
    wrapped = None if dm or len(sec.lines) < 2 else wrapped_term(before, sec.lines[-2].text, text)
    if dm:
        sec.definitions.append(Unit(dm.group(1).strip(), [ln]))
    elif wrapped:
        # The previous line opened this definition; take it back from the one before.
        if sec.definitions and sec.definitions[-1].lines[-1] is sec.lines[-2]:
            sec.definitions[-1].lines.pop()
        sec.definitions.append(Unit(wrapped, [sec.lines[-2], ln]))
    elif sec.definitions:
        sec.definitions[-1].lines.append(ln)


def wrapped_term(before: str | None, first: str, second: str) -> str | None:
    """The term of a definition whose quoted term wraps from `first` onto `second`, or None.
    The line before (`before`, None at the start) must end a clause."""
    m1, m2 = WRAPPED_TERM.match(first), TERM_END.match(second)
    if m1 and m2 and (before is None or CLAUSE_END.search(before)):
        return f"{m1.group(1).strip()} {m2.group(1).strip()}"
    return None


def definition_starts(texts: list[str]) -> list[tuple[int, str]]:
    """(line index, term) for every definition that opens in `texts` (same rules as the parser)."""
    out = []
    for i, t in enumerate(texts):
        m = DEFINITION.match(t)
        if m:
            out.append((i, m.group(1).strip()))
        elif i + 1 < len(texts):
            term = wrapped_term(texts[i - 1] if i else None, t, texts[i + 1])
            if term:
                out.append((i, term))
    return out


def parse_section_text(text: str, number: int, pages: list[int] | None = None,
                       page_labels: list[str | None] | None = None) -> tuple[Section, list[str]]:
    """Text-input path: structure one section from a plain-text transcription.

    Used for the proofread NRS sections (2b-4), which have no page geometry. `pages` and
    `page_labels` give each line's page (same length as the lines); without them every line
    gets page 0. The same line rules as the PDF path apply.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    pages = pages or [0] * len(lines)
    page_labels = page_labels or [None] * len(lines)
    if len(pages) != len(lines) or len(page_labels) != len(lines):
        raise ValueError("pages and page_labels must give one entry per non-empty line")
    first = SECTION_START.match(lines[0]) if lines else None
    if not first or int(first.group(1)) != number:
        raise ValueError(f"text does not start with section {number}")
    sec = Section(number=number, chapter=None, part=None)
    anomalies: list[str] = []
    for i, line in enumerate(lines):
        ln = SourceLine(line, pages[i], page_labels[i], float(i), 0.0)
        if i == 0:
            sec.lines.append(ln)
            sm = SUB_IN_FIRST_LINE.match(line)
            if sm:
                sec.subsections.append(Unit(f"({sm.group(1)})", [ln]))
            continue
        _add_section_line(sec, ln, anomalies)
    return sec, anomalies


@lru_cache(maxsize=None)
def parse_cached(name: str) -> ParsedAct:
    """parse() once per process for a profiled raw file name (tests and the chunk build share it)."""
    return parse(RAW_DIR / name)


# --- parser -----------------------------------------------------------------------------------

def parse(pdf_path: str | Path, profile: FileProfile | None = None) -> ParsedAct:
    path = Path(pdf_path)
    profile = profile or FILE_PROFILES[path.name]
    if profile.ocr_dir:
        pages = pages_from_ocr(ROOT / profile.ocr_dir)  # the scan itself is not opened
    else:
        pages = extract_pages(path, fullsize_margin_notes=profile.fullsize_margin_notes)
    anomalies: list[str] = []
    rotated = [p.index for p in pages if p.rotated]
    if rotated:
        anomalies.append(f"{len(rotated)} page(s) set sideways (landscape tables) not parsed: "
                         f"{_ranges(rotated)}")
    lines = _flatten([p for p in pages if not p.rotated])
    vocab = hyphen_vocabulary(ln.text for ln in lines)

    start = next((i for i, ln in enumerate(lines) if LONG_TITLE.match(ln.text)), None)
    if start is None:
        raise ValueError(f"{path.name}: long title 'AN ACT TO' not found")

    sections: list[Section] = []
    schedules: list[Schedule] = []
    chapter: tuple[str, str] | None = None
    part: tuple[str, str] | None = None
    pending_title: str | None = None  # "chapter" or "part" awaiting a title line
    expected = 1
    state = "front_matter"

    for ln in lines[start:]:
        text = ln.text

        if state in ("front_matter", "body") and expected > profile.expected_sections and SCHEDULE.match(text):
            state = "schedules"
        if state == "schedules":
            m = SCHEDULE.match(text)
            if m:
                schedules.append(Schedule(ordinal=m.group(1).capitalize()))
                continue
            sched = schedules[-1]
            sched.lines.append(ln)
            if len(sched.title_lines) < 3 and not sched.paragraphs and not SCHEDULE_PARA.match(text):
                sched.title_lines.append(text)
            pm = SCHEDULE_PARA.match(text)
            if pm:
                sched.paragraphs.append(Unit(pm.group(1), [ln]))
            elif sched.paragraphs:
                sched.paragraphs[-1].lines.append(ln)
            continue
        # The "SCHEDULES" banner after the last section belongs to no section.
        if expected > profile.expected_sections and text.strip().upper() == "SCHEDULES":
            continue

        # Chapter and Part headings (upper case only; "Chapter Two of this Act" is a reference).
        m = CHAPTER.match(text)
        if m:
            chapter, part = (m.group(1), (m.group(2) or "").strip()), None
            pending_title = "chapter"  # a title may also wrap onto the next all-capitals line
            continue
        m = PART.match(text)
        if m:
            part = (m.group(1), (m.group(2) or "").strip())
            pending_title = "part"
            continue
        if pending_title and text.isupper() and not SECTION_START.match(text):
            if pending_title == "chapter" and chapter:
                chapter = (chapter[0], (chapter[1] + " " + text).strip())
            elif part:
                part = (part[0], (part[1] + " " + text).strip())
            continue
        pending_title = None

        m = SECTION_START.match(text) or (
            SECTION_START_NO_PERIOD.match(text) if profile.dash_without_period else None)
        if m:
            number = int(m.group(1))
            if number == expected:
                state = "body"
                sec = Section(number=number, chapter=chapter, part=part)
                sections.append(sec)
                expected += 1
                sec.lines.append(ln)
                sm = SUB_IN_FIRST_LINE.match(text)
                if sm:
                    sec.subsections.append(Unit(f"({sm.group(1)})", [ln]))
                continue
            if state == "body":
                anomalies.append(f"out-of-sequence start '{number}.' on page {ln.page} while "
                                 f"expecting {expected}: {text[:60]!r}")
        if state != "body":
            continue

        _add_section_line(sections[-1], ln, anomalies)

    if expected - 1 != profile.expected_sections:
        anomalies.append(f"found {expected - 1} sections, expected {profile.expected_sections}")
    _attach_headings(sections, pages, profile)
    toc = _arrangement_of_sections(pages, lines[start].page)
    for sec in sections:
        sec.toc_title = toc.get(sec.number)
        sec.cross_refs = sorted({m.group(0).strip() for m in CROSS_REF.finditer(sec.text(vocab))})
        if sec.heading is None:
            sec.heading, sec.heading_source = sec.toc_title, "arrangement"
            anomalies.append(f"s{sec.number}: no marginal-note heading level with the section "
                             f"(page {sec.pages[0]}); heading taken from the arrangement of sections")
        elif sec.toc_title and _similar(sec.heading, sec.toc_title) < TOC_MATCH_MIN:
            anomalies.append(f"s{sec.number}: heading {sec.heading!r} does not match the arrangement "
                             f"of sections {sec.toc_title!r}")
    found = [s.ordinal for s in schedules]
    if found:
        last = max(SCHEDULE_WORDS.index(o) for o in found)
        missing = [o for o in SCHEDULE_WORDS[: last + 1] if o not in found]
        if missing:
            anomalies.append(f"schedule heading(s) not found: {', '.join(missing)} (their pages are "
                             f"absorbed by the preceding schedule or skipped as sideways pages)")
    if len(toc) != profile.expected_sections:
        anomalies.append(f"arrangement of sections lists {len(toc)} entries, expected "
                         f"{profile.expected_sections}")
    return ParsedAct(path.name, profile.act_id, sections, schedules, anomalies, vocab)


SIDE_NOTE = re.compile(
    rf"^(?:{'|'.join(SCHEDULE_WORDS)}|Schedule|Cap\.?\s*[A-Z]\d+.*|Act No\..*|No\. ?\d+.*|\d{{4}}|LFN.*|"
    r"Laws of the Federation.*)$", re.I)


def _attach_headings(sections: list[Section], pages: list[Page], profile: FileProfile) -> None:
    """Give each section the marginal note that starts level with its first line.

    A note runs down from that line while the gap to the next margin line stays under
    NOTE_LINE_GAP, and stops before a line level with another section's start or a side note
    (a reference such as "Ninth Schedule" or "Cap. C21") that annotates the body text.
    """
    gap, tol = profile.note_line_gap, profile.align_tolerance
    margin = {p.index: p.margin for p in pages}
    starts: dict[int, list[float]] = {}
    for sec in sections:
        starts.setdefault(sec.lines[0].page, []).append(sec.lines[0].top)
    if profile.ocr_dir:
        _attach_headings_by_block(sections, margin, starts, gap, tol)
        return
    for sec in sections:
        first = sec.lines[0]
        notes = margin.get(first.page, [])
        # Only the first line of a note block can open a heading (not "ogy" from "technol-/ogy").
        block_starts = [i for i in range(len(notes))
                        if i == 0 or notes[i].top - notes[i - 1].top >= gap
                        or SIDE_NOTE.match(notes[i - 1].text)
                        or any(abs(notes[i].top - t) <= tol for t in starts[first.page])]
        k = min(block_starts, key=lambda i: abs(notes[i].top - first.top), default=None)
        if k is None or abs(notes[k].top - first.top) > HEADING_ALIGN or SIDE_NOTE.match(notes[k].text):
            continue
        parts = [notes[k].text]
        for prev, nxt in zip(notes[k:], notes[k + 1:]):
            other_start = any(abs(nxt.top - t) <= tol - 1.0 for t in starts[first.page] if t != first.top)
            if nxt.top - prev.top >= gap or other_start or SIDE_NOTE.match(nxt.text):
                break
            parts.append(nxt.text)
        sec.heading = join_lines(parts).replace("\n", " ")


OCR_NOTE_SPLIT_GAP = 15.0  # within-note line gaps in the scan are 10 to 15.3 pt; split only if level with a start


def _attach_headings_by_block(sections: list[Section], margin: dict[int, list[Line]],
                              starts: dict[int, list[float]], gap: float, tol: float) -> None:
    """OCR variant: notes are grouped into blocks by vertical gap, and each block goes to the
    section start nearest its span. In the scan a note is centred on its section's first line
    rather than starting level with it, so "Use of Tax" / "ID" straddles the start of s8.

    A block also ends where a gap of at least OCR_NOTE_SPLIT_GAP meets a line level with a
    section start: the notes of back-to-back sections (s15/s16, s19/s20) are 18.5 pt apart.
    """
    by_page: dict[int, list[Section]] = {}
    for sec in sections:
        by_page.setdefault(sec.lines[0].page, []).append(sec)
    for page, secs in by_page.items():
        blocks: list[list[Line]] = []
        for ln in margin.get(page, []):
            if SIDE_NOTE.match(ln.text):
                blocks.append([])  # a side note ends the current block and is not a heading
                continue
            step = ln.top - blocks[-1][-1].top if blocks and blocks[-1] else None
            level_with_start = any(abs(ln.top - t) <= tol for t in starts.get(page, []))
            if step is not None and step < gap and not (step >= OCR_NOTE_SPLIT_GAP and level_with_start):
                blocks[-1].append(ln)
            else:
                blocks.append([ln])
        taken = set()
        for sec in secs:
            top = sec.lines[0].top

            def distance(block):
                lo, hi = block[0].top, block[-1].top
                return 0.0 if lo <= top <= hi else min(abs(top - lo), abs(top - hi))

            candidates = [i for i, b in enumerate(blocks) if b and i not in taken]
            best = min(candidates, key=lambda i: distance(blocks[i]), default=None)
            if best is not None and distance(blocks[best]) <= HEADING_ALIGN:
                taken.add(best)
                sec.heading = join_lines([ln.text for ln in blocks[best]]).replace("\n", " ")


def section_counts(raw_dir: str | Path) -> dict[str, int]:
    """Number of sections found per profiled file (used by tests and the build notes)."""
    raw = Path(raw_dir)
    return {name: len(parse(raw / name).sections) for name in FILE_PROFILES}
