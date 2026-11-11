"""Grounded generation with guardrails (Phase 5, D-22 to D-29; Phase 6, D-30 to D-40).

    from generation.generate import generate
    answer = generate("Does a small company have to pay the development levy?")
    print(answer.rendered)

Flow (guardrails design, section 2): pre-checks on the question (invalid question, section and
Schedule references, other-Act and scope notes, another country's tax law, D-110); the note tagger (D-77; three calls and a 2-of-3 vote, D-88), started with the
pre-check, run alongside retrieval and generation on every status and joined before rendering, so the notes
shown are the union of the pattern notes and the tagger's; retrieve the top 5 chunks plus one keyword slot,
then up to two attachments (the commencement chunk and one definition), with config_v3.json (D-54,
D-98 to D-100); the relevance floor; show the chunks to the model as passages P1 to P8 and
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
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.load import (BUILD_MANIFEST, CHUNKS, REGISTER, read_chunks, read_register, schedule_no,  # noqa: E402
                        sha256_file)
from generation.audit_log import LOG_DIR, SCHEMA, SCHEMA_TOOLS, write_record  # noqa: E402
from generation.disclosure import Disclosure, Policy, disclose, load_policy  # noqa: E402
from generation.guardrails import (LOW_RELEVANCE_TEXT, POLICY as GUARDRAILS_POLICY,  # noqa: E402
                                   GuardrailsPolicy, add_printed_subsections, load_guardrails_policy, pre_check,
                                   relevance_floor)
from generation.llm import (EFFORT, MAX_TOKENS, MODEL, VERIFIER_EFFORT, VERIFIER_MAX_TOKENS,  # noqa: E402
                            VERIFIER_MODEL, AnthropicLLMClient, GenerationError, LLMClient, LLMResponse, cost_usd,
                            request_params)
from generation.prompt import (LABELS, OUTPUT_SCHEMA, OUTPUT_SCHEMA_TOOLS, PROMPT_VERSION, SCHEMA_VERSION,  # noqa: E402
                               SYSTEM_PROMPT, SYSTEM_PROMPT_TOOLS, TOOL_DEFS, TOOL_DEFS_VERSION, TOOL_LABELS,
                               TOOL_PASSAGE_LABELS, TOOLS_PROMPT_VERSION, TOOLS_SCHEMA_VERSION, build_user_message,
                               passage_block)
from generation.note_tagger import (OFF as TAGGER_OFF, TAGGER_EFFORT, TAGGER_MAX_TOKENS, TAGGER_MODEL,  # noqa: E402
                                    TAGGER_THRESHOLD, TAGGER_VOTES, TaggerResult, merge_notes, tag_question)
from generation.verify import (NO_PASSAGES_GAP, VerificationResult, extract_tokens, source_tokens,  # noqa: E402
                               verify_answer)
from retrieval.retrieve import Retriever  # noqa: E402
from tools.lookup import LIMIT_TEXT as LOOKUP_LIMIT_TEXT, lookup_section  # noqa: E402
from tools.taxcalc import (NOT_ASSESSMENT_LINE, ROUNDED_LINE, SCENARIOS, Computation, RateTable, Rule,  # noqa: E402
                           calculate, load_rates, naira, relied_item, structural_problem)

ATTACHMENT_REASONS = ("commencement lookup", "definition attachment")  # retrieval reasons of P7, P8 (D-100)
RETRIEVAL_CONFIG = ROOT / "eval" / "retrieval" / "config_v3.json"  # D-100 (config_v2.json plus the attachments, D-54); config_final.json stays the G1 record (D-21)
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
LABEL_IN_TEXT_TOOLS = re.compile(r"\[?\bP(?:1[0-4]|[1-9])\b\]?")  # P1 to P14 (tools on)
REGISTER_FIELDS = ("register_id", "act_id", "cited_unit", "comparison_text", "type", "cited_reading",
                   "comparison_reading", "notes")

# Tools (Phase 7, D-117 to D-123; tools design, sections 2, 5 and 6) ------------------------------
TOOL_FAILED_TEXT = "The tool failed; no result."
CALC_LIMIT_TEXT = "The calculation limit for this question has been reached."
CALCULATION_HEADING = "Calculation (worked out by code from the figures in the question)"
CIT_AND_LEVY = ("company_income_tax", "development_levy")
# I4: shown notes that withhold results (K5), applied when the notes are joined, before rendering.
SCOPE_VETOES = {"scope:personal_income_tax": CIT_AND_LEVY, "scope:petroleum_taxes": CIT_AND_LEVY,
                "scope:economic_development_incentives": CIT_AND_LEVY, "scope:pre_2026_periods": SCENARIOS}
VETO_TOPICS = {"scope:personal_income_tax": "personal income tax", "scope:petroleum_taxes": "petroleum taxes",
               "scope:economic_development_incentives": "economic development incentives",
               "scope:pre_2026_periods": "tax periods before 2026"}
RATE_NOT_CONFIRMED_LINE = ("1 calculation was not shown because the rate it uses was not confirmed in the cited "
                           "provisions.",
                           "{n} calculations were not shown because the rates they use were not confirmed in the "
                           "cited provisions.")
VETO_LINE = ("1 calculation was not shown because the note on {topic} applies to this question.",
             "{n} calculations were not shown because the note on {topic} applies to this question.")


@dataclass
class ToolsConfig:
    """The Phase 7 tools (D-117 to D-123). Off by default: the request is then the frozen gen-prompt/8
    and gen-schema/1 request, byte for byte, and the record is `gen-log/2`."""
    enabled: bool = False
    max_model_calls: int = 3        # calls 1 and 2 under tool_choice auto, the last under none
    max_lookup_calls: int = 2
    max_lookup_chunks: int = 3      # new chunks from all lookups of one question
    max_calc_calls: int = 2
    max_calc_sources: int = 3       # new chunks attached as calculation sources
    gap_figure_filter: bool = True
    rates: RateTable | None = None  # None: the pinned data/curated/calc_rates.yml
    config_file: str | None = None  # the frozen Phase 7 configuration, for the log
    config_sha256: str | None = None

    def as_log(self) -> dict:
        return {"enabled": self.enabled, "max_model_calls": self.max_model_calls,
                "max_lookup_calls": self.max_lookup_calls, "max_lookup_chunks": self.max_lookup_chunks,
                "max_calc_calls": self.max_calc_calls, "max_calc_sources": self.max_calc_sources,
                "gap_figure_filter": self.gap_figure_filter}


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
    tagger_votes: int = TAGGER_VOTES          # D-88: parallel tagger calls per question
    tagger_threshold: int = TAGGER_THRESHOLD  # D-88: successful calls that must give a label
    tools: ToolsConfig = field(default_factory=ToolsConfig)  # Phase 7; off by default


@dataclass
class Passage:
    label: str
    chunk_id: str
    citation: str
    location: str
    rank: int
    score: float
    disputed: bool
    reasons: list[str] = field(default_factory=list)  # why retrieval returned it (attachments: D-98, D-99)

    @property
    def attachment(self) -> str | None:
        """The attachment reason ("commencement lookup: ...", "definition attachment: ..."), else None."""
        return next((r for r in self.reasons if r.startswith(ATTACHMENT_REASONS)), None)


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
    calculations: list[str] = field(default_factory=list)  # tools on: computed ids the answer lists (K3)


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
    computations: list[dict] = field(default_factory=list)  # tools on: every computation record, with K1 to K6
    tools: dict | None = None                               # tools on: the gen-log/3 tools block

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

    def subsections(self, chunk_ids: list[str]) -> dict[str, list[str]]:
        """Each chunk's subsection metadata ("(1)", ...); empty for a chunk that holds a whole
        section (the lookup tool's subsection filter, D-118)."""
        rows = self.conn.execute("SELECT chunk_id, subsections FROM corpus_chunks WHERE chunk_id = ANY(%s)",
                                 (list(chunk_ids),)).fetchall()
        return {chunk_id: list(subs) for chunk_id, subs in rows}

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
        self._subsections: dict[str, list[str]] = {}
        self._sections: dict[str, dict[str, set]] = {}
        for chunk in read_chunks(chunks_path):
            self._subsections[chunk["chunk_id"]] = list(chunk["subsections"])
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

    def subsections(self, chunk_ids: list[str]) -> dict[str, list[str]]:
        return {chunk_id: list(self._subsections[chunk_id]) for chunk_id in chunk_ids if chunk_id in self._subsections}

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


def _schema_problem(data, tools: bool = False) -> str | None:
    """Why `data` does not match OUTPUT_SCHEMA (OUTPUT_SCHEMA_TOOLS with `tools`), or None. Checked
    even though the API enforces the schema, because a fake client can return anything."""
    keys = {"status", "claims", "gaps"} | ({"calculations"} if tools else set())
    if not isinstance(data, dict) or set(data) != keys:
        return "top-level keys"
    if tools and (not isinstance(data["calculations"], list)
                  or not all(isinstance(c, str) for c in data["calculations"])):
        return "calculations"
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


def parse_output(response: LLMResponse, label_map: dict[str, str], *, tools: bool = False,
                 computed_ids: tuple[str, ...] = ()) -> ParseResult:
    """Checks of design section 4.3. `label_map` maps the labels shown to their chunk ids. With
    `tools` (gen-schema/2), the answer also lists calculation ids; an id that is not a computed
    calculation (`computed_ids`) is dropped and flagged `calculation_id_rejected`, and nothing is
    shown for it."""
    if response.stop_reason != "end_turn":
        return ParseResult("error", error=f"stop_reason {response.stop_reason}")
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return ParseResult("error", error="output is not valid JSON")
    problem = _schema_problem(data, tools)
    if problem:
        return ParseResult("error", error=f"output does not match the schema ({problem})")
    result = _parse_claims(data, label_map, LABEL_IN_TEXT_TOOLS if tools else LABEL_IN_TEXT)
    if tools:
        for cid in dict.fromkeys(data["calculations"]):
            if cid in computed_ids:
                result.calculations.append(cid)
            else:
                result.flags.append({"claim": None, "flag": "calculation_id_rejected", "id": cid})
    return result


def _parse_claims(data: dict, label_map: dict[str, str], label_in_text: re.Pattern) -> ParseResult:
    claims, rejected, flags = [], [], []
    for i, item in enumerate(data["claims"]):
        text = item["text"].strip()
        accepted = []
        for label in item["passages"]:
            if label not in label_map:
                rejected.append({"claim": i, "label": label, "reason": "not among the passages shown"})
            elif label not in accepted:
                accepted.append(label)
        if label_in_text.search(text):
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
           removal_lines: list[str] | None = None, calculation_lines: list[str] | None = None) -> str:
    """Markdown answer. Answered and declined: notes, then claims with citations (and the
    removed-claims lines), gaps, sources, shown disclosures, disclaimer. Refused and error: the
    refusal or error text, notes, question-triggered disclosures, disclaimer (D-44: notes are shown
    whatever the status). With tools on, an answered answer's calculation blocks and withheld lines
    (`calculation_lines`, rendered by code from the computation records) come after the claims and
    before the gaps (tools design, section 5.3); without them the text is as before."""
    parts: list[str] = []
    note_block = ("Notes\n" + "\n".join(f"- {n.text}" for n in notes)) if notes else None
    if status in ("answered", "not_covered") and note_block:
        parts.append(note_block)
    if status == "answered":
        for claim in claims:
            parts.append(f"{claim.text} [{'; '.join(chunks[c]['citation'] for c in claim.chunk_ids)}]")
        parts.extend(removal_lines or [])
        parts.extend(calculation_lines or [])
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


# Tools: the bounded loop (D-117), lookups (D-118), calculations (D-119 to D-122) -------------------


class ToolSession:
    """One question's tool state: the passages tools add (P9 to P14), the calls and their limits,
    and the computation records. Every effect of a tool call is decided here, in code."""

    def __init__(self, question: str, cfg: ToolsConfig, retriever, store, policy: GuardrailsPolicy, corpus: dict,
                 query_vector, chunks: dict, passages: list[Passage], label_map: dict[str, str], rates: RateTable):
        self.question, self.cfg, self.retriever, self.store, self.policy = question, cfg, retriever, store, policy
        self.corpus, self.query_vector, self.rates = corpus, query_vector, rates
        self.chunks, self.passages, self.label_map = chunks, passages, label_map
        self.lookup_calls = self.calc_calls = self.lookup_chunks = self.calc_sources = 0
        self.computations: list[Computation] = []
        self.tool_uses: list[dict] = []
        self.passages_added: list[dict] = []
        self.calls: list[dict] = []

    def shown(self) -> dict[str, str]:
        return {chunk_id: label for label, chunk_id in self.label_map.items()}

    def free_labels(self) -> list[str]:
        return [label for label in TOOL_PASSAGE_LABELS if label not in self.label_map]

    def add_passage(self, label: str, chunk_id: str, reason: str) -> None:
        chunk = self.store.chunks([chunk_id])[chunk_id]
        self.chunks[chunk_id] = chunk
        self.label_map[label] = chunk_id
        self.passages.append(Passage(label, chunk_id, chunk["citation"], chunk["context_header"],
                                     len(self.passages) + 1, None, bool(chunk["register_ids"]), [reason]))
        self.passages_added.append({"label": label, "chunk_id": chunk_id, "reason": reason})

    def run(self, block: dict, turn: int) -> dict:
        """Run one tool_use block; returns its tool_result block. A tool that raises gives an
        `is_error` result and its computation is recorded as `error`, never shown."""
        name, inputs = block.get("name"), block.get("input")
        entry = {"id": block.get("id"), "turn": turn, "name": name, "input": inputs, "is_error": False,
                 "result": None, "limit_hit": None, "calculation_id": None, "lookup": None, "exception": None}
        try:
            if name == "lookup_section":
                is_error, content = self._lookup(inputs, entry)
            elif name == "calculate_liability":
                is_error, content = self._calculate(inputs, entry)
            else:
                is_error, content = True, f"Unknown tool {name!r}; the tools are lookup_section and calculate_liability."
        except Exception as exc:  # noqa: BLE001 - fail closed: the answer goes on without this tool's result
            is_error, content = True, TOOL_FAILED_TEXT
            entry["exception"] = f"{type(exc).__name__}: {exc}"
            if entry["calculation_id"] and not any(c.id == entry["calculation_id"] for c in self.computations):
                self.computations.append(Computation(entry["calculation_id"], (inputs or {}).get("scenario", ""),
                                                     status="error", rates_sha256=self.rates.sha256))
        entry["is_error"], entry["result"] = is_error, content
        self.tool_uses.append(entry)
        return {"type": "tool_result", "tool_use_id": block.get("id"), "content": content, "is_error": is_error}

    def _lookup(self, inputs, entry: dict) -> tuple[bool, str]:
        if self.lookup_calls >= self.cfg.max_lookup_calls:
            entry["limit_hit"] = "max_lookup_calls"
            return True, LOOKUP_LIMIT_TEXT
        self.lookup_calls += 1
        result = lookup_section(inputs, retriever=self.retriever, store=self.store, policy=self.policy,
                                corpus=self.corpus, query_vector=self.query_vector, shown=self.shown(),
                                free_labels=self.free_labels(),
                                chunk_budget=self.cfg.max_lookup_chunks - self.lookup_chunks)
        entry["lookup"] = result.as_log()
        if result.added:
            act = self.policy.acts[inputs["act"]]["name"]
            reason = f"lookup_section: Section {inputs['section_number'].strip()}, {act}"
            for label, chunk_id in result.added:
                self.add_passage(label, chunk_id, reason)
            self.lookup_chunks += len(result.added)
        return result.is_error, result.content

    def _calculate(self, inputs, entry: dict) -> tuple[bool, str]:
        if self.calc_calls >= self.cfg.max_calc_calls:
            entry["limit_hit"] = "max_calc_calls"
            return True, CALC_LIMIT_TEXT
        self.calc_calls += 1
        cid = entry["calculation_id"] = f"C{self.calc_calls}"
        problem = structural_problem(inputs)
        if problem:
            return True, problem
        comp = calculate(cid, inputs["scenario"], inputs, self.question, self.rates)
        self.computations.append(comp)
        if not comp.computed:
            return False, json.dumps({"status": "declined", "id": cid, "scenario": comp.scenario,
                                      "reason": comp.declined_reason}, ensure_ascii=False)
        new = []
        for chunk_id in comp.sources:
            free = self.free_labels()
            if chunk_id in self.shown() or self.calc_sources >= self.cfg.max_calc_sources or not free:
                continue
            self.add_passage(free[0], chunk_id, f"calculation source: {cid} {comp.scenario}")
            self.calc_sources += 1
            new.append(free[0])
        shown = self.shown()
        result = {"status": "computed", "id": cid, "scenario": comp.scenario,
                  "result": naira(Decimal(comp.result)) if comp.result is not None else "not charged",
                  "relies_on": [{("rule" if isinstance(item, Rule) else "constant"): key, "states": item.states,
                                 "passage": shown.get(item.chunk_id)}
                                for key in comp.relies_on for item in [relied_item(key, self.rates)]],
                  "assumptions": comp.assumptions, "not_computed": comp.not_computed, "new_passages": new}
        blocks = [passage_block(label, self.chunks[self.label_map[label]]) for label in new]
        return False, "\n".join([json.dumps(result, ensure_ascii=False)] + blocks)


def tools_request(user: str, messages: list[dict], config: GenerationConfig, final: bool) -> dict:
    """gen-prompt/9 and gen-schema/2 with both tools; tool_choice `auto`, or `none` on the last call."""
    params = request_params(SYSTEM_PROMPT_TOOLS, user, OUTPUT_SCHEMA_TOOLS, model=config.model, effort=config.effort,
                            max_tokens=config.max_tokens)
    params["messages"] = [dict(m) for m in messages]
    params["tools"] = TOOL_DEFS
    params["tool_choice"] = {"type": "none" if final else "auto"}
    return params


def _call_log(turn: int, params: dict, response: LLMResponse | None, model: str, error: str | None = None) -> dict:
    cost = cost_usd(response.usage, model) if response else 0.0
    return {"turn": turn,
            "request": {"prompt_version": TOOLS_PROMPT_VERSION, "schema_version": TOOLS_SCHEMA_VERSION,
                        "tool_defs_version": TOOL_DEFS_VERSION, **params},
            "response": None if response is None else {
                "request_id": response.request_id, "model": response.model, "stop_reason": response.stop_reason,
                "stop_details": response.stop_details, "content": response.content},
            "usage": None if response is None else response.usage, "cost_usd": round(cost, 6),
            "latency_ms": 0 if response is None else response.latency_ms, "error": error}


def run_tool_loop(session: ToolSession, user: str, llm: LLMClient, config: GenerationConfig):
    """The bounded loop (design section 2.2): calls run while `stop_reason` is `tool_use`, at most
    `max_model_calls`; the last call is sent with tool_choice `none`, so it must answer. Each
    assistant turn is appended unchanged (thinking blocks included); all tool results of a turn go
    back in one user message, in call order. Returns (final request, final response, parsed); an API
    error or a final call that is not `end_turn` gives status `error`."""
    messages: list[dict] = [{"role": "user", "content": user}]
    params = response = None
    for turn in range(1, config.tools.max_model_calls + 1):
        params = tools_request(user, messages, config, final=turn == config.tools.max_model_calls)
        try:
            response = llm.create(params)
        except GenerationError as exc:
            session.calls.append(_call_log(turn, params, None, config.model, str(exc)))
            return params, None, ParseResult("error", error=str(exc))
        session.calls.append(_call_log(turn, params, response, config.model))
        if response.stop_reason != "tool_use" or turn == config.tools.max_model_calls:
            break
        uses = [block for block in response.content if block.get("type") == "tool_use"]
        if not uses:
            return params, response, ParseResult("error", error="stop_reason tool_use with no tool_use block")
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": [session.run(block, turn) for block in uses]})
    computed = tuple(c.id for c in session.computations if c.computed)
    return params, response, parse_output(response, session.label_map, tools=True, computed_ids=computed)


def _summed_usage(calls: list[dict]) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: sum((c["usage"] or {}).get(k, 0) for c in calls) for k in keys}


def computed_checks(session: ToolSession, status: str, listed: list[str], verification: VerificationResult | None,
                    note_refs: set[str]) -> tuple[list[dict], list[Computation]]:
    """Checks K1 to K6 (design section 5.2) on every computation: (records with checks, the
    computations to show in listing order). Code only, no model call."""
    kept = verification.kept if verification is not None and status == "answered" else []
    records, shown = [], {}
    for comp in session.computations:
        veto = [ref for ref, scenarios in SCOPE_VETOES.items() if ref in note_refs and comp.scenario in scenarios]
        k4 = {}
        if comp.computed:
            for key in comp.relies_on:
                c = relied_item(key, session.rates)
                if isinstance(c, Rule):  # D-129: a text rule needs the citation only, no token
                    k4[key] = [k.no for k in kept if c.chunk_id in k.kept_chunk_ids]
                else:
                    k4[key] = [k.no for k in kept
                               if c.chunk_id in k.kept_chunk_ids and c.token in extract_tokens(k.text)]
        checks = {"K1": comp.computed, "K2": status == "answered", "K3": comp.id in listed, "K4": k4,
                  "K5": not veto, "K6": comp.computed}
        k4_ok = comp.computed and bool(k4) and all(k4.values())
        withheld = None
        if not checks["K1"]:
            withheld = comp.status
        elif not checks["K2"]:
            withheld = f"status {status}"
        elif not checks["K3"]:
            withheld = "not_listed"
        elif veto:
            withheld = f"veto {veto[0]}"
        elif not k4_ok:
            withheld = "rate_not_confirmed"
        record = comp.as_log() | {"listed": checks["K3"], "checks": checks, "shown": withheld is None,
                                  "withheld_reason": withheld}
        records.append(record)
        if withheld is None:
            shown[comp.id] = comp
    return records, [shown[cid] for cid in listed if cid in shown]


def calculation_lines(records: list[dict], shown: list[Computation]) -> list[str]:
    """The rendered calculation blocks (code-written from the records, never the model's text), then
    one line per withholding reason for listed calculations held back by K4 or K5."""
    parts = []
    for comp in shown:
        lines = [CALCULATION_HEADING, f"- What: {comp.what}", f"- From the question: {'; '.join(comp.from_question)}"]
        if comp.assumptions:
            lines.append(f"- Assumed: {'; '.join(comp.assumptions)}")
        lines += [f"- Working: {w}" for w in comp.working]
        lines.append(f"- Result: {comp.result_text}")
        lines += [f"- Not computed: {n}." for n in comp.not_computed]
        if comp.result_kind == "amount":
            lines.append(f"- {ROUNDED_LINE}")
        lines.append(f"- {NOT_ASSESSMENT_LINE}")
        parts.append("\n".join(lines))
    withheld = [r for r in records if r["listed"] and r["checks"]["K1"] and r["checks"]["K2"] and not r["shown"]]
    rate = sum(r["withheld_reason"] == "rate_not_confirmed" for r in withheld)
    if rate:
        parts.append(RATE_NOT_CONFIRMED_LINE[0] if rate == 1 else RATE_NOT_CONFIRMED_LINE[1].format(n=rate))
    vetoes: dict[str, int] = {}
    for r in withheld:
        if r["withheld_reason"].startswith("veto "):
            ref = r["withheld_reason"][len("veto "):]
            vetoes[ref] = vetoes.get(ref, 0) + 1
    for ref, n in vetoes.items():
        line = VETO_LINE[0] if n == 1 else VETO_LINE[1]
        parts.append(line.format(n=n, topic=VETO_TOPICS[ref]))
    return parts


def gap_figures_allowed(question: str, chunks: dict[str, dict]) -> set[str]:
    """Figures a gap may hold with tools on: those of the question and of every shown passage."""
    allowed = extract_tokens(question, source=True)
    for chunk in chunks.values():
        allowed |= source_tokens(chunk)
    return allowed


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
    if reason in ("invalid_question", "no_such_section", "section_outside_v1", "foreign_law"):  # foreign_law: D-110
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
                           max_tokens=config.tagger_max_tokens, votes=config.tagger_votes,
                           threshold=config.tagger_threshold) if pool is not None else None)
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
    tools_on = config.tools.enabled
    corpus = store.sections()
    check = pre_check(question, guardrails_policy, corpus)
    retrieved, floor = [], None
    if check.refusal is None:
        if tools_on and query_vector is None:  # embedded once, kept for lookups (design section 6.1)
            query_vector = retriever.client.embed_query(question)
        retrieved = retriever.retrieve(question, k, query_vector=query_vector)[:len(LABELS)]
        floor = relevance_floor(retrieved, guardrails_policy.tau)
    chunks = store.chunks([item.chunk_id for item in retrieved])
    passages = [Passage(label, item.chunk_id, chunks[item.chunk_id]["citation"],
                        chunks[item.chunk_id]["context_header"], item.rank, item.score,
                        bool(chunks[item.chunk_id]["register_ids"]), list(item.reasons))
                for label, item in zip(LABELS, retrieved)]
    label_map = {p.label: p.chunk_id for p in passages}

    request = response = session = None
    refusal = None  # {status, reason, stage}
    if check.refusal is not None:
        refusal = {"status": "refused", "reason": check.refusal, "stage": "pre_check"}
        parsed = ParseResult("refused")
    elif floor is not None and floor.refuse:
        refusal = {"status": "refused", "reason": "low_relevance", "stage": "relevance_floor"}
        parsed = ParseResult("refused")
    elif not passages:
        parsed = ParseResult("not_covered", gaps=[NO_PASSAGES_GAP])
    elif tools_on:
        user = build_user_message([chunks[p.chunk_id] for p in passages], question)
        rates = config.tools.rates or load_rates()
        session = ToolSession(question, config.tools, retriever, store, guardrails_policy, corpus, query_vector,
                              chunks, passages, label_map, rates)
        request, response, parsed = run_tool_loop(session, user, llm, config)
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
                                     other_acts=guardrails_policy.other_acts,
                                     gap_figure_allowed=(gap_figures_allowed(question, chunks)
                                                         if tools_on and config.tools.gap_figure_filter else None))
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
    computations, calc_lines, tools_log = [], None, None
    if session is not None:  # the scope-note veto and K1 to K6, after the notes are joined (design 6.1)
        computations, shown_calcs = computed_checks(session, status, parsed.calculations, verification,
                                                    {n.ref for n in notes})
        calc_lines = calculation_lines(computations, shown_calcs) if status == "answered" else None
    cited = cited_in_order(claims)  # empty unless answered, so other statuses get question notes only
    rows = store.register_rows(cited, sorted(policy.anchor_terms))
    disclosures = disclose(cited, rows, question, policy, {c: chunks[c]["citation"] for c in cited},
                           retrieved=[item.chunk_id for item in retrieved], dq12_tagged=tagged.dq12)
    rendered = render(status, claims, gaps, cited, chunks, disclosures, notes=notes,
                      refusal_text=_refusal_text(refusal["reason"], check) if refusal else None,
                      removal_lines=verification.removal_lines if verification else None,
                      calculation_lines=calc_lines)
    if session is not None:
        usage = _summed_usage(session.calls)
        cost = sum(c["cost_usd"] for c in session.calls)
        tools_log = {"config": {"file": config.tools.config_file, "sha256": config.tools.config_sha256},
                     "settings": config.tools.as_log(), "calls": session.calls, "tool_uses": session.tool_uses,
                     "passages_added": session.passages_added, "computations": computations,
                     "cost_usd": round(cost, 6), "latency_ms": sum(c["latency_ms"] for c in session.calls)}
    else:
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
            "request": None if request is None else (
                {"prompt_version": TOOLS_PROMPT_VERSION, "schema_version": TOOLS_SCHEMA_VERSION,
                 "tool_defs_version": TOOL_DEFS_VERSION, **request} if tools_on
                else {"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION, **request}),
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
        if tools_on:  # gen-log/3: gen-log/2 plus the tools block (tools on only; tools off stays gen-log/2)
            record["schema"] = SCHEMA_TOOLS
            record["parsed"]["calculations"] = parsed.calculations
            record["tools"] = tools_log
        write_record(record, timestamp, log_dir)

    return Answer(question=question, status=status, claims=claims, gaps=gaps,
                  passages=passages, cited_chunk_ids=cited, rejected_citations=parsed.rejected,
                  disclosures=disclosures, rendered=rendered, model=config.model, usage=usage,
                  cost_usd=cost, record_id=record_id, error=error,
                  refusal_reason=refusal["reason"] if refusal else None, flags=flags, notes=notes,
                  verification=verification, verification_cost_usd=verification.cost_usd if verification else 0.0,
                  tagger=tagged, pattern_notes=check.notes, computations=computations, tools=tools_log)
