"""Grounded generation with guardrails (Phase 5, D-22 to D-29; Phase 6, D-30 to D-40).

    from generation.generate import generate
    answer = generate("Does a small company have to pay the development levy?")
    print(answer.rendered)

Flow (guardrails design, section 2): pre-checks on the question (invalid question, section and
Schedule references, other-Act and scope notes); the note tagger (D-77), started with the pre-check,
run alongside retrieval and generation on every status and joined before rendering, so the notes
shown are the union of the pattern notes and the tagger's; retrieve the top 5 chunks plus one keyword slot
with config_v2.json (D-54); the relevance floor; show the chunks to the model as passages P1 to P6 and
get a JSON answer (claims citing passage labels, plus gaps); check the citations; verify every
claim and gap (generation.verify); work out the disclosures for the kept claims' supporting
citations (D-23 as amended by D-32); render the answer with its notes, sources, disclosures and
the disclaimer; and log the full call (gen-log/2, D-28).

The parser checks only that every citation is a passage that was shown, and drops claims left
with no citation. Whether a cited chunk supports its claim is checked by the verification pass.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.load import (BUILD_MANIFEST, CHUNKS, REGISTER, read_chunks, read_register, schedule_no,  # noqa: E402
                        sha256_file)
from generation.audit_log import LOG_DIR, SCHEMA, write_record  # noqa: E402
from generation.disclosure import Disclosure, Policy, disclose, load_policy  # noqa: E402
from generation.guardrails import (LOW_RELEVANCE_TEXT, POLICY as GUARDRAILS_POLICY,  # noqa: E402
                                   GuardrailsPolicy, add_printed_subsections, load_guardrails_policy, pre_check,
                                   relevance_floor)
from generation.llm import (EFFORT, MAX_TOKENS, MODEL, VERIFIER_EFFORT, VERIFIER_MAX_TOKENS,  # noqa: E402
                            VERIFIER_MODEL, AnthropicLLMClient, GenerationError, LLMClient, LLMResponse, cost_usd,
                            request_params)
from generation.prompt import (LABELS, OUTPUT_SCHEMA, PROMPT_VERSION, SCHEMA_VERSION, SYSTEM_PROMPT,  # noqa: E402
                               build_user_message)
from generation.note_tagger import (OFF as TAGGER_OFF, TAGGER_EFFORT, TAGGER_MAX_TOKENS, TAGGER_MODEL,  # noqa: E402
                                    TaggerResult, merge_notes, tag_question)
from generation.verify import NO_PASSAGES_GAP, VerificationResult, verify_answer  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402

RETRIEVAL_CONFIG = ROOT / "eval" / "retrieval" / "config_v2.json"  # D-54; config_final.json stays the G1 record (D-21)
DISCLAIMER = ("This is a research and reference tool. It is not tax advice and is not a substitute for a "
              "licensed tax professional. It must not be used for an actual filing or compliance decision. "
              "Answers come only from the Nigeria Tax Act 2025 and selected parts of the Nigeria Tax "
              "Administration Act 2025 (Official Gazette text, corpus snapshot 18 October 2026).")
DECLINE_TEXT = "The provisions retrieved from the corpus do not answer this question."
ERROR_TEXT = "The system could not produce a grounded answer to this question."
UNSUPPORTED_ANSWER_TEXT = ("The system could not check an answer to this question against the provisions it cited, "
                           "so no answer is shown.")
VERIFICATION_UNAVAILABLE_TEXT = "The answer could not be checked, so it is not shown. Please try again later."
LABEL_IN_TEXT = re.compile(r"\[?\bP[1-9]\b\]?")
REGISTER_FIELDS = ("register_id", "act_id", "cited_unit", "comparison_text", "type", "cited_reading",
                   "comparison_reading", "notes")


@dataclass
class GenerationConfig:
    model: str = MODEL
    effort: str = EFFORT
    max_tokens: int = MAX_TOKENS
    verify: bool = True
    verifier_model: str = VERIFIER_MODEL
    verifier_effort: str = VERIFIER_EFFORT
    verifier_max_tokens: int = VERIFIER_MAX_TOKENS
    note_tagger: bool = True             # D-77
    tagger_model: str = TAGGER_MODEL
    tagger_effort: str = TAGGER_EFFORT
    tagger_max_tokens: int = TAGGER_MAX_TOKENS


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
    status: str             # "answered" | "not_covered" | "error" | "refused" (guardrail stage)
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
    cost_usd: float                     # generation only
    record_id: str
    error: str | None
    refusal_reason: str | None = None
    flags: list[str] = field(default_factory=list)
    notes: list[Disclosure] = field(default_factory=list)
    verification: VerificationResult | None = None
    verification_cost_usd: float = 0.0
    tagger: TaggerResult = field(default_factory=lambda: TaggerResult("off"))  # D-77; `notes` is the union
    pattern_notes: list[Disclosure] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return self.cost_usd + self.verification_cost_usd + self.tagger.cost_usd


# Corpus stores -------------------------------------------------------------------------------


class DbCorpusStore:
    """Chunk and register rows from the loaded database (the runtime source)."""

    def __init__(self, conn):
        self.conn = conn

    def chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT chunk_id, act_id, citation, context_header, text, register_ids, page_labels, cross_refs "
            "FROM corpus_chunks WHERE chunk_id = ANY(%s)", (list(chunk_ids),)).fetchall()
        keys = ("chunk_id", "act_id", "citation", "context_header", "text", "register_ids", "page_labels",
                "cross_refs")  # cross_refs: the gap reference check (D-79)
        return {row[0]: dict(zip(keys, row)) for row in rows}

    def register_rows(self, chunk_ids: list[str], register_ids: list[str]) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT {', '.join(REGISTER_FIELDS)} FROM discrepancy_register "
            "WHERE cited_unit = ANY(%s) OR register_id = ANY(%s) ORDER BY register_id",
            (list(chunk_ids), list(register_ids))).fetchall()
        return [{k: (v if v is not None else "") for k, v in zip(REGISTER_FIELDS, row)} for row in rows]

    def sections(self) -> dict[str, dict[str, set]]:
        """Per Act, the section numbers and Schedule numbers in the corpus (guardrail pre-checks)."""
        out: dict[str, dict[str, set]] = {}
        for act_id, section, schedule in self.conn.execute(
                "SELECT DISTINCT act_id, section, schedule_no FROM corpus_chunks").fetchall():
            entry = out.setdefault(act_id, {"sections": set(), "schedules": set()})
            if section:
                entry["sections"].add(section)
            if schedule is not None:
                entry["schedules"].add(int(schedule))
        for chunk_id, text in self.conn.execute("SELECT chunk_id, text FROM corpus_chunks").fetchall():
            add_printed_subsections(out, chunk_id, text)  # D-81
        return out

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
        self._sections: dict[str, dict[str, set]] = {}
        for chunk in read_chunks(chunks_path):
            entry = self._sections.setdefault(chunk["act_id"], {"sections": set(), "schedules": set()})
            if chunk["section"]:
                entry["sections"].add(chunk["section"])
            if chunk["schedule"]:
                entry["schedules"].add(schedule_no(chunk["schedule"]["number"]))
            add_printed_subsections(self._sections, chunk["chunk_id"], chunk["text"])  # D-81
            self._chunks[chunk["chunk_id"]] = {
                "chunk_id": chunk["chunk_id"], "act_id": chunk["act_id"], "citation": chunk["citation"],
                "context_header": chunk["context_header"], "text": chunk["text"],
                "register_ids": list(chunk["discrepancy"]["register_ids"]), "page_labels": chunk["page_labels"],
                "cross_refs": list(chunk["cross_refs"])}
        self._register = [{k: row[k] for k in REGISTER_FIELDS} for row in read_register(register_path)]

    def chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        return {chunk_id: self._chunks[chunk_id] for chunk_id in chunk_ids if chunk_id in self._chunks}

    def register_rows(self, chunk_ids: list[str], register_ids: list[str]) -> list[dict]:
        wanted_chunks, wanted_ids = set(chunk_ids), set(register_ids)
        return [row for row in self._register
                if row["cited_unit"] in wanted_chunks or row["register_id"] in wanted_ids]

    def all_register_rows(self) -> list[dict]:
        return list(self._register)

    def sections(self) -> dict[str, dict[str, set]]:
        return {act: {k: ({s: set(labels) for s, labels in v.items()} if k == "subsections" else set(v))
                      for k, v in entry.items()} for act, entry in self._sections.items()}

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
           disclosures: list[Disclosure], *, notes: list[Disclosure] | None = None, refusal_text: str | None = None,
           removal_lines: list[str] | None = None) -> str:
    """Markdown answer. Answered and declined: notes, then claims with citations (and the
    removed-claims lines), gaps, sources, shown disclosures, disclaimer. Refused and error: the
    refusal or error text, notes, question-triggered disclosures, disclaimer (D-44: notes are shown
    whatever the status)."""
    parts: list[str] = []
    note_block = ("Notes\n" + "\n".join(f"- {n.text}" for n in notes)) if notes else None
    if status in ("answered", "not_covered") and note_block:
        parts.append(note_block)
    if status == "answered":
        for claim in claims:
            parts.append(f"{claim.text} [{'; '.join(chunks[c]['citation'] for c in claim.chunk_ids)}]")
        parts.extend(removal_lines or [])
    elif status == "not_covered":
        parts.append(DECLINE_TEXT)
    else:
        parts.append(refusal_text or (UNSUPPORTED_ANSWER_TEXT if status == "refused" else ERROR_TEXT))
        if note_block:
            parts.append(note_block)
    if status in ("answered", "not_covered") and gaps:
        parts.append("Not covered by the retrieved provisions:\n" + "\n".join(f"- {gap}" for gap in gaps))
    if status == "answered":
        parts.append("Sources\n" + "\n".join(f"- {chunks[c]['citation']}{_pages(chunks[c]['page_labels'])}"
                                              for c in cited))
    shown = [d for d in disclosures if d.kind != "hidden"]
    if shown:
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
             query_vector: list[float] | None = None, policy: Policy | None = None,
             verifier: LLMClient | None = None, guardrails_policy: GuardrailsPolicy | None = None,
             tagger: LLMClient | None = None) -> Answer:
    """Answer `question` from the top `k` retrieved chunks (design section 2.1). Opens its own
    database connection when `retriever` or `store` is not given. `verifier` is the verification
    client (Haiku 5.5 by default; unused when `config.verify` is off). `tagger` is the note tagger
    client (D-77): with `config.note_tagger` on, Haiku 5.5 by default when the generator client is
    also the default; a caller that passes its own generator client (tests, runners) passes its own
    tagger client, or the tagger is off for that call and logged as `off`. `log_dir=None` turns
    logging off (unit tests only)."""
    config = config or GenerationConfig()
    policy = policy or load_policy()
    guardrails_policy = guardrails_policy or load_guardrails_policy()
    if not config.note_tagger:
        tagger = None
    elif tagger is None and llm is None:
        tagger = AnthropicLLMClient(model=config.tagger_model)
    llm = llm or AnthropicLLMClient(model=config.model)
    if config.verify:
        verifier = verifier or AnthropicLLMClient(model=config.verifier_model)

    conn = None
    if retriever is None or store is None:
        conn = connect()
    try:
        retriever = retriever or Retriever(conn, retrieval_config())
        store = store or DbCorpusStore(conn)
        return _generate(question, k, retriever, llm, verifier, store, config, log_dir, query_vector, policy,
                         guardrails_policy, tagger)
    finally:
        if conn is not None:
            conn.close()


def _refusal_text(reason: str, check) -> str:
    if reason in ("invalid_question", "no_such_section", "section_outside_v1"):
        return check.refusal_text
    return {"low_relevance": LOW_RELEVANCE_TEXT, "unsupported_answer": UNSUPPORTED_ANSWER_TEXT,
            "verification_unavailable": VERIFICATION_UNAVAILABLE_TEXT}[reason]


def _generate(question, k, retriever, llm, verifier, store, config, log_dir, query_vector, policy,
              guardrails_policy, tagger=None) -> Answer:
    """Starts the note tagger (when on) with the pre-check, then answers; the tagger runs alongside
    retrieval and generation and is joined before rendering (D-77)."""
    timestamp = datetime.now(timezone.utc)
    record_id = str(uuid.uuid4())
    pool = ThreadPoolExecutor(max_workers=1) if tagger is not None else None
    tagging = (pool.submit(tag_question, question, tagger, effort=config.tagger_effort,
                           max_tokens=config.tagger_max_tokens) if pool is not None else None)
    try:
        return _answer(question, k, retriever, llm, verifier, store, config, log_dir, query_vector, policy,
                       guardrails_policy, timestamp, record_id, tagging)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)


def _join_tagger(tagging) -> TaggerResult:
    """The tagger's result; an unexpected exception in the call counts as a tagger failure."""
    if tagging is None:
        return TAGGER_OFF
    try:
        return tagging.result()
    except Exception as exc:  # noqa: BLE001 - tag_question catches model errors; this is a safety net
        return TaggerResult("failed", reason=f"tagger raised {type(exc).__name__}: {exc}")


def _answer(question, k, retriever, llm, verifier, store, config, log_dir, query_vector, policy,
            guardrails_policy, timestamp, record_id, tagging) -> Answer:
    check = pre_check(question, guardrails_policy, store.sections())
    retrieved, floor = [], None
    if check.refusal is None:
        retrieved = retriever.retrieve(question, k, query_vector=query_vector)[:len(LABELS)]
        floor = relevance_floor(retrieved, guardrails_policy.tau)
    chunks = store.chunks([item.chunk_id for item in retrieved])
    passages = [Passage(label, item.chunk_id, chunks[item.chunk_id]["citation"],
                        chunks[item.chunk_id]["context_header"], item.rank, item.score,
                        bool(chunks[item.chunk_id]["register_ids"]))
                for label, item in zip(LABELS, retrieved)]
    label_map = {p.label: p.chunk_id for p in passages}

    request = response = None
    refusal = None  # {status, reason, stage}
    if check.refusal is not None:
        refusal = {"status": "refused", "reason": check.refusal, "stage": "pre_check"}
        parsed = ParseResult("refused")
    elif floor is not None and floor.refuse:
        refusal = {"status": "refused", "reason": "low_relevance", "stage": "relevance_floor"}
        parsed = ParseResult("refused")
    elif not passages:
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

    status, claims, gaps, error = parsed.status, parsed.claims, parsed.gaps, parsed.error
    verification: VerificationResult | None = None
    flags = list(check.flags)
    if config.verify and status in ("answered", "not_covered") and (
            claims or any(gap != NO_PASSAGES_GAP for gap in gaps)):
        verification = verify_answer(question, status, claims, gaps, chunks,
                                     {chunk_id: label for label, chunk_id in label_map.items()},
                                     verifier, guardrails_policy.disputed_gap_patterns,
                                     effort=config.verifier_effort, max_tokens=config.verifier_max_tokens,
                                     other_acts=guardrails_policy.other_acts)
        status, gaps = verification.status, verification.gaps
        claims = ([Claim(c.text, c.labels, c.kept_chunk_ids) for c in verification.kept]
                  if status == "answered" else [])
        flags += verification.flags
        if verification.refusal_reason:
            refusal = {"status": status, "reason": verification.refusal_reason, "stage": "verification"}
            error = verification.refusal_reason if status == "error" else None

    tagged = _join_tagger(tagging)  # D-77: joined before rendering, whatever the status
    notes = merge_notes(check.notes, tagged, guardrails_policy)
    if tagged.status == "failed":
        flags.append("note_tagger_failed")
    cited = cited_in_order(claims)  # empty unless answered, so other statuses get question notes only
    rows = store.register_rows(cited, sorted(policy.anchor_terms))
    disclosures = disclose(cited, rows, question, policy, {c: chunks[c]["citation"] for c in cited},
                           retrieved=[item.chunk_id for item in retrieved], dq12_tagged=tagged.dq12)
    rendered = render(status, claims, gaps, cited, chunks, disclosures, notes=notes,
                      refusal_text=_refusal_text(refusal["reason"], check) if refusal else None,
                      removal_lines=verification.removal_lines if verification else None)
    usage = response.usage if response else {"input_tokens": 0, "output_tokens": 0,
                                             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    cost = cost_usd(usage, config.model) if response else 0.0

    if log_dir is not None:
        record = {
            "schema": SCHEMA, "record_id": record_id, "timestamp": timestamp.isoformat(), "question": question,
            "corpus": store.corpus_info(),
            "guardrails": {"policy_file": GUARDRAILS_POLICY.relative_to(ROOT).as_posix(),
                           "policy_sha256": guardrails_policy.sha256, "pre_check": check.as_log(),
                           "floor": None if floor is None else floor.as_log(),
                           "floor_calibrated": guardrails_policy.tau is not None, "verify": config.verify,
                           # D-77: the pattern notes and the note tagger; `notes` below is their union
                           "pattern_notes": [n.ref for n in check.notes],
                           "note_tagger": tagged.as_log() | {
                               "model": None if tagging is None else config.tagger_model,
                               "effort": None if tagging is None else config.tagger_effort}},
            "retrieval": None if check.refusal else {
                "config_file": RETRIEVAL_CONFIG.relative_to(ROOT).as_posix(),
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
            "verification": None if verification is None else verification.as_log(),
            "refusal": refusal,
            "disclosures": [asdict(d) for d in disclosures],
            "notes": [asdict(n) for n in notes],
            "rendered": rendered,
            "refusal_flag": status in ("not_covered", "refused"),
            "error": error,
        }
        write_record(record, timestamp, log_dir)

    return Answer(question=question, status=status, claims=claims, gaps=gaps,
                  passages=passages, cited_chunk_ids=cited, rejected_citations=parsed.rejected,
                  disclosures=disclosures, rendered=rendered, model=config.model, usage=usage,
                  cost_usd=cost, record_id=record_id, error=error,
                  refusal_reason=refusal["reason"] if refusal else None, flags=flags, notes=notes,
                  verification=verification, verification_cost_usd=verification.cost_usd if verification else 0.0,
                  tagger=tagged, pattern_notes=check.notes)
