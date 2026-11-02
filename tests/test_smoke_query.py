"""Phase 3 exit gate with the real embedding API (marker `api`): nta2025 s186 in the top 5 for
"Which supplies are exempt from VAT?". The other seven smoke questions are reported by
python/retrieval/smoke_query.py, not asserted. Vectors load into a throwaway schema from the
local cache where it has them, so a repeat run only pays for the question embeddings."""
import os

import pytest
from dotenv import load_dotenv

from embed import clients, load
from retrieval import smoke_query

load_dotenv(clients.ROOT / ".env")
pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not os.getenv("EMBEDDING_API_KEY"), reason="EMBEDDING_API_KEY is not set in .env"),
]


def test_openai_client_returns_1536_dimensions_and_usage():
    client = clients.OpenAIEmbeddingClient()
    vectors = client.embed_documents(["Value added tax", "Development levy"])
    assert [len(v) for v in vectors] == [1536, 1536]
    assert client.requests == 1 and client.tokens_used > 0


def test_exit_gate_vat_exemption_question_finds_s186(db_schema):
    client = clients.OpenAIEmbeddingClient()
    report = load.load(db_schema, client, log=lambda *_: None)
    assert db_schema.execute("SELECT count(*) FROM chunk_embeddings WHERE model = %s",
                             (client.model,)).fetchone()[0] == 400
    assert report.embeddings_from_cache + report.embeddings_from_api == 400
    results = smoke_query.run(db_schema, client)
    assert all(len(r["hits"]) == 5 for r in results)
    gate = results[smoke_query.GATE_QUESTION]
    assert gate["question"] == "Which supplies are exempt from VAT?"
    assert gate["rank"] is not None, [chunk_id for chunk_id, _ in gate["hits"]]
