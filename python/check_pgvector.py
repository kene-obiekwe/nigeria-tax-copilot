"""Check that the local pgvector database (docker-compose.yml, port 5434) works.

Loads .env, connects, enables the extension, then runs a nearest-neighbour query on a TEMP
table. The connection string and password are never printed.
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

ROWS = [("a", "[1,0,0]"), ("b", "[0,1,0]"), ("c", "[0.9,0.1,0]")]
QUERY_VECTOR = "[1,0,0]"


def connect() -> psycopg.Connection:
    load_dotenv(ROOT / ".env")
    if os.getenv("DATABASE_URL"):
        return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5)
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5434")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        connect_timeout=5,
    )


def run_check() -> tuple[str, list[tuple[str, float]]]:
    """Return (pgvector version, [(label, distance), ...] ordered nearest first)."""
    with connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        version = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()[0]
        conn.execute("CREATE TEMP TABLE items (label text, embedding vector(3))")
        for label, vec in ROWS:
            conn.execute("INSERT INTO items VALUES (%s, %s::vector)", (label, vec))
        result = conn.execute(
            "SELECT label, embedding <-> %s::vector AS dist FROM items ORDER BY dist",
            (QUERY_VECTOR,),
        ).fetchall()
    return version, [(label, float(dist)) for label, dist in result]


if __name__ == "__main__":
    version, result = run_check()
    print(f"pgvector version: {version}")
    print(f"nearest to {QUERY_VECTOR}:")
    for label, dist in result:
        print(f"  {label}: {dist:.4f}")
