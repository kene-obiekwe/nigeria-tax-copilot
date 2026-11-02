"""Grounded generation (Phase 5; generation design, approved as D-22 to D-29).

    from generation.generate import generate
    answer = generate("Does a small company have to pay the development levy?")
    print(answer.rendered)

Flow: retrieve the top 5 chunks with the frozen configuration (D-21), show them to the model as
passages P1 to P5, get a JSON answer (claims citing passage labels, plus gaps), check the
citations, work out the disclosures for the cited chunks (D-23), render the answer with its
sources, disclosures and the disclaimer, and log the full call (D-28).

The parser checks only that every citation is a passage that was shown, and drops claims left
with no citation. Whether a cited chunk supports its claim is checked by hand in the Phase 5 exit
check and by the verification pass in Phase 6.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.load import BUILD_MANIFEST, CHUNKS, REGISTER, read_chunks, read_register, sha256_file  # noqa: E402
from generation.audit_log import LOG_DIR, SCHEMA, write_record  # noqa: E402
from generation.disclosure import Disclosure, Policy, disclose, load_policy  # noqa: E402
from generation.llm import (EFFORT, MAX_TOKENS, MODEL, AnthropicLLMClient, GenerationError,  # noqa: E402
                            LLMClient, LLMResponse, cost_usd, request_params)
from generation.prompt import (LABELS, OUTPUT_SCHEMA, PROMPT_VERSION, SCHEMA_VERSION, SYSTEM_PROMPT,  # noqa: E402
                               build_user_message)
from retrieval.retrieve import Retriever  # noqa: E402

RETRIEVAL_CONFIG = ROOT / "eval" / "retrieval" / "config_final.json"
DISCLAIMER = ("This is a research and reference tool. It is not tax advice and is not a substitute for a "
              "licensed tax professional. It must not be used for an actual filing or compliance decision. "
              "Answers come only from the Nigeria Tax Act 2025 and selected parts of the Nigeria Tax "
              "Administration Act 2025 (Official Gazette text, corpus snapshot 18 October 2026).")
DECLINE_TEXT = "The provisions retrieved from the corpus do not answer this question."
ERROR_TEXT = "The system could not produce a grounded answer to this question."
NO_PASSAGES_GAP = "No provisions were retrieved for this question."
LABEL_IN_TEXT = re.compile(r"\[?\bP[1-9]\b\]?")
REGISTER_FIELDS = ("register_id", "act_id", "cited_unit", "comparison_text", "type", "cited_reading",
                   "comparison_reading", "notes")


@dataclass
class GenerationConfig:
    model: str = MODEL
    effort: str = EFFORT
    max_tokens: int = MAX_TOKENS


@dataclass
class Passage:
    label: str
    chunk_id: str
    citation: str
    location: str
    rank: int
    score: float
    disputed: bool


@dataclass
class Claim:
    text: str
    labels: list[str]       # as returned by the model
    chunk_ids: list[str]    # accepted citations, as chunk ids


@dataclass
class ParseResult:
    status: str             # "answered" | "not_covered" | "error"
    claims: list[Claim] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class Answer:
    question: str
    status: str
    claims: list[Claim]
    gaps: list[str]
    passages: list[Passage]
    cited_chunk_ids: list[str]
    rejected_citations: list[dict]
    disclosures: list[Disclosure]
    rendered: str
    model: str
    usage: dict
    cost_usd: float
    record_id: str
    error: str | None


# Corpus stores -------------------------------------------------------------------------------


class DbCorpusStore:
    """Chunk and register rows from the loaded database (the runtime source)."""

    def __init__(self, conn):
        self.conn = conn

    def chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT chunk_id, act_id, citation, context_header, text, register_ids, page_labels "
            "FROM corpus_chunks WHERE chunk_id = ANY(%s)", (list(chunk_ids),)).fetchall()
        keys = ("chunk_id", "act_id", "citation", "context_header", "text", "register_ids", "page_labels")
        return {row[0]: dict(zip(keys, row)) for row in rows}

    def register_rows(self, chunk_ids: list[str], register_ids: list[str]) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT {', '.join(REGISTER_FIELDS)} FROM discrepancy_register "
            "WHERE cited_unit = ANY(%s) OR register_id = ANY(%s) ORDER BY register_id",
            (list(chunk_ids), list(register_ids))).fetchall()
        return [{k: (v if v is not None else "") for k, v in zip(REGISTER_FIELDS, row)} for row in rows]

    def corpus_info(self) -> dict:
        row = self.conn.execute(
            "SELECT load_id, corpus_snapshot, chunks_sha256, register_sha256, build_manifest_sha256 "
            "FROM corpus_loads ORDER BY load_id DESC LIMIT 1").fetchone()
        if row is None:
            return {"source": "database", "load_id": None}
        return {"source": "database", "load_id": row[0], "corpus_snapshot": row[1].isoformat(),
                "chunks_sha256": row[2], "register_sha256": row[3], "build_manifest_sha256": row[4]}


class FileCorpusStore:
    """The same lookups from the committed chunk and register files (tests, offline use)."""

    def __init__(self, chunks_path: Path = CHUNKS, register_path: Path = REGISTER):
        self.chunks_path, self.register_path = chunks_path, register_path
        self._chunks = {}
        for chunk in read_chunks(chunks_path):
            self._chunks[chunk["chunk_id"]] = {
                "chunk_id": chunk["chunk_id"], "act_id": chunk["act_id"], "citation": chunk["citation"],
                "context_header": chunk["context_header"], "text": chunk["text"],
                "register_ids": list(chunk["discrepancy"]["register_ids"]), "page_labels": chunk["page_labels"]}
        self._register = [{k: row[k] for k in REGISTER_FIELDS} for row in read_register(register_path)]

    def chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        return {chunk_id: self._chunks[chunk_id] for chunk_id in chunk_ids if chunk_id in self._chunks}

    def register_rows(self, chunk_ids: list[str], register_ids: list[str]) -> list[dict]:
        wanted_chunks, wanted_ids = set(chunk_ids), set(register_ids)
        return [row for row in self._register
                if row["cited_unit"] in wanted_chunks or row["register_id"] in wanted_ids]

    def all_register_rows(self) -> list[dict]:
        return list(self._register)

    def corpus_info(self) -> dict:
        snapshot = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8"))["corpus_snapshot"]
        return {"source": "files", "corpus_snapshot": snapshot, "chunks_sha256": sha256_file(self.chunks_path),
                "register_sha256": sha256_file(self.register_path),
                "build_manifest_sha256": sha256_file(BUILD_MANIFEST)}


# Parsing ---------------------------------------------------------------------------------------


def _schema_problem(data) -> str | None:
    """Why `data` does not match OUTPUT_SCHEMA, or None. Checked even though the API enforces
    the schema, because a fake client can return anything."""
    if not isinstance(data, dict) or set(data) != {"status", "claims", "gaps"}:
        return "top-level keys"
    if data["status"] not in ("answered", "not_covered"):
        return "status"
    if not isinstance(data["claims"], list) or not isinstance(data["gaps"], list):
        return "claims or gaps not a list"
    for claim in data["claims"]:
        if not isinstance(claim, dict) or set(claim) != {"text", "passages"}:
            return "claim keys"
        if not isinstance(claim["text"], str) or not isinstance(claim["passages"], list):
            return "claim types"
        if not all(isinstance(label, str) for label in claim["passages"]):
            return "claim passages"
    if not all(isinstance(gap, str) for gap in data["gaps"]):
        return "gap types"
    return None


def parse_output(response: LLMResponse, label_map: dict[str, str]) -> ParseResult:
    """Checks of design section 4.3. `label_map` maps the labels shown to their chunk ids."""
    if response.stop_reason != "end_turn":
        return ParseResult("error", error=f"stop_reason {response.stop_reason}")
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return ParseResult("error", error="output is not valid JSON")
    problem = _schema_problem(data)
    if problem:
        return ParseResult("error", error=f"output does not match the schema ({problem})")

    claims, rejected, flags = [], [], []
    for i, item in enumerate(data["claims"]):
        text = item["text"].strip()
        accepted = []
        for label in item["passages"]:
            if label not in label_map:
                rejected.append({"claim": i, "label": label, "reason": "not among the passages shown"})
            elif label not in accepted:
                accepted.append(label)
        if LABEL_IN_TEXT.search(text):
            flags.append({"claim": i, "flag": "label in claim text"})
        if not text:
            rejected.append({"claim": i, "label": None, "reason": "empty claim text; claim dropped"})
            continue
        if not accepted:
            rejected.append({"claim": i, "label": None, "reason": "no citation left; claim dropped"})
            continue
        claims.append(Claim(text, list(item["passages"]), [label_map[label] for label in accepted]))
    gaps = [gap.strip() for gap in data["gaps"] if gap.strip()]

    if data["status"] == "not_covered":
        if data["claims"]:
            flags.append({"claim": None, "flag": "claims returned with not_covered; dropped"})
        return ParseResult("not_covered", [], gaps, rejected, flags)
    if not data["claims"]:
        flags.append({"claim": None, "flag": "answered with no claims; treated as not_covered"})
        return ParseResult("not_covered", [], gaps, rejected, flags)
    if not claims:
        return ParseResult("error", [], gaps, rejected, flags, error="no claim kept a citation")
    return ParseResult("answered", claims, gaps, rejected, flags)


def cited_in_order(claims: list[Claim]) -> list[str]:
    out: list[str] = []
    for claim in claims:
        for chunk_id in claim.chunk_ids:
            if chunk_id not in out:
                out.append(chunk_id)
    return out


# Rendering -------------------------------------------------------------------------------------


def _pages(labels: list[str]) -> str:
    if not labels:
        return ""
    return f" (gazette page{'s' if len(labels) > 1 else ''} {', '.join(labels)})"


def render(status: str, claims: list[Claim], gaps: list[str], cited: list[str], chunks: dict[str, dict],
           disclosures: list[Disclosure]) -> str:
    """Markdown answer: claims with citations, gaps, sources, shown disclosures, disclaimer."""
    parts: list[str] = []
    if status == "answered":
        for claim in claims:
            parts.append(f"{claim.text} [{'; '.join(chunks[c]['citation'] for c in claim.chunk_ids)}]")
    elif status == "not_covered":
        parts.append(DECLINE_TEXT)
    else:
        parts.append(ERROR_TEXT)
    if status != "error" and gaps:
        parts.append("Not covered by the retrieved provisions:\n" + "\n".join(f"- {gap}" for gap in gaps))
    if status == "answered":
        parts.append("Sources\n" + "\n".join(f"- {chunks[c]['citation']}{_pages(chunks[c]['page_labels'])}"
                                              for c in cited))
    shown = [d for d in disclosures if d.kind != "hidden"]
    if status != "error" and shown:
        lines = []
        for d in shown:
            refs = ", ".join([d.ref] + d.merged)
            lead = "" if d.applies_to == "question" else f"{d.applies_to}: "
            lines.append(f"- {lead}{d.text} ({refs})")
        parts.append("Differences between official texts\n" + "\n".join(lines))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


# Generation ------------------------------------------------------------------------------------


def retrieval_config() -> dict:
    return json.loads(RETRIEVAL_CONFIG.read_text(encoding="utf-8"))


def generate(question: str, *, k: int = 5, retriever=None, llm: LLMClient | None = None, store=None,
             config: GenerationConfig | None = None, log_dir: Path | None = LOG_DIR,
             query_vector: list[float] | None = None, policy: Policy | None = None) -> Answer:
    """Answer `question` from the top `k` retrieved chunks (design section 2.1). Opens its own
    database connection when `retriever` or `store` is not given. `log_dir=None` turns logging
    off (unit tests only)."""
    config = config or GenerationConfig()
    policy = policy or load_policy()
    llm = llm or AnthropicLLMClient(model=config.model)
    conn = None
    if retriever is None or store is None:
        conn = connect()
    try:
        retriever = retriever or Retriever(conn, retrieval_config())
        store = store or DbCorpusStore(conn)
        return _generate(question, k, retriever, llm, store, config, log_dir, query_vector, policy)
    finally:
        if conn is not None:
            conn.close()


def _generate(question, k, retriever, llm, store, config, log_dir, query_vector, policy) -> Answer:
    timestamp = datetime.now(timezone.utc)
    record_id = str(uuid.uuid4())
    retrieved = retriever.retrieve(question, k, query_vector=query_vector)[:len(LABELS)]
    chunks = store.chunks([item.chunk_id for item in retrieved])
    passages = [Passage(label, item.chunk_id, chunks[item.chunk_id]["citation"],
                        chunks[item.chunk_id]["context_header"], item.rank, item.score,
                        bool(chunks[item.chunk_id]["register_ids"]))
                for label, item in zip(LABELS, retrieved)]
    label_map = {p.label: p.chunk_id for p in passages}

    request = response = None
    if not passages:
        parsed = ParseResult("not_covered", gaps=[NO_PASSAGES_GAP])
    else:
        user = build_user_message([chunks[p.chunk_id] for p in passages], question)
        request = request_params(SYSTEM_PROMPT, user, OUTPUT_SCHEMA, model=config.model, effort=config.effort,
                                 max_tokens=config.max_tokens)
        try:
            response = llm.create(request)
            parsed = parse_output(response, label_map)
        except GenerationError as exc:
            parsed = ParseResult("error", error=str(exc))

    cited = cited_in_order(parsed.claims)
    if parsed.status == "error":
        disclosures: list[Disclosure] = []
    else:
        rows = store.register_rows(cited, sorted(policy.anchor_terms))
        disclosures = disclose(cited, rows, question, policy, {c: chunks[c]["citation"] for c in cited})
    rendered = render(parsed.status, parsed.claims, parsed.gaps, cited, chunks, disclosures)
    usage = response.usage if response else {"input_tokens": 0, "output_tokens": 0,
                                             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    cost = cost_usd(usage, config.model) if response else 0.0

    if log_dir is not None:
        record = {
            "schema": SCHEMA, "record_id": record_id, "timestamp": timestamp.isoformat(), "question": question,
            "corpus": store.corpus_info(),
            "retrieval": {"config_file": RETRIEVAL_CONFIG.relative_to(ROOT).as_posix(),
                          "config_sha256": sha256_file(RETRIEVAL_CONFIG), "k": k,
                          "chunks": [{"rank": item.rank, "label": label, "chunk_id": item.chunk_id,
                                      "score": item.score, "distance": item.distance, "reasons": item.reasons}
                                     for label, item in zip(LABELS, retrieved)]},
            "request": None if request is None else {"prompt_version": PROMPT_VERSION,
                                                     "schema_version": SCHEMA_VERSION, **request},
            "response": None if response is None else {
                "request_id": response.request_id, "model": response.model, "stop_reason": response.stop_reason,
                "stop_details": response.stop_details, "content": response.content, "usage": response.usage,
                "latency_ms": response.latency_ms, "cost_usd": round(cost, 6)},
            "parsed": {"status": parsed.status, "claims": [asdict(c) for c in parsed.claims], "gaps": parsed.gaps,
                       "rejected_citations": parsed.rejected, "flags": parsed.flags},
            "disclosures": [asdict(d) for d in disclosures],
            "rendered": rendered,
            "refusal_flag": parsed.status == "not_covered",
            "verification": None,
            "error": parsed.error,
        }
        write_record(record, timestamp, log_dir)

    return Answer(question=question, status=parsed.status, claims=parsed.claims, gaps=parsed.gaps,
                  passages=passages, cited_chunk_ids=cited, rejected_citations=parsed.rejected,
                  disclosures=disclosures, rendered=rendered, model=config.model, usage=usage,
                  cost_usd=cost, record_id=record_id, error=parsed.error)
