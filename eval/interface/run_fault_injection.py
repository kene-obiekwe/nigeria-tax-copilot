"""Phase 9 step 8, part 4: fault injection with fake clients (interface design, Sections 9 and 11).

    .venv\\Scripts\\python python\\audit\\migrate.py --schema audit_faults     # once
    .venv\\Scripts\\python eval\\interface\\run_fault_injection.py

Four cases, each through the app's own page (`app/main.py`, Streamlit AppTest) with the real `AppService`
and the real card code, but fake model, verifier, tagger and retriever clients (no paid call, no key), and
the audit rows written to the separate kept schema `audit_faults`, never to `audit`:

    1. verifier outage            -> status error, reason verification_unavailable
    2. embedding failure          -> status app_error, record_id NULL
    3. close failure (twice)      -> row stays received, delivery withheld_log_failure
    4. UI timeout                 -> delivery ui_timeout, the worker still finishes and logs

JSONL records of these fake runs go to logs/generation_faults/ (git-ignored), not to logs/generation/.
Writes eval/interface/results/fault_cases_v1.json and refuses to run if it exists.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT / "python"), str(ROOT), str(ROOT / "tests")):
    sys.path.insert(0, _p)
os.environ["TAXY_DEPLOYMENT"] = "local"

RESULT = ROOT / "eval" / "interface" / "results" / "fault_cases_v1.json"
SCHEMA = "audit_faults"
JSONL_DIR = ROOT / "logs" / "generation_faults"


def main() -> int:
    if RESULT.exists():
        print(f"refused: {RESULT.relative_to(ROOT)} exists")
        return 2

    from streamlit.testing.v1 import AppTest

    import test_tools as T
    from app import cards
    from app import copy as ui
    from app.services import Services, db_chunks
    from audit.migrate import Names
    from audit.project import APP_ERROR_TEXT
    from audit.store import AuditStore
    from check_pgvector import connect
    from generation import generate as gen
    from interface.identity import LOCAL
    from interface.limits import LocalGate
    from interface.service import AppService, db_chunk_hashes
    from interface.startup import shipped_config, startup_check
    from test_app_service import FailingStore, FakeGenerate
    from test_audit_write import DownVerifier

    names = Names(schema=SCHEMA)
    store = AuditStore(connect, SCHEMA)
    start = startup_check(deployment="local", store=store, connect=connect, names=names)
    assert start.ok, start.problems
    chunks = db_chunks()

    def make_services(generate_fn, *, real_store=None, **kwargs) -> Services:
        service = AppService(store=real_store or store, gate=LocalGate(), start_id=start.start_id,
                             config=shipped_config(), chunk_hashes=db_chunk_hashes(connect), generate_fn=generate_fn,
                             jsonl_dir=JSONL_DIR, close_retry_delay_s=0.05, parity=cards.make_parity(chunks))
        return Services("local", start, service, chunks, lambda: LOCAL, **kwargs)

    def ask(services: Services, question: str):
        at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60)
        at.session_state["services"] = services
        at.run()
        at.text_area(key="question_box").set_value(question)
        next(b for b in at.button if b.label == ui.ASK_BUTTON).click()
        return at.run(timeout=60)

    def facts(reference: str) -> dict:
        with connect() as conn:
            q = conn.execute(f"SELECT query_id::text, state, status, refusal_reason, record_id::text, app_error "
                             f"FROM {SCHEMA}.queries WHERE query_id::text LIKE %s", (reference[2:].lower() + "%",)
                             ).fetchone()
            d = conn.execute(f"SELECT outcome, shown_sha256 FROM {SCHEMA}.deliveries WHERE query_id = %s "
                             f"ORDER BY delivery_id", (q[0],)).fetchall()
        return {"query_id": q[0], "state": q[1], "status": q[2], "reason": q[3], "record_id": q[4],
                "app_error": q[5], "deliveries": [{"outcome": x[0], "shown_sha256": x[1]} for x in d]}

    def page_texts(at) -> list[str]:
        out = []
        for kind in ("markdown", "caption", "text", "info", "warning", "error", "success"):
            out += [e.value for e in getattr(at, kind)]
        return out

    cases = []

    # 1. verifier outage
    class VerifierOutage:
        def __call__(self, question, *, config, log_dir):
            return gen.generate(question, retriever=T.FakeRetriever(["nta2025:s148"]),
                                llm=T.ScriptLLM(T.final([(T.VAT_CLAIM[0], ["P1"])])), store=T.STORE, config=config,
                                log_dir=log_dir, verifier=DownVerifier(), tagger=T.FakeTagger([]))

    at = ask(make_services(VerifierOutage()), T.E01)
    card = at.session_state["entries"][-1]["card"]
    cases.append({"case": "verifier_outage", "design": "Section 9: Verifier outage", "reference": card.reference,
                  "expected": "status error / reason verification_unavailable; the card shows the pipeline's error text; delivery shown",
                  "card_status": card.status, "card_reason": card.refusal_reason, "card_text_first_line": card.text.split("\n")[0],
                  "row": facts(card.reference)})

    # 2. embedding failure inside generate() (raises, no record)
    def embedding_down(question, *, config, log_dir):
        raise OSError("embedding service unreachable")

    at = ask(make_services(embedding_down), T.E01)
    card = at.session_state["entries"][-1]["card"]
    shown = " ".join(page_texts(at))
    cases.append({"case": "embedding_failure", "design": "Section 9: Embedding or database failure inside generate()",
                  "reference": card.reference,
                  "expected": "status app_error, record_id NULL, app_error = exception type and message through redact(); user sees ERROR_TEXT + disclaimer, no exception text; delivery shown",
                  "card_status": card.status, "shows_error_text": APP_ERROR_TEXT in shown,
                  "exception_text_on_page": "embedding service unreachable" in shown, "row": facts(card.reference)})

    # 3. close fails twice
    wrapped = FailingStore(store, close_failures=2)
    at = ask(make_services(FakeGenerate(), real_store=wrapped), T.E01)
    entry = at.session_state["entries"][-1]
    shown = " ".join(page_texts(at))
    cases.append({"case": "close_failure", "design": "Section 9: Close fails twice", "reference": entry["reference"],
                  "expected": "row stays received; delivery withheld_log_failure; user sees WITHHELD_TEXT with the reference; the answer text is not on the page",
                  "entry_kind": entry["kind"], "withheld_text_shown": entry["text"] == ui.WITHHELD_TEXT.format(reference=entry["reference"]),
                  "answer_text_on_page": "at the rate of 7.5%" in shown, "row": facts(entry["reference"])})

    # 4. UI timeout (wait shortened to 0.4 s; the worker is held, then released)
    release = threading.Event()
    slow = FakeGenerate(before=lambda: release.wait(20))
    at = ask(make_services(slow, ui_wait_s=0.4, poll_s=0.05, stage_seconds=(0.1, 0.2)), T.E01)
    entry = at.session_state["entries"][-1]
    at_timeout = facts(entry["reference"])
    release.set()
    deadline = time.time() + 30
    while time.time() < deadline and facts(entry["reference"])["state"] != "final":
        time.sleep(0.2)
    cases.append({"case": "ui_timeout", "design": "Section 9: UI wait over 120 s (wait shortened to 0.4 s for the test)",
                  "reference": entry["reference"],
                  "expected": "the timeout text with the reference; delivery ui_timeout; the worker still finishes: row final",
                  "timeout_text_shown": entry["text"] == ui.TIMEOUT_TEXT.format(reference=entry["reference"]),
                  "row_at_timeout": at_timeout, "row": facts(entry["reference"])})

    doc = {"schema": "fault-cases/1", "run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "audit_schema": SCHEMA, "jsonl_dir": "logs/generation_faults", "paid_calls": 0, "cases": cases}
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    for c in cases:
        row = c["row"]
        print(c["case"], c["reference"], row["state"], row["status"], row["reason"],
              [d["outcome"] for d in row["deliveries"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
