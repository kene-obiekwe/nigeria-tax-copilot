"""Page-level text extraction for the gazette-style statute PDFs (chunking design, section 1).

Each page is split by character geometry into three regions:

- header: the running header, i.e. characters within HEADER_BAND points of the top-most text
  line ("A 426 2025 No. 7 Nigeria Tax Act, 2025");
- margin: marginal notes, i.e. characters smaller than MARGIN_MAX_SIZE that lie wholly outside
  the body column (the x-range of the full-size characters on that page);
- body: everything else, including small-caps headings that sit inside the column.

Lines are normalised conservatively (normalise_line); nothing else in the text is changed.
Raw files are only read.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

HEADER_BAND = 6.0  # points below the top-most text line that still count as running header
MARGIN_MAX_SIZE = 10.0  # marginal notes are set in 9 pt, body text in 11 pt
COLUMN_TOLERANCE = 1.0  # points of overlap allowed between a margin char and the body column

PAGE_LABEL = re.compile(r"(?<![A-Za-z])A\s*(\d{3})\b")
NAIRA = re.compile(r"(?<![A-Za-z0-9])N\s?(?=\d)")
SPACES = re.compile(r"[ \t ]+")


@dataclass
class Line:
    text: str
    top: float
    x0: float


@dataclass
class Page:
    index: int  # 1-based PDF page index
    label: str | None  # gazette page label, e.g. "A 426"
    header: str
    body: list[Line] = field(default_factory=list)
    margin: list[Line] = field(default_factory=list)
    rotated: bool = False  # most text is set sideways (landscape tables); body text is unusable

    @property
    def body_text(self) -> str:
        return "\n".join(line.text for line in self.body)


def normalise_line(text: str) -> str:
    """Apply the design's normalisation rules to one line.

    NFKC; U+FFFD (an unmapped dash glyph in these CID fonts) to an en dash; "N" drawn before a
    figure to the naira sign; runs of spaces collapsed. Words, figures and punctuation are
    otherwise left exactly as extracted.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("�", "–")
    text = NAIRA.sub("₦", text)
    text = SPACES.sub(" ", text)
    return text.strip()


def _lines(page_obj) -> list[Line]:
    out = []
    for ln in page_obj.extract_text_lines(layout=False, strip=True, return_chars=False):
        text = normalise_line(ln["text"])
        if text:
            out.append(Line(text=text, top=round(ln["top"], 1), x0=round(ln["x0"], 1)))
    return out


@dataclass
class _Geometry:
    header_bottom: float
    left: float  # body column bounds from this page's full-size characters
    right: float
    side: str | None  # "left" or "right": where the marginal notes sit (from the label position)
    small_margin: bool  # small-type characters were found outside the column on that side


def _geometry(page) -> _Geometry | None:
    chars = page.chars
    if not chars:
        return None
    header_bottom = min(c["top"] for c in chars) + HEADER_BAND
    header_chars = [c for c in chars if c["top"] < header_bottom]
    full_size = [c for c in chars if c["top"] >= header_bottom and c["size"] >= MARGIN_MAX_SIZE]
    if full_size:
        left = min(c["x0"] for c in full_size)
        right = max(c["x1"] for c in full_size)
    else:
        left, right = 0.0, float(page.width)
    # Even pages print "A 426 …" at the left of the header and carry notes in the left margin;
    # odd pages print "… A 427" at the right and carry notes on the right.
    side = None
    ordered = [c for c in sorted(header_chars, key=lambda c: c["x0"]) if c["text"].strip()]
    for i, c in enumerate(ordered[:-1]):
        nxt = ordered[i + 1]
        # The page label is a lone "A" followed by figures ("A 426"); skip the "A" of "Act".
        if c["text"] == "A" and nxt["text"].isdigit() and nxt["x0"] - c["x1"] < 12:
            side = "left" if c["x0"] < page.width / 2 else "right"
            break
    small = [c for c in chars if c["top"] >= header_bottom and c["size"] < MARGIN_MAX_SIZE]
    if side == "left":
        small_margin = any(c["x1"] <= left + COLUMN_TOLERANCE for c in small)
    elif side == "right":
        small_margin = any(c["x0"] >= right - COLUMN_TOLERANCE for c in small)
    else:
        small_margin = False
    return _Geometry(header_bottom, left, right, side, small_margin)


def _column_templates(geoms: list[_Geometry | None]) -> dict[str, float]:
    """Median margin-side column edge per side, learned from pages whose margin separated by size."""
    edges = {"left": [], "right": []}
    for g in geoms:
        if g and g.small_margin and g.side:
            edges[g.side].append(g.left if g.side == "left" else g.right)
    out = {}
    for side, values in edges.items():
        if values:
            values.sort()
            out[side] = values[len(values) // 2]
    return out


GUTTER = 3.0  # points either side of the column edge that must be empty for a margin split


def split_page(page, index: int, geom: _Geometry | None, templates: dict[str, float]) -> Page:
    if geom is None:
        return Page(index=index, label=None, header="")
    edge = templates.get(geom.side or "")
    # Characters that touch the gutter band at the column edge. A line that has one of these runs
    # through the edge (a wide schedule line), so rule 2 must not split it.
    straddling = []
    if edge is not None:
        straddling = [c for c in page.chars if c["top"] >= geom.header_bottom
                      and c["x0"] < edge + GUTTER and c["x1"] > edge - GUTTER]

    def runs_through(c) -> bool:
        return any(d["top"] < c["bottom"] and d["bottom"] > c["top"] for d in straddling)

    def is_header(c):
        return c["top"] < geom.header_bottom

    def is_margin(c):
        if is_header(c):
            return False
        # Rule 1 (design): small type wholly outside this page's own body column.
        if c["size"] < MARGIN_MAX_SIZE and (
            c["x1"] <= geom.left + COLUMN_TOLERANCE or c["x0"] >= geom.right - COLUMN_TOLERANCE
        ):
            return True
        # Rule 2: type of any size wholly beyond the file's usual column edge on the margin side,
        # with an empty gutter at the edge on that line. Needed where a reprint sets marginal
        # notes at body size (the National Assembly copies).
        if edge is None:
            return False
        beyond = c["x1"] <= edge - GUTTER if geom.side == "left" else c["x0"] >= edge + GUTTER
        return beyond and not runs_through(c)

    def keep(predicate):
        return page.filter(lambda o: o.get("object_type") != "char" or predicate(o))

    header = normalise_line(keep(is_header).extract_text() or "")
    label_match = PAGE_LABEL.search(header)
    below = [c for c in page.chars if not is_header(c)]
    sideways = sum(1 for c in below if not c.get("upright", True))
    return Page(
        index=index,
        label=f"A {label_match.group(1)}" if label_match else None,
        header=header,
        body=_lines(keep(lambda c: not is_header(c) and not is_margin(c))),
        margin=_lines(keep(is_margin)),
        rotated=bool(below) and sideways > 0.5 * len(below),
    )


def extract_pages(pdf_path: str | Path, fullsize_margin_notes: bool = False,
                  only: list[int] | None = None) -> list[Page]:
    """Return one Page per PDF page, in order (or only the 1-based pages listed in `only`).

    fullsize_margin_notes enables rule 2 (marginal notes set at body size). It is a per-file
    tolerance: only the National Assembly reprint of the Nigeria Tax Act needs it, and on the
    gazette it would wrongly split the Tenth Schedule tables. Rule 2 learns the column edge
    from the whole file, so `only` cannot be combined with it.
    """
    if only is not None and fullsize_margin_notes:
        raise ValueError("fullsize_margin_notes needs every page; do not pass `only`")
    with pdfplumber.open(str(pdf_path)) as pdf:
        indices = only or list(range(1, len(pdf.pages) + 1))
        geoms = {i: _geometry(pdf.pages[i - 1]) for i in indices}
        templates = _column_templates(list(geoms.values())) if fullsize_margin_notes else {}
        return [split_page(pdf.pages[i - 1], i, geoms[i], templates) for i in indices]


# --- line joining -----------------------------------------------------------------------------

KEEP_HYPHEN_PREFIXES = {
    "non", "self", "co", "sub", "pre", "post", "re", "cross", "inter", "multi", "semi", "quasi",
    "anti", "ex", "e", "half", "low", "high", "well", "long", "short", "part", "tax", "value",
}
WORD_END_HYPHEN = re.compile(r"([A-Za-z]+)-$")
WORD_START = re.compile(r"^([a-z][A-Za-z]*)")


def hyphen_vocabulary(texts) -> tuple[set[str], set[str]]:
    """Collect hyphenated and plain words that appear mid-line in a document."""
    hyphenated, plain = set(), set()
    for text in texts:
        for word in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)+", text):
            hyphenated.add(word.lower())
        for word in re.findall(r"[A-Za-z]+", text):
            plain.add(word.lower())
    return hyphenated, plain


def join_lines(lines: list[str], vocab: tuple[set[str], set[str]] | None = None) -> str:
    """Join lines into text, rejoining end-of-line hyphenation.

    A line ending "word-" followed by a line starting with a lower-case word is a candidate. The
    hyphen is kept if the document uses the hyphenated form elsewhere ("non-resident"), or the
    first half is a common prefix; otherwise the halves are joined ("commence-" + "ment").
    Line breaks are otherwise kept.
    """
    hyphenated, plain = vocab or (set(), set())
    out: list[str] = []
    for line in lines:
        if out:
            prev = out[-1]
            m_end, m_start = WORD_END_HYPHEN.search(prev), WORD_START.match(line)
            if m_end and m_start:
                a, b = m_end.group(1), m_start.group(1)
                if f"{a}-{b}".lower() in hyphenated or (
                    a.lower() in KEEP_HYPHEN_PREFIXES and f"{a}{b}".lower() not in plain
                ):
                    out[-1] = prev + line  # keep the hyphen, drop the break
                else:
                    out[-1] = prev[:-1] + line
                continue
        out.append(line)
    return "\n".join(out)
