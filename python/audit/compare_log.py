"""Check the audit rows against the local JSONL record with the same record_id, field by field
(interface design, Sections 5.1 and 11; D-150).

    .venv\\Scripts\\python python\\audit\\compare_log.py Q-1a2b3c4d [Q-...]      # one line per difference
    .venv\\Scripts\\python python\\audit\\compare_log.py --all-final              # every final row with a record

The audit rows are a projection of the `gen-log/3` record (D-150). This is the projection in reverse: for
every field the database holds, the expected value is derived from the JSONL record alone, and any field
that disagrees is named. Exit code 0 means zero differences; 1 means at least one; 2 is a usage or lookup
error. The exit gate runs it on all 13 live queries (Section 11).

Fields that only the running app knows cannot come from the record and are not compared: the flags the app
added (`ui_fallback`), `latency_ms`, the timestamps, the tier and session fields, `cost_reserved_usd` and
the passage `citation`, `context_header` and `disputed` (the citation and header are checked against the
corpus instead). What the user saw is checked on its own: a `final` row needs a `shown` (or `shown_plain`)
delivery whose `shown_sha256` equals the sha256 of the record's rendered text.

Needs the local `logs/generation/` JSONL (git-ignored); the hosted service keeps none. Nothing here prints a
connection string, a key or a question.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from audit import project as proj  # noqa: E402
from audit.migrate import DEFAULT_SCHEMA  # noqa: E402
from audit.report import ReferenceError_, connect_review, fetch_query, resolve_reference  # noqa: E402
from audit.store import reference  # noqa: E402
from generation.audit_log import LOG_DIR, read_records  # noqa: E402

TOLERANCE = 1e-6
# C-12: the record holds three cost parts, each rounded to 6 places, so their sum can differ from the stored
# round(total, 6) by up to 2e-6 (0.5e-6 per part plus 0.5e-6 for the total), plus float slack. Only the total
# gets this tolerance; every other field keeps TOLERANCE.
TOTAL_COST_TOLERANCE = 2.5e-6


@dataclass(frozen=True)
class Diff:
    where: str       # table.field
    stored: object
    expected: object

    def line(self) -> str:
        clip = lambda v: (repr(v)[:117] + "...") if len(repr(v)) > 120 else repr(v)  # noqa: E731
        return f"{self.where}: database {clip(self.stored)} != record {clip(self.expected)}"


def find_record(record_id: str, gen_log_file: str | None, log_dir: Path = LOG_DIR, root: Path = ROOT) -> dict | None:
    """The JSONL record with this record_id: in the file the row names, else in any day file."""
    candidates = []
    if gen_log_file:
        named = Path(gen_log_file)
        candidates.append(named if named.is_absolute() else root / named)
    candidates += sorted(Path(log_dir).glob("*.jsonl"))
    for path in candidates:
        if path.exists():
            for record in read_records(path):
                if record.get("record_id") == record_id:
                    return record
    return None


def _norm(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    return value


def same(stored, expected, tolerance: float = TOLERANCE) -> bool:
    stored, expected = _norm(stored), _norm(expected)
    if isinstance(stored, (int, float)) and isinstance(expected, (int, float)) and not isinstance(stored, bool) \
            and not isinstance(expected, bool):
        return abs(stored - expected) <= tolerance
    if isinstance(stored, dict) and isinstance(expected, dict):
        return stored.keys() == expected.keys() and all(same(stored[k], expected[k], tolerance) for k in stored)
    if isinstance(stored, list) and isinstance(expected, list):
        return len(stored) == len(expected) and all(same(a, b, tolerance) for a, b in zip(stored, expected))
    return stored == expected


def _sum(*values) -> float:
    return round(sum(v or 0.0 for v in values), 6)


def expected_status(record: dict) -> str:
    refusal, verification, parsed = record.get("refusal"), record.get("verification"), record["parsed"]
    if refusal:
        return refusal["status"]
    if verification:
        return verification["summary"]["status"]
    return parsed["status"]


def expected_query(record: dict, status: str) -> dict:
    """What the `queries` row must hold, from the record alone."""
    tools, verification = record.get("tools"), record.get("verification")
    guardrails, refusal = record.get("guardrails") or {}, record.get("refusal") or {}
    calls = (tools or {}).get("calls") or []
    first = (calls[0].get("request") or {}) if calls else {}
    last_response = calls[-1].get("response") if calls else None
    parsed = record["parsed"]
    rendered = record["rendered"]
    tagger = guardrails.get("note_tagger") or {}
    return {
        "record_id": record["record_id"], "question": record["question"], "log_schema": record["schema"],
        "refusal_flag": record["refusal_flag"], "refusal_reason": refusal.get("reason"),
        "refusal_stage": refusal.get("stage"),
        "refusal_text": rendered.split("\n\n", 1)[0] if status in ("refused", "error") else None,
        "error": record["error"], "status": status,
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
        "tagger_model": tagger.get("model"), "versions": proj._versions(record),
        "pre_check": guardrails.get("pre_check"), "relevance_floor": guardrails.get("floor"),
        "note_tagger": proj._note_tagger(guardrails.get("note_tagger")),
        "notes": record["notes"], "disclosures": record["disclosures"],
        "parse": {"status": parsed["status"],
                  "claims": [{"labels": c["labels"], "chunk_ids": c["chunk_ids"]} for c in parsed["claims"]],
                  "gaps": parsed["gaps"], "rejected_citations": parsed["rejected_citations"],
                  "flags": parsed["flags"], "calculations": parsed.get("calculations", [])},
        "generator_output": proj._text_block((last_response or {}).get("content")),
        "verification_summary": None if verification is None else {
            "summary": verification["summary"], "actions": verification["actions"],
            "effort": verification.get("effort"), "usage": verification["usage"],
            "cost_usd": verification["cost_usd"], "latency_ms": verification["latency_ms"],
            "gap_request": (verification.get("gaps") or {}).get("request"),
            "gap_error": (verification.get("gaps") or {}).get("error")},
        "tool_uses": (tools or {}).get("tool_uses"), "model_calls": proj.model_calls(record),
        "rendered": rendered, "rendered_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "generation_cost_usd": round(((tools or {}).get("cost_usd")
                                      if tools is not None else (record.get("response") or {}).get("cost_usd")) or 0.0, 6),
        "verification_cost_usd": round((verification or {}).get("cost_usd") or 0.0, 6),
        "tagger_cost_usd": round(tagger.get("cost_usd") or 0.0, 6),
    }


def expected_passages(record: dict) -> list[dict]:
    retrieved = (record.get("retrieval") or {}).get("chunks") or []
    added = (record.get("tools") or {}).get("passages_added") or []
    cited = {c for claim in record["parsed"]["claims"] for c in claim["chunk_ids"]}
    out = [{"label": r["label"], "rank": r["rank"], "chunk_id": r["chunk_id"], "score": r["score"],
            "distance": r["distance"], "reasons": list(r["reasons"]), "origin": proj.origin_of(list(r["reasons"])),
            "cited": r["chunk_id"] in cited} for r in retrieved]
    out += [{"label": a["label"], "chunk_id": a["chunk_id"], "score": None, "distance": None,
             "reasons": [a["reason"]], "origin": proj.origin_of([a["reason"]]), "cited": a["chunk_id"] in cited}
            for a in added]
    return out


def expected_gaps(record: dict, status: str) -> list[dict]:
    checked = ((record.get("verification") or {}).get("gaps") or {}).get("gaps")
    rendered = record["rendered"]
    shows = status in proj.SHOWS_GAPS
    out = []
    if checked:
        for i, g in enumerate(checked, start=1):
            out.append({"gap_no": i, "text": g["text"], "origin": g["origin"], "class": g["class"],
                        "kept": bool(g["kept"]), "reason": g["reason"], "shown": bool(g["kept"]) and shows})
    else:
        for i, text in enumerate(record["parsed"]["gaps"], start=1):
            shown = shows and f"- {text}" in rendered
            out.append({"gap_no": i, "text": text, "class": None, "kept": shown, "reason": None, "shown": shown})
    return out


def compare(data: dict, record: dict | None) -> list[Diff]:
    """Every field of the database rows that disagrees with the JSONL record."""
    q = data["query"]
    if record is None:
        return [Diff("record", "no JSONL record with this record_id", "a gen-log/3 record")]
    diffs: list[Diff] = []

    def check(where, stored, expected, tolerance: float = TOLERANCE):
        if not same(stored, expected, tolerance):
            diffs.append(Diff(where, stored, expected))

    status = expected_status(record)
    expected = expected_query(record, status)
    for key, value in expected.items():
        check(f"queries.{key}", q[key], value)
    check("queries.total_cost_usd", q["total_cost_usd"], _sum(expected["generation_cost_usd"],
                                                              expected["verification_cost_usd"],
                                                              expected["tagger_cost_usd"]), TOTAL_COST_TOLERANCE)
    check("queries.state", q["state"], "final")

    claims = proj._claims(record.get("verification"), status)
    stored_claims = {c["claim_no"]: c for c in data["claims"]}
    check("claims.count", len(stored_claims), len(claims))
    for c in claims:
        row = stored_claims.get(c["claim_no"])
        if row is None:
            diffs.append(Diff(f"claims[{c['claim_no']}]", "missing", c))
            continue
        for key, value in c.items():
            check(f"claims[{c['claim_no']}].{key}", row[key], value)

    gaps = expected_gaps(record, status)
    stored_gaps = {g["gap_no"]: g for g in data["gaps"]}
    check("gaps.count", len(stored_gaps), len(gaps))
    for g in gaps:
        row = stored_gaps.get(g["gap_no"])
        if row is None:
            diffs.append(Diff(f"gaps[{g['gap_no']}]", "missing", g))
            continue
        for key, value in g.items():
            check(f"gaps[{g['gap_no']}].{key}", row[key], value)

    comps = proj._computations(record.get("tools"))
    stored_comps = {c["calc_id"]: c for c in data["computations"]}
    check("computations.count", len(stored_comps), len(comps))
    for c in comps:
        row = stored_comps.get(c["calc_id"])
        if row is None:
            diffs.append(Diff(f"computations[{c['calc_id']}]", "missing", c))
            continue
        for key, value in c.items():
            check(f"computations[{c['calc_id']}].{key}", row[key], value)

    passages = expected_passages(record)
    stored_passages = {p["label"]: p for p in data["passages"]}
    check("passages.count", len(stored_passages), len(passages))
    shown_chunks = {k for c in claims if c["shown"] for k in c["kept_chunk_ids"]}
    shown_chunks |= {s for c in comps if c["shown"] for s in c["sources"]}
    for p in passages:
        row = stored_passages.get(p["label"])
        if row is None:
            diffs.append(Diff(f"passages[{p['label']}]", "missing", p))
            continue
        for key, value in p.items():
            check(f"passages[{p['label']}].{key}", row[key], value)
        check(f"passages[{p['label']}].supporting", row["supporting"], p["chunk_id"] in shown_chunks)
        corpus = (data.get("corpus_text") or {}).get(p["chunk_id"])
        if corpus is not None:
            check(f"passages[{p['label']}].citation", row["citation"], corpus["citation"])
            check(f"passages[{p['label']}].context_header", row["context_header"], corpus["context_header"])
            check(f"passages[{p['label']}].chunk_text_sha256", row["chunk_text_sha256"], corpus["text_sha256"])

    sha = expected["rendered_sha256"]  # what the user saw (gate item 8)
    shown = [d for d in data["deliveries"] if d["outcome"] in ("shown", "shown_plain")]
    if not any(d["shown_sha256"] == sha for d in shown):
        diffs.append(Diff("deliveries", [(d["outcome"], d["shown_sha256"]) for d in data["deliveries"]],
                          f"a shown delivery with shown_sha256 {sha}"))
    return diffs


def compare_query(conn, query_id: str, schema: str = DEFAULT_SCHEMA, log_dir: Path = LOG_DIR,
                  root: Path = ROOT) -> tuple[str, list[Diff]]:
    data = fetch_query(conn, query_id, schema)
    q = data["query"]
    ref = reference(q["query_id"])
    if q["state"] != "final":
        return ref, [Diff("queries.state", q["state"], "final")]
    if q["record_id"] is None:
        return ref, []  # an app_error row has no record to compare
    record = find_record(str(q["record_id"]), q["gen_log_file"], log_dir, root)
    return ref, compare(data, record)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("references", nargs="*", help="Q-xxxxxxxx, a query id or a record id")
    parser.add_argument("--all-final", action="store_true", help="every final row that has a record")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    if not args.references and not args.all_final:
        parser.error("give at least one reference, or --all-final")
    conn, _ = connect_review()
    try:
        ids = []
        if args.all_final:
            ids = [r[0] for r in conn.execute(
                f"SELECT query_id::text FROM {args.schema}.queries WHERE state = 'final' AND record_id IS NOT NULL "
                "ORDER BY received_at").fetchall()]
        try:
            ids += [resolve_reference(conn, r, args.schema) for r in args.references]
        except ReferenceError_ as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        total = 0
        for query_id in dict.fromkeys(ids):
            ref, diffs = compare_query(conn, query_id, args.schema)
            print(f"{ref}: {len(diffs)} difference(s)")
            for d in diffs:
                print(f"  {d.line()}")
            total += len(diffs)
        print(f"{len(set(ids))} query(ies) compared, {total} difference(s) in all")
        return 1 if total else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
