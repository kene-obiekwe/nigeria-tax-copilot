"""One question, from the personal-data screen to a logged answer (interface design, Sections 2, 4.5,
8 and 9; D-150, D-153, D-157, D-160).

    service = AppService(store=AuditStore(connect), gate=LocalGate(), start_id=result.start_id,
                         config=shipped_config(), chunk_hashes=db_chunk_hashes(connect))
    sub = service.submit(question, identity, session_key)    # blocked | unrecorded | limited | running
    out = service.wait(sub, timeout=120)                     # None after the UI wait: delivery ui_timeout
    service.deliver(out, "shown", text_shown)                # what the card displayed

Flow, as Section 2 numbers it:

1. the screen: a match is a hard block; nothing is stored or sent, and no database call is made;
2. `open_query()`: admission and the `received` row in one call; if it fails, the question is not sent;
3. a worker thread runs `generate(question, config=SHIPPED, log_dir=<fresh temp dir>)`;
4. the one `gen-log/3` record is read back from the temp dir and its `record_id` checked against the
   answer's; locally it is appended to logs/generation/ under a process-wide lock;
5. the record is projected to audit rows and `close_query()` writes them in one transaction, retried
   once after 1 second; if it still fails, the answer is withheld (delivery `withheld_log_failure`);
6. the UI shows the answer only if (5) succeeded, and records what it showed with `deliver()`.

Steps 3 to 5 run on a module-level worker pool, so a Streamlit rerun or a closed tab does not stop
them: the work finishes and is logged. A `generate()` that raises before writing a record gives an
`app_error` row with a NULL record_id. A record that is missing, does not match, or cannot be
projected is treated like a failed close: the answer is withheld, never shown unlogged.

The server log gets the reference and the exception type only: never a question, a message text, a
key or a connection string.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from audit.project import APP_ERROR_TEXT, AuditRows, Extras, project, project_app_error
from audit.store import reference
from generation import generate as gen
from generation.audit_log import LOG_DIR, read_records
from interface import screen
from interface.identity import Identity
from interface.limits import Gate, RequestContext, day_start

LOG = logging.getLogger("taxy.service")
ROOT = Path(__file__).resolve().parents[2]
WORKERS = 4
UI_WAIT_S = 120.0
CLOSE_RETRY_DELAY_S = 1.0
FEEDBACK_REASONS = ("wrong_citation", "missing_information", "should_have_answered", "should_not_have_answered",
                    "hard_to_read", "other")
DELIVERY_OUTCOMES = ("shown", "shown_plain", "shown_in_history")  # the UI's own; the service records the others

_JSONL_LOCK = threading.Lock()   # one writer at a time into logs/generation/YYYY-MM-DD.jsonl
_POOL_LOCK = threading.Lock()
_POOL: ThreadPoolExecutor | None = None


def worker_pool() -> ThreadPoolExecutor:
    """The process-wide worker pool (Build B wraps it in st.cache_resource)."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="taxy-worker")
        return _POOL


class RecordError(RuntimeError):
    """The temp dir does not hold exactly one gen-log/3 record for this answer."""


@dataclass(frozen=True)
class Submitted:
    kind: str                         # blocked | unrecorded | limited | running
    query_id: str | None = None
    reference: str | None = None
    reason: str | None = None         # limited: session_limit | daily_limit | anonymous_pool | cost_cap
    remaining: int | None = None
    future: Future | None = None


@dataclass
class Outcome:
    kind: str                         # final | app_error | withheld
    query_id: str
    reference: str
    answer: gen.Answer | None = None
    record: dict | None = None
    rows: AuditRows | None = None
    shown_text: str | None = None     # what the card may show: rendered, or the app_error text
    ui_fallback: bool = False

    @property
    def showable(self) -> bool:
        return self.kind in ("final", "app_error")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def db_chunk_hashes(connect: Callable) -> Callable[[list[str]], dict[str, str]]:
    """`corpus_chunks.text_sha256` for the given chunk ids, read at write time (Section 4.4)."""
    def read(chunk_ids: list[str]) -> dict[str, str]:
        with connect() as conn:
            rows = conn.execute("SELECT chunk_id, text_sha256 FROM corpus_chunks WHERE chunk_id = ANY(%s)",
                                (list(chunk_ids),)).fetchall()
        return dict(rows)
    return read


def read_one_record(log_dir: Path, record_id: str) -> tuple[dict, Path]:
    """The single record generate() wrote into `log_dir`, checked against the answer's record_id."""
    files = sorted(log_dir.glob("*.jsonl"))
    if len(files) != 1:
        raise RecordError(f"expected one log file, found {len(files)}")
    records = read_records(files[0])
    if len(records) != 1:
        raise RecordError(f"expected one record, found {len(records)}")
    if records[0].get("record_id") != record_id:
        raise RecordError("the logged record_id is not the answer's")
    return records[0], files[0]


def append_jsonl(source: Path, jsonl_dir: Path) -> str:
    """Append the temp file's lines, byte for byte, to the same-named day file under the lock.
    Returns the day file's path (relative to the repository when inside it)."""
    target = jsonl_dir / source.name
    data = source.read_bytes()
    if data and not data.endswith(b"\n"):
        data += b"\n"
    with _JSONL_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as fh:
            fh.write(data)
    try:
        return target.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return target.resolve().as_posix()


class AppService:
    def __init__(self, *, store, gate: Gate, start_id: int, config: gen.GenerationConfig,
                 chunk_hashes: Callable[[list[str]], dict[str, str]], generate_fn: Callable | None = None,
                 jsonl_dir: Path | None = LOG_DIR, pool: ThreadPoolExecutor | None = None,
                 parity: Callable[[dict, gen.Answer], bool] | None = None,
                 close_retry_delay_s: float = CLOSE_RETRY_DELAY_S,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """`generate_fn` defaults to the shipped `generate` (it opens its own connection and clients);
        tests pass one with fake clients. `jsonl_dir` is None on the hosted service (D-152). `parity`
        is the card's parity check (Build B); when it returns False the row is flagged `ui_fallback`."""
        self.store, self.gate, self.start_id, self.config = store, gate, start_id, config
        self.chunk_hashes = chunk_hashes
        self.generate_fn = generate_fn or gen.generate
        self.jsonl_dir, self.pool, self.parity = jsonl_dir, pool or worker_pool(), parity
        self.close_retry_delay_s, self.clock = close_retry_delay_s, clock

    # Steps 1 and 2: screen and receipt (script thread) ----------------------------------------------

    def submit(self, question: str, identity: Identity, session_key: str) -> Submitted:
        if screen.blocks(question):
            return Submitted("blocked")  # D-160: not sent, not stored, no database call
        ctx = RequestContext(identity.tier, identity.user_key, session_key, self.clock())
        limits = self.gate.limits_for(ctx)
        query_id = str(uuid.uuid4())
        try:
            admission = self.store.open_query(query_id=query_id, start_id=self.start_id, tier=identity.tier,
                                              user_key=identity.user_key, session_key=session_key, question=question,
                                              reserve_usd=limits.reserve_usd, limits=limits.as_json())
        except Exception as exc:  # noqa: BLE001 - D-153: no receipt, no paid call
            LOG.error("receipt failed (%s); the question was not sent", type(exc).__name__)
            return Submitted("unrecorded")
        if not admission.admitted:
            return Submitted("limited", reason=admission.reason, remaining=0)
        future = self.pool.submit(self._run, query_id, question)
        return Submitted("running", query_id, reference(query_id), remaining=admission.remaining, future=future)

    # Steps 3 to 5: the worker ------------------------------------------------------------------------

    def _run(self, query_id: str, question: str) -> Outcome:
        ref = reference(query_id)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="taxy-gen-") as tmp:
            log_dir = Path(tmp)
            try:
                answer = self.generate_fn(question, config=self.config, log_dir=log_dir)
            except Exception as exc:  # noqa: BLE001 - raised before a record was written (Section 9)
                LOG.error("%s: generate raised %s", ref, type(exc).__name__)
                extras = Extras(self.clock(), int((time.monotonic() - started) * 1000), {},
                                self._keep_records(log_dir))
                rows = project_app_error(question, exc, extras)
                if self._close(query_id, rows):
                    return Outcome("app_error", query_id, ref, rows=rows, shown_text=APP_ERROR_TEXT)
                return Outcome("withheld", query_id, ref, rows=rows)
            latency = int((time.monotonic() - started) * 1000)
            try:
                record, path = read_one_record(log_dir, answer.record_id)
                gen_log_file = append_jsonl(path, self.jsonl_dir) if self.jsonl_dir is not None else None
                fallback = self.parity is not None and not self.parity(record, answer)
                hashes = self.chunk_hashes([p.chunk_id for p in answer.passages]) if answer.passages else {}
                rows = project(record, answer, Extras(self.clock(), latency, hashes, gen_log_file,
                                                      ("ui_fallback",) if fallback else ()))
            except Exception as exc:  # noqa: BLE001 - cannot log it faithfully: withhold (D-153)
                LOG.error("%s: the record could not be projected (%s); answer withheld", ref, type(exc).__name__)
                self._withheld_delivery(query_id)
                return Outcome("withheld", query_id, ref, answer=answer)
        if not self._close(query_id, rows):
            return Outcome("withheld", query_id, ref, answer=answer, record=record, rows=rows)
        return Outcome("final", query_id, ref, answer=answer, record=record, rows=rows, shown_text=answer.rendered,
                       ui_fallback=fallback)

    def _keep_records(self, log_dir: Path) -> str | None:
        """Locally, append whatever a raising generate() left in the temp dir (normally nothing)."""
        if self.jsonl_dir is None:
            return None
        kept = None
        for path in sorted(log_dir.glob("*.jsonl")):
            kept = append_jsonl(path, self.jsonl_dir)
        return kept

    def _close(self, query_id: str, rows: AuditRows) -> bool:
        """close_query, retried once after the delay; on a second failure the answer is withheld."""
        for attempt in (1, 2):
            try:
                self.store.close_query(query_id, rows.payload())
                return True
            except Exception as exc:  # noqa: BLE001
                LOG.error("%s: close attempt %d failed (%s)", reference(query_id), attempt, type(exc).__name__)
                if attempt == 1:
                    time.sleep(self.close_retry_delay_s)
        self._withheld_delivery(query_id)
        return False

    def _withheld_delivery(self, query_id: str) -> None:
        try:
            self.store.add_delivery(query_id, "withheld_log_failure", None)
        except Exception as exc:  # noqa: BLE001 - "if the database allows it"
            LOG.error("%s: the withheld delivery could not be recorded (%s)", reference(query_id), type(exc).__name__)

    # Step 6: waiting and delivering (script thread) ---------------------------------------------------

    def wait(self, submitted: Submitted, timeout: float = UI_WAIT_S) -> Outcome | None:
        """The worker's outcome, or None after `timeout` seconds; then the delivery `ui_timeout` is
        recorded and the worker still finishes and logs (D-157)."""
        try:
            return submitted.future.result(timeout=timeout)
        except FutureTimeout:
            try:
                self.store.add_delivery(submitted.query_id, "ui_timeout", None)
            except Exception as exc:  # noqa: BLE001
                LOG.error("%s: the timeout delivery could not be recorded (%s)", submitted.reference,
                          type(exc).__name__)
            return None

    def deliver(self, query_id: str, outcome: str, shown_text: str) -> None:
        """Record what the card displayed: `shown`, `shown_plain` (parity fallback) or
        `shown_in_history`, with the sha256 of the displayed text."""
        if outcome not in DELIVERY_OUTCOMES:
            raise ValueError(f"the UI records only {DELIVERY_OUTCOMES}")
        self.store.add_delivery(query_id, outcome, sha256_text(shown_text))

    # Signed-in extras, feedback and the limit display ------------------------------------------------

    def history(self, identity: Identity, limit: int = 50, offset: int = 0) -> list:
        """The signed-in user's own final rows; the key comes from the session's identity only."""
        if not identity.signed_in:
            return []
        return self.store.user_history(identity.user_key, limit, offset)

    def open_history_item(self, identity: Identity, item) -> str:
        if not identity.signed_in:
            raise PermissionError("history is for signed-in users")
        self.deliver(item.query_id, "shown_in_history", item.rendered or "")
        return item.rendered or ""

    def erase_history(self, identity: Identity) -> int:
        if not identity.signed_in:
            raise PermissionError("history is for signed-in users")
        return self.store.erase_user(identity.user_key)

    def feedback(self, query_id: str, session_key: str, rating: int, reason: str | None = None) -> None:
        if rating not in (-1, 1) or (reason is not None and reason not in FEEDBACK_REASONS):
            raise ValueError("a rating is -1 or 1, with an optional fixed reason")
        self.store.add_feedback(query_id, session_key, rating, reason)

    def usage_today(self, identity: Identity, session_key: str) -> dict:
        """Today's spend (West Africa Time) and this user's or session's question count."""
        since = day_start(self.clock())
        kind, key = ("user", identity.user_key) if identity.signed_in else ("session", session_key)
        return {"since": since, "spend_usd": self.store.spend_since(since),
                "questions": self.store.count_since(kind, key, since)}
