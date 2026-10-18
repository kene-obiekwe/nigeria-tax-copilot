"""Structure parser for the statute PDFs (chunking design, section 3).

A line-level state machine over the body text from extract.py, in page order:

    front_matter -> body (chapters, parts, sections) -> schedules

Section starts are accepted only as the next expected number; any other match is logged as an
anomaly and treated as text. Subsection and paragraph labels are kept exactly as printed.
Marginal notes become section headings when they sit level with the section's first line.

Per-file tolerances live in FILE_PROFILES and are listed in docs/phase02_chunking/build_notes.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ingest.extract import Line, Page, extract_pages, hyphen_vocabulary, join_lines

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
DEFINITION = re.compile(r"^[“\"]([^”\"]{1,80})[”\"]\s*(?:means|includes|has the|shall|in relation)")
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
}


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


TOC_ENTRY = re.compile(r"^(\d{1,3})\s?\.\s*([A-Za-z].*)$")
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


# --- parser -----------------------------------------------------------------------------------

def parse(pdf_path: str | Path, profile: FileProfile | None = None) -> ParsedAct:
    path = Path(pdf_path)
    profile = profile or FILE_PROFILES[path.name]
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

        # Chapter and Part headings (upper case only; "Chapter Two of this Act" is a reference).
        m = CHAPTER.match(text)
        if m:
            chapter, part = (m.group(1), (m.group(2) or "").strip()), None
            pending_title = None if m.group(2) else "chapter"
            continue
        m = PART.match(text)
        if m:
            part = (m.group(1), (m.group(2) or "").strip())
            pending_title = None if m.group(2) else "part"
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

        sec = sections[-1]
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
                continue
            sm = None  # a "(n)" that does not advance the numbering is ordinary text
        if sec.subsections:
            sec.subsections[-1].lines.append(ln)
        pm = PARAGRAPH.match(text) if opens_unit else None
        if pm:
            sec.paragraphs.append(Unit(f"({pm.group(1)})", [ln]))
        elif sec.paragraphs:
            sec.paragraphs[-1].lines.append(ln)
        dm = DEFINITION.match(text)
        if dm:
            sec.definitions.append(Unit(dm.group(1).strip(), [ln]))
        elif sec.definitions:
            sec.definitions[-1].lines.append(ln)

    if expected - 1 != profile.expected_sections:
        anomalies.append(f"found {expected - 1} sections, expected {profile.expected_sections}")
    _attach_headings(sections, pages)
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


def _attach_headings(sections: list[Section], pages: list[Page]) -> None:
    """Give each section the marginal note that starts level with its first line.

    A note runs down from that line while the gap to the next margin line stays under
    NOTE_LINE_GAP, and stops before a line level with another section's start or a side note
    (a reference such as "Ninth Schedule" or "Cap. C21") that annotates the body text.
    """
    margin = {p.index: p.margin for p in pages}
    starts: dict[int, list[float]] = {}
    for sec in sections:
        starts.setdefault(sec.lines[0].page, []).append(sec.lines[0].top)
    for sec in sections:
        first = sec.lines[0]
        notes = margin.get(first.page, [])
        # Only the first line of a note block can open a heading (not "ogy" from "technol-/ogy").
        block_starts = [i for i in range(len(notes))
                        if i == 0 or notes[i].top - notes[i - 1].top >= NOTE_LINE_GAP
                        or SIDE_NOTE.match(notes[i - 1].text)
                        or any(abs(notes[i].top - t) <= 3.0 for t in starts[first.page])]
        k = min(block_starts, key=lambda i: abs(notes[i].top - first.top), default=None)
        if k is None or abs(notes[k].top - first.top) > HEADING_ALIGN or SIDE_NOTE.match(notes[k].text):
            continue
        parts = [notes[k].text]
        for prev, nxt in zip(notes[k:], notes[k + 1:]):
            other_start = any(abs(nxt.top - t) <= 2.0 for t in starts[first.page] if t != first.top)
            if nxt.top - prev.top >= NOTE_LINE_GAP or other_start or SIDE_NOTE.match(nxt.text):
                break
            parts.append(nxt.text)
        sec.heading = join_lines(parts).replace("\n", " ")


def section_counts(raw_dir: str | Path) -> dict[str, int]:
    """Number of sections found per profiled file (used by tests and the build notes)."""
    raw = Path(raw_dir)
    return {name: len(parse(raw / name).sections) for name in FILE_PROFILES}
