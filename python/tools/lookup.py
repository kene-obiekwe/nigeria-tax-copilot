"""The section lookup tool (Phase 7, D-118; tools design, section 3).

    from tools.lookup import lookup_section
    result = lookup_section({"act": "nta2025", "section_number": "148", "subsection": None},
                            retriever=retriever, store=store, policy=policy, corpus=store.sections(),
                            query_vector=vector, shown={"nta2025:s56": "P1"}, free_labels=["P9", "P10"],
                            chunk_budget=3)

Nothing here is new retrieval. A reference is checked with the pre-check's own code
(`guardrails.check_section`: `ok`, `no_such_section` or `section_outside_v1`, with the same texts),
and an in-corpus section is fetched with the D-21 exact section lookup (`Retriever.lookup`), nearest
to the question's own embedding first. A subsection that the section does not print gets the D-81
text. Chunks already shown are named by their labels, never repeated, and at most `chunk_budget` new
chunks are returned (the question's cap is 3 across all lookups). New chunks are written exactly as
`prompt.passage_block` writes passages, under the next free labels (P9 onward).

The tool result is data for the model. A refusal text here is never an answer: the answer still goes
through the parser and the verifier.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from generation.guardrails import GuardrailsPolicy, Reference, _labels_text, check_section  # noqa: E402
from generation.prompt import passage_block  # noqa: E402
from retrieval.references import CORPUS_ACTS  # noqa: E402
from retrieval.retrieve import SectionRef  # noqa: E402

INPUT_FIELDS = ("act", "section_number", "subsection")
_SECTION_NUMBER = re.compile(r"\s*(\d+)\s*([A-Za-z]?)\s*")
_SUBSECTION = re.compile(r"\s*\(?\s*(\d+)\s*\)?\s*")
LIMIT_TEXT = "The lookup limit for this question has been reached."
PASSAGE_LIMIT_TEXT = "The limit of passages that lookups may add for this question has been reached."


@dataclass
class LookupResult:
    is_error: bool
    content: str
    verdict: str                        # ok | already_shown | no_such_section | section_outside_v1 |
    #                                     no_such_subsection | invalid_input | limit
    added: list[tuple[str, str]] = field(default_factory=list)   # (label, chunk_id) for new passages
    already_shown: list[tuple[str, str]] = field(default_factory=list)
    section_chunks: int = 0             # chunks of the section (after the subsection filter)

    def as_log(self) -> dict:
        return {"verdict": self.verdict, "added": [list(x) for x in self.added],
                "already_shown": [list(x) for x in self.already_shown], "section_chunks": self.section_chunks}


def input_problem(inputs) -> str | None:
    """Why a tool input is not a well-formed lookup_section call, or None (an `is_error` result)."""
    if not isinstance(inputs, dict) or set(inputs) != set(INPUT_FIELDS):
        return "the input must have exactly the fields " + ", ".join(INPUT_FIELDS)
    if inputs["act"] not in CORPUS_ACTS:
        return "act must be nta2025 or ntaa2025"
    if not isinstance(inputs["section_number"], str) or not _SECTION_NUMBER.fullmatch(inputs["section_number"]):
        return 'section_number must be a section number such as "148"'
    sub = inputs["subsection"]
    if sub is not None and (not isinstance(sub, str) or not _SUBSECTION.fullmatch(sub)):
        return 'subsection must be a subsection number such as "1", or null'
    return None


def _section_name(policy: GuardrailsPolicy, act: str, number: str) -> str:
    return f"Section {number} of the {policy.acts[act]['name']}"


def lookup_section(inputs: dict, *, retriever, store, policy: GuardrailsPolicy, corpus: dict,
                   query_vector: list[float], shown: dict[str, str], free_labels: list[str],
                   chunk_budget: int) -> LookupResult:
    """Run one lookup. `shown` maps every chunk id already shown to its label; `free_labels` are the
    labels still free, in order; `chunk_budget` is how many new chunks this question may still add."""
    problem = input_problem(inputs)
    if problem:
        return LookupResult(True, problem, "invalid_input")
    act = inputs["act"]
    m = _SECTION_NUMBER.fullmatch(inputs["section_number"])
    ref = Reference("section", int(m.group(1)), act, suffix=m.group(2).upper())
    sections = {a: corpus.get(a, {}).get("sections", set()) for a in CORPUS_ACTS}
    check_section(ref, policy, sections)
    if ref.verdict != "ok":
        return LookupResult(True, ref.text, ref.verdict)

    number = str(ref.number)
    name = _section_name(policy, act, number)
    rows = retriever.lookup([SectionRef(number, act)], query_vector)
    chunk_ids = [chunk_id for chunk_id, *_ in rows]
    if inputs["subsection"] is not None:
        sub = int(_SUBSECTION.fullmatch(inputs["subsection"]).group(1))
        printed = sorted(corpus.get(act, {}).get("subsections", {}).get(number, set()))
        if sub not in printed:
            if printed:
                has = f"has subsection{'s' if len(printed) > 1 else ''} {_labels_text(printed)} only"
            else:
                has = "has no numbered subsections"
            text = f"As printed, section {number} of the {policy.acts[act]['name']} {has}; there is no subsection ({sub})."
            return LookupResult(True, text, "no_such_subsection")
        metadata = store.subsections(chunk_ids)
        # A chunk with no subsection metadata holds its whole section; a split chunk lists its own.
        chunk_ids = [c for c in chunk_ids if not metadata.get(c) or f"({sub})" in metadata[c]]
        name += f", subsection ({sub})"

    already = [(shown[c], c) for c in chunk_ids if c in shown]
    new = [c for c in chunk_ids if c not in shown]
    if new and chunk_budget <= 0:
        return LookupResult(True, PASSAGE_LIMIT_TEXT, "limit", already_shown=already, section_chunks=len(chunk_ids))
    take = new[:min(chunk_budget, len(free_labels))]
    added = list(zip(free_labels, take))
    lines = []
    if already:
        labels = ", ".join(label for label, _ in already)
        lines.append(f"{name} is already among the passages as {labels}.")
    if take:
        if len(chunk_ids) == 1:
            lines.append(f"{name}: 1 chunk, returned below.")
        else:
            lines.append(f"{name} has {len(chunk_ids)} chunks in the corpus; {len(take)} new "
                         f"chunk{'s are' if len(take) > 1 else ' is'} returned below, nearest to the question first.")
    elif new:
        lines.append(f"{name} has {len(new)} further chunk{'s' if len(new) > 1 else ''} not returned, because the "
                     "passage limit for this question has been reached.")
    chunks = store.chunks(take)
    blocks = [passage_block(label, chunks[chunk_id]) for label, chunk_id in added]
    content = "\n".join(lines + blocks)
    return LookupResult(False, content, "ok" if take else "already_shown", added, already, len(chunk_ids))
