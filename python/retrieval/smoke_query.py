"""Phase 3 smoke check: eight fixed questions against the loaded vectors (exact cosine search).

    .venv\\Scripts\\python python\\retrieval\\smoke_query.py [--model openai/text-embedding-3-small]

The questions and expected sections were fixed before any result was seen and are not tuned.
Only question 1 gates the phase (nta2025 s186 in the top 5); the others are reported as found.
Phase 4 does the measured evaluation. Each question is embedded with the API (needs
EMBEDDING_API_KEY in .env, never printed).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.clients import DEFAULT_MODEL, EmbeddingClient, get_client  # noqa: E402
from embed.load import vector_literal  # noqa: E402

TOP_K = 5

# (question, expected) where expected lists section ids ("nta2025:s186" matches the whole section
# and its subsection chunks) or exact chunk ids (the s202 definition of "small company").
QUESTIONS = [
    ("Which supplies are exempt from VAT?", ["nta2025:s186"]),
    ("What is the VAT rate?", ["nta2025:s148"]),
    ("What tax rate does a small company pay?", ["nta2025:s56", "nta2025:s202:def:small-company"]),
    ("What is the development levy on companies?", ["nta2025:s59"]),
    ("Are basic food items zero-rated for VAT?", ["nta2025:s187"]),
    ("How do I object to a tax assessment and how long do I have?", ["ntaa2025:s41"]),
    ("What is the penalty for failing to register for tax?", ["ntaa2025:s100"]),
    ("What is the penalty for failing to file tax returns?", ["ntaa2025:s101"]),
]
GATE_QUESTION = 0


def matches(chunk_id: str, expected: str) -> bool:
    """True if `chunk_id` is the expected chunk, or a subsection chunk of the expected section."""
    return chunk_id == expected or chunk_id.startswith(expected + "(")


def top_k(conn, query_vector: list[float], model: str, k: int = TOP_K) -> list[tuple[str, float]]:
    """Nearest chunks by cosine distance, exact scan over one model's vectors."""
    rows = conn.execute(
        "SELECT chunk_id, embedding <=> %s::vector AS distance FROM chunk_embeddings "
        "WHERE model = %s ORDER BY distance, chunk_id LIMIT %s",
        (vector_literal(query_vector), model, k)).fetchall()
    return [(chunk_id, float(distance)) for chunk_id, distance in rows]


def run(conn, client: EmbeddingClient, k: int = TOP_K) -> list[dict]:
    results = []
    for question, expected in QUESTIONS:
        hits = top_k(conn, client.embed_query(question), client.model, k)
        rank = next((i for i, (chunk_id, _) in enumerate(hits, start=1)
                     if any(matches(chunk_id, e) for e in expected)), None)
        results.append({"question": question, "expected": expected, "hits": hits, "rank": rank})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    client = get_client(args.model)
    with connect() as conn:
        results = run(conn, client)
    for i, result in enumerate(results, start=1):
        found = f"rank {result['rank']}" if result["rank"] else "not in top 5"
        print(f"Q{i}. {result['question']}  expected {' or '.join(result['expected'])}: {found}")
        for rank, (chunk_id, distance) in enumerate(result["hits"], start=1):
            print(f"    {rank}. {chunk_id}  (cosine distance {distance:.4f})")
    print(f"query embedding: {client.requests} requests, {client.tokens_used} tokens reported, "
          f"cost ${client.tokens_used * client.usd_per_million_tokens / 1_000_000:.6f}")
    gate = results[GATE_QUESTION]["rank"] is not None
    print("exit gate (Q1, nta2025 s186 in top 5):", "PASS" if gate else "FAIL")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
