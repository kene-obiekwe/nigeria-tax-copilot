"""Section, Schedule and Act references in a question: one parser for retrieval and the guardrails
(D-54; G2 fix design, section 2.3).

    from retrieval.references import load_other_acts, parse_references
    refs = parse_references("Does section 23 of CITA still apply?", load_other_acts())
    # [Reference(kind="section", number=23, act="other:Companies Income Tax Act")]

**Which Act a reference belongs to.** A reference takes the Act named after it (before the next
reference), else the Act named before it, else none. Acts named are the two corpus Acts and the
Acts outside the corpus listed in data/curated/guardrails_policy.yml (`other_acts`); where an
outside Act's name overlaps a corpus Act's pattern, the outside Act wins. An Act is given as a
corpus Act id (`nta2025`, `ntaa2025`), `None` (no Act named) or `other:<name>`.

**Retrieval** looks up a section reference only when it belongs to a corpus Act or to none; one
attributed to an Act outside the corpus is not looked up (`other_act_refs: skip`). The guardrails
read the same references to refuse or add notes.

**Numbers (G2 fix design 2, sections 3.1 and 4.2).** A list of numbers may be separated by ",",
", and", ", or", "and" or "or" ("sections 163(1)(p), and 187"), and a range "sections 22 to 25"
covers every number in it (D-79). A number may carry one trailing letter ("57A", "101a"); it is
read as a lettered number (`Reference.suffix`), which neither Act has, so the guardrails refuse or
note it as `no_such_section` and retrieval never looks it up (D-80). `parse_subsection_refs` reads
the first numbered subsection after a section number ("section 100(4)") for the subsection note
(D-81).

`parse_section_refs_d21` keeps the D-21 reading with a frozen copy of the D-21 expressions (section
references only, the two corpus Acts only, "Nigeria" required before "Tax Administration Act", no
lettered numbers, no ranges), so the frozen configuration (`config_final.json`) ranks exactly as it
did for gate G1.

**Finance Acts (D-78).** An `other_acts` name holding `{year}` takes the year the question gives
with the Act ("Finance Act 2023", "Finance Act, 2023", "the 2023 Finance Act"), so the note reads
"The Finance Act 2023 ..." with the label `act:finance_act_2023`; with no year the name is the
plain name ("Finance Act").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "data" / "curated" / "guardrails_policy.yml"
CORPUS_ACTS = ("nta2025", "ntaa2025")

# The D-21 expressions, frozen (gate G1): "section 148", "sections 20 and 21", "Section 41(5)",
# "s. 66", "s148", "sec. 9". Never edit these two; parse_section_refs_d21 reads only them.
SECTION_REF_D21 = re.compile(
    r"(?<!\.)\b(?:sections?|secs?\.|s\.|s(?=\d))\s*(\d+)((?:\s*\([0-9a-z]+\))*(?:\s*(?:,|and|or)\s*\d+(?:\s*\([0-9a-z]+\))*)*)",
    re.IGNORECASE)
LIST_NUMBER_D21 = re.compile(r"(?:,|and|or)\s*(\d+)", re.IGNORECASE)

# The shared expressions (D-79, D-80): a number with at most one trailing letter, and the
# separators ",", ", and", ", or", "and", "or" and "to" (a range).
_NUMBER = r"\d+(?:[a-z](?![a-z]))?"
_SEPARATOR = r",\s*(?:and|or)\b|,|\band\b|\bor\b|\bto\b"
_PARENS = r"(?:\s*\([0-9a-z]+\))*"
SECTION_REF = re.compile(
    r"(?<!\.)\b(?:sections?|secs?\.|s\.|s(?=\d))\s*(" + _NUMBER + r")(" + _PARENS
    + r"(?:\s*(?:" + _SEPARATOR + r")\s*" + _NUMBER + _PARENS + r")*)",
    re.IGNORECASE)
LIST_ITEM = re.compile(r"\s*(" + _SEPARATOR + r")\s*(" + _NUMBER + r")(" + _PARENS + r")", re.IGNORECASE)
LIST_NUMBER = LIST_NUMBER_D21  # kept for existing callers (retrieval.retrieve), D-21 form
_FIRST_PARENS = re.compile(r"^(" + _PARENS + r")", re.IGNORECASE)
_PAREN_ITEM = re.compile(r"\(([0-9a-z]+)\)", re.IGNORECASE)
MAX_RANGE = 20  # a "to" range wider than this, or running backwards, is not read as a range
ORDINAL_WORDS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
                 "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth").split()
ORDINALS = {word: i for i, word in enumerate(ORDINAL_WORDS, start=1)}
ORDINALS["thirtenth"] = 13  # as misprinted in the gazette (DQ-14)
SCHEDULE_REF = re.compile(
    r"\b(" + "|".join(sorted(ORDINALS, key=len, reverse=True)) + r"|\d+(?:st|nd|rd|th))\s+schedule\b"
    r"|\bschedule\s+(\d+)\b", re.IGNORECASE)
# The administration Act first, so "Nigeria Tax Administration Act" is never read as the Tax Act.
# "Tax Administration Act" without "Nigeria" also names the Administration Act (tuning run 1).
CORPUS_ACT = re.compile(r"\b(?:Nigeria\s+)?Tax\s+Administration\s+Act\b|\bNigeria\s+Tax\s+Act\b|"
                        r"\b(?-i:NTAA)\b|\b(?-i:NTA)\b", re.IGNORECASE)
# The D-21 retrieval pattern (gate G1): "Nigeria" required before "Tax Administration Act".
CORPUS_ACT_D21 = re.compile(r"\bNigeria\s+Tax\s+Administration\s+Act\b|\bNigeria\s+Tax\s+Act\b|"
                            r"\b(?-i:NTAA)\b|\b(?-i:NTA)\b", re.IGNORECASE)
MATCHED_NAME = "{match}"  # an other_acts entry whose name is the matched text (a Nigeria Tax Act of another year)
YEAR_NAME = "{year}"      # in an other_acts name: replaced by " <year>" when the question gives one (D-78)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass(frozen=True)
class Reference:
    kind: str          # "section" | "schedule"
    number: int
    act: str | None    # corpus Act id, None (no Act named) or "other:<name>"
    suffix: str = ""   # the letter of a lettered number ("57A" -> 57, "A"); neither Act has one (D-80)

    @property
    def other_act(self) -> bool:
        return bool(self.act) and self.act.startswith("other:")

    @property
    def label(self) -> str:
        """The number as printed in the question, upper case: "57A", "148"."""
        return f"{self.number}{self.suffix}"


@dataclass(frozen=True)
class SubsectionRef:
    section: int
    subsection: int
    act: str | None    # as Reference.act


def act_of(match_text: str) -> str:
    """The corpus Act a CORPUS_ACT match names."""
    return "ntaa2025" if "administration" in match_text.lower() or match_text == "NTAA" else "nta2025"


def load_other_acts(path: Path = POLICY) -> tuple[tuple[str, re.Pattern], ...]:
    """(name, pattern) for every Act outside the corpus in the guardrails policy, matched ignoring
    case unless the pattern says otherwise."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple((item["name"], re.compile(item["pattern"], re.IGNORECASE)) for item in data["other_acts"])


def other_act_mentions(question: str, other_acts) -> list[tuple[int, int, str, re.Pattern]]:
    """(start, end, name, pattern) for every Act outside the corpus named in `question`. For the
    `{match}` entry the name is the matched text, and its pattern is that exact text."""
    out = []
    for name, pattern in other_acts:
        for m in pattern.finditer(question):
            if name == MATCHED_NAME:
                label = " ".join(m.group(0).split())
                out.append((m.start(), m.end(), label, re.compile(r"\b" + re.escape(label).replace(r"\ ", r"\s+") + r"\b",
                                                                  re.IGNORECASE)))
            elif YEAR_NAME in name:
                year = _YEAR.search(m.group(0))
                out.append((m.start(), m.end(), name.replace(YEAR_NAME, f" {year.group(0)}" if year else ""), pattern))
            else:
                out.append((m.start(), m.end(), name, pattern))
    return out


def act_mentions(question: str, other_acts=(), corpus_pattern: re.Pattern = CORPUS_ACT) -> list[tuple[int, int, str]]:
    """(start, end, act) for every Act named; Acts outside the corpus win where spans overlap."""
    others = [(s, e, f"other:{name}") for s, e, name, _ in other_act_mentions(question, other_acts)]
    mentions = list(others)
    for m in corpus_pattern.finditer(question):
        if not any(s < m.end() and m.start() < e for s, e, _ in others):
            mentions.append((m.start(), m.end(), act_of(m.group(0))))
    return sorted(mentions)


def named_other_acts(question: str, other_acts) -> dict[str, re.Pattern]:
    """The Acts outside the corpus named in `question`, in order of first mention: name -> pattern."""
    out: dict[str, re.Pattern] = {}
    for _, _, name, pattern in sorted(other_act_mentions(question, other_acts), key=lambda m: m[0]):
        out.setdefault(name, pattern)
    return out


def _split_number(text: str) -> tuple[int, str]:
    """"57A" -> (57, "A"); "148" -> (148, "")."""
    return (int(text[:-1]), text[-1].upper()) if text[-1].isalpha() else (int(text), "")


def _first_subsection(parens: str) -> int | None:
    """The subsection in a "(4)(a)" group: its first item, when that item is a number."""
    items = _PAREN_ITEM.findall(parens)
    return int(items[0]) if items and items[0].isdigit() else None


def section_items(m: re.Match) -> list[tuple[int, str, int | None]]:
    """(number, suffix, subsection) for every number in a SECTION_REF match, ranges expanded
    (D-79). A range end that runs backwards, is lettered, or is more than MAX_RANGE past its start
    is dropped."""
    number, suffix = _split_number(m.group(1))
    rest = m.group(2)
    first = _FIRST_PARENS.match(rest).group(1)
    items = [(number, suffix, _first_subsection(first))]
    for sep, num, parens in LIST_ITEM.findall(rest[len(first):]):
        n, s = _split_number(num)
        if sep.lower() == "to":
            start, start_suffix, _ = items[-1]
            if s or start_suffix or not start < n <= start + MAX_RANGE:
                continue
            items += [(x, "", None) for x in range(start + 1, n)]
        items.append((n, s, _first_subsection(parens)))
    return items


def _hits(question: str, other_acts, corpus_pattern: re.Pattern, schedules: bool, section_pattern: re.Pattern):
    """(kind, match, act) for every section and Schedule reference, in order."""
    hits = [(m.start(), m.end(), "section", m) for m in section_pattern.finditer(question)]
    if schedules:
        hits += [(m.start(), m.end(), "schedule", m) for m in SCHEDULE_REF.finditer(question)]
    hits.sort(key=lambda h: h[0])
    acts = act_mentions(question, other_acts, corpus_pattern)
    for i, (start, end, kind, m) in enumerate(hits):
        next_start = hits[i + 1][0] if i + 1 < len(hits) else len(question)
        after = [a for s, _, a in acts if end <= s < next_start]
        before = [a for s, _, a in acts if s < start]
        yield kind, m, after[0] if after else (before[-1] if before else None)


def parse_references(question: str, other_acts=(), corpus_pattern: re.Pattern = CORPUS_ACT,
                     schedules: bool = True) -> list[Reference]:
    """Section (and Schedule) references in `question`, each with its Act, in order."""
    out: list[Reference] = []
    for kind, m, act in _hits(question, other_acts, corpus_pattern, schedules, SECTION_REF):
        if kind == "section":
            numbers = [(n, s) for n, s, _ in section_items(m)]
        else:
            word = (m.group(1) or "").lower()
            numbers = [(ORDINALS[word] if word in ORDINALS else int(re.sub(r"\D", "", word or m.group(2))), "")]
        for number, suffix in numbers:
            ref = Reference(kind, number, act, suffix)
            if ref not in out:
                out.append(ref)
    return out


def parse_subsection_refs(question: str, other_acts=(), corpus_pattern: re.Pattern = CORPUS_ACT) -> list[SubsectionRef]:
    """Numbered subsection references written after a section number ("section 100(4)",
    "sections 100(4) and 101(3)"), each with its Act, in order (D-81)."""
    out: list[SubsectionRef] = []
    for _, m, act in _hits(question, other_acts, corpus_pattern, False, SECTION_REF):
        for number, suffix, subsection in section_items(m):
            ref = SubsectionRef(number, subsection, act)
            if subsection is not None and not suffix and ref not in out:
                out.append(ref)
    return out


def parse_section_refs_d21(question: str) -> list[Reference]:
    """Section references as the D-21 configuration reads them (gate G1), with the frozen D-21
    expressions: no lettered numbers ("57A" is read as 57), no ranges, no ", and" separator."""
    out: list[Reference] = []
    for _, m, act in _hits(question, (), CORPUS_ACT_D21, False, SECTION_REF_D21):
        for number in [int(m.group(1))] + [int(n) for n in LIST_NUMBER_D21.findall(m.group(2))]:
            ref = Reference("section", number, act)
            if ref not in out:
                out.append(ref)
    return out
