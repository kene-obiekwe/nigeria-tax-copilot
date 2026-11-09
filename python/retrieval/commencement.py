"""Commencement lookup (D-99; G2 fix design 3, section 3.3).

    from retrieval.commencement import load_commencement
    rule = load_commencement()
    rule.target("When did the NTA kick in?")   # "nta2025"
    rule.chunk_for("nta2025")                    # "nta2025:s203"

The embedding does not read "NTA" as the Nigeria Tax Act, so a question asking when the Act starts
can miss its commencement section (ADV3-043: `nta2025:s203` ranked 61st). This rule reads the
question instead, with no embedding. It fires when the question has either

(a) a commencement phrase anywhere ("come into force", "take effect", "commencement date of the
    Act", ...), or
(b) an Act word followed within two words by a start verb ("kick in", "start", "apply", ...),
    together with a time word ("when", "date", "since", ...);

and the Act is the Nigeria Tax Act: either the question names it (the shared parser's
`CORPUS_ACT`, so "NTA" counts), or it names no Act, has an Act word and does not contain
"administration". A question that names only the Administration Act, or only Acts outside the
corpus, never fires. The Administration Act has no commencement chunk in the v1 corpus (DQ-12),
so nothing is attached for it.

The patterns, the time words, the exclusion and the chunk map are in
data/curated/retrieval_policy.yml (`commencement`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from retrieval.references import act_mentions

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "data" / "curated" / "retrieval_policy.yml"
ACT_WORD_FIELD = "{act_word}"


@dataclass(frozen=True)
class CommencementRule:
    act_word: re.Pattern
    phrase: re.Pattern      # (a): a commencement phrase anywhere
    subject: re.Pattern     # (b): an Act word, then a start verb within two words
    time_word: re.Pattern   # (b) also needs one of these
    exclude: re.Pattern     # a no-Act question with this word is about the Administration Act
    chunks: dict            # corpus Act id -> its commencement chunk id
    other_acts: tuple = ()  # (name, pattern) of the Acts outside the corpus (references.load_other_acts)

    def wording(self, question: str) -> bool:
        """True if the question asks when an Act starts, whatever the Act."""
        return bool(self.phrase.search(question)) or (
            bool(self.subject.search(question)) and bool(self.time_word.search(question)))

    def target(self, question: str) -> str | None:
        """The corpus Act whose commencement the question asks about ("nta2025"), else None."""
        if not self.wording(question):
            return None
        acts = {act for _, _, act in act_mentions(question, self.other_acts)}
        if "nta2025" in acts:
            return "nta2025"
        if not acts and not self.exclude.search(question) and self.act_word.search(question):
            return "nta2025"
        return None

    def chunk_for(self, act_id: str | None) -> str | None:
        return self.chunks.get(act_id) if act_id else None


def build_rule(section: dict, other_acts=()) -> CommencementRule:
    """The rule from the policy's `commencement` section."""
    act_word = section["act_word"]
    phrases = "|".join(p.replace(ACT_WORD_FIELD, act_word) for p in section["phrases"])
    verbs = "|".join(section["start_verbs"])
    subject = act_word + r"(?:\s+2025)?\s+(?:\w+\s+){0,2}?(?:first\s+)?(?:" + verbs + r")\b"
    time_word = r"\b(?:" + "|".join(re.escape(w) for w in section["time_words"]) + r")\b"
    return CommencementRule(
        act_word=re.compile(act_word, re.IGNORECASE),
        phrase=re.compile(phrases, re.IGNORECASE),
        subject=re.compile(subject, re.IGNORECASE),
        time_word=re.compile(time_word, re.IGNORECASE),
        exclude=re.compile(r"\b" + re.escape(section["exclude_word"]) + r"\b", re.IGNORECASE),
        chunks=dict(section["chunks"]),
        other_acts=tuple(other_acts))


def load_commencement(other_acts=(), path: Path = POLICY) -> CommencementRule:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return build_rule(data["commencement"], other_acts)
