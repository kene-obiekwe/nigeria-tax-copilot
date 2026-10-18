import psycopg
import pytest

from check_pgvector import run_check


def test_pgvector_nearest_neighbour():
    try:
        version, result = run_check()
    except psycopg.OperationalError as exc:
        pytest.fail(f"local pgvector database unreachable on the configured port: {type(exc).__name__}")
    assert version
    assert [label for label, _ in result] == ["a", "c", "b"]
    assert result[0][1] == pytest.approx(0.0)
