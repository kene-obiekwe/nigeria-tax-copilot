"""Phase 9 step 8: the capped live check (interface design, Sections 9, 11 and 12; D-168).

    $env:TAXY_DEPLOYMENT = "local"
    .venv\\Scripts\\python eval\\interface\\run_live_check.py

Runs the 13 questions of Section 11 one after another through the app's own page (`app/main.py`
under Streamlit AppTest) with the REAL services: the real start-up check, the real audit store on
schema `audit`, the real `generate()` and the page's own card code and `deliver()` call. Each question
gets a fresh page session. Nothing is faked and nothing is injected.

Budget: cap $0.40 for the whole phase. Before each question the runner stops if the spend so far plus
$0.04 would pass the cap. The check runs ONCE: the result file is written after every question and the
runner refuses to start if it exists. It stops (no rerun, no skipping on) at the first question whose
log entry is not as the pass rule needs: an exception, no `final` row, a status `error` or `app_error`,
or no `shown` / `shown_plain` delivery. Answer quality is not judged here (G3 did that).

Rerun mode (D-170, one question, once):

    .venv\\Scripts\\python eval\\interface\\run_live_check.py --rerun-example 6

runs that one example through the same path and writes `results/live_check_v1_rerun6.json` (never
`live_check_v1.json`); it refuses if that file exists. The stop rule counts the whole phase's spend (the sum
of `queries.total_cost_usd` in the audit schema, read before the call): it stops if that + $0.04 would pass
$0.40. The rerun has its own cap of $0.05, checked and reported after the call.

Cost is `queries.total_cost_usd` (generation + verification + tagger, as the pipeline records it); the
query embedding call is not in that figure (it is a few hundredths of a cent).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT))

RESULT = ROOT / "eval" / "interface" / "results" / "live_check_v1.json"
RERUN_CAP_USD = Decimal("0.05")
CAP_USD = Decimal("0.40")
NEXT_QUESTION_MARGIN_USD = Decimal("0.04")
SCHEMA = "audit"

EXTRA_QUESTIONS = [
    ("What does Section 412 of the Nigeria Tax Act 2025 say?", "refused", "no_such_section"),
    ("What is the standard VAT rate in Botswana?", "refused", "foreign_law"),
    ("What is a good recipe for jollof rice?", "refused", "low_relevance"),
]


def questions() -> list[dict]:
    import yaml
    examples = yaml.safe_load((ROOT / "app" / "examples.yml").read_text(encoding="utf-8"))
    out = [{"n": i, "source": f"examples.yml #{i}", "topic": e["topic"], "question": e["question"], "expect": None}
           for i, e in enumerate(examples, 1)]
    for j, (question, status, reason) in enumerate(EXTRA_QUESTIONS, len(out) + 1):
        out.append({"n": j, "source": "design Section 11", "topic": reason, "question": question,
                    "expect": {"status": status, "reason": reason}})
    return out


def save(doc: dict, path: Path = RESULT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def phase_spend() -> Decimal:
    """Everything the audit schema has recorded so far (live check, api test and any rerun)."""
    from check_pgvector import connect
    with connect() as conn:
        return Decimal(str(conn.execute(f"SELECT COALESCE(sum(total_cost_usd), 0) FROM {SCHEMA}.queries").fetchone()[0]))


def row_facts(query_id: str) -> dict:
    from check_pgvector import connect
    with connect() as conn:
        q = conn.execute(
            f"SELECT state, status, refusal_reason, refusal_stage, record_id::text, flags, total_cost_usd, "
            f"generation_cost_usd, verification_cost_usd, tagger_cost_usd, latency_ms FROM {SCHEMA}.queries "
            f"WHERE query_id = %s", (query_id,)).fetchone()
        deliveries = conn.execute(
            f"SELECT outcome, shown_sha256 FROM {SCHEMA}.deliveries WHERE query_id = %s ORDER BY delivery_id",
            (query_id,)).fetchall()
    if q is None:
        return {"found": False}
    return {"found": True, "state": q[0], "status": q[1], "reason": q[2], "stage": q[3], "record_id": q[4],
            "flags": list(q[5] or []), "cost_usd": str(q[6]) if q[6] is not None else None,
            "generation_cost_usd": str(q[7]) if q[7] is not None else None,
            "verification_cost_usd": str(q[8]) if q[8] is not None else None,
            "tagger_cost_usd": str(q[9]) if q[9] is not None else None, "latency_ms": q[10],
            "deliveries": [{"outcome": d[0], "shown_sha256": d[1]} for d in deliveries]}


def ask_one(question: str):
    """A fresh page session: the question goes through the page's own form and worker."""
    from streamlit.testing.v1 import AppTest
    from app import copy as ui
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=240)
    at.run()
    if at.exception:
        raise RuntimeError(f"the page raised on first render: {at.exception[0].value[:200]}")
    if not at.text_area:
        raise RuntimeError("the page shows no question box (start-up check failed?)")
    at.text_area(key="question_box").set_value(question)
    next(b for b in at.button if b.label == ui.ASK_BUTTON).click()
    at.run(timeout=240)
    return at


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 9 capped live check (once), or one rerun (D-170).")
    parser.add_argument("--rerun-example", type=int, metavar="N",
                        help="rerun only example N (1 to 10) once; writes live_check_v1_rerun<N>.json")
    args = parser.parse_args(argv)
    rerun = args.rerun_example
    path = RESULT if rerun is None else RESULT.with_name(f"live_check_v1_rerun{rerun}.json")
    if path.exists():
        print(f"refused: {path.relative_to(ROOT)} exists. It runs once (design Section 12; D-97, D-170).")
        return 2
    if os.environ.get("TAXY_DEPLOYMENT") != "local":
        print("refused: set TAXY_DEPLOYMENT=local")
        return 2

    items = questions()
    if rerun is not None:
        if not 1 <= rerun <= 10:
            print("refused: --rerun-example takes an example number from 1 to 10")
            return 2
        items = [i for i in items if i["n"] == rerun]
    prior = phase_spend() if rerun is not None else Decimal("0")   # the stop rule counts the whole phase
    doc = {"schema": "live-check/1", "mode": "live" if rerun is None else f"rerun_example_{rerun}",
           "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "deployment": "local", "cap_usd": str(CAP_USD), "next_question_margin_usd": str(NEXT_QUESTION_MARGIN_USD),
           "phase_spend_before_usd": str(prior), "rerun_cap_usd": str(RERUN_CAP_USD) if rerun is not None else None,
           "complete": False, "stopped": None, "total_usd": "0", "results": []}
    save(doc, path)
    total = Decimal("0")

    for item in items:
        if prior + total + NEXT_QUESTION_MARGIN_USD > CAP_USD:
            doc["stopped"] = (f"stop rule before question {item['n']}: spend {prior + total} + "
                              f"{NEXT_QUESTION_MARGIN_USD} > {CAP_USD}")
            break
        entry = {"n": item["n"], "source": item["source"], "topic": item["topic"], "question": item["question"],
                 "expected": item["expect"]}
        problem = None
        started = time.monotonic()
        try:
            at = ask_one(item["question"])
            entry["wall_s"] = round(time.monotonic() - started, 1)
            entries = at.session_state["entries"] if "entries" in at.session_state else []
            if at.exception:
                problem = f"the page raised: {at.exception[0].value[:200]}"
            if not entries:
                problem = problem or "the page recorded no answer entry"
            else:
                last = entries[-1]
                entry["entry_kind"] = last["kind"]
                if last["kind"] != "card":
                    entry["reference"] = last.get("reference")
                    entry["message_shown"] = last.get("text")
                    problem = problem or f"the page showed a message, not a card ({last.get('text', '')[:80]})"
                else:
                    card = last["card"]
                    entry.update({"reference": card.reference, "query_id": card.query_id,
                                  "ui_fallback": bool(card.ui_fallback)})
        except Exception as exc:  # noqa: BLE001 - report, never retry
            entry["wall_s"] = round(time.monotonic() - started, 1)
            problem = f"{type(exc).__name__}: {str(exc)[:200]}"

        if entry.get("query_id"):
            facts = row_facts(entry["query_id"])
            entry["row"] = facts
            if facts.get("found"):
                entry["record_id"] = facts["record_id"]
                entry["status"], entry["refusal_reason"] = facts["status"], facts["reason"]
                entry["delivery"] = [d["outcome"] for d in facts["deliveries"]]
                cost = Decimal(facts["cost_usd"] or "0")
                entry["cost_usd"] = str(cost)
                total += cost
                if facts["state"] != "final":
                    problem = problem or f"row state is {facts['state']}, not final"
                if facts["status"] in ("error", "app_error"):
                    problem = problem or f"status {facts['status']} (reason {facts['reason']})"
                if not any(d in ("shown", "shown_plain") for d in entry["delivery"]):
                    problem = problem or f"no shown or shown_plain delivery (deliveries: {entry['delivery']})"
            else:
                problem = problem or "no audit row for the query id"
        entry["total_after_usd"] = str(total)
        entry["phase_total_usd"] = str(prior + total)
        if rerun is not None:
            entry["over_rerun_cap"] = total > RERUN_CAP_USD
        exp = item["expect"]
        if exp and not problem:
            entry["matches_expected"] = (entry.get("status"), entry.get("refusal_reason")) == (exp["status"], exp["reason"])
        entry["problem"] = problem
        doc["results"].append(entry)
        doc["total_usd"] = str(total)
        save(doc, path)
        print(f"{item['n']:>2} {entry.get('reference')} {entry.get('status')}/{entry.get('refusal_reason')} "
              f"{entry.get('delivery')} ${entry.get('cost_usd')} total ${total}"
              + (f"  PROBLEM: {problem}" if problem else ""), flush=True)
        if problem:
            doc["stopped"] = f"stopped at question {item['n']}: {problem}"
            break

    doc["complete"] = len(doc["results"]) == len(items) and not doc["stopped"]
    doc["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["total_usd"] = str(total)
    doc["phase_total_usd"] = str(prior + total)
    save(doc, path)
    print(f"done: complete={doc['complete']} total ${total}; phase ${prior + total} of cap ${CAP_USD}"
          + (f"; {doc['stopped']}" if doc["stopped"] else ""))
    return 0 if doc["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
