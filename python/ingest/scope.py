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


# Nigeria Tax Administration Act 2025 (section numbering is the same in all three texts).
NTAA_IN_SCOPE: list[int] = expand(
    "1-3, 4-10, 11, 15, 22-23, 25-33, 34-36, 38-49, 51-53, 55-56, 65-66, 72-76, "
    "100-108, 121-122, 124, 126-127, 147"
)


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
