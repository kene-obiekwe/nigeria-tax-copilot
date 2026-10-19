"""In-scope sections of the v1 corpus (D-10), as listed in docs/phase01_corpus/v1_corpus_definition.md.

The lists are written out here so code does not depend on parsing prose; a test checks them
against the tables in that document so the two cannot drift apart.
"""
from __future__ import annotations

import re
from pathlib import Path


def expand(spec: str) -> list[int]:
    """'11, 15, 22-23' -> [11, 15, 22, 23]"""
    out: list[int] = []
    for part in re.split(r",\s*", spec.strip()):
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            out.extend(range(a, b + 1))
        elif part:
            out.append(int(part))
    return out


# Nigeria Tax Act 2025, in the numbering of the cited gazette (D-09).
NTA_IN_SCOPE: list[int] = expand(
    "1-2, 3-5, 6-11, 17-19, 20-21, 22-26, 27, 33-55, 56-57, 59, 144-158, 163-165, 186-189, 190-203"
)
NTA_SCHEDULES_IN_SCOPE: list[str] = ["First", "Third", "Eleventh", "Thirteenth"]
# Only Part I of the First Schedule (capital allowance for companies) is in scope.
NTA_FIRST_SCHEDULE_PARTS: list[str] = ["I"]

# Nigeria Tax Administration Act 2025 (section numbering is the same in all three texts).
NTAA_IN_SCOPE: list[int] = expand(
    "1-3, 4-10, 11, 15, 22-23, 25-33, 34-36, 38-49, 51-53, 55-56, 65-66, 72-76, "
    "100-108, 121-122, 124, 126-127, 147"
)


def nta_in_scope_from_doc(doc: Path) -> tuple[list[int], list[str]]:
    """Read the NTA table (section 2): gazette sections and in-scope schedules."""
    text = doc.read_text(encoding="utf-8")
    block = text.split("## 2.", 1)[1].split("## 3.", 1)[0]
    sections: list[int] = []
    schedules: list[str] = []
    for row in block.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        if re.fullmatch(r"\d[\d,\- ]*", cells[2]):
            sections.extend(expand(cells[2]))
        m = re.fullmatch(r"(\w+) Schedule", cells[0])
        if m and cells[2] == "-":
            schedules.append(m.group(1))
    return sections, schedules


def ntaa_in_scope_from_doc(doc: Path) -> list[int]:
    """Read the "Sections" column of the NTAA table (section 3) of the corpus definition."""
    text = doc.read_text(encoding="utf-8")
    block = text.split("## 3.", 1)[1].split("## 4.", 1)[0]
    sections: list[int] = []
    for row in block.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) == 3 and re.fullmatch(r"\d[\d,\- ]*", cells[2]):
            sections.extend(expand(cells[2]))
    return sections
