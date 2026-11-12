"""Admission limits, the cost cap, retention and erasure in the database (interface design, Sections
4.6, 6.1, 7.3, 8 and 10).

Against the local container on a throwaway schema. No paid call."""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg import errors

from audit_testlib import audit_db, db_connect, no_paid_clients  # noqa: F401
from interface.identity import user_key as make_key

KEY_A = make_key(b"test-secret", "https://issuer.test", "user-a")
KEY_B = make_key(b"test-secret", "https://issuer.test", "user-b")
NO_LIMITS: dict = {}


def admit(db, start_id, *, tier="local", user_key=None, session_key="s1", limits=NO_LIMITS, reserve="0.05",
          question="What is the VAT rate?"):
    query_id = str(uuid.uuid4())
    admission = db.store.open_query(query_id=query_id, start_id=start_id, tier=tier, user_key=user_key,
                                    session_key=session_key, question=question, reserve_usd=Decimal(reserve),
                                    limits=limits)
    return query_id, admission


def close(db, query_id, question="What is the VAT rate?", total="0.0100"):
    db.store.close_query(query_id, {"question": question, "query": {
        "record_id": str(uuid.uuid4()), "status": "answered", "rendered": "r", "total_cost_usd": float(total)},
        "passages": [{"label": "P1", "rank": 1, "chunk_id": "nta2025:s148", "citation": "c", "context_header": "h",
                      "origin": "retrieval", "reasons": [], "disputed": False, "cited": True, "supporting": True,
                      "chunk_text_sha256": "a" * 64}]})


def count(db, table, where="true", args=()):
    return db.conn.execute(f"SELECT count(*) FROM {db.q(table)} WHERE {where}", args).fetchone()[0]


def admission_counts(db):
    return {(t, r): n for t, r, n in db.conn.execute(f"SELECT tier, reason, n FROM {db.q('admission_counts')}")}


def day_start(db) -> datetime:
    return db.conn.execute(f"SELECT {db.names.schema}.wat_day_start(now())").fetchone()[0]


# Counts and the cap ------------------------------------------------------------------------------------


def test_the_per_session_limit_and_admission_counts(audit_db):
    start = audit_db.start_id()
    limits = {"per_session": 2}
    (_, a1), (_, a2), (_, a3) = (admit(audit_db, start, tier="anonymous", limits=limits) for _ in range(3))
    assert (a1.admitted, a1.remaining) == (True, 1) and (a2.admitted, a2.remaining) == (True, 0)
    assert (a3.admitted, a3.reason, a3.remaining) == (False, "session_limit", 0)
    _, other = admit(audit_db, start, tier="anonymous", session_key="s2", limits=limits)
    assert other.admitted
    admit(audit_db, start, tier="anonymous", limits=limits)
    assert admission_counts(audit_db) == {("anonymous", "session_limit"): 2}
    assert count(audit_db, "queries") == 3  # a refusal stores nothing but the count


def test_the_anonymous_pool_and_the_per_user_limit(audit_db):
    start = audit_db.start_id()
    assert audit_db.store.touch_user(KEY_A, "google") == "signed_in"
    pool = {"anonymous_pool_day": 2, "per_session": 10}
    assert admit(audit_db, start, tier="anonymous", session_key="a", limits=pool)[1].admitted
    assert admit(audit_db, start, tier="anonymous", session_key="b", limits=pool)[1].admitted
    refused = admit(audit_db, start, tier="anonymous", session_key="c", limits=pool)[1]
    assert (refused.admitted, refused.reason) == (False, "anonymous_pool")
    per_user = {"per_user_day": 1}
    assert admit(audit_db, start, tier="signed_in", user_key=KEY_A, limits=per_user)[1].admitted
    second = admit(audit_db, start, tier="signed_in", user_key=KEY_A, limits=per_user)[1]
    assert (second.admitted, second.reason) == (False, "daily_limit")
    assert admission_counts(audit_db) == {("anonymous", "anonymous_pool"): 1, ("signed_in", "daily_limit"): 1}
    with pytest.raises(errors.InvalidParameterValue):  # a per-user limit with no user key
        admit(audit_db, start, tier="anonymous", limits=per_user)


def test_the_cost_cap_counts_reserves_and_real_costs_and_is_checked_first(audit_db):
    start = audit_db.start_id()
    cap = {"cost_cap_day_usd": "0.10", "per_session": 1}
    q1, a1 = admit(audit_db, start, session_key="x", limits=cap)       # 0.05 reserved
    assert a1.admitted
    q2, a2 = admit(audit_db, start, session_key="y", limits=cap)       # 0.10 reserved: at the cap, allowed
    assert a2.admitted
    blocked = admit(audit_db, start, session_key="x", limits=cap)[1]   # session limit too: the cap wins
    assert (blocked.admitted, blocked.reason) == (False, "cost_cap")
    close(audit_db, q1, total="0.0100")                                # 0.01 real + 0.05 reserved = 0.06
    assert audit_db.store.spend_since(day_start(audit_db)) == Decimal("0.0600")
    again = admit(audit_db, start, session_key="z", limits=cap)[1]     # 0.06 + 0.05 = 0.11 > 0.10
    assert (again.admitted, again.reason) == (False, "cost_cap")
    assert admission_counts(audit_db) == {("local", "cost_cap"): 2}


def test_the_cap_arithmetic(audit_db):
    start = audit_db.start_id()
    cap = {"cost_cap_day_usd": "0.10"}
    q1, _ = admit(audit_db, start, limits=cap)          # reserve 0.05
    close(audit_db, q1, total="0.0100")                 # spend 0.01
    assert admit(audit_db, start, limits=cap)[1].admitted       # 0.01 + 0.05 = 0.06 <= 0.10
    third = admit(audit_db, start, limits=cap)[1]               # 0.06 + 0.05 = 0.11 > 0.10
    assert (third.admitted, third.reason) == (False, "cost_cap")
    assert audit_db.store.spend_since(day_start(audit_db)) == Decimal("0.0600")
    assert audit_db.store.count_since("all", None, day_start(audit_db)) == 2
    # A received row whose close failed keeps counting at its reserve; a free reserve of 0 is allowed.
    assert admit(audit_db, start, limits={"cost_cap_day_usd": "0.06"}, reserve="0")[1].admitted
    with pytest.raises(errors.InvalidParameterValue):
        admit(audit_db, start, reserve="-0.01")


def test_two_concurrent_admissions_for_the_last_slot_admit_exactly_one(audit_db):
    start = audit_db.start_id()
    limits = {"anonymous_pool_day": 1}
    s = audit_db.names.schema
    first = db_connect()
    results = {}

    def second():
        results["second"] = admit(audit_db, start, tier="anonymous", session_key="b", limits=limits)[1]

    try:
        with first.transaction():  # holds the advisory lock and its uncommitted row
            row = first.execute(
                f"SELECT * FROM {s}.open_query(%s::uuid, %s, 'anonymous', NULL, 'a', 'q', 0.05, %s::jsonb)",
                (str(uuid.uuid4()), start, '{"anonymous_pool_day": 1}')).fetchone()
            assert row[0] is True
            thread = threading.Thread(target=second)
            thread.start()
            time.sleep(0.8)
            assert thread.is_alive(), "the second admission did not wait for the lock"
        thread.join(timeout=10)
    finally:
        first.close()
    assert (results["second"].admitted, results["second"].reason) == (False, "anonymous_pool")
    assert count(audit_db, "queries") == 1


def test_the_limit_day_starts_at_midnight_west_africa_time(audit_db):
    s = audit_db.names.schema
    f = f"{s}.wat_day_start"
    assert audit_db.conn.execute(f"SELECT {f}('2026-11-12 23:30:00+00')").fetchone()[0] == datetime(
        2026, 11, 12, 23, 0, tzinfo=timezone.utc)
    assert audit_db.conn.execute(f"SELECT {f}('2026-11-12 22:59:59+00')").fetchone()[0] == datetime(
        2026, 11, 11, 23, 0, tzinfo=timezone.utc)
    start = audit_db.start_id()
    boundary = day_start(audit_db)
    audit_db.owner_query_row(start, received_at=boundary - timedelta(seconds=1), session_key="w")
    audit_db.owner_query_row(start, received_at=boundary + timedelta(seconds=1), session_key="w")
    assert audit_db.store.count_since("session", "w", boundary) == 1
    assert admit(audit_db, start, session_key="w", limits={"per_session": 2})[1].admitted
    assert not admit(audit_db, start, session_key="w", limits={"per_session": 2})[1].admitted


def test_count_since_kinds(audit_db):
    start = audit_db.start_id()
    audit_db.store.touch_user(KEY_A, "google")
    admit(audit_db, start, tier="anonymous", session_key="a")
    admit(audit_db, start, tier="signed_in", user_key=KEY_A, session_key="b")
    since = day_start(audit_db)
    assert [audit_db.store.count_since(k, key, since) for k, key in (
        ("session", "a"), ("user", KEY_A), ("anonymous_pool", None), ("all", None))] == [1, 1, 1, 2]
    with pytest.raises(errors.InvalidParameterValue):
        audit_db.store.count_since("ip", "x", since)


# Users and history -------------------------------------------------------------------------------------


def test_touch_user_stores_only_a_hex_key_and_returns_the_tier(audit_db):
    assert audit_db.store.touch_user(KEY_A, "google") == "signed_in"
    audit_db.conn.execute(f"UPDATE {audit_db.q('users')} SET tier = 'reviewer' WHERE user_key = %s", (KEY_A,))
    assert audit_db.store.touch_user(KEY_A, "google") == "reviewer"
    for bad in ("someone@example.com", "1234567890", KEY_A.upper()):
        with pytest.raises(errors.InvalidParameterValue):
            audit_db.store.touch_user(bad, "google")
    assert count(audit_db, "users") == 1


def test_user_history_returns_only_the_key_passed(audit_db):
    start = audit_db.start_id()
    for key in (KEY_A, KEY_B):
        audit_db.store.touch_user(key, "google")
    qa, _ = admit(audit_db, start, tier="signed_in", user_key=KEY_A, question="A's question?")
    close(audit_db, qa, question="A's question?")
    admit(audit_db, start, tier="signed_in", user_key=KEY_A, question="A's unfinished?")  # received: not listed
    qb, _ = admit(audit_db, start, tier="signed_in", user_key=KEY_B, question="B's question?")
    close(audit_db, qb, question="B's question?")
    (item,) = audit_db.store.user_history(KEY_A)
    assert (item.query_id, item.question, item.status, item.reference) == (qa, "A's question?", "answered",
                                                                           "Q-" + qa.replace("-", "")[:8])
    assert item.cited_passages == [{"label": "P1", "chunk_id": "nta2025:s148", "citation": "c",
                                    "context_header": "h", "disputed": False}]
    assert [i.question for i in audit_db.store.user_history(KEY_B)] == ["B's question?"]
    assert audit_db.store.user_history("0" * 64) == []


def test_erase_user_removes_only_that_users_rows(audit_db):
    start = audit_db.start_id()
    for key in (KEY_A, KEY_B):
        audit_db.store.touch_user(key, "google")
    qa, _ = admit(audit_db, start, tier="signed_in", user_key=KEY_A)
    close(audit_db, qa)
    audit_db.store.add_delivery(qa, "shown", None)
    qb, _ = admit(audit_db, start, tier="signed_in", user_key=KEY_B)
    close(audit_db, qb)
    anon, _ = admit(audit_db, start, tier="anonymous")
    assert audit_db.store.erase_user(KEY_A) == 1
    assert count(audit_db, "users") == 1 and count(audit_db, "queries") == 2
    for table in ("passages", "deliveries"):
        assert count(audit_db, table, "query_id = %s", (qa,)) == 0
    assert count(audit_db, "passages", "query_id = %s", (qb,)) == 1


def test_the_delete_flag_is_off_again_after_an_erase(audit_db):
    """erase_user switches the trigger's delete flag off before it returns, so a later DELETE in the
    same transaction is refused again."""
    start = audit_db.start_id()
    audit_db.store.touch_user(KEY_A, "google")
    keep = audit_db.owner_query_row(start)
    s = audit_db.names.schema
    with db_connect() as conn, pytest.raises(errors.InsufficientPrivilege, match="deleted only by"):
        with conn.transaction():
            conn.execute(f"SELECT {s}.erase_user(%s)", (KEY_A,))
            conn.execute(f"DELETE FROM {s}.queries WHERE query_id = %s", (keep,))
    assert count(audit_db, "queries", "query_id = %s", (keep,)) == 1


# Retention and erasure (owner only) ----------------------------------------------------------------------


def test_purge_expired_deletes_only_old_rows_and_refuses_fewer_than_90_days(audit_db):
    start = audit_db.start_id()
    now = datetime.now(timezone.utc)
    old = audit_db.owner_query_row(start, received_at=now - timedelta(days=100), state="final", total_cost="0.01")
    audit_db.conn.execute(
        f"INSERT INTO {audit_db.q('passages')} (query_id, label, rank, chunk_id, citation, context_header, origin, "
        "reasons, disputed, cited, supporting, chunk_text_sha256) VALUES (%s, 'P1', 1, 'c', 'c', 'h', 'retrieval', "
        "'{}', false, false, false, 'x')", (old,))
    recent = audit_db.owner_query_row(start, received_at=now - timedelta(days=10), state="final", total_cost="0.01")
    audit_db.conn.execute(f"INSERT INTO {audit_db.q('users')} (user_key, provider, last_seen) VALUES "
                          "(%s, 'google', now() - interval '120 days'), (%s, 'google', now())", (KEY_A, KEY_B))
    audit_db.conn.execute(f"INSERT INTO {audit_db.q('admission_counts')} (day, tier, reason, n) VALUES "
                          "(current_date - 500, 'anonymous', 'session_limit', 3), "
                          "(current_date - 10, 'anonymous', 'session_limit', 1)")
    for days in (89, 0, -1):
        with pytest.raises(errors.InvalidParameterValue, match="at least 90 days"):
            audit_db.admin.purge_expired(days)
    assert audit_db.admin.purge_expired(90) == {"queries": 1, "users": 1, "admission_counts": 1}
    assert [str(q) for (q,) in audit_db.conn.execute(f"SELECT query_id FROM {audit_db.q('queries')}")] == [recent]
    assert count(audit_db, "passages", "query_id = %s", (old,)) == 0
    assert count(audit_db, "users") == 1 and count(audit_db, "app_starts") == 1


def test_erase_query_removes_one_query_and_its_children(audit_db):
    start = audit_db.start_id()
    q1, _ = admit(audit_db, start)
    close(audit_db, q1)
    audit_db.store.add_delivery(q1, "shown", None)
    audit_db.store.add_feedback(q1, "s1", 1, None)
    q2, _ = admit(audit_db, start)
    close(audit_db, q2)
    assert audit_db.admin.erase_query(q1) is True
    assert audit_db.admin.erase_query(q1) is False
    for table in ("queries", "passages", "deliveries", "feedback"):
        assert count(audit_db, table, "query_id = %s", (q1,)) == 0, table
    assert count(audit_db, "queries", "query_id = %s", (q2,)) == 1
