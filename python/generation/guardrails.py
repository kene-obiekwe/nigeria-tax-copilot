"""Guardrail pre-checks and the relevance floor (D-34, D-35; guardrails design, section 4).

    from generation.guardrails import load_guardrails_policy, pre_check, relevance_floor
    check = pre_check(question, load_guardrails_policy(), store.sections())

Pre-checks run before retrieval and cost nothing. They refuse only where code can be certain:

1. **Invalid question:** empty, no letters, or longer than the pinned limit.
2. **References:** a section number past the end of the Act (`no_such_section`), a Schedule the
   Nigeria Tax Act does not have (`no_such_section`), or a real section that is not in the v1
   corpus (`section_outside_v1`, named by its heading from data/derived/section_map.csv). A
   question is refused only when it has at least one reference to the corpus Acts, every such
   reference fails, and none is valid. Otherwise failed references become notes.
3. **Notes, never refusals:** an Act outside the corpus (CITA, ...), and scope topics from the v1
   known gaps (stamp duties, PAYE, ...).

References follow the retrieval pattern (`retrieve._SECTION_REF`) and its rule for which Act a
reference belongs to (the Act named after it, else the one named before it), with Acts outside the
corpus added. `retrieve()` itself is never changed (D-21): a question naming "section 24 of CITA"
still retrieves as before, and the note tells the reader what the section numbers mean.

The relevance floor runs after retrieval: when tau is set, a question whose best vector score is
below tau is refused (`low_relevance`); questions answered by a section lookup are exempt.
"""
from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from generation.disclosure import Disclosure  # noqa: E402
from retrieval.retrieve import _LIST_NUMBER, _SECTION_REF  # noqa: E402

POLICY = ROOT / "data" / "curated" / "guardrails_policy.yml"
SECTION_MAP = ROOT / "data" / "derived" / "section_map.csv"
CORPUS_ACTS = ("nta2025", "ntaa2025")
CITED_PRINT = {"nta2025": "gazette", "ntaa2025": "nrs"}
ORDINAL_WORDS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
                 "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth").split()
ORDINALS = {word: i for i, word in enumerate(ORDINAL_WORDS, start=1)}
ORDINALS["thirtenth"] = 13  # as misprinted in the gazette (DQ-14)
_SCHEDULE_REF = re.compile(
    r"\b(" + "|".join(sorted(ORDINALS, key=len, reverse=True)) + r"|\d+(?:st|nd|rd|th))\s+schedule\b"
    r"|\bschedule\s+(\d+)\b", re.IGNORECASE)
_CORPUS_ACT = re.compile(r"\bNigeria\s+Tax\s+Administration\s+Act\b|\bNigeria\s+Tax\s+Act\b|"
                         r"\b(?-i:NTAA)\b|\b(?-i:NTA)\b", re.IGNORECASE)

INVALID_TEXT = ("The question could not be read. Please ask about the Nigeria Tax Act 2025 or the Nigeria "
                "Tax Administration Act 2025.")
LOW_RELEVANCE_TEXT = "No provision in the corpus matches this question closely enough to answer it."


@dataclass(frozen=True)
class GuardrailsPolicy:
    acts: dict                       # act id -> {"name", "sections", "schedules"}
    other_acts: tuple                # (name, compiled pattern)
    other_act_note: str
    scope_topics: dict               # topic -> (compiled pattern, text)
    scope_note_suffix: str
    disputed_gap_patterns: tuple
    max_question_chars: int
    tau: float | None
    margin: float
    headings: dict                   # (act id, section number) -> heading of the cited print
    sha256: str


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_section_headings(path: Path = SECTION_MAP) -> dict[tuple[str, int], str]:
    """Every section of the cited print of each Act, with its heading."""
    out = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["act_id"] in CITED_PRINT and row["cited_print"] == CITED_PRINT[row["act_id"]] \
                    and row["cited_section"].isdigit():
                out.setdefault((row["act_id"], int(row["cited_section"])), row["cited_heading"].strip())
    return out


def load_guardrails_policy(path: Path = POLICY, section_map: Path = SECTION_MAP) -> GuardrailsPolicy:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    floor = data["relevance_floor"]
    return GuardrailsPolicy(
        acts={k: dict(v) for k, v in data["acts"].items()},
        other_acts=tuple((item["name"], re.compile(item["pattern"], re.IGNORECASE)) for item in data["other_acts"]),
        other_act_note=" ".join(data["other_act_note"].split()),
        scope_topics={k: (re.compile(v["pattern"], re.IGNORECASE), " ".join(v["text"].split()))
                      for k, v in data["scope_topics"].items()},
        scope_note_suffix=data["scope_note_suffix"],
        disputed_gap_patterns=tuple(re.compile(p, re.IGNORECASE) for p in data["disputed_gap_patterns"]),
        max_question_chars=int(data["max_question_chars"]),
        tau=None if floor["tau"] is None else float(floor["tau"]),
        margin=float(floor["margin"]),
        headings=load_section_headings(section_map),
        sha256=_sha256(path),
    )


# References --------------------------------------------------------------------------------------


@dataclass
class Reference:
    kind: str                   # "section" | "schedule"
    number: int
    act: str | None             # corpus act id, None (no Act named) or "other:<name>"
    verdict: str = "unchecked"  # ok | no_such_section | section_outside_v1 | other_act | unchecked
    text: str = ""              # refusal or note text for a failed reference


@dataclass
class PreCheck:
    refusal: str | None = None              # invalid_question | no_such_section | section_outside_v1
    refusal_text: str | None = None
    references: list[Reference] = field(default_factory=list)
    notes: list[Disclosure] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def as_log(self) -> dict:
        return {"refusal": self.refusal,
                "references": [{"kind": r.kind, "number": r.number, "act": r.act, "verdict": r.verdict}
                               for r in self.references],
                "notes": [n.ref for n in self.notes], "labels": note_labels(self.notes, self.refusal),
                "flags": self.flags}


def _act_mentions(question: str, policy: GuardrailsPolicy) -> list[tuple[int, int, str]]:
    """(start, end, act) for every Act named; Acts outside the corpus win where spans overlap."""
    others = []
    for name, pattern in policy.other_acts:
        for m in pattern.finditer(question):
            label = " ".join(m.group(0).split()) if name == "{match}" else name
            others.append((m.start(), m.end(), f"other:{label}"))
    mentions = list(others)
    for m in _CORPUS_ACT.finditer(question):
        if not any(s < m.end() and m.start() < e for s, e, _ in others):
            act = "ntaa2025" if "administration" in m.group(0).lower() or m.group(0) == "NTAA" else "nta2025"
            mentions.append((m.start(), m.end(), act))
    return sorted(mentions)


def parse_references(question: str, policy: GuardrailsPolicy) -> list[Reference]:
    """Section and Schedule references, each with its Act, by the retrieval rule."""
    hits = [(m.start(), m.end(), "section", m) for m in _SECTION_REF.finditer(question)]
    hits += [(m.start(), m.end(), "schedule", m) for m in _SCHEDULE_REF.finditer(question)]
    hits.sort(key=lambda h: h[0])
    acts = _act_mentions(question, policy)
    out: list[Reference] = []
    for i, (start, end, kind, m) in enumerate(hits):
        next_start = hits[i + 1][0] if i + 1 < len(hits) else len(question)
        after = [a for s, _, a in acts if end <= s < next_start]
        before = [a for s, _, a in acts if s < start]
        act = after[0] if after else (before[-1] if before else None)
        if kind == "section":
            numbers = [int(m.group(1))] + [int(n) for n in _LIST_NUMBER.findall(m.group(2))]
        else:
            word = (m.group(1) or "").lower()
            numbers = [ORDINALS[word] if word in ORDINALS else int(re.sub(r"\D", "", word or m.group(2)))]
        for number in numbers:
            ref = Reference(kind, number, act)
            if not any((r.kind, r.number, r.act) == (kind, number, act) for r in out):
                out.append(ref)
    return out


def _ordinal(n: int) -> str:
    return ORDINAL_WORDS[n - 1].capitalize() if 1 <= n <= len(ORDINAL_WORDS) else f"{n}th"


def _check_section(ref: Reference, policy: GuardrailsPolicy, sections: dict[str, set[str]]) -> None:
    acts = policy.acts
    if ref.act is None:
        candidates = [a for a in CORPUS_ACTS if 1 <= ref.number <= acts[a]["sections"]]
        if not candidates:
            ref.verdict = "no_such_section"
            ref.text = (f"Neither the {acts['nta2025']['name']} ({acts['nta2025']['sections']} sections) nor the "
                        f"{acts['ntaa2025']['name']} ({acts['ntaa2025']['sections']} sections) has a section "
                        f"{ref.number}.")
            return
    else:
        if not 1 <= ref.number <= acts[ref.act]["sections"]:
            ref.verdict = "no_such_section"
            ref.text = (f"The {acts[ref.act]['name']} has no section {ref.number}. It has "
                        f"{acts[ref.act]['sections']} sections.")
            return
        candidates = [ref.act]
    if any(str(ref.number) in sections.get(a, set()) for a in candidates):
        ref.verdict = "ok"
        return
    ref.verdict = "section_outside_v1"
    parts = []
    for a in candidates:
        heading = policy.headings.get((a, ref.number))
        parts.append(f"Section {ref.number} of the {acts[a]['name']}" + (f' ("{heading}")' if heading else ""))
    verb = "is" if len(parts) == 1 else "are"
    ref.text = f"{' and '.join(parts)} {verb} outside the corpus this tool covers (v1)."


def _check_schedule(ref: Reference, policy: GuardrailsPolicy, schedules: dict[str, set[int]]) -> None:
    act = ref.act or "nta2025"  # only the Nigeria Tax Act has Schedules on record
    count = policy.acts[act]["schedules"]
    name = policy.acts[act]["name"]
    if count is None:
        return  # unchecked
    if not 1 <= ref.number <= count:
        ref.verdict = "no_such_section"
        ref.text = f"The {name} has no {_ordinal(ref.number)} Schedule. It has {count} Schedules."
    elif ref.number in schedules.get(act, set()):
        ref.verdict = "ok"
    else:
        ref.verdict = "section_outside_v1"
        ref.text = f"The {_ordinal(ref.number)} Schedule of the {name} is outside the corpus this tool covers (v1)."


def _note(ref: str, text: str) -> Disclosure:
    return Disclosure("note", ref, "question", "guardrail", text)


# Note labels, as the adversarial set's `required_notes` names them: `scope:<topic>` (the policy's
# topic keys), `act:<act_name>` (the Act's name in lower case, words joined by "_"),
# `reference:<verdict>` (no_such_section, section_outside_v1) and the disclosure ids (DQ-12, ...).
REFERENCE_REFUSALS = ("no_such_section", "section_outside_v1")


def act_label(name: str) -> str:
    return "act:" + "_".join(re.findall(r"[a-z0-9]+", name.lower()))


def note_labels(notes: list[Disclosure], refusal_reason: str | None = None,
                disclosures: list[Disclosure] = ()) -> list[str]:
    """Every note label an answer shows: guardrail notes, a reference refusal (its text plays the
    note's part), and shown disclosures."""
    labels = [n.ref for n in notes]
    if refusal_reason in REFERENCE_REFUSALS:
        labels.append(f"reference:{refusal_reason}")
    labels += [d.ref for d in disclosures if d.kind != "hidden"]
    return list(dict.fromkeys(labels))


def pre_check(question: str, policy: GuardrailsPolicy, corpus: dict[str, dict[str, set]]) -> PreCheck:
    """Pre-checks for `question`. `corpus` is the store's `sections()`: per Act, the section
    numbers ("sections", strings) and Schedule numbers ("schedules", ints) in the v1 corpus."""
    result = PreCheck()
    text = question.strip()
    if not text or not re.search(r"[A-Za-z]", text) or len(text) > policy.max_question_chars:
        result.refusal, result.refusal_text = "invalid_question", INVALID_TEXT
        return result

    sections = {a: corpus.get(a, {}).get("sections", set()) for a in CORPUS_ACTS}
    schedules = {a: corpus.get(a, {}).get("schedules", set()) for a in CORPUS_ACTS}
    result.references = parse_references(question, policy)
    for ref in result.references:
        if ref.act and ref.act.startswith("other:"):
            ref.verdict = "other_act"
        elif ref.kind == "section":
            _check_section(ref, policy, sections)
        else:
            _check_schedule(ref, policy, schedules)

    corpus_refs = [r for r in result.references if r.verdict in ("ok", "no_such_section", "section_outside_v1")]
    failed = [r for r in corpus_refs if r.verdict != "ok"]
    if corpus_refs and len(failed) == len(corpus_refs):
        result.refusal = failed[0].verdict
        result.refusal_text = " ".join(dict.fromkeys(r.text for r in failed))
    elif failed:
        for verdict, text in dict.fromkeys((r.verdict, r.text) for r in failed):
            result.notes.append(_note(f"reference:{verdict}", text))
        result.flags.append("reference_note")

    named_others = dict.fromkeys(a[len("other:"):] for _, _, a in _act_mentions(question, policy)
                                 if a.startswith("other:"))
    for name in named_others:
        result.notes.append(_note(act_label(name), policy.other_act_note.format(name=name)))
    if named_others:
        result.flags.append("act_note")

    for topic, (pattern, note) in policy.scope_topics.items():
        if pattern.search(question):
            result.notes.append(_note(f"scope:{topic}", f"{note} {policy.scope_note_suffix}"))
            if "scope_note" not in result.flags:
                result.flags.append("scope_note")
    return result


# Relevance floor ---------------------------------------------------------------------------------


@dataclass
class Floor:
    status: str              # not_calibrated | exempt_lookup | no_passages | pass | below
    top1: float | None
    tau: float | None

    @property
    def refuse(self) -> bool:
        return self.status == "below"

    def as_log(self) -> dict:
        return {"status": self.status, "top1_vector_score": self.top1, "tau": self.tau}


def relevance_floor(retrieved: list, tau: float | None) -> Floor:
    """Floor check on the retrieval results (`Retrieved` items, rank order)."""
    vector = [item.score for item in retrieved if not any(r.startswith("section lookup") for r in item.reasons)]
    top1 = max(vector) if vector else None
    if any(r.startswith("section lookup") for item in retrieved for r in item.reasons):
        return Floor("exempt_lookup", top1, tau)
    if tau is None:
        return Floor("not_calibrated", top1, tau)
    if top1 is None:
        return Floor("no_passages", top1, tau)
    return Floor("below" if top1 < tau else "pass", top1, tau)
