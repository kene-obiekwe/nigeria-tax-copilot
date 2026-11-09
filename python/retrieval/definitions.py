"""Definition index and definition attachment (D-98; G2 fix design 3, section 2.2).

    from retrieval.definitions import DefinitionIndex
    index = DefinitionIndex.from_db(conn)
    index.terms_used("nta2025:s56")   # {("nta2025:s202", "small company"), ...}

Nothing in vector retrieval connects a defined term a passage uses to its definition (ADV3-044:
the small company definition ranked 6th and lost the sixth passage to the keyword slot). This
module reads the definition chunks of the corpus and finds the terms a passage uses.

1. **Index.** Each definition chunk (an id with `:def:`) gives its term: the quoted words before
   "means", "includes", "has", "refers", "is" or "shall", or the chunk id's slug where the text has
   no such quote. Chunks of one definition section with the same term are one definition (two
   definitions span several chunks). A term matches whole words, ignoring case unless it is printed
   with a capital ("Service", "Minister"), and its last word may take a plural ending (-s, -es,
   -ies).
2. **Scope**, from the statute's own words (data/curated/retrieval_policy.yml,
   `definition_scopes`): a Nigeria Tax Act section 202 term applies to Nigeria Tax Act chunks, an
   Administration Act section 147 term to Administration Act chunks, a section 189 term to Chapter 6
   and Part IV of Chapter 8, a First Schedule Part I term to that Part only.
3. **Use.** Where matches in a passage overlap, the longest wins, so "a small company" uses "small
   company", not "company". A definition chunk does not use its own term.

The choice of the one definition to attach is `Retriever.definition_attachment`
(retrieval.retrieve).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "data" / "curated" / "retrieval_policy.yml"
DEF_MARK = ":def:"
# The quoted term before its defining verb: "small company" means ..., ''Service'' has ...
_TERM = re.compile(r"[\"“]([^\"“”']{2,90}?)(?:''|[\"”'])\s+(?:means|includes|has|refers?|is|shall)")
# Policy keys -> corpus_chunks columns
SCOPE_COLUMNS = {"act": "act_id", "chapter": "chapter_no", "part": "part_no", "schedule": "schedule_no",
                 "schedule_part": "schedule_part"}
CHUNK_COLUMNS = ("chunk_id",) + tuple(SCOPE_COLUMNS.values()) + ("text",)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def term_of(chunk_id: str, text: str) -> str:
    """The term a definition chunk defines (rule 1)."""
    m = _TERM.search(_collapse(text))
    if m:
        return m.group(1).strip()
    slug = re.sub(r"\(.*$", "", chunk_id.split(DEF_MARK, 1)[1])
    return slug.replace("-", " ")


def term_variants(term: str) -> list[str]:
    """The term with its last word as printed and with a plural ending (-s, -es, -ies)."""
    words = term.split()
    last = words[-1]
    forms = {last, last + "s", last + "es"}
    if last.endswith("y"):
        forms.add(last[:-1] + "ies")
    return [" ".join(words[:-1] + [form]) for form in sorted(forms)]


def term_pattern(term: str) -> re.Pattern:
    """Whole-word match of the term or a plural form; case kept when the term starts with a capital."""
    alternatives = sorted({re.escape(v).replace(r"\ ", r"\s+") for v in term_variants(term)}, key=len, reverse=True)
    flags = 0 if term[:1].isupper() else re.IGNORECASE
    return re.compile(r"(?<![\w-])(?:" + "|".join(alternatives) + r")(?![\w-])", flags)


@dataclass(frozen=True)
class Scope:
    definitions: str               # chunk-id prefix of the definition chunks
    applies_to: tuple[dict, ...]   # alternatives; each a {column: value} that must all hold

    def covers(self, chunk: dict) -> bool:
        return any(all(chunk.get(SCOPE_COLUMNS[key]) == value for key, value in alt.items())
                   for alt in self.applies_to)


@dataclass(frozen=True)
class Definition:
    key: tuple[str, str]   # (definition section, term in lower case), e.g. ("nta2025:s202", "small company")
    term: str
    chunks: tuple[str, ...]
    pattern: re.Pattern
    scope: Scope


def load_scopes(path: Path = POLICY) -> tuple[Scope, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for item in data["definition_scopes"]:
        unknown = {k for alt in item["applies_to"] for k in alt} - set(SCOPE_COLUMNS)
        if unknown:
            raise ValueError(f"unknown scope keys {sorted(unknown)}")
        out.append(Scope(item["definitions"], tuple(dict(alt) for alt in item["applies_to"])))
    return tuple(out)


class DefinitionIndex:
    """The corpus's definitions and the terms each chunk uses. `chunks` are corpus_chunks rows as
    dicts with CHUNK_COLUMNS."""

    def __init__(self, chunks: list[dict], scopes: tuple[Scope, ...]):
        self.chunks = {c["chunk_id"]: c for c in chunks}
        self._text = {cid: _collapse(c["text"]) for cid, c in self.chunks.items()}
        found: dict[tuple[str, str], dict] = {}
        for cid in sorted(self.chunks):
            if DEF_MARK not in cid:
                continue
            scope = next((s for s in scopes if cid.startswith(s.definitions)), None)
            if scope is None:
                raise ValueError(f"definition chunk {cid} has no scope in the retrieval policy")
            term = term_of(cid, self.chunks[cid]["text"])
            key = (cid.split(DEF_MARK, 1)[0], term.lower())
            found.setdefault(key, {"term": term, "chunks": [], "scope": scope})["chunks"].append(cid)
        self.definitions: dict[tuple[str, str], Definition] = {
            key: Definition(key, d["term"], tuple(d["chunks"]), term_pattern(d["term"]), d["scope"])
            for key, d in found.items()}
        self._used: dict[str, list[tuple[str, str]]] = {}

    @classmethod
    def from_db(cls, conn, scopes: tuple[Scope, ...] | None = None) -> "DefinitionIndex":
        rows = conn.execute(f"SELECT {', '.join(CHUNK_COLUMNS)} FROM corpus_chunks ORDER BY chunk_id").fetchall()
        return cls([dict(zip(CHUNK_COLUMNS, row)) for row in rows], scopes if scopes is not None else load_scopes())

    def terms_used(self, chunk_id: str) -> list[tuple[str, str]]:
        """Definition keys whose term the chunk uses, in order of first use; where matches overlap
        the longest wins. A definition chunk does not use its own term."""
        if chunk_id not in self._used:
            chunk, text = self.chunks.get(chunk_id), self._text.get(chunk_id, "")
            hits = []
            if chunk is not None:
                for key, d in self.definitions.items():
                    if chunk_id in d.chunks or not d.scope.covers(chunk):
                        continue
                    hits += [(m.start(), m.end(), key) for m in d.pattern.finditer(text)]
            kept = [h for h in hits if not any(o[0] <= h[0] and h[1] <= o[1] and o[1] - o[0] > h[1] - h[0]
                                               for o in hits)]
            ordered: list[tuple[str, str]] = []
            for _, _, key in sorted(kept, key=lambda h: (h[0], h[1], h[2])):
                if key not in ordered:
                    ordered.append(key)
            self._used[chunk_id] = ordered
        return self._used[chunk_id]
