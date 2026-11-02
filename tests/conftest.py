import uuid

import psycopg
import pytest

from check_pgvector import connect


@pytest.fixture
def db_schema():
    """A connection whose search_path points at a throwaway schema, dropped afterwards.

    Fails, not skips, when the database is down.
    """
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.fail(f"local pgvector database unreachable on the configured port: {type(exc).__name__}")
    conn.autocommit = True
    schema = f"test_vs_{uuid.uuid4().hex[:10]}"
    # The extension goes in the default schema, never the throwaway one.
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path TO {schema}, public, extensions")
    try:
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()
