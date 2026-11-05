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

`parse_section_refs_d21` keeps the D-21 reading (section references only, the two corpus Acts
only, and "Nigeria" required before "Tax Administration Act"), so the frozen configuration
(`config_final.json`) ranks exactly as it did for gate G1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "data" / "curated" / "guardrails_policy.yml"
CORPUS_ACTS = ("nta2025", "ntaa2025")

# "section 148", "sections 20 and 21", "Section 41(5)", "s. 66", "s148", "sec. 9"
SECTION_REF = re.compile(
    r"(?<!\.)\b(?:sections?|secs?\.|s\.|s(?=\d))\s*(\d+)((?:\s*\([0-9a-z]+\))*(?:\s*(?:,|and|or)\s*\d+(?:\s*\([0-9a-z]+\))*)*)",
    re.IGNORECASE)
LIST_NUMBER = re.compile(r"(?:,|and|or)\s*(\d+)", re.IGNORECASE)
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


@dataclass(frozen=True)
class Reference:
    kind: str          # "section" | "schedule"
    number: int
    act: str | None    # corpus Act id, None (no Act named) or "other:<name>"

    @property
    def other_act(self) -> bool:
        return bool(self.act) and self.act.startswith("other:")


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


def parse_references(question: str, other_acts=(), corpus_pattern: re.Pattern = CORPUS_ACT,
                     schedules: bool = True) -> list[Reference]:
    """Section (and Schedule) references in `question`, each with its Act, in order."""
    hits = [(m.start(), m.end(), "section", m) for m in SECTION_REF.finditer(question)]
    if schedules:
        hits += [(m.start(), m.end(), "schedule", m) for m in SCHEDULE_REF.finditer(question)]
    hits.sort(key=lambda h: h[0])
    acts = act_mentions(question, other_acts, corpus_pattern)
    out: list[Reference] = []
    for i, (start, end, kind, m) in enumerate(hits):
        next_start = hits[i + 1][0] if i + 1 < len(hits) else len(question)
        after = [a for s, _, a in acts if end <= s < next_start]
        before = [a for s, _, a in acts if s < start]
        act = after[0] if after else (before[-1] if before else None)
        if kind == "section":
            numbers = [int(m.group(1))] + [int(n) for n in LIST_NUMBER.findall(m.group(2))]
        else:
            word = (m.group(1) or "").lower()
            numbers = [ORDINALS[word] if word in ORDINALS else int(re.sub(r"\D", "", word or m.group(2)))]
        for number in numbers:
            ref = Reference(kind, number, act)
            if ref not in out:
                out.append(ref)
    return out


def parse_section_refs_d21(question: str) -> list[Reference]:
    """Section references as the D-21 configuration reads them (gate G1)."""
    return parse_references(question, (), CORPUS_ACT_D21, schedules=False)
