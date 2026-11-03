"""Answer disclosure rule for disputed text (D-23 to D-25; generation design, section 5).

    from generation.disclosure import load_policy, disclose
    disclose(cited_chunk_ids, register_rows, question, load_policy())

Only register rows on chunks the answer cites are candidates. Each candidate is shown, merged
under a duplicate, or hidden:

1. on the always list (DQ-14 printing slips and DR-206): shown, whatever its type;
2. an anchor row (missing_content with an empty cited reading): hidden with its chunk, and shown
   on its own only when the question names its missing term;
3. on the cross-reference list: shown, whatever its type (the merge list folds duplicates);
4. substantive, missing_content or numbering: shown;
5. wording: hidden.

Hidden rows are returned with kind "hidden" so they reach the log, but the renderer skips them.
Two data-quality notes are added: DQ-16 when NTA s191 is cited, and DQ-12 on Administration Act
commencement questions. The lists and texts are pinned in data/curated/disclosure_policy.yml.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from retrieval.retrieve import _ACT_NAME, _act_of  # noqa: E402

POLICY = ROOT / "data" / "curated" / "disclosure_policy.yml"
SHOWN_TYPES = {"substantive", "missing_content", "numbering"}
COMPARISON_NAMES = {"nass": "National Assembly text", "gombe": "Gombe State IRS print"}


@dataclass
class Disclosure:
    kind: str           # "register" or "note" (rendered); "hidden" (logged, not rendered)
    ref: str            # register id or DQ id
    applies_to: str     # citation of the cited chunk, or "question"
    reason: str         # always | cross_reference | type | question | note | wording | anchor
    text: str
    merged: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Policy:
    always: frozenset[str]
    cross_reference: frozenset[str]
    merge: dict[str, str]
    display_text: dict[str, str]
    anchor_terms: dict[str, str]
    dq16_chunk: str
    dq16_text: str
    dq12_patterns: tuple[re.Pattern, ...]
    dq12_text: str
    dq12_chunks: frozenset[str] = frozenset()
    dq12_time_words: re.Pattern | None = None


def load_policy(path: Path = POLICY) -> Policy:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    notes = data["notes"]
    return Policy(
        always=frozenset(data["always"]),
        cross_reference=frozenset(data["cross_reference"]),
        merge=dict(data["merge"]),
        display_text={k: " ".join(v.split()) for k, v in data["display_text"].items()},
        anchor_terms={k: v.lower() for k, v in data["anchor_terms"].items()},
        dq16_chunk=notes["DQ-16"]["when_cited"],
        dq16_text=" ".join(notes["DQ-16"]["text"].split()),
        dq12_patterns=tuple(re.compile(p, re.IGNORECASE) for p in notes["DQ-12"]["patterns"]),
        dq12_text=" ".join(notes["DQ-12"]["text"].split()),
        dq12_chunks=frozenset(notes["DQ-12"].get("retrieved_chunks", [])),
        dq12_time_words=(re.compile(notes["DQ-12"]["time_words"], re.IGNORECASE)
                         if notes["DQ-12"].get("time_words") else None),
    )


def is_anchor(row: dict) -> bool:
    return row["type"] == "missing_content" and not (row.get("cited_reading") or "").strip()


def decide(row: dict, policy: Policy) -> tuple[bool, str]:
    """(shown with its chunk, reason) for one register row, by the rule in the module docstring."""
    rid = row["register_id"]
    if rid in policy.always:
        return True, "always"
    if is_anchor(row):
        return False, "anchor"
    if rid in policy.cross_reference:
        return True, "cross_reference"
    if row["type"] in SHOWN_TYPES:
        return True, "type"
    return False, "wording"


def row_text(row: dict, policy: Policy) -> str:
    """The register note, else the policy text, else both readings."""
    note = (row.get("notes") or "").strip()
    if note:
        return note
    if row["register_id"] in policy.display_text:
        return policy.display_text[row["register_id"]]
    other = COMPARISON_NAMES.get(row["comparison_text"], row["comparison_text"])
    return (f'Cited text: "{(row.get("cited_reading") or "").strip()}"; {other}: '
            f'"{(row.get("comparison_reading") or "").strip()}".')


def names_term(question: str, term: str) -> bool:
    return re.search(r"\b" + re.escape(term) + r"\b", question, re.IGNORECASE) is not None


def is_ntaa_commencement_question(question: str, policy: Policy, retrieved: list[str] | None = None) -> bool:
    """A commencement pattern matches, or (D-39) a trigger chunk was retrieved and the question has
    a time word; in both cases the question must not name only the Nigeria Tax Act."""
    by_pattern = any(p.search(question) for p in policy.dq12_patterns)
    by_retrieval = bool(retrieved and policy.dq12_time_words is not None
                        and policy.dq12_chunks & set(retrieved) and policy.dq12_time_words.search(question))
    if not (by_pattern or by_retrieval):
        return False
    acts = {_act_of(m.group(0)) for m in _ACT_NAME.finditer(question)}
    return not acts or "ntaa2025" in acts


def disclose(cited: list[str], register_rows: list[dict], question: str, policy: Policy,
             citations: dict[str, str] | None = None, retrieved: list[str] | None = None) -> list[Disclosure]:
    """Disclosures for an answer citing `cited` (chunk ids, first-citation order).

    `register_rows` holds at least every row on the cited chunks and the anchor rows; other rows
    are ignored. `citations` maps chunk id to its human-readable citation (default: the id).
    `retrieved` (all retrieved chunk ids) feeds the DQ-12 retrieval trigger only.
    """
    citations = citations or {}
    order = {chunk_id: i for i, chunk_id in enumerate(cited)}
    by_id = {row["register_id"]: row for row in register_rows}
    candidates = sorted((row for row in register_rows if row.get("cited_unit") in order),
                        key=lambda row: (order[row["cited_unit"]], row["register_id"]))
    out: list[Disclosure] = []
    shown: dict[str, Disclosure] = {}
    deferred_merges: list[dict] = []
    for row in candidates:
        rid = row["register_id"]
        applies_to = citations.get(row["cited_unit"], row["cited_unit"])
        is_shown, reason = decide(row, policy)
        if reason == "anchor" and names_term(question, policy.anchor_terms.get(rid, "\0")):
            continue  # shown below as a question note instead
        if is_shown and rid in policy.merge:
            deferred_merges.append(row)
            continue
        item = Disclosure("register" if is_shown else "hidden", rid, applies_to, reason, row_text(row, policy))
        out.append(item)
        if is_shown:
            shown[rid] = item
    for row in deferred_merges:
        parent = shown.get(policy.merge[row["register_id"]])
        if parent is not None and by_id.get(parent.ref, {}).get("cited_unit") == row["cited_unit"]:
            parent.merged.append(row["register_id"])
        else:  # parent not among the candidates: show the row on its own
            out.append(Disclosure("register", row["register_id"],
                                  citations.get(row["cited_unit"], row["cited_unit"]),
                                  decide(row, policy)[1], row_text(row, policy)))

    if policy.dq16_chunk in order:
        out.append(Disclosure("note", "DQ-16", citations.get(policy.dq16_chunk, policy.dq16_chunk),
                              "note", policy.dq16_text))
    for rid, term in sorted(policy.anchor_terms.items()):
        if rid in by_id and names_term(question, term):
            row = by_id[rid]
            reading = (row.get("comparison_reading") or "").strip()
            other = COMPARISON_NAMES.get(row["comparison_text"], row["comparison_text"])
            out.append(Disclosure("note", rid, "question", "question",
                                  f"{row_text(row, policy)} The {other} reads: {reading}"))
    if is_ntaa_commencement_question(question, policy, retrieved):
        out.append(Disclosure("note", "DQ-12", "question", "question", policy.dq12_text))
    return out
