"""Shared fixtures for the Phase 9 audit and app-service tests (interface design, Section 10).

- `no_paid_clients` (autouse in every module that imports it): building an Anthropic or embedding
  client raises, so an accidental paid call fails the test instead of spending money.
- `audit_db`: the audit migrations applied to a throwaway schema `test_audit_<hex>` with throwaway
  group roles, against the local container. Fails, not skips, when the database is down. The schema
  and the roles are removed afterwards; the corpus tables in public are never touched.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
import pytest

from audit.migrate import Names, migrate
from audit.store import AuditAdmin, AuditStore
from check_pgvector import connect


class PaidClientBuilt(AssertionError):
    pass


def _refuse(*args, **kwargs):
    raise PaidClientBuilt("a test built a real model or embedding client")


@pytest.fixture(autouse=True)
def no_paid_clients(monkeypatch):
    import anthropic
    import openai

    from embed import clients
    from generation import llm

    monkeypatch.setattr(llm.AnthropicLLMClient, "__init__", _refuse)
    monkeypatch.setattr(clients.OpenAIEmbeddingClient, "__init__", _refuse)
    monkeypatch.setattr(clients.BgeSmallEmbeddingClient, "__init__", _refuse)
    monkeypatch.setattr(anthropic.Anthropic, "__init__", _refuse)
    monkeypatch.setattr(openai.OpenAI, "__init__", _refuse)


def db_connect() -> psycopg.Connection:
    try:
        return connect()
    except psycopg.OperationalError as exc:
        pytest.fail(f"local pgvector database unreachable on the configured port: {type(exc).__name__}")


@dataclass
class AuditDb:
    conn: psycopg.Connection           # owner connection, autocommit
    names: Names
    store: AuditStore                  # the app's calls, made here as the owner
    admin: AuditAdmin
    hex: str

    def role(self, suffix: str) -> str:
        return f"test_{suffix}_{self.hex}"

    def q(self, table: str) -> str:
        return f"{self.names.schema}.{table}"

    def start_id(self) -> int:
        return self.store.record_start(
            deployment="test", git_commit=None, app_version="app/1", audit_schema="audit-schema/1",
            python_version="3.12", streamlit_version="1.66.0", config_file="eval/tools/config_p7.json",
            config_sha256="0" * 64, corpus_load_id=None, corpus_snapshot=None, chunks_sha256=None,
            outcome="ok", problems=[])

    def owner_query_row(self, start_id: int, *, received_at: datetime | None = None, tier: str = "local",
                        user_key: str | None = None, session_key: str = "s", state: str = "received",
                        cost_reserved: str = "0.05", total_cost: str | None = None) -> str:
        """Insert a query row directly as the owner (to set received_at; the trigger allows inserts)."""
        query_id = str(uuid.uuid4())
        received_at = received_at or datetime.now(timezone.utc)
        final = state == "final"
        self.conn.execute(
            f"INSERT INTO {self.q('queries')} (query_id, record_id, start_id, state, received_at, finished_at, "
            "tier, user_key, session_key, question, question_chars, cost_reserved_usd, status, total_cost_usd) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'q', 1, %s, %s, %s)",
            (query_id, str(uuid.uuid4()) if final else None, start_id, state, received_at,
             received_at if final else None, tier, user_key, session_key, cost_reserved,
             "answered" if final else None, total_cost))
        return query_id


def drop_role(conn, role: str) -> None:
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        conn.execute(f"DROP OWNED BY {role}")
        conn.execute(f"DROP ROLE {role}")


@pytest.fixture
def audit_db():
    conn = db_connect()
    conn.autocommit = True
    hex_ = uuid.uuid4().hex[:10]
    names = Names(f"test_audit_{hex_}", f"test_app_{hex_}", f"test_rev_{hex_}")
    extra_roles = [f"test_none_{hex_}"]
    try:
        migrate(conn, names)
        yield AuditDb(conn, names, AuditStore(connect, names.schema), AuditAdmin(connect, names.schema), hex_)
    finally:
        try:
            conn.execute("RESET ROLE")
            conn.execute(f"DROP SCHEMA IF EXISTS {names.schema} CASCADE")
            for role in (names.app_role, names.review_role, *extra_roles):
                drop_role(conn, role)
        finally:
            conn.close()
