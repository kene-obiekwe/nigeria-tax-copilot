"""Retrieval layer (Phase 4b): ranked chunks for a question, each with its score and the reasons
it was returned.

    from retrieval.retrieve import retrieve
    retrieve("What is the VAT rate?", k=5)

Components, in the order they shape the ranking:

1. **Exact section lookup.** A question that names a section ("section 148 of the Nigeria Tax
   Act", "s. 66 of the NTAA") gets that section's chunks first, ordered by cosine distance to the
   question. Each reference takes the Act named after it (before the next reference), else the
   Act named before it. A reference with no Act named anywhere is ambiguous: the section is looked
   up in both Acts and the chunks of both are ordered together by cosine distance, so the Act whose
   text is closer to the question leads and the other still appears. A section not in the v1
   corpus is ignored. Section numbers are the gazette numbers (D-09).
2. **Vector search.** Exact cosine distance over one model's vectors (no ANN index, D-19).
3. **Keyword search (optional).** PostgreSQL full text (`to_tsvector('english', ...)` over the
   context header and text) supplies the stemmed lexemes; they are scored with BM25 (k1 1.2,
   b 0.75, the textbook defaults, not tuned), because `ts_rank` has no inverse document
   frequency and lets words such as "tax" dominate. The question's lexemes are OR-ed.
4. **Fusion.** With keyword search on, the vector and keyword candidate lists are merged by
   reciprocal rank fusion: score = sum of 1 / (rrf_k + rank), rrf_k 60 (the published default).
   Ties go to the better vector rank, then the chunk id.
5. **Metadata filter (optional).** `filters` limits every component to matching chunks. Keys:
   `act` ("nta2025" or "ntaa2025"), `chapter` and `part` (normalised numbers), `unit_type` (one
   or a list), and `tax_area` ("vat", "income" or "administration", see TAX_AREAS). Filters are
   never inferred from the question.

Without lookup or keyword search the ranking is the Phase 3 vector search exactly.

**Revision for generation (D-54, `config_v2.json`).** Three options, all off in `config_final.json`
(D-21, gate G1), which therefore ranks exactly as before:

- `other_act_refs: "skip"`: references are read by the shared parser (`retrieval.references`), and a
  section reference attributed to an Act outside the corpus ("section 23 of CITA") is not looked up.
- `act_lookup: {"max_chunks": N}`: when the question names an Act outside the corpus (the names in
  data/curated/guardrails_policy.yml, `other_acts`), the corpus chunks whose text names that Act come
  next after the section lookup, nearest first, at most N. An Act-name lookup result that is also a
  vector result keeps its vector rank as a reason, so the relevance floor still reads the top vector
  score; it is not exempt from the floor the way a section lookup is.
- `keyword_slots: N`: after the first `k` results, the best N BM25 chunks not already among them
  are appended (rank k+1, ...). They never displace or reorder the first `k`, and the relevance
  floor never reads them.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.clients import DEFAULT_MODEL, EmbeddingClient, get_client  # noqa: E402
from embed.load import vector_literal  # noqa: E402
from retrieval.references import (CORPUS_ACT_D21, LIST_NUMBER, SECTION_REF, act_of, load_other_acts,  # noqa: E402
                                  named_other_acts, parse_references, parse_section_refs_d21)

ACT_NAMES = {"nta2025": "Nigeria Tax Act 2025", "ntaa2025": "Nigeria Tax Administration Act 2025"}
DEFAULT_CANDIDATES = 50
DEFAULT_RRF_K = 60
BM25_K1, BM25_B = 1.2, 0.75

# Tax areas as unions of (act, chapter, part, schedule) conditions on corpus_chunks. NTA Chapter 9
# (general provisions, including the Section 202 definitions) applies to the whole Act, so it is
# in both NTA areas.
TAX_AREAS = {
    "vat": "act_id = 'nta2025' AND (chapter_no IN (6, 9) OR (chapter_no = 8 AND part_no = 4) "
           "OR schedule_no = 11)",
    "income": "act_id = 'nta2025' AND (chapter_no IN (1, 2, 9) OR (chapter_no = 8 AND part_no = 1) "
              "OR schedule_no IN (1, 3, 13))",
    "administration": "act_id = 'ntaa2025'",
}

# The reference patterns live in retrieval.references (one parser for retrieval and the guardrails,
# D-54). These names are kept for existing callers; _ACT_NAME is the D-21 corpus-Act pattern.
_SECTION_REF, _LIST_NUMBER, _ACT_NAME, _act_of = SECTION_REF, LIST_NUMBER, CORPUS_ACT_D21, act_of
OTHER_ACT_REFS = (None, "skip")


@dataclass(frozen=True)
class SectionRef:
    section: str
    act_id: str | None  # None when no Act is named: looked up in both


@dataclass
class Retrieved:
    chunk_id: str
    rank: int
    score: float  # cosine similarity for lookup and vector-only results, else the RRF score
    distance: float | None  # cosine distance to the question
    reasons: list[str] = field(default_factory=list)


def parse_section_refs(question: str, other_acts=None) -> list[SectionRef]:
    """Section references to look up, each with its corpus Act (None if unnamed).

    With `other_acts` None this is the D-21 reading. With an `other_acts` list (D-54) the shared
    parser is used, and a reference attributed to an Act outside the corpus is left out, as is a
    lettered number ("57A"), which neither Act has (D-80)."""
    if other_acts is None:
        refs = parse_section_refs_d21(question)
    else:
        refs = [r for r in parse_references(question, other_acts)
                if r.kind == "section" and not r.other_act and not r.suffix]
    out: list[SectionRef] = []
    for ref in refs:
        item = SectionRef(str(ref.number), ref.act)
        if item not in out:
            out.append(item)
    return out


def _collapse(text: str) -> str:
    return " ".join(text.split())


def filter_clause(filters: dict | None) -> tuple[str, list]:
    """SQL condition on corpus_chunks (alias-free) and its parameters for `filters`."""
    if not filters:
        return "TRUE", []
    known = {"act", "chapter", "part", "unit_type", "tax_area"}
    unknown = set(filters) - known
    if unknown:
        raise ValueError(f"unknown filter keys {sorted(unknown)}; known: {sorted(known)}")
    parts, params = [], []
    if "act" in filters:
        if filters["act"] not in ACT_NAMES:
            raise ValueError(f"unknown act {filters['act']!r}")
        parts.append("act_id = %s")
        params.append(filters["act"])
    for key, column in (("chapter", "chapter_no"), ("part", "part_no")):
        if key in filters:
            parts.append(f"{column} = %s")
            params.append(int(filters[key]))
    if "unit_type" in filters:
        value = filters["unit_type"]
        parts.append("unit_type = ANY(%s)")
        params.append([value] if isinstance(value, str) else list(value))
    if "tax_area" in filters:
        if filters["tax_area"] not in TAX_AREAS:
            raise ValueError(f"unknown tax_area {filters['tax_area']!r}; known: {sorted(TAX_AREAS)}")
        parts.append(f"({TAX_AREAS[filters['tax_area']]})")
    return " AND ".join(parts), params


def rrf_fuse(rankings: list[list[str]], rrf_k: int = DEFAULT_RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion of ranked id lists. Ties: better rank in the first list, then id."""
    scores: Counter = Counter()
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (rrf_k + rank)
    first = {chunk_id: rank for rank, chunk_id in enumerate(rankings[0], start=1)} if rankings else {}
    return sorted(scores.items(), key=lambda item: (-item[1], first.get(item[0], math.inf), item[0]))


class KeywordIndex:
    """BM25 over PostgreSQL's English full-text lexemes of `context_header || ' ' || text`."""

    def __init__(self, conn):
        rows = conn.execute(
            "SELECT c.chunk_id, t.lexeme, coalesce(array_length(t.positions, 1), 1) "
            "FROM corpus_chunks c, unnest(to_tsvector('english', c.context_header || ' ' || c.text)) t "
            "ORDER BY c.chunk_id").fetchall()
        self.tf: dict[str, dict[str, int]] = {}
        for chunk_id, lexeme, count in rows:
            self.tf.setdefault(chunk_id, {})[lexeme] = count
        for (chunk_id,) in conn.execute("SELECT chunk_id FROM corpus_chunks").fetchall():
            self.tf.setdefault(chunk_id, {})
        self.length = {chunk_id: sum(terms.values()) for chunk_id, terms in self.tf.items()}
        self.avg_length = sum(self.length.values()) / max(len(self.length), 1)
        self.df = Counter(lexeme for terms in self.tf.values() for lexeme in terms)
        self.n = len(self.tf)
        self._conn = conn

    def query_lexemes(self, question: str) -> list[str]:
        rows = self._conn.execute("SELECT lexeme FROM unnest(to_tsvector('english', %s)) ORDER BY lexeme",
                                  (question,)).fetchall()
        return [lexeme for (lexeme,) in rows]

    def search(self, question: str, n: int, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        lexemes = self.query_lexemes(question)
        scores = []
        for chunk_id, terms in self.tf.items():
            if allowed is not None and chunk_id not in allowed:
                continue
            score = 0.0
            for lexeme in lexemes:
                tf = terms.get(lexeme)
                if not tf:
                    continue
                idf = math.log(1 + (self.n - self.df[lexeme] + 0.5) / (self.df[lexeme] + 0.5))
                norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * self.length[chunk_id] / self.avg_length)
                score += idf * tf * (BM25_K1 + 1) / norm
            if score > 0:
                scores.append((chunk_id, score))
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:n]


class Retriever:
    """One retrieval configuration over one connection. `config` keys: model, section_lookup,
    keyword, candidates, rrf_k, filters, other_act_refs, act_lookup, keyword_slots, passages (all
    optional; see the module docstring). `other_acts` overrides the policy's list (tests)."""

    def __init__(self, conn, config: dict | None = None, client: EmbeddingClient | None = None,
                 other_acts=None):
        config = dict(config or {})
        self.model = config.get("model", DEFAULT_MODEL)
        self.section_lookup = bool(config.get("section_lookup", True))
        self.keyword = bool(config.get("keyword", False))
        self.candidates = int(config.get("candidates", DEFAULT_CANDIDATES))
        self.rrf_k = int(config.get("rrf_k", DEFAULT_RRF_K))
        self.filters = config.get("filters")
        self.other_act_refs = config.get("other_act_refs")
        if self.other_act_refs not in OTHER_ACT_REFS:
            raise ValueError(f"other_act_refs must be one of {OTHER_ACT_REFS}, got {self.other_act_refs!r}")
        self.act_lookup = int((config.get("act_lookup") or {}).get("max_chunks", 0))
        self.keyword_slots = int(config.get("keyword_slots", 0))
        self.passages = config.get("passages")
        self.other_acts = None
        if self.other_act_refs or self.act_lookup:
            self.other_acts = tuple(other_acts) if other_acts is not None else load_other_acts()
        self._chunk_text: dict[str, str] | None = None
        self.conn = conn
        self._client = client
        self._keyword_index: KeywordIndex | None = None

    @property
    def client(self) -> EmbeddingClient:
        if self._client is None:
            self._client = get_client(self.model)
        return self._client

    def _allowed(self, filters: dict | None) -> list[str] | None:
        if not filters:
            return None
        clause, params = filter_clause(filters)
        return [r[0] for r in self.conn.execute(
            f"SELECT chunk_id FROM corpus_chunks WHERE {clause} ORDER BY chunk_id", params).fetchall()]

    def vector_search(self, query_vector: list[float], n: int, allowed: list[str] | None = None):
        rows = self.conn.execute(
            "SELECT chunk_id, embedding <=> %s::vector AS distance FROM chunk_embeddings "
            "WHERE model = %s AND (%s::text[] IS NULL OR chunk_id = ANY(%s)) "
            "ORDER BY distance, chunk_id LIMIT %s",
            (vector_literal(query_vector), self.model, allowed, allowed, n)).fetchall()
        return [(chunk_id, float(distance)) for chunk_id, distance in rows]

    def lookup(self, refs: list[SectionRef], query_vector: list[float], allowed: list[str] | None = None):
        """Chunks of the referenced sections, nearest first: [(chunk_id, distance, ref)]."""
        found = {}
        for ref in refs:
            rows = self.conn.execute(
                "SELECT c.chunk_id, c.act_id, e.embedding <=> %s::vector AS distance "
                "FROM corpus_chunks c JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = %s "
                "WHERE c.section = %s AND (%s::text IS NULL OR c.act_id = %s) "
                "AND (%s::text[] IS NULL OR c.chunk_id = ANY(%s))",
                (vector_literal(query_vector), self.model, ref.section, ref.act_id, ref.act_id,
                 allowed, allowed)).fetchall()
            for chunk_id, act_id, distance in rows:
                found.setdefault(chunk_id, (float(distance), ref, act_id))
        return sorted(((cid, d, ref, act) for cid, (d, ref, act) in found.items()),
                      key=lambda item: (item[1], item[0]))

    def keyword_index(self) -> KeywordIndex:
        if self._keyword_index is None:
            self._keyword_index = KeywordIndex(self.conn)
        return self._keyword_index

    def chunk_text(self) -> dict[str, str]:
        """Every chunk's text, whitespace collapsed (for the Act-name lookup)."""
        if self._chunk_text is None:
            rows = self.conn.execute("SELECT chunk_id, text FROM corpus_chunks ORDER BY chunk_id").fetchall()
            self._chunk_text = {chunk_id: _collapse(text) for chunk_id, text in rows}
        return self._chunk_text

    def act_name_lookup(self, question: str, query_vector: list[float], allowed: list[str] | None = None,
                        exclude: set[str] = frozenset()):
        """Corpus chunks whose text names an Act outside the corpus that the question names, nearest
        first, at most `act_lookup`: [(chunk_id, distance, act name)]."""
        names = named_other_acts(question, self.other_acts or ())
        if not names or not self.act_lookup:
            return []
        allowed_set = set(allowed) if allowed is not None else None
        naming: dict[str, str] = {}
        for name, pattern in names.items():
            for chunk_id, text in self.chunk_text().items():
                if chunk_id in exclude or (allowed_set is not None and chunk_id not in allowed_set):
                    continue
                if pattern.search(text):
                    naming.setdefault(chunk_id, name)
        if not naming:
            return []
        rows = self.conn.execute(
            "SELECT chunk_id, embedding <=> %s::vector FROM chunk_embeddings WHERE model = %s AND chunk_id = ANY(%s)",
            (vector_literal(query_vector), self.model, sorted(naming))).fetchall()
        ranked = sorted(((chunk_id, float(d)) for chunk_id, d in rows), key=lambda item: (item[1], item[0]))
        return [(chunk_id, d, naming[chunk_id]) for chunk_id, d in ranked[:self.act_lookup]]

    def retrieve(self, question: str, k: int = 5, filters: dict | None = None,
                 query_vector: list[float] | None = None) -> list[Retrieved]:
        filters = filters if filters is not None else self.filters
        if query_vector is None:
            query_vector = self.client.embed_query(question)
        allowed = self._allowed(filters)
        n = max(self.candidates, k)

        vector = self.vector_search(query_vector, n, allowed)
        distances = dict(vector)
        vector_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(vector, start=1)}

        results: list[Retrieved] = []
        seen: set[str] = set()
        if self.section_lookup:
            refs = parse_section_refs(question, self.other_acts if self.other_act_refs == "skip" else None)
            for chunk_id, distance, ref, act_id in self.lookup(refs, query_vector, allowed):
                how = ACT_NAMES[act_id] if ref.act_id else f"{ACT_NAMES[act_id]} (no Act named)"
                results.append(Retrieved(chunk_id, 0, 1 - distance, distance,
                                         [f"section lookup: Section {ref.section}, {how} "
                                          f"(cosine distance {distance:.4f})"]))
                seen.add(chunk_id)
        for chunk_id, distance, name in self.act_name_lookup(question, query_vector, allowed, seen):
            reasons = [f"act-name lookup: names the {name} (cosine distance {distance:.4f})"]
            if chunk_id in vector_rank:
                reasons.append(f"vector rank {vector_rank[chunk_id]} (cosine distance {distance:.4f})")
            results.append(Retrieved(chunk_id, 0, 1 - distance, distance, reasons))
            seen.add(chunk_id)

        if self.keyword:
            keyword = self.keyword_index().search(question, n, set(allowed) if allowed is not None else None)
            keyword_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(keyword, start=1)}
            keyword_score = dict(keyword)
            ranked = rrf_fuse([[c for c, _ in vector], [c for c, _ in keyword]], self.rrf_k)
        else:
            keyword_rank, keyword_score = {}, {}
            ranked = [(chunk_id, 1 - distance) for chunk_id, distance in vector]

        missing = [c for c, _ in ranked if c not in distances]
        if missing:  # keyword-only candidates: fetch their distance for the record
            distances.update(dict(self.conn.execute(
                "SELECT chunk_id, embedding <=> %s::vector FROM chunk_embeddings "
                "WHERE model = %s AND chunk_id = ANY(%s)",
                (vector_literal(query_vector), self.model, missing)).fetchall()))
        for chunk_id, score in ranked:
            if chunk_id in seen:
                continue
            reasons = []
            if chunk_id in vector_rank:
                reasons.append(f"vector rank {vector_rank[chunk_id]} (cosine distance {distances[chunk_id]:.4f})")
            if chunk_id in keyword_rank:
                reasons.append(f"keyword rank {keyword_rank[chunk_id]} (BM25 {keyword_score[chunk_id]:.3f})")
            results.append(Retrieved(chunk_id, 0, score, float(distances[chunk_id]), reasons))
            seen.add(chunk_id)

        results = results[:k]
        if self.keyword_slots:
            shown = {item.chunk_id for item in results}
            bm25 = self.keyword_index().search(question, n + k, set(allowed) if allowed is not None else None)
            slots = [(rank, chunk_id, s) for rank, (chunk_id, s) in enumerate(bm25, start=1)
                     if chunk_id not in shown][:self.keyword_slots]
            if slots:
                extra = dict(self.conn.execute(
                    "SELECT chunk_id, embedding <=> %s::vector FROM chunk_embeddings "
                    "WHERE model = %s AND chunk_id = ANY(%s)",
                    (vector_literal(query_vector), self.model, [chunk_id for _, chunk_id, _ in slots])).fetchall())
                for bm25_rank, chunk_id, s in slots:
                    distance = float(extra[chunk_id])
                    results.append(Retrieved(chunk_id, 0, 1 - distance, distance,
                                             [f"keyword slot: BM25 rank {bm25_rank} (BM25 {s:.3f}, "
                                              f"cosine distance {distance:.4f})"]))
        for rank, item in enumerate(results, start=1):
            item.rank = rank
        return results


def retrieve(question: str, k: int = 5, model: str = DEFAULT_MODEL, filters: dict | None = None,
             *, section_lookup: bool = True, keyword: bool = False, conn=None,
             client: EmbeddingClient | None = None) -> list[Retrieved]:
    """Top `k` chunks for `question`. Opens its own connection when `conn` is not given."""
    config = {"model": model, "section_lookup": section_lookup, "keyword": keyword}
    if conn is not None:
        return Retriever(conn, config, client).retrieve(question, k, filters)
    with connect() as own:
        return Retriever(own, config, client).retrieve(question, k, filters)
