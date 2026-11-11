"""Guardrail pre-checks and the relevance floor (D-34, D-35; guardrails design, section 4).

    from generation.guardrails import load_guardrails_policy, pre_check, relevance_floor
    check = pre_check(question, load_guardrails_policy(), store.sections())

Pre-checks run before retrieval and cost nothing. They refuse only where code can be certain:

1. **Invalid question:** empty, no letters, or longer than the pinned limit.
2. **References:** a section number past the end of the Act (`no_such_section`), a Schedule the
   Nigeria Tax Act does not have (`no_such_section`), or a real section that is not in the v1
   corpus (`section_outside_v1`, named by its heading from data/derived/section_map.csv, with the
   checked corrections in data/curated/heading_corrections.yml applied, D-60). A
   question is refused only when it has at least one reference to the corpus Acts, every such
   reference fails, and none is valid. Otherwise failed references become notes. When the same
   section number is named with an Act and also without one, the unnamed reference is left out of
   the refusal text, so the fact is stated once (D-60).
   A lettered number ("section 57A") is `no_such_section`: neither Act has a lettered section
   (D-80).
3. **Notes, never refusals:** an Act outside the corpus (CITA, ...), scope topics from the v1
   known gaps (stamp duties, PAYE, ...), and a subsection that is not printed in an in-corpus
   section ("section 100(4)" where section 100 prints only (1) and (2): the note
   `reference:no_such_subsection`, D-81). The printed subsection labels of each in-corpus section
   are read from its chunk text (`printed_subsections`): a line opening "(n)" or "N. (1)"; DQ-07's
   (1), (2) and (4) for Nigeria Tax Act section 57 are kept as printed.
4. **Another country's tax law** (`foreign_law`, D-110, amending D-34 for this one class): the
   question places a tax in a foreign country (a strong form, or a weak form with no first-person
   word) and carries no Nigerian, cross-border or Nigerian-treatment hook (G2 fix design 4, section
   2.3). It is checked last, only when no other refusal applies; a reference refusal needs a section
   or Schedule reference, which is a hook, so the two never both apply. The lists are in the
   policy's `foreign_law` block.

References are read by the parser retrieval also uses (`retrieval.references`, D-54): the Act
named after a reference, else the one named before it, with Acts outside the corpus included. Under
`config_v2.json` retrieval does not look up a reference attributed to an Act outside the corpus,
and adds the corpus chunks that name that Act; the note tells the reader what the section numbers
mean.

The relevance floor runs after retrieval: when tau is set, a question whose best vector score is
below tau is refused (`low_relevance`); questions answered by a section lookup are exempt. Only
vector results count: an Act-name lookup is not exempt and a keyword slot is never read (D-54), nor
is a commencement lookup or a definition attachment (D-100, `config_v3.json`).
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
from retrieval import references  # noqa: E402
from retrieval.references import CORPUS_ACTS, ORDINAL_WORDS, ORDINALS  # noqa: E402

POLICY = ROOT / "data" / "curated" / "guardrails_policy.yml"
SECTION_MAP = ROOT / "data" / "derived" / "section_map.csv"
HEADING_CORRECTIONS = ROOT / "data" / "curated" / "heading_corrections.yml"
CITED_PRINT = {"nta2025": "gazette", "ntaa2025": "nrs"}
_SCHEDULE_REF, _CORPUS_ACT = references.SCHEDULE_REF, references.CORPUS_ACT  # kept for existing callers

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
    foreign_law: "ForeignLawRules | None" = None  # D-110; None when the policy has no foreign_law block


# Another country's tax law (D-110; G2 fix design 4, section 2.3) ---------------------------------


def _alt(items) -> str:
    return "|".join(items)


@dataclass(frozen=True)
class ForeignLawRules:
    refusal_text: str
    strong: tuple            # (form name, compiled pattern), S1 to S7
    there: re.Pattern        # S8, read only when `foreign_place` also matches
    foreign_place: re.Pattern
    located: re.Pattern      # W1
    tax_near: re.Pattern     # W2; group 1 holds the words between the tax word and "in"
    trading_in: re.Pattern   # W3
    counterparty: re.Pattern
    first_person: re.Pattern
    hook: re.Pattern


def load_foreign_law(data: dict) -> ForeignLawRules:
    """Build the forms of G2 fix design 4, section 2.3 (prototype 4, with the narrowed S2) from the
    policy's `foreign_law` lists."""
    place = rf"(?:the\s+)?(?:(?i:{_alt(data['countries'])})|(?-i:{_alt(data['countries_cased'])}))"
    city = rf"(?i:{_alt(data['cities'])})"
    demonym = rf"(?i:{_alt(data['demonyms'])})"
    loc = rf"(?:{place}|{city})"
    authority = _alt(a.replace("{place}", place).replace("{demonym}", demonym) for a in data["authorities"])
    tax, not_after = data["tax_word"], data["not_after_tax_word"]
    taxpayer, located_taxpayer = _alt(data["taxpayers"]), _alt(data["located_taxpayers"])

    def c(pattern: str) -> re.Pattern:
        return re.compile(pattern, re.IGNORECASE)

    strong = (
        ("S1", c(rf"\b(?:{place}|{demonym})(?:['’]s)?\s+{tax}\b{not_after}")),
        ("S2", c(authority)),
        ("S3", c(rf"\b(?:does|do|did|will|would|can|is)\s+{place}\s+(?:government\s+)?(?:{_alt(data['taxing_verbs'])})\b")),
        ("S4", c(rf"\b{tax}\s+(?:(?:rates?|laws?|rules|regime|system)\s+)?in\s+{loc}\b")),
        ("S5", c(rf"\b{tax}\s+rates?\s+(?:for|of|on)\s+(?:\w+\s+){{0,3}}?(?:{taxpayer})\s+in\s+{loc}\b")),
        ("S6", c(rf"\b(?:taxed|taxable|chargeable to tax)\s+in\s+{loc}\b")),
        ("S7", c(rf"^\s*(?:In|Under|Within)\s+{loc}\s*,")),
    )
    return ForeignLawRules(
        refusal_text=" ".join(data["refusal_text"].split()),
        strong=strong,
        there=c(rf"\b(?:{_alt(data['there_verbs'])})\s+there\b"),
        foreign_place=c(rf"\b(?:{place}|{city}|{demonym})\b|{authority}"),
        located=c(rf"\b(?:{located_taxpayer})\s+(?:(?:set up|based|operating|trading)\s+)?in\s+{loc}\b"),
        tax_near=c(rf"\b{tax}\s+((?:[\w'’-]+\s+){{0,4}}?)in\s+{loc}\b"),
        trading_in=c(rf"\b(?:{_alt(data['located_verbs'])})\s+in\s+{loc}\b"),
        counterparty=c(rf"\b(?:{_alt(data['counterparty_words'])})\b"),
        first_person=c(_alt(data["first_person"])),
        hook=c(_alt(data["hooks"])),
    )


@dataclass
class ForeignLawCheck:
    fired: bool
    strong: list[tuple[str, str]]   # (form, matched text)
    weak: list[tuple[str, str]]
    first_person: list[str]
    hooks: list[str]

    def as_log(self) -> dict:
        return {"fired": self.fired, "strong": [list(x) for x in self.strong], "weak": [list(x) for x in self.weak],
                "first_person": self.first_person, "hooks": self.hooks}


def foreign_law_check(question: str, rules: ForeignLawRules) -> ForeignLawCheck:
    """fire = (strong or (weak and no first-person word)) and no hook (design 4, section 2.3)."""
    strong = [(name, m.group(0)) for name, pattern in rules.strong for m in [pattern.search(question)] if m]
    if rules.foreign_place.search(question) and (m := rules.there.search(question)):
        strong.append(("S8", m.group(0)))
    weak = []
    for m in rules.located.finditer(question):  # W1: no counterparty word in the 4 words before
        if not rules.counterparty.search(" ".join(question[:m.start()].split()[-4:])):
            weak.append(("W1", m.group(0)))
            break
    for m in rules.tax_near.finditer(question):  # W2: no counterparty word between
        if not rules.counterparty.search(m.group(1)):
            weak.append(("W2", m.group(0)))
            break
    if m := rules.trading_in.search(question):
        weak.append(("W3", m.group(0)))
    first_person = [m.group(0) for m in rules.first_person.finditer(question)]
    hooks = [m.group(0) for m in rules.hook.finditer(question)]
    fired = bool(strong or (weak and not first_person)) and not hooks
    return ForeignLawCheck(fired, strong, weak, first_person, hooks)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_heading_corrections(path: Path | None = HEADING_CORRECTIONS) -> list[dict]:
    """The checked heading corrections (DQ-18, D-60); none when `path` is None."""
    if path is None:
        return []
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))["corrections"]
    for row in rows:
        if CITED_PRINT.get(row["act_id"]) != row["print"]:
            raise ValueError(f"heading correction for {row['act_id']} s{row['section']} is not for the cited print")
    return rows


def load_section_headings(path: Path = SECTION_MAP,
                          corrections: Path | None = HEADING_CORRECTIONS) -> dict[tuple[str, int], str]:
    """Every section of the cited print of each Act, with its heading. A correction replaces a
    derived heading only while the derived file still holds the heading it was written against."""
    out = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["act_id"] in CITED_PRINT and row["cited_print"] == CITED_PRINT[row["act_id"]] \
                    and row["cited_section"].isdigit():
                out.setdefault((row["act_id"], int(row["cited_section"])), row["cited_heading"].strip())
    for row in load_heading_corrections(corrections):
        key = (row["act_id"], int(row["section"]))
        if out.get(key) != row["derived"]:
            raise ValueError(f"heading correction for {key[0]} s{key[1]} expects the derived heading "
                             f"{row['derived']!r}, found {out.get(key)!r}")
        out[key] = row["heading"]
    return out


def load_guardrails_policy(path: Path = POLICY, section_map: Path = SECTION_MAP,
                           heading_corrections: Path | None = HEADING_CORRECTIONS) -> GuardrailsPolicy:
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
        headings=load_section_headings(section_map, heading_corrections),
        sha256=_sha256(path),
        foreign_law=load_foreign_law(data["foreign_law"]) if data.get("foreign_law") else None,
    )


# References --------------------------------------------------------------------------------------


@dataclass
class Reference:
    kind: str                   # "section" | "schedule"
    number: int
    act: str | None             # corpus act id, None (no Act named) or "other:<name>"
    verdict: str = "unchecked"  # ok | no_such_section | section_outside_v1 | other_act | unchecked
    text: str = ""              # refusal or note text for a failed reference
    suffix: str = ""            # the letter of a lettered number ("57A"), D-80

    @property
    def label(self) -> str:
        return f"{self.number}{self.suffix}"


@dataclass
class PreCheck:
    refusal: str | None = None  # invalid_question | no_such_section | section_outside_v1 | foreign_law
    refusal_text: str | None = None
    references: list[Reference] = field(default_factory=list)
    notes: list[Disclosure] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    foreign_law: ForeignLawCheck | None = None  # D-110; None when not checked

    def as_log(self) -> dict:
        return {"refusal": self.refusal,
                "references": [{"kind": r.kind, "number": r.number, "suffix": r.suffix, "act": r.act,
                                "verdict": r.verdict} for r in self.references],
                "notes": [n.ref for n in self.notes], "labels": note_labels(self.notes, self.refusal),
                "flags": self.flags,
                "foreign_law": None if self.foreign_law is None else self.foreign_law.as_log()}


def _act_mentions(question: str, policy: GuardrailsPolicy) -> list[tuple[int, int, str]]:
    """(start, end, act) for every Act named; Acts outside the corpus win where spans overlap."""
    return references.act_mentions(question, policy.other_acts)


def parse_references(question: str, policy: GuardrailsPolicy) -> list[Reference]:
    """Section and Schedule references, each with its Act, by the shared parser (D-54)."""
    return [Reference(r.kind, r.number, r.act, suffix=r.suffix)
            for r in references.parse_references(question, policy.other_acts)]


def _ordinal(n: int) -> str:
    return ORDINAL_WORDS[n - 1].capitalize() if 1 <= n <= len(ORDINAL_WORDS) else f"{n}th"


def check_section(ref: Reference, policy: GuardrailsPolicy, sections: dict[str, set[str]]) -> None:
    """Set `ref.verdict` and `ref.text` for a section reference: `ok`, `no_such_section` (past the
    end of the Act, or lettered, D-80) or `section_outside_v1` (a real section not in the corpus,
    named by its corrected heading, D-60). `sections` holds, per corpus Act, the section numbers in
    the v1 corpus. Public since Phase 7 (D-118): the lookup tool checks a reference with the
    pre-check's own code."""
    acts = policy.acts
    if ref.suffix:  # D-80: neither Act numbers a section with a letter
        ref.verdict = "no_such_section"
        if ref.act is None:
            nta, ntaa = acts["nta2025"], acts["ntaa2025"]
            ref.text = (f"Neither the {nta['name']} nor the {ntaa['name']} has a section {ref.label}. Their sections "
                        f"are numbered 1 to {nta['sections']} and 1 to {ntaa['sections']}, none with a letter.")
        else:
            ref.text = (f"The {acts[ref.act]['name']} has no section {ref.label}. Its sections are numbered 1 to "
                        f"{acts[ref.act]['sections']}, none with a letter.")
        return
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


# Subsections (D-81) --------------------------------------------------------------------------------

_SECTION_CHUNK = re.compile(r"^(nta2025|ntaa2025):s(\d+)")
_SUBSECTION_LINE = re.compile(r"\s*(?:\d+[A-Z]?\.\s*)?\((\d+)\)")


def printed_subsections(chunk_id: str, text: str) -> tuple[str, str, set[int]] | None:
    """(act, section, subsection labels printed in this chunk) for a section chunk, or None for a
    definition entry, a Schedule chunk or anything else. A label is a line opening "(n)" or
    "N. (1)" (G2 fix design 2, section 5.2(b))."""
    m = _SECTION_CHUNK.match(chunk_id)
    if not m or ":def:" in chunk_id:
        return None
    labels = set()
    for line in text.split("\n"):
        x = _SUBSECTION_LINE.match(line)
        if x:
            labels.add(int(x.group(1)))
    return m.group(1), m.group(2), labels


def add_printed_subsections(out: dict[str, dict], chunk_id: str, text: str) -> None:
    """Add one chunk's printed subsection labels to a `sections()` result (per Act, "subsections")."""
    found = printed_subsections(chunk_id, text)
    if found is not None:
        act, section, labels = found
        out.setdefault(act, {"sections": set(), "schedules": set()}).setdefault("subsections", {}) \
            .setdefault(section, set()).update(labels)


def _labels_text(labels: list[int]) -> str:
    items = [f"({n})" for n in labels]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _subsection_notes(question: str, policy: GuardrailsPolicy, printed: dict[str, dict[str, set[int]]]) -> list[str]:
    """Note texts for subsection references that the in-corpus section does not print (D-81).
    A reference to an Act outside the corpus, or to a section outside the corpus, gets none."""
    out: list[str] = []
    for ref in references.parse_subsection_refs(question, policy.other_acts):
        if ref.act and ref.act.startswith("other:"):
            continue
        acts = [ref.act] if ref.act else list(CORPUS_ACTS)
        candidates = [a for a in acts if str(ref.section) in printed.get(a, {})]
        if not candidates or any(ref.subsection in printed[a][str(ref.section)] for a in candidates):
            continue
        parts = []
        for a in candidates:
            labels = sorted(printed[a][str(ref.section)])
            name = policy.acts[a]["name"]
            if labels:
                has = f"has subsection{'s' if len(labels) > 1 else ''} {_labels_text(labels)} only"
            else:
                has = "has no numbered subsections"
            parts.append(f"As printed, section {ref.section} of the {name} {has}; there is no subsection "
                         f"({ref.subsection}).")
        text = " ".join(parts)
        if text not in out:
            out.append(text)
    return out


def _note(ref: str, text: str) -> Disclosure:
    return Disclosure("note", ref, "question", "guardrail", text)


# Note labels, as the adversarial set's `required_notes` names them: `scope:<topic>` (the policy's
# topic keys), `act:<act_name>` (the Act's name in lower case, words joined by "_"),
# `reference:<verdict>` (no_such_section, section_outside_v1), `reference:no_such_subsection` (D-81)
# and the disclosure ids (DQ-12, ...).
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
    numbers ("sections", strings) and Schedule numbers ("schedules", ints) in the v1 corpus, and
    each in-corpus section's printed subsection labels ("subsections": section -> set of ints)."""
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
            check_section(ref, policy, sections)
        else:
            _check_schedule(ref, policy, schedules)

    corpus_refs = [r for r in result.references if r.verdict in ("ok", "no_such_section", "section_outside_v1")]
    failed = [r for r in corpus_refs if r.verdict != "ok"]
    if corpus_refs and len(failed) == len(corpus_refs):
        # D-60: a section number also named with an Act is stated once, under the named Act.
        named = {(r.kind, r.number, r.suffix) for r in failed if r.act}
        stated = [r for r in failed if r.act or (r.kind, r.number, r.suffix) not in named]
        result.refusal = stated[0].verdict
        result.refusal_text = " ".join(dict.fromkeys(r.text for r in stated))
    elif failed:
        for verdict, text in dict.fromkeys((r.verdict, r.text) for r in failed):
            result.notes.append(_note(f"reference:{verdict}", text))
        result.flags.append("reference_note")

    printed = {a: corpus.get(a, {}).get("subsections", {}) for a in CORPUS_ACTS}
    for text in _subsection_notes(question, policy, printed):
        result.notes.append(_note("reference:no_such_subsection", text))
        if "reference_note" not in result.flags:
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

    # D-110: another country's tax law, only when no reference refusal applies; notes are kept.
    if result.refusal is None and policy.foreign_law is not None:
        result.foreign_law = foreign_law_check(question, policy.foreign_law)
        if result.foreign_law.fired:
            result.refusal, result.refusal_text = "foreign_law", policy.foreign_law.refusal_text
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


NOT_READ_BY_FLOOR = ("section lookup", "keyword slot", "commencement lookup", "definition attachment")


def _is_vector_result(item) -> bool:
    """A result the floor reads: not a section lookup, a keyword slot (D-54), a commencement lookup or
    a definition attachment (D-100); an Act-name lookup only where it is also a vector result (D-54)."""
    if any(r.startswith(NOT_READ_BY_FLOOR) for r in item.reasons):
        return False
    if any(r.startswith("act-name lookup") for r in item.reasons):
        return any(r.startswith("vector rank") for r in item.reasons)
    return True


def relevance_floor(retrieved: list, tau: float | None) -> Floor:
    """Floor check on the retrieval results (`Retrieved` items, rank order)."""
    vector = [item.score for item in retrieved if _is_vector_result(item)]
    top1 = max(vector) if vector else None
    if any(r.startswith("section lookup") for item in retrieved for r in item.reasons):
        return Floor("exempt_lookup", top1, tau)
    if tau is None:
        return Floor("not_calibrated", top1, tau)
    if top1 is None:
        return Floor("no_passages", top1, tau)
    return Floor("below" if top1 < tau else "pass", top1, tau)
