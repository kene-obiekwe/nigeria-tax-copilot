"""The projection of one `gen-log/3` record to audit rows (interface design, Sections 4.1 and 4.4;
D-150, D-152).

    rows = project(record, answer, extras)     # a pure function: no I/O, no clock
    store.close_query(query_id, rows.payload())

The record that `generate()` wrote is the single source; the `Answer` adds only what the record does
not hold (the final status, the flags, each passage's citation, header and disputed flag, the cost
figures and the usage), as Section 4.4 lists. Before returning, the projection checks that record and
answer agree (same record id, question, passages and rendered text, and the shown gaps equal the
answer's gaps), and raises ProjectionError if not: the app then withholds the answer rather than log
something it did not show.

Not projected (D-152): request bodies (system prompts, passage blocks, tool definitions), response
content other than the final JSON text block, and the verifier, gap and tagger request and response
bodies. Locally they stay in the JSONL record named by `gen_log_file`.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from generation.audit_log import SCHEMA_TOOLS  # noqa: E402
from generation.generate import ATTACHMENT_REASONS, DISCLAIMER, ERROR_TEXT  # noqa: E402
from generation.llm import redact  # noqa: E402
from generation.verify import NO_PASSAGES_GAP  # noqa: E402

STATUSES = ("answered", "not_covered", "refused", "error")
SHOWS_GAPS = ("answered", "not_covered")
APP_ERROR_TEXT = f"{ERROR_TEXT}\n\n{DISCLAIMER}"  # what an app_error card shows (Section 9)


class ProjectionError(ValueError):
    """The record and the answer disagree, or the record is not a `gen-log/3` record."""


@dataclass(frozen=True)
class Extras:
    """What the app measures or reads at write time (Section 4.4)."""
    finished_at: datetime
    latency_ms: int
    chunk_text_sha256: Mapping[str, str] = field(default_factory=dict)  # corpus_chunks.text_sha256 by chunk id
    gen_log_file: str | None = None      # local only: the JSONL file holding the full record
    flags: tuple[str, ...] = ()          # app flags added to Answer.flags (ui_fallback)


@dataclass
class AuditRows:
    question: str
    query: dict
    passages: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    computations: list[dict] = field(default_factory=list)

    def payload(self) -> dict:
        """The close_query payload (jsonb)."""
        return {"question": self.question, "query": self.query, "passages": self.passages, "claims": self.claims,
                "gaps": self.gaps, "computations": self.computations}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def origin_of(reasons: list[str]) -> str:
    """retrieval | attachment | lookup | calculation_source, from a passage's reasons."""
    if any(r.startswith("lookup_section:") for r in reasons):
        return "lookup"
    if any(r.startswith("calculation source:") for r in reasons):
        return "calculation_source"
    if any(r.startswith(ATTACHMENT_REASONS) for r in reasons):
        return "attachment"
    return "retrieval"


def _text_block(content) -> str | None:
    texts = [b.get("text") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    return texts[-1] if texts else None


def _call_entry(kind: str, response: dict | None, *, turn=None, usage=None, cost=None, latency=None, error=None) -> dict:
    response = response or {}
    return {"kind": kind, "turn": turn, "request_id": response.get("request_id"), "model": response.get("model"),
            "stop_reason": response.get("stop_reason"),
            "usage": usage if usage is not None else response.get("usage"),
            "cost_usd": cost if cost is not None else response.get("cost_usd"),
            "latency_ms": latency if latency is not None else response.get("latency_ms"), "error": error}


def model_calls(record: dict) -> list[dict]:
    """Per model call: kind, turn, request id, model, stop reason, usage, cost, latency, error. Never
    a request or response body."""
    out = []
    for call in (record.get("tools") or {}).get("calls") or []:
        out.append(_call_entry("generation", call.get("response"), turn=call.get("turn"), usage=call.get("usage"),
                               cost=call.get("cost_usd"), latency=call.get("latency_ms"), error=call.get("error")))
    verification = record.get("verification") or {}
    for claim in verification.get("claims") or []:
        out.append(_call_entry("verifier", claim.get("response"), turn=claim.get("no"), usage=claim.get("usage"),
                               cost=claim.get("cost_usd"), latency=claim.get("latency_ms"), error=claim.get("error")))
    for i, gap in enumerate((verification.get("gaps") or {}).get("gaps") or [], start=1):
        if gap.get("request") is None and gap.get("response") is None and gap.get("error") is None:
            continue  # removed by a code filter: no call was made
        out.append(_call_entry("gap", gap.get("response"), turn=i, usage=gap.get("usage"), cost=gap.get("cost_usd"),
                               latency=gap.get("latency_ms"), error=gap.get("error")))
    tagger = (record.get("guardrails") or {}).get("note_tagger") or {}
    calls = tagger.get("calls")
    if calls:
        for i, call in enumerate(calls, start=1):
            out.append(_call_entry("tagger", call.get("response"), turn=i, usage=call.get("usage"),
                                   cost=call.get("cost_usd"), latency=call.get("latency_ms"), error=call.get("reason")
                                   if call.get("status") == "failed" else None))
    elif tagger.get("status") not in (None, "off"):
        out.append(_call_entry("tagger", tagger.get("response"), turn=1, usage=tagger.get("usage"),
                               cost=tagger.get("cost_usd"), latency=tagger.get("latency_ms"),
                               error=tagger.get("reason") if tagger.get("status") == "failed" else None))
    return out


def _note_tagger(tagger: dict | None) -> dict | None:
    """The tagger log without its request and response bodies."""
    if tagger is None:
        return None
    out = {k: v for k, v in tagger.items() if k not in ("request", "response", "calls")}
    if tagger.get("calls") is not None:
        out["calls"] = [{k: v for k, v in call.items() if k != "response"} for call in tagger["calls"]]
    return out


def _versions(record: dict) -> dict:
    verification = record.get("verification") or {}
    guardrails = record.get("guardrails") or {}
    tagger = guardrails.get("note_tagger") or {}
    tools = record.get("tools") or {}
    computations = tools.get("computations") or []
    out = {"verifier_schema_version": verification.get("schema_version"),
           "gap_schema_version": verification.get("gap_schema_version"),
           "normaliser": verification.get("normaliser"),
           "tagger_prompt_version": tagger.get("prompt_version"),
           "tagger_schema_version": tagger.get("schema_version"),
           "tagger_effort": tagger.get("effort"),
           "verifier_effort": verification.get("effort"),
           "calc_versions": sorted({c["code"]["calc_version"] for c in computations}) or None,
           "rates_sha256": sorted({c["code"]["rates_sha256"] for c in computations}) or None,
           "retrieval_config_file": (record.get("retrieval") or {}).get("config_file"),
           "guardrails_policy_file": guardrails.get("policy_file"),
           "tools_settings": tools.get("settings")}
    return {k: v for k, v in out.items() if v is not None}


def _passages(record: dict, answer, cited: set[str], supporting: set[str], hashes: Mapping[str, str]) -> list[dict]:
    retrieved = {c["label"]: c for c in ((record.get("retrieval") or {}).get("chunks") or [])}
    added = {p["label"]: p for p in ((record.get("tools") or {}).get("passages_added") or [])}
    record_labels = [*retrieved, *added]
    if record_labels != [p.label for p in answer.passages]:
        raise ProjectionError("the record's passages are not the answer's passages")
    rows = []
    for p in answer.passages:
        r, a = retrieved.get(p.label), added.get(p.label)
        source = r or a
        if source["chunk_id"] != p.chunk_id:
            raise ProjectionError(f"passage {p.label} names a different chunk in the record")
        reasons = list(r["reasons"]) if r else [a["reason"]]
        if p.chunk_id not in hashes:
            raise ProjectionError(f"no text_sha256 for the chunk of passage {p.label}")
        rows.append({"label": p.label, "rank": r["rank"] if r else p.rank, "chunk_id": p.chunk_id,
                     "citation": p.citation, "context_header": p.location, "origin": origin_of(reasons),
                     "score": r["score"] if r else None, "distance": r["distance"] if r else None,
                     "reasons": reasons, "disputed": bool(p.disputed), "cited": p.chunk_id in cited,
                     "supporting": p.chunk_id in supporting, "chunk_text_sha256": hashes[p.chunk_id]})
    return rows


def _claims(verification: dict | None, status: str) -> list[dict]:
    rows = []
    for c in (verification or {}).get("claims") or []:
        rows.append({"claim_no": c["no"], "text": c["text"], "cited_labels": list(c["labels"]),
                     "cited_chunk_ids": list(c["chunk_ids"]), "rule": c["rule"], "llm_verdict": c["llm_verdict"],
                     "second": c["second"], "verdict": c["verdict"], "reason": c["reason"],
                     "sub_reason": c["sub_reason"], "unsupported_part": c["unsupported_part"],
                     "reasoning": c["reasoning"], "evidence": c["evidence"], "citations": c["citations"],
                     "kept_chunk_ids": list(c["kept_chunk_ids"]), "action": c["action"],
                     "shown": c["action"] == "kept" and status == "answered", "call_error": c["error"],
                     "cost_usd": c["cost_usd"], "latency_ms": c["latency_ms"]})
    return rows


def _gaps(record: dict, answer, status: str) -> list[dict]:
    checked = ((record.get("verification") or {}).get("gaps") or {}).get("gaps")
    rows = []
    if checked:
        for i, g in enumerate(checked, start=1):
            rows.append({"gap_no": i, "text": g["text"], "origin": g["origin"], "class": g["class"],
                         "kept": bool(g["kept"]), "reason": g["reason"],
                         "shown": bool(g["kept"]) and status in SHOWS_GAPS})
    else:  # no gap check: the parser's gaps, class NULL
        for i, text in enumerate(record["parsed"]["gaps"], start=1):
            kept = text in answer.gaps
            rows.append({"gap_no": i, "text": text, "origin": "code" if text == NO_PASSAGES_GAP else "model",
                         "class": None, "kept": kept, "reason": None, "shown": kept and status in SHOWS_GAPS})
    shown = [r["text"] for r in rows if r["shown"]]
    if status in SHOWS_GAPS and shown != list(answer.gaps):
        raise ProjectionError("the shown gaps are not the answer's gaps")
    return rows


def _computations(tools: dict | None) -> list[dict]:
    rows = []
    for c in (tools or {}).get("computations") or []:
        rows.append({"calc_id": c["id"], "scenario": c["scenario"], "status": c["status"], "inputs": c["inputs"],
                     "input_matches": c["input_matches"], "constants": c["constants"], "relies_on": c["relies_on"],
                     "steps": c["steps"], "rounding": c["rounding"], "result": c["result"],
                     "result_kind": c["result_kind"], "result_text": c["result_text"], "what": c["what"],
                     "from_question": c["from_question"], "assumptions": c["assumptions"], "working": c["working"],
                     "not_computed": c["not_computed"], "declined_code": c["declined_code"],
                     "declined_reason": c["declined_reason"], "sources": c["sources"], "listed": c["listed"],
                     "checks": c["checks"], "shown": c["shown"], "withheld_reason": c["withheld_reason"],
                     "calc_version": c["code"]["calc_version"], "rates_sha256": c["code"]["rates_sha256"]})
    return rows


def project(record: dict, answer, extras: Extras) -> AuditRows:
    """The audit rows for one question (Section 4.4)."""
    if record.get("schema") != SCHEMA_TOOLS:
        raise ProjectionError(f"expected a {SCHEMA_TOOLS} record")
    if record.get("record_id") != answer.record_id:
        raise ProjectionError("the record's record_id is not the answer's")
    if record.get("question") != answer.question:
        raise ProjectionError("the record's question is not the answer's")
    if record.get("rendered") != answer.rendered:
        raise ProjectionError("the record's rendered text is not the answer's")
    status = answer.status
    if status not in STATUSES:
        raise ProjectionError(f"unknown answer status {status!r}")

    tools = record.get("tools")
    verification = record.get("verification")
    guardrails = record.get("guardrails") or {}
    refusal = record.get("refusal") or {}
    claim_rows = _claims(verification, status)
    computation_rows = _computations(tools)
    cited = {c for claim in record["parsed"]["claims"] for c in claim["chunk_ids"]}
    supporting = {c for row in claim_rows if row["shown"] for c in row["kept_chunk_ids"]}
    supporting |= {c for row in computation_rows if row["shown"] for c in row["sources"]}

    calls = (tools or {}).get("calls") or []
    first = (calls[0].get("request") or {}) if calls else {}
    last_response = calls[-1].get("response") if calls else None
    parsed = record["parsed"]
    rendered = record["rendered"]
    query = {
        "record_id": record["record_id"], "finished_at": extras.finished_at.isoformat(), "status": status,
        "refusal_flag": record["refusal_flag"], "refusal_reason": refusal.get("reason"),
        "refusal_stage": refusal.get("stage"),
        "refusal_text": rendered.split("\n\n", 1)[0] if status in ("refused", "error") else None,
        "error": record["error"], "app_error": None,
        "flags": list(dict.fromkeys([*answer.flags, *extras.flags])),
        "log_schema": record["schema"],
        "config_file": ((tools or {}).get("config") or {}).get("file"),
        "config_sha256": ((tools or {}).get("config") or {}).get("sha256"),
        "retrieval_config_sha256": (record.get("retrieval") or {}).get("config_sha256"),
        "guardrails_policy_sha256": guardrails.get("policy_sha256"), "corpus": record["corpus"],
        "generator_model": (calls[-1].get("request") or {}).get("model") if calls else None,
        "prompt_version": first.get("prompt_version"), "schema_version": first.get("schema_version"),
        "tool_defs_version": first.get("tool_defs_version"),
        "verifier_model": (verification or {}).get("verifier_model"),
        "verifier_prompt_version": (verification or {}).get("prompt_version"),
        "gap_prompt_version": (verification or {}).get("gap_prompt_version"),
        "tagger_model": (guardrails.get("note_tagger") or {}).get("model"),
        "versions": _versions(record),
        "pre_check": guardrails.get("pre_check"), "relevance_floor": guardrails.get("floor"),
        "note_tagger": _note_tagger(guardrails.get("note_tagger")),
        "notes": record["notes"], "disclosures": record["disclosures"],
        "parse": {"status": parsed["status"],
                  "claims": [{"labels": c["labels"], "chunk_ids": c["chunk_ids"]} for c in parsed["claims"]],
                  "gaps": parsed["gaps"], "rejected_citations": parsed["rejected_citations"],
                  "flags": parsed["flags"], "calculations": parsed.get("calculations", [])},
        "generator_output": _text_block((last_response or {}).get("content")),
        "verification_summary": None if verification is None else {
            "summary": verification["summary"], "actions": verification["actions"],
            "effort": verification.get("effort"), "usage": verification["usage"],
            "cost_usd": verification["cost_usd"], "latency_ms": verification["latency_ms"],
            "gap_request": (verification.get("gaps") or {}).get("request"),
            "gap_error": (verification.get("gaps") or {}).get("error")},
        "tool_uses": (tools or {}).get("tool_uses"),
        "model_calls": model_calls(record),
        "rendered": rendered, "rendered_sha256": sha256_text(rendered),
        "usage": answer.usage,
        "generation_cost_usd": round(answer.cost_usd, 6),
        "verification_cost_usd": round(answer.verification_cost_usd, 6),
        "tagger_cost_usd": round(answer.tagger.cost_usd, 6),
        "total_cost_usd": round(answer.total_cost_usd, 6),
        "latency_ms": int(extras.latency_ms), "gen_log_file": extras.gen_log_file,
    }
    return AuditRows(record["question"], query,
                     _passages(record, answer, cited, supporting, extras.chunk_text_sha256), claim_rows,
                     _gaps(record, answer, status), computation_rows)


def project_app_error(question: str, exc: BaseException, extras: Extras) -> AuditRows:
    """The row for a question whose `generate()` raised before writing a record (Section 9):
    status app_error, NULL record_id, the exception type and its redacted message, no child rows."""
    return AuditRows(question, {
        "record_id": None, "finished_at": extras.finished_at.isoformat(), "status": "app_error",
        "app_error": f"{type(exc).__name__}: {redact(str(exc))}", "flags": list(extras.flags),
        "rendered": APP_ERROR_TEXT, "rendered_sha256": sha256_text(APP_ERROR_TEXT),
        "latency_ms": int(extras.latency_ms), "gen_log_file": extras.gen_log_file})
