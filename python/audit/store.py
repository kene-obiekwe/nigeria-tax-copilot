"""Calls to the audit functions (interface design, Sections 4.5, 4.6 and 8).

    store = AuditStore(connect)                     # schema "audit"
    admission = store.open_query(...)               # receipt before any paid call
    store.close_query(query_id, rows.payload())     # one transaction, all or nothing

The app holds no table privileges: every write and read goes through a SECURITY DEFINER function.
Each call opens its own short connection from `connect` and runs in one transaction with a statement
timeout, so a slow or locked database fails the call instead of hanging it. Nothing here logs a
connection string, a key or a question.

`AuditAdmin` holds the owner-only maintenance calls (`purge_expired`, `erase_query`); the app never
builds one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable

from psycopg import sql

STATEMENT_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class Admission:
    admitted: bool
    reason: str | None       # session_limit | daily_limit | anonymous_pool | cost_cap, when refused
    remaining: int | None    # questions left after this one under the tightest count limit; None: no count limit


@dataclass(frozen=True)
class HistoryItem:
    query_id: str
    reference: str
    record_id: str | None
    received_at: datetime
    question: str
    status: str
    rendered: str | None
    cited_passages: list[dict]


def reference(query_id: str) -> str:
    """The user-facing reference: "Q-" and the first 8 hex characters of the query id (Section 3.5)."""
    return "Q-" + str(query_id).replace("-", "")[:8]


class _Calls:
    def __init__(self, connect: Callable, schema: str = "audit"):
        self._connect, self.schema = connect, schema

    def _call(self, function: str, args: tuple, casts: tuple[str, ...], *, rows: bool = False, table: bool = False):
        placeholders = sql.SQL(", ").join(sql.SQL("%s::" + cast) for cast in casts)
        target = sql.SQL("{}.{}").format(sql.Identifier(self.schema), sql.Identifier(function))
        query = (sql.SQL("SELECT * FROM {}({})") if table else sql.SQL("SELECT {}({})")).format(target, placeholders)
        with self._connect() as conn:
            with conn.transaction():
                conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT_MS)))
                cur = conn.execute(query, args)
                return cur.fetchall() if rows else cur.fetchone()


class AuditStore(_Calls):
    """The app role's functions: the ten the app calls (`schema_versions()`, the eleventh, is read by the
    start-up check through `migrate.check_schema`, D-210)."""

    def record_start(self, *, deployment: str, git_commit: str | None, app_version: str, audit_schema: str,
                     python_version: str, streamlit_version: str, config_file: str, config_sha256: str,
                     corpus_load_id: int | None, corpus_snapshot: date | str | None, chunks_sha256: str | None,
                     outcome: str, problems: list[str]) -> int:
        return self._call("record_start", (deployment, git_commit, app_version, audit_schema, python_version,
                                           streamlit_version, config_file, config_sha256, corpus_load_id,
                                           corpus_snapshot, chunks_sha256, outcome, list(problems)),
                          ("text",) * 8 + ("bigint", "date", "text", "text", "text[]"))[0]

    def open_query(self, *, query_id: str, start_id: int, tier: str, user_key: str | None, session_key: str,
                   question: str, reserve_usd: Decimal, limits: dict) -> Admission:
        admitted, reason, remaining = self._call(
            "open_query", (str(query_id), start_id, tier, user_key, session_key, question, Decimal(reserve_usd),
                           json.dumps(limits)),
            ("uuid", "bigint", "text", "text", "text", "text", "numeric", "jsonb"), table=True)
        return Admission(admitted, reason, remaining)

    def close_query(self, query_id: str, payload: dict) -> None:
        self._call("close_query", (str(query_id), json.dumps(payload, ensure_ascii=False)), ("uuid", "jsonb"))

    def add_delivery(self, query_id: str, outcome: str, shown_sha256: str | None) -> int:
        return self._call("add_delivery", (str(query_id), outcome, shown_sha256), ("uuid", "text", "text"))[0]

    def add_feedback(self, query_id: str, session_key: str, rating: int, reason: str | None) -> None:
        self._call("add_feedback", (str(query_id), session_key, int(rating), reason),
                   ("uuid", "text", "integer", "text"))

    def touch_user(self, user_key: str, provider: str) -> str:
        return self._call("touch_user", (user_key, provider), ("text", "text"))[0]

    def user_history(self, user_key: str, limit: int = 50, offset: int = 0) -> list[HistoryItem]:
        rows = self._call("user_history", (user_key, limit, offset), ("text", "integer", "integer"), rows=True,
                          table=True)
        return [HistoryItem(str(q), ref, None if r is None else str(r), at, question, status, rendered, list(cited))
                for q, ref, r, at, question, status, rendered, cited in rows]

    def erase_user(self, user_key: str) -> int:
        return self._call("erase_user", (user_key,), ("text",))[0]

    def spend_since(self, since: datetime) -> Decimal:
        return self._call("spend_since", (since,), ("timestamptz",))[0]

    def count_since(self, kind: str, key: str | None, since: datetime) -> int:
        return self._call("count_since", (kind, key, since), ("text", "text", "timestamptz"))[0]


class AuditAdmin(_Calls):
    """Owner-only maintenance (Sections 4.6 and 6.1). Run as the owner, never by the app."""

    def purge_expired(self, days: int) -> dict:
        return self._call("purge_expired", (int(days),), ("integer",))[0]

    def erase_query(self, query_id: str) -> bool:
        return self._call("erase_query", (str(query_id),), ("uuid",))[0]
