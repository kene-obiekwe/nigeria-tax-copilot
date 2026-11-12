"""The reviewer's report on one query (interface design, Sections 5.1 and 11): what was retrieved and how
the final answer was checked, read from the audit rows.

    conn = connect_review()                          # REVIEW_DATABASE_URL (read-only), never printed
    query_id = resolve_reference(conn, "Q-1a2b3c4d")  # a prefix is refused if it matches more than one row
    report = build_report(fetch_query(conn, query_id))
    print(render_markdown(report))

`fetch_query` only reads (the reviewer role has SELECT and nothing else). `build_report` and
`render_markdown` are pure, so the Streamlit viewer (`app/review.py`), the CLI (`show_query.py`) and the
tests share one report. The report holds the eight items of the exit gate (Section 11): the question, time,
reference, record id, config sha and model ids; every passage with rank, score, reasons, origin and chunk
text; the pre-check and floor results; for every claim the rule check, model verdict, final verdict,
evidence quotes found in the cited chunk text, kept citations, action and whether it was shown; every gap;
every computation with K1 to K6; the notes and disclosures (hidden ones marked "not shown to the user",
D-156); and the rendered answer with the deliveries' `shown_sha256` compared with `rendered_sha256`.

Nothing here prints a connection string or a key.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from audit.migrate import DEFAULT_SCHEMA  # noqa: E402
from audit.store import reference  # noqa: E402
from generation.verify import evidence_found  # noqa: E402

REVIEW_URL_ENV = "REVIEW_DATABASE_URL"
PINNED_CONFIG_SHA256 = "e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25"  # = interface.startup
CORPUS_COLUMNS = "chunk_id, citation, context_header, text, text_sha256, page_labels"
HIDDEN_NOTE = "not shown to the user"
CHILD_ORDER = {"passages": "rank, label", "claims": "claim_no", "gaps": "gap_no", "computations": "calc_id",
               "deliveries": "delivery_id", "feedback": "feedback_id"}
_UUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_PREFIX = re.compile(r"^(?:Q-)?([0-9a-fA-F]{4,32})$")


class ReferenceError_(LookupError):  # noqa: N801 - read as "reference error"
    """A reference that matches no query, or more than one."""


def connect_review() -> tuple[psycopg.Connection, bool]:
    """(connection, read_only). With `REVIEW_DATABASE_URL` set, the reviewer's read-only connection. Without
    it, the project connection (the owner locally, Phase 9: no login role exists yet) and read_only False,
    which the viewer says on screen. Nothing is printed."""
    url = os.environ.get(REVIEW_URL_ENV)
    if url:
        return psycopg.connect(url, connect_timeout=5), True
    from check_pgvector import connect
    return connect(), False


# Finding a query ---------------------------------------------------------------------------------------------------


def _dicts(cur) -> list[dict]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def resolve_reference(conn, ref: str, schema: str = DEFAULT_SCHEMA) -> str:
    """`Q-` plus a prefix of the query id, a full query id or a record id -> the query id. A prefix that
    matches more than one row is refused (Section 3.5)."""
    ref = ref.strip()
    queries = sql.Identifier(schema, "queries")
    if _UUID.match(ref):
        rows = conn.execute(sql.SQL("SELECT query_id::text FROM {} WHERE query_id = %s OR record_id = %s").format(
            queries), (ref, ref)).fetchall()
    else:
        m = _PREFIX.match(ref)
        if not m:
            raise ReferenceError_("not a reference: use Q-xxxxxxxx, a query id or a record id")
        prefix = m.group(1).lower()
        rows = conn.execute(sql.SQL("SELECT query_id::text FROM {} WHERE replace(query_id::text, '-', '') LIKE %s "
                                    "OR replace(coalesce(record_id::text, ''), '-', '') LIKE %s ORDER BY received_at"
                                    ).format(queries), (prefix + "%", prefix + "%")).fetchall()
    ids = sorted({r[0] for r in rows})
    if not ids:
        raise ReferenceError_("no query matches that reference")
    if len(ids) > 1:
        raise ReferenceError_(f"that reference matches {len(ids)} queries; give more characters")
    return ids[0]


def list_queries(conn, schema: str = DEFAULT_SCHEMA, *, day: date | None = None, status: str | None = None,
                 reason: str | None = None, tier: str | None = None, ui_fallback: bool = False,
                 unfinished: bool = False, rating: int | None = None, limit: int = 100) -> list[dict]:
    """Rows for the Find list, newest first, filtered by date (West Africa Time), status, refusal reason, tier,
    the `ui_fallback` flag, unfinished rows (state `received`) and feedback rating."""
    where, args = [sql.SQL("TRUE")], []
    if day is not None:
        where.append(sql.SQL("(q.received_at AT TIME ZONE 'Africa/Lagos')::date = %s"))
        args.append(day)
    for column, value in (("status", status), ("refusal_reason", reason), ("tier", tier)):
        if value:
            where.append(sql.SQL("q.{} = %s").format(sql.Identifier(column)))
            args.append(value)
    if ui_fallback:
        where.append(sql.SQL("'ui_fallback' = ANY(q.flags)"))
    if unfinished:
        where.append(sql.SQL("q.state = 'received'"))
    if rating is not None:
        where.append(sql.SQL("EXISTS (SELECT 1 FROM {} f WHERE f.query_id = q.query_id AND f.rating = %s)").format(
            sql.Identifier(schema, "feedback")))
        args.append(rating)
    query = sql.SQL("SELECT q.query_id::text AS query_id, q.record_id::text AS record_id, q.received_at, q.state, "
                    "q.status, q.refusal_reason, q.tier, q.question, q.total_cost_usd, q.latency_ms, q.flags "
                    "FROM {} q WHERE {} ORDER BY q.received_at DESC LIMIT %s").format(
        sql.Identifier(schema, "queries"), sql.SQL(" AND ").join(where))
    cur = conn.execute(query, [*args, limit])
    out = _dicts(cur)
    for row in out:
        row["reference"] = reference(row["query_id"])
    return out


# Reading the rows ------------------------------------------------------------------------------------------------------


def fetch_query(conn, query_id: str, schema: str = DEFAULT_SCHEMA) -> dict:
    """Every audit row of one query, the app start, and the current corpus text of its passages."""
    cur = conn.execute(sql.SQL("SELECT * FROM {} WHERE query_id = %s").format(sql.Identifier(schema, "queries")),
                       (query_id,))
    found = _dicts(cur)
    if not found:
        raise ReferenceError_("no query matches that reference")
    data = {"query": found[0]}
    for table, order in CHILD_ORDER.items():
        cur = conn.execute(sql.SQL("SELECT * FROM {} WHERE query_id = %s ORDER BY " + order).format(
            sql.Identifier(schema, table)), (query_id,))
        data[table] = _dicts(cur)
    cur = conn.execute(sql.SQL("SELECT * FROM {} WHERE start_id = %s").format(sql.Identifier(schema, "app_starts")),
                       (data["query"]["start_id"],))
    start = _dicts(cur)
    data["start"] = start[0] if start else None
    data["corpus_text"] = fetch_corpus_text(conn, [p["chunk_id"] for p in data["passages"]])
    return data


def fetch_corpus_text(conn, chunk_ids: list[str]) -> dict | None:
    """Current `corpus_chunks` rows by chunk id, or None when the reviewer cannot read the corpus tables
    (the D-169 grant has not been applied)."""
    if not chunk_ids:
        return {}
    try:
        cur = conn.execute(f"SELECT {CORPUS_COLUMNS} FROM corpus_chunks WHERE chunk_id = ANY(%s)", (chunk_ids,))
        return {r["chunk_id"]: r for r in _dicts(cur)}
    except psycopg.errors.InsufficientPrivilege:
        conn.rollback()
        return None


# Evidence highlighting ---------------------------------------------------------------------------------------------


def highlight(text: str, quotes: list[str]) -> list[tuple[str, bool]]:
    """`text` split into (segment, is_quote) pieces, each evidence quote marked where it occurs. The match
    collapses whitespace like the evidence check does (runs of whitespace in the passage match one space
    in the quote) and ignores case; quotes with other normalisation (curly quotes, dashes) are not marked
    here, and the claim still shows whether the verifier's own check found them."""
    spans: list[tuple[int, int]] = []
    for quote in quotes:
        words = quote.split()
        if not words:
            continue
        m = re.search(r"\s+".join(re.escape(w) for w in words), text, re.IGNORECASE)
        if m:
            spans.append(m.span())
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, pos = [], 0
    for start, end in merged:
        if start > pos:
            out.append((text[pos:start], False))
        out.append((text[start:end], True))
        pos = end
    if pos < len(text):
        out.append((text[pos:], False))
    return out or [(text, False)]


# The report (pure) ---------------------------------------------------------------------------------------------------


def _iso(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _num(value):
    return float(value) if isinstance(value, Decimal) else value


def build_report(data: dict) -> dict:
    q, start = data["query"], data["start"] or {}
    corpus = data["corpus_text"]
    ref = reference(q["query_id"])
    config_sha = q["config_sha256"] or start.get("config_sha256")
    config_file = q["config_file"] or start.get("config_file")
    own_config = q["config_sha256"] is not None
    header = {
        "reference": ref, "query_id": str(q["query_id"]), "record_id": None if q["record_id"] is None else str(q["record_id"]),
        "received_at": _iso(q["received_at"]), "finished_at": _iso(q["finished_at"]), "state": q["state"],
        "status": q["status"], "refusal_reason": q["refusal_reason"], "refusal_stage": q["refusal_stage"],
        "error": q["error"], "app_error": q["app_error"], "flags": list(q["flags"] or []), "tier": q["tier"],
        "latency_ms": q["latency_ms"],
        "cost_usd": {"reserved": _num(q["cost_reserved_usd"]), "generation": _num(q["generation_cost_usd"]),
                     "verification": _num(q["verification_cost_usd"]), "tagger": _num(q["tagger_cost_usd"]),
                     "total": _num(q["total_cost_usd"])},
        "config": {"file": config_file, "sha256": config_sha, "from": "query" if own_config else "app start",
                   "pinned": config_sha == PINNED_CONFIG_SHA256, "pinned_sha256": PINNED_CONFIG_SHA256},
        "app_start": {k: _iso(start.get(k)) for k in ("start_id", "started_at", "deployment", "git_commit", "outcome",
                                                      "app_version", "audit_schema", "problems")},
        "models": {"generator": q["generator_model"], "verifier": q["verifier_model"], "tagger": q["tagger_model"]},
        "prompts": {"generation": q["prompt_version"], "schema": q["schema_version"], "tool_defs": q["tool_defs_version"],
                    "verifier": q["verifier_prompt_version"], "gap": q["gap_prompt_version"]},
        "corpus": q["corpus"], "log_schema": q["log_schema"],
    }
    floor = q["relevance_floor"]
    checks = {"pre_check": q["pre_check"], "relevance_floor": floor,
              "floor_line": None if floor is None else (
                  f"floor {floor.get('status')}: top-1 vector score {floor.get('top1_vector_score')} "
                  f"against tau {floor.get('tau')}"),
              "refusal": {"reason": q["refusal_reason"], "stage": q["refusal_stage"], "text": q["refusal_text"]}}

    passages = []
    for p in data["passages"]:
        current = None if corpus is None else corpus.get(p["chunk_id"])
        passages.append({
            "label": p["label"], "rank": p["rank"], "chunk_id": p["chunk_id"], "citation": p["citation"],
            "context_header": p["context_header"], "origin": p["origin"], "score": p["score"],
            "distance": p["distance"], "reasons": list(p["reasons"]), "disputed": p["disputed"],
            "cited": p["cited"], "supporting": p["supporting"],
            "text": None if current is None else current["text"],
            "text_changed": None if current is None else current["text_sha256"] != p["chunk_text_sha256"],
            "text_available": current is not None})
    by_id = {p["chunk_id"]: p for p in passages}
    by_label = {p["label"]: p for p in passages}

    claims = []
    for c in data["claims"]:
        evidence = []
        for item in c["evidence"] or []:
            label = item.get("label")
            passage = by_label.get(label)
            text = None if passage is None else passage["text"]
            quote = item.get("quote", "")
            evidence.append({"label": label, "quote": quote,
                             "found": None if text is None else evidence_found(quote, text,
                                                                               (passage or {}).get("context_header", "")),
                             "segments": None if text is None else highlight(text, [quote])})
        claims.append({
            "claim_no": c["claim_no"], "text": c["text"], "cited_labels": list(c["cited_labels"]),
            "cited_chunk_ids": list(c["cited_chunk_ids"]), "rule": c["rule"], "llm_verdict": c["llm_verdict"],
            "second": c["second"], "verdict": c["verdict"], "reason": c["reason"], "sub_reason": c["sub_reason"],
            "unsupported_part": c["unsupported_part"], "reasoning": c["reasoning"], "evidence": evidence,
            "citations": c["citations"], "kept_chunk_ids": list(c["kept_chunk_ids"]), "action": c["action"],
            "shown": c["shown"], "call_error": c["call_error"]})
    gaps = [{"gap_no": g["gap_no"], "text": g["text"], "origin": g["origin"], "class": g["class"], "kept": g["kept"],
             "reason": g["reason"], "shown": g["shown"]} for g in data["gaps"]]
    computations = [{k: v for k, v in c.items() if k != "query_id"} for c in data["computations"]]

    disclosures = [{**d, "shown": d.get("kind") != "hidden",
                    "mark": HIDDEN_NOTE if d.get("kind") == "hidden" else None} for d in (q["disclosures"] or [])]
    rendered_sha = q["rendered_sha256"]
    deliveries = [{"at": _iso(d["at"]), "outcome": d["outcome"], "shown_sha256": d["shown_sha256"],
                   "matches_rendered": None if d["shown_sha256"] is None else d["shown_sha256"] == rendered_sha}
                  for d in data["deliveries"]]
    feedback = [{"at": _iso(f["at"]), "rating": f["rating"], "reason": f["reason"]} for f in data["feedback"]]
    return {
        "header": header, "question": q["question"], "checks": checks, "passages": passages,
        "generator": {"parse": q["parse"], "output": q["generator_output"], "model_calls": q["model_calls"]},
        "verification": {"claims": claims, "gaps": gaps, "summary": q["verification_summary"]},
        "tools": {"tool_uses": q["tool_uses"], "computations": computations},
        "notes": {"notes": q["notes"] or [], "disclosures": disclosures, "note_tagger": q["note_tagger"]},
        "user_saw": {"rendered": q["rendered"], "rendered_sha256": rendered_sha, "deliveries": deliveries,
                     "feedback": feedback},
        "local": {"gen_log_file": q["gen_log_file"], "record_id": header["record_id"]},
        "corpus_text_available": corpus is not None,
        "unknown_chunks": [c for c in by_id if corpus is not None and c not in corpus],
    }


# Markdown ------------------------------------------------------------------------------------------------------------


def _fmt(value) -> str:
    return "none" if value is None else str(value)


def _segments_md(segments) -> str:
    return "".join(f"[[{text}]]" if hit else text for text, hit in segments)


def render_markdown(report: dict) -> str:
    h, out = report["header"], []
    add = out.append
    add(f"# Query {h['reference']}")
    if not h["config"]["pinned"]:
        add(f"\n**WARNING: the configuration sha256 is not the pinned config_p7 value ({h['config']['pinned_sha256']}).**")
    add("\n## 1. Header")
    add(f"- Time: {h['received_at']} (finished {_fmt(h['finished_at'])}); state {h['state']}; tier {h['tier']}")
    add(f"- Reference {h['reference']}; query id {h['query_id']}; record id {_fmt(h['record_id'])}")
    add(f"- Status {_fmt(h['status'])}; refusal reason {_fmt(h['refusal_reason'])}; stage {_fmt(h['refusal_stage'])}; "
        f"flags {', '.join(h['flags']) or 'none'}")
    if h["error"] or h["app_error"]:
        add(f"- Error: {_fmt(h['error'])}; app error: {_fmt(h['app_error'])}")
    cost = h["cost_usd"]
    add(f"- Latency {_fmt(h['latency_ms'])} ms; cost total {_fmt(cost['total'])} "
        f"(generation {_fmt(cost['generation'])}, verification {_fmt(cost['verification'])}, tagger {_fmt(cost['tagger'])}; "
        f"reserved {_fmt(cost['reserved'])})")
    cfg = h["config"]
    add(f"- Configuration: {_fmt(cfg['file'])} sha256 {_fmt(cfg['sha256'])} (from the {cfg['from']}); "
        f"{'equals' if cfg['pinned'] else 'DOES NOT EQUAL'} the pinned config_p7")
    s = h["app_start"]
    add(f"- App start {_fmt(s['start_id'])}: {_fmt(s['started_at'])}, {_fmt(s['deployment'])}, commit "
        f"{_fmt(s['git_commit'])}, outcome {_fmt(s['outcome'])}")
    m, p = h["models"], h["prompts"]
    add(f"- Models: generator {_fmt(m['generator'])}, verifier {_fmt(m['verifier'])}, tagger {_fmt(m['tagger'])}")
    add(f"- Prompt labels: generation {_fmt(p['generation'])}, schema {_fmt(p['schema'])}, tool definitions "
        f"{_fmt(p['tool_defs'])}, verifier {_fmt(p['verifier'])}, gap {_fmt(p['gap'])}")
    add(f"- Corpus load: {json.dumps(h['corpus'], sort_keys=True)}")
    add("\n## 2. Question\n")
    add(report["question"])

    c = report["checks"]
    add("\n## 3. Pre-check and relevance floor")
    add(f"- Pre-check: {json.dumps(c['pre_check'], sort_keys=True)}")
    add(f"- Relevance floor: {_fmt(c['floor_line'])}")
    add(f"- Refusal: reason {_fmt(c['refusal']['reason'])}, stage {_fmt(c['refusal']['stage'])}, "
        f"text {_fmt(c['refusal']['text'])}")

    add("\n## 4. Retrieval (passages shown to the model)")
    if not report["corpus_text_available"]:
        add("- Chunk text is not available: the reviewer role cannot read the corpus tables (D-169 grant not applied).")
    if not report["passages"]:
        add("- No passages (no retrieval was run).")
    for p in report["passages"]:
        add(f"\n### {p['label']} rank {p['rank']}: {p['citation']}")
        add(f"- chunk {p['chunk_id']}; origin {p['origin']}; score {_fmt(p['score'])}; distance {_fmt(p['distance'])}; "
            f"disputed {p['disputed']}; cited {p['cited']}; supporting {p['supporting']}")
        add(f"- reasons: {'; '.join(p['reasons'])}")
        if p["text_changed"]:
            add("- **Text changed since this answer** (the current corpus text differs from the text at write time).")
        if p["text"] is not None:
            add(f"\n```text\n{p['text']}\n```")

    add("\n## 5. Generator output")
    parse = report["generator"]["parse"] or {}
    add(f"- Parser: status {_fmt(parse.get('status'))}; rejected citations {json.dumps(parse.get('rejected_citations'))}; "
        f"flags {json.dumps(parse.get('flags'))}; listed calculations {json.dumps(parse.get('calculations'))}")
    add(f"\n```json\n{_fmt(report['generator']['output'])}\n```")

    add("\n## 6. Verification")
    if not report["verification"]["claims"]:
        add("- No claims were checked.")
    for cl in report["verification"]["claims"]:
        add(f"\n### Claim {cl['claim_no']}: {cl['verdict']} -> {cl['action']} ({'shown' if cl['shown'] else 'not shown'})")
        add(f"- text: {cl['text']}")
        rule = cl["rule"] or {}
        add(f"- rule check: tokens {json.dumps(rule.get('tokens'))}; missing {json.dumps(rule.get('missing'))}; "
            f"reason {_fmt(rule.get('reason'))}; hint {_fmt(rule.get('hint'))}")
        add(f"- model verdict {_fmt(cl['llm_verdict'])}; second call {json.dumps(cl['second'])}; final "
            f"{cl['verdict']} / {_fmt(cl['reason'])} / {_fmt(cl['sub_reason'])}; unsupported part {_fmt(cl['unsupported_part'])}")
        add(f"- cited {', '.join(cl['cited_labels'])}; kept citations {', '.join(cl['kept_chunk_ids']) or 'none'}")
        for ev in cl["evidence"]:
            found = {True: "found in the cited chunk text", False: "NOT found", None: "chunk text not available"}[ev["found"]]
            add(f"- evidence ({ev['label']}, {found}): {ev['quote']}")
            if ev["segments"] and any(hit for _, hit in ev["segments"]):
                add(f"\n```text\n{_segments_md(ev['segments'])}\n```")
    for g in report["verification"]["gaps"]:
        add(f"\n- Gap {g['gap_no']}: {g['text']} (class {_fmt(g['class'])}, kept {g['kept']}, shown {g['shown']}, "
            f"origin {_fmt(g['origin'])}, reason {_fmt(g['reason'])})")
    add(f"\n- Action-rule summary: {json.dumps(report['verification']['summary'], sort_keys=True)}")

    add("\n## 7. Tools")
    for use in report["tools"]["tool_uses"] or []:
        add(f"- tool use: {json.dumps(use, sort_keys=True)}")
    for comp in report["tools"]["computations"]:
        add(f"\n### Computation {comp['calc_id']}: {comp['scenario']} ({comp['status']})")
        add(f"- shown {comp['shown']}; withheld reason {_fmt(comp['withheld_reason'])}; listed {comp['listed']}")
        add(f"- checks K1 to K6: {json.dumps(comp['checks'], sort_keys=True)}")
        add(f"- result: {_fmt(comp['result_text'])}; sources {', '.join(comp['sources']) or 'none'}")

    add("\n## 8. Notes and disclosures")
    for n in report["notes"]["notes"]:
        add(f"- note: {n.get('text')}")
    for d in report["notes"]["disclosures"]:
        mark = f" [{d['mark']}]" if d["mark"] else ""
        add(f"- disclosure ({d.get('kind')}, {d.get('ref')}){mark}: {d.get('text')}")

    add("\n## 9. What the user saw")
    saw = report["user_saw"]
    add(f"- rendered sha256 {_fmt(saw['rendered_sha256'])}")
    for d in saw["deliveries"]:
        verdict = {True: "equals rendered_sha256", False: "DOES NOT EQUAL rendered_sha256", None: "no text recorded"}[
            d["matches_rendered"]]
        add(f"- delivery {d['outcome']} at {d['at']}: shown sha256 {_fmt(d['shown_sha256'])} ({verdict})")
    for f in saw["feedback"]:
        add(f"- feedback at {f['at']}: rating {f['rating']}, reason {_fmt(f['reason'])}")
    add(f"\n```text\n{_fmt(saw['rendered'])}\n```")

    add("\n## 10. Local only")
    add(f"- JSONL record: {_fmt(report['local']['gen_log_file'])}, record id {_fmt(report['local']['record_id'])}")
    return "\n".join(out) + "\n"


def report_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)
