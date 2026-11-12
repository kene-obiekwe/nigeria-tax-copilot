"""One question through the real app service, against the live APIs (interface design, Sections 10 and 12).

Marked `api`: it runs only with TAXY_RUN_API_TESTS=1 (D-108) and needs the keys in .env. It uses the real
services (`app.services.build_services()`: the real start-up check, the real audit store on schema `audit`,
the real `generate()`), the page's own card code and `deliver()`. Example question 6 (the First Schedule
question). The question is a real, logged query: `compare_log.py` runs on it afterwards. Cost cap: $0.05.
The test checks the log entry, never the wording of the answer. Fails, not skips, when the database is down.
"""
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from embed import clients  # noqa: E402

load_dotenv(clients.ROOT / ".env")
pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="LLM_API_KEY is not set in .env"),
    pytest.mark.skipif(not os.getenv("EMBEDDING_API_KEY"), reason="EMBEDDING_API_KEY is not set in .env"),
]

CAP_USD = Decimal("0.05")
EXAMPLE_INDEX = 5  # example 6 of app/examples.yml (zero-based): the Schedules example, the building expenditure
#                    question since D-170 (it replaced the first wording, which retrieval missed Table I for)


def test_one_question_through_the_real_service_is_logged_and_delivered(monkeypatch):
    monkeypatch.setenv("TAXY_DEPLOYMENT", "local")
    import yaml
    from app import cards
    from app.services import build_services
    from check_pgvector import connect
    from interface.identity import LOCAL

    question = yaml.safe_load((ROOT / "app" / "examples.yml").read_text(encoding="utf-8"))[EXAMPLE_INDEX]["question"]
    assert "First Schedule" in question

    services = build_services()
    assert services.ok, services.startup.problems
    sub = services.service.submit(question, LOCAL, "test-app-live")
    assert sub.kind == "running", sub.kind
    outcome = services.service.wait(sub, timeout=120)
    assert outcome is not None and outcome.kind == "final" and outcome.showable
    card = cards.card_from_outcome(outcome, question, datetime.now(timezone.utc), services.chunks,
                                   services.config_sha256)
    services.service.deliver(outcome.query_id, "shown_plain" if card.ui_fallback else "shown", card.text)
    print(f"\ntest_app_live reference: {outcome.reference}  query_id: {outcome.query_id}")

    with connect() as conn:
        state, status, cost, record_id = conn.execute(
            "SELECT state, status, total_cost_usd, record_id::text FROM audit.queries WHERE query_id = %s",
            (outcome.query_id,)).fetchone()
        deliveries = [r[0] for r in conn.execute(
            "SELECT outcome FROM audit.deliveries WHERE query_id = %s ORDER BY delivery_id",
            (outcome.query_id,)).fetchall()]
    # The gate checks the log entry, not the answer: in the live check this question came back not_covered,
    # so either status is a correct final row here.
    assert state == "final" and status in ("answered", "not_covered"), (state, status)
    assert record_id == outcome.answer.record_id
    assert deliveries and deliveries[-1] in ("shown", "shown_plain")
    assert Decimal(str(cost)) <= CAP_USD, f"cost {cost} is over the ${CAP_USD} cap"
