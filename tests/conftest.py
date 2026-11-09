import os
import uuid

import psycopg
import pytest

from check_pgvector import connect

# D-108: tests marked `api` call paid APIs. They run only when this variable is "1", whatever keys
# are set or loaded from .env; otherwise they skip with this reason.
API_TESTS_FLAG = "TAXY_RUN_API_TESTS"
API_SKIP_REASON = f"paid API test: set {API_TESTS_FLAG}=1 to run it (D-108)"


def api_tests_enabled(environ=os.environ) -> bool:
    return environ.get(API_TESTS_FLAG) == "1"


def pytest_collection_modifyitems(config, items):
    """Skip every `api` test unless TAXY_RUN_API_TESTS=1 (D-108). Read at collection, after the test
    modules are imported, so a module that loads .env cannot turn its tests on."""
    if api_tests_enabled():
        return
    skip = pytest.mark.skip(reason=API_SKIP_REASON)
    for item in items:
        if item.get_closest_marker("api") is not None:
            item.add_marker(skip)


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
