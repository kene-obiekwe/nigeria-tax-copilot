"""Phase 8 end-to-end runner (D-133 to D-142; evaluation design, Sections 5, 7 and 10).

    .venv\\Scripts\\python eval\\e2e\\run_e2e.py --status                  # free: pins, freeze, records, ledger
    .venv\\Scripts\\python eval\\e2e\\run_e2e.py --freeze                  # free: writes config_p8.json once
    .venv\\Scripts\\python eval\\e2e\\run_e2e.py --smoke                   # PAID, once, cap $0.05, 3 tuning cases
    .venv\\Scripts\\python eval\\e2e\\run_e2e.py --run 1                   # PAID: the 90 cases, tools on, cap $1.10
    .venv\\Scripts\\python eval\\e2e\\run_e2e.py --run 1 --rerun-after-void   # PAID: the one re-run after a void run 1

The 90 cases are the 30 retrieval test questions (subset T; ids from `labels_test_v1.jsonl`, text from
`questions_v1.jsonl`, D-135) and the 60 rows of `e2e_v1.jsonl` (subset N and R, D-136). The sealed
files are loaded only through the sha256-checked loader below, kept in memory and never printed: the
console shows ids, statuses and costs only.

**Pins (Section 7.2).** One block, `PINS`. A runner action refuses to start while a pin it needs is
None: `--freeze` needs the four input pins (config_p8.json does not exist before the freeze, so its
own pin cannot be set yet); `--smoke` and `--run` need all five.

**Configuration (Section 5).** `config_p8.json` records `eval/tools/config_p7.json` by sha256 as its
base, with no overrides, the system part of the live configuration (`current_config()` of
`eval/tools/run_exit_check.py` without its `exit_questions` block), the pinned input hashes, the case
order and its seed, and the caps. It is frozen once, before the smoke run. Every smoke and final run
checks that the frozen file, the pins and the live configuration all agree.

**Records (Section 7.2).** Every record is opened with mode "x". A run first writes its marker
`e2e_run<N>_started.json`, then makes its first paid call. A run whose marker exists is refused,
except for the one re-run after a void run. No run record holds a verdict or an automatic check.

**API-error stop (Section 7.4, D-128).** The generation, verifier and tagger clients, the query
embedding call and the database share one stop: the first error stops the run after that case and no
later call is made. The record is then void (`stopped_on_api_error`: case, kind, error by type and
redacted message; review "void"), the run does not count, and the runner exits with code 3. A void
run may be re-run once, `--run N --rerun-after-void`. An unexpected exception in a case is recorded
the same way with kind "unexpected", but such a void run is never re-run automatically: it may be a
system defect that belongs in the result, so Kene and the overseer decide.

**Cost stops (Section 7.5).** Before each case, the run's spend plus the dearest case so far (at
least $0.036) must stay within $1.10. Before any paid call series, the sum of `cost_usd` over every
record under `results/` (a marker without a record counts at its cap) plus this run's cap must stay
within $3.00. A run stopped on cost is incomplete: it has no verdict, does not count, and the runner
exits with code 4.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "eval" / "guardrails"))
sys.path.insert(0, str(ROOT / "eval" / "retrieval"))

import psycopg  # noqa: E402
import run_adversarial  # noqa: E402
import run_eval  # noqa: E402
from check_pgvector import connect  # noqa: E402
from embed.clients import EmbeddingError, get_client  # noqa: E402
from generation import generate as gen  # noqa: E402
from generation.llm import APIError, redact  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402
from tools import taxcalc  # noqa: E402


def _load_exit_check():
    """`eval/tools/run_exit_check.py` under its own module name (the Phase 5 runner is
    `run_exit_check` in sys.modules)."""
    name = "e2e_tools_run_exit_check"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / "tools" / "run_exit_check.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


exit_check = _load_exit_check()
GuardError = exit_check.GuardError
read_record = exit_check.read_record
is_void_record = exit_check.is_void_record
write_record = exit_check.write_record
sha256_file = exit_check.sha256_file
sha256_text = exit_check.sha256_text

RESULTS_DIR = HERE / "results"
CONFIG = HERE / "config_p8.json"
PATHS = {"e2e_v1": HERE / "e2e_v1.jsonl", "labels_test_v1": HERE / "labels_test_v1.jsonl",
         "labels_dev_v1": HERE / "labels_dev_v1.jsonl", "blind_calibration_v1": HERE / "blind_calibration_v1.jsonl",
         "config_p8": CONFIG}
# The pins block (Section 7.2): the sha256 of each file, set from the step 2a values (and, for
# config_p8.json, from the freeze) and recorded in the decision log. None means "not set yet".
PINS: dict[str, str | None] = {
    "e2e_v1": "dcab23f9cd14cfa45734d3029a202a5ec9d46a248d50425d533b29ca27c96d52",
    "labels_test_v1": "03773fc77a6f8ad787130ca6da7baa03d2115dc36b2681d8d9a1912dc92dd3f5",
    "labels_dev_v1": "ad3d89cd228fb5b1225d4a577dc1cf42d2c33e75890e86c06e489b7d8374c94d",
    "blind_calibration_v1": "49afc6dabfd3afbbc4dbcd9d4cfea2fb794f21e77835f37e4ee7d540e5537499",
    "config_p8": None}  # set after the freeze (D-144)
INPUT_PINS = ("e2e_v1", "labels_test_v1", "labels_dev_v1", "blind_calibration_v1")
P7_CONFIG = ROOT / "eval" / "tools" / "config_p7.json"
P7_CONFIG_SHA256 = exit_check.CONFIG_SHA256[1]
DECISION = "D-133 to D-142"
RUN_NUMBERS = (1, 2)
RUN_CAP_USD = 1.10
SMOKE_CAP_USD = 0.05
LEDGER_CAP_USD = 3.00
MIN_CASE_ESTIMATE_USD = 0.036   # the dearest Phase 7 question (design Section 7.5)
CASE_ORDER_SEED = 20261111
EXPECTED_COUNTS = {"T": 30, "N": 20, "R": 40}
SUBSETS = tuple(EXPECTED_COUNTS)
SMOKE_CASE_IDS = ("Q014", "ADV-017", "ADV-001")  # a dev answer, a model decline, a pre-check refusal


# Small helpers -----------------------------------------------------------------------------------


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def parse_rows(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def load_sealed(path: Path, pinned: str | None) -> list[dict]:
    """The rows of a pinned data file. The only way the run code reads a sealed file: it needs the
    pin, checks the sha256 of the bytes it parses, and keeps the rows in memory (never printed)."""
    if not pinned:
        raise GuardError(f"no sha256 is pinned for {path.name} yet")
    if not path.exists():
        raise GuardError(f"{path.name} does not exist")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != pinned:
        raise GuardError(f"{path.name} does not match the pinned sha256 {pinned}")
    rows = parse_rows(data)
    ids = [r.get("id") for r in rows]
    if None in ids or len(set(ids)) != len(ids):
        raise GuardError(f"{path.name} has a missing or duplicate id")
    return rows


# Cases -------------------------------------------------------------------------------------------


def ordered_ids(ids: list[str], seed: int = CASE_ORDER_SEED) -> list[str]:
    """The case order: the sorted ids, shuffled with a fixed seed (recorded in config_p8.json)."""
    out = sorted(ids)
    random.Random(seed).shuffle(out)
    return out


def load_test_questions() -> dict[str, dict]:
    """The retrieval test split by id, through the sha256-checked loader (text never printed)."""
    return {q["id"]: q for q in run_eval.load_questions("test")}


def load_cases(paths: dict | None = None, pins: dict | None = None, *, questions=None,
               expected_counts: dict | None = None) -> list[dict]:
    """The 90 cases in run order: {id, subset, category, question}. T from `labels_test_v1.jsonl` and
    `questions_v1.jsonl`; N and R from `e2e_v1.jsonl`. Raises GuardError on a missing pin, a hash
    mismatch, an id seen twice, an unknown subset or counts other than `expected_counts`."""
    paths, pins = paths or PATHS, pins or PINS
    expected_counts = EXPECTED_COUNTS if expected_counts is None else expected_counts
    new = load_sealed(paths["e2e_v1"], pins.get("e2e_v1"))
    labels = load_sealed(paths["labels_test_v1"], pins.get("labels_test_v1"))
    by_id = (questions or load_test_questions)()
    cases = [{"id": r["id"], "subset": r["subset"], "category": r["category"], "question": r["question"]}
             for r in new]
    for r in labels:
        if r["id"] not in by_id:
            raise GuardError(f"label row {r['id']} has no question in the retrieval test split")
        cases.append({"id": r["id"], "subset": "T", "category": by_id[r["id"]]["category"],
                      "question": by_id[r["id"]]["question"]})
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise GuardError("a case id appears in more than one file")
    if any(c["subset"] not in SUBSETS for c in cases):
        raise GuardError("a case has a subset other than T, N or R")
    counts = {s: sum(c["subset"] == s for c in cases) for s in SUBSETS}
    if expected_counts and counts != expected_counts:
        raise GuardError(f"the case counts are {counts}, not {expected_counts}")
    by_case = {c["id"]: c for c in cases}
    return [by_case[i] for i in ordered_ids(ids)]


def smoke_cases() -> list[dict]:
    """The 3 tuning cases of the smoke run, in this order (dev split and adversarial v1 only; the
    retrieval test split is not opened)."""
    dev = {q["id"]: q for q in run_eval.load_questions("dev")}
    adv = {r["id"]: r for part in ("tune", "holdout") for r in run_adversarial.load_adversarial("v1", part)}
    out = []
    for cid in SMOKE_CASE_IDS:
        row = dev.get(cid) or adv.get(cid)
        if row is None:
            raise GuardError(f"smoke case {cid} not found")
        out.append({"id": cid, "subset": "smoke", "category": row.get("category", "adversarial"),
                    "question": row["question"]})
    return out


# Configuration (Section 5) -----------------------------------------------------------------------


def live_config(rates: taxcalc.RateTable | None = None) -> dict:
    """The live system configuration: the Phase 7 one without its `exit_questions` block."""
    cfg = exit_check.current_config(rates)
    cfg.pop("exit_questions")
    return cfg


def check_p7_base(live: dict) -> None:
    """config_p7.json is the pinned base and still equals the live system (M2)."""
    if not P7_CONFIG.exists() or sha256_file(P7_CONFIG) != P7_CONFIG_SHA256:
        raise GuardError(f"{P7_CONFIG.name} does not match its frozen sha256 {P7_CONFIG_SHA256}")
    p7 = json.loads(P7_CONFIG.read_text(encoding="utf-8"))
    comparable = {k: v for k, v in p7.items() if k not in exit_check.FREEZE_FIELDS and k != "exit_questions"}
    if comparable != live:
        raise GuardError(f"the live configuration differs from {P7_CONFIG.name}")


def expected_config(case_ids: list[str], pins: dict, live: dict) -> dict:
    """What config_p8.json must hold (without `frozen_at` and `decision`)."""
    return {
        "base": {"file": rel(P7_CONFIG), "sha256": P7_CONFIG_SHA256},
        "overrides": {},
        "system": live,
        "inputs": {"questions_v1": {"file": rel(run_eval.QUESTIONS), "sha256": run_eval.QUESTIONS_SHA256},
                   **{name: {"file": rel(PATHS[name]), "sha256": pins[name]} for name in INPUT_PINS}},
        "case_order": {"seed": CASE_ORDER_SEED, "ids": case_ids},
        "caps": {"run_usd": RUN_CAP_USD, "smoke_usd": SMOKE_CAP_USD, "ledger_usd": LEDGER_CAP_USD,
                 "min_case_estimate_usd": MIN_CASE_ESTIMATE_USD},
        "smoke_cases": list(SMOKE_CASE_IDS),
    }


def missing_pins(pins: dict, names) -> list[str]:
    return [n for n in names if not pins.get(n)]


def require_pins(pins: dict, names) -> None:
    missing = missing_pins(pins, names)
    if missing:
        raise GuardError(f"these sha256 pins are not set yet: {', '.join(missing)}")


def verify_input_hashes(paths: dict, pins: dict) -> None:
    for name in INPUT_PINS:
        path = paths[name]
        if not path.exists() or sha256_file(path) != pins[name]:
            raise GuardError(f"{path.name} does not match the pinned sha256")


@dataclass
class Context:
    """Everything a guard reads from outside the code, so tests can replace it. The defaults are the
    live repository."""
    paths: dict = field(default_factory=lambda: dict(PATHS))
    pins: dict = field(default_factory=lambda: dict(PINS))
    results_dir: Path = RESULTS_DIR
    live: Callable[[], dict] = live_config
    questions: Callable[[], dict] | None = None
    expected_counts: dict | None = None
    git: Callable[[], dict] | None = None
    db: Callable[[], None] | None = None
    environ: dict | None = None

    def cases(self) -> list[dict]:
        return load_cases(self.paths, self.pins, questions=self.questions, expected_counts=self.expected_counts)


def git_state() -> dict:
    """{"clean", "commit"}: the working tree outside eval/e2e/results is clean."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", ".", ":(exclude)eval/e2e/results"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return {"clean": not dirty, "commit": commit}


def db_check() -> None:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        raise GuardError(f"the database does not answer on its port ({type(exc).__name__}); is Docker Desktop up?")


def freeze_config(ctx: Context | None = None, *, path: Path | None = None, now: datetime | None = None) -> str:
    """Writes config_p8.json once and returns its sha256. Makes no model call. Needs the four input
    pins (the file's own pin is set after the freeze). The sha256 must then be recorded in PINS and in
    a decision row before the smoke run."""
    ctx = ctx or Context()
    path = path or ctx.paths["config_p8"]
    if path.exists():
        raise GuardError(f"{path.name} already exists; it is frozen once and never edited")
    require_pins(ctx.pins, INPUT_PINS)
    verify_input_hashes(ctx.paths, ctx.pins)
    cases = ctx.cases()
    live = ctx.live()
    check_p7_base(live)
    config = expected_config([c["id"] for c in cases], ctx.pins, live) | {
        "frozen_at": (now or datetime.now(timezone.utc)).isoformat(), "decision": DECISION}
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(config, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return sha256_file(path)


# Records, markers and the ledger (Section 7.2 and 7.5) -------------------------------------------


def result_path(run_no: int, results_dir: Path | None = None, *, rerun: bool = False) -> Path:
    return (results_dir or RESULTS_DIR) / f"e2e_run{run_no}{'_rerun' if rerun else ''}.json"


def marker_path(run_no: int, results_dir: Path | None = None, *, rerun: bool = False) -> Path:
    return (results_dir or RESULTS_DIR) / f"e2e_run{run_no}{'_rerun' if rerun else ''}_started.json"


def smoke_path(results_dir: Path | None = None) -> Path:
    return (results_dir or RESULTS_DIR) / "smoke.json"


def smoke_marker_path(results_dir: Path | None = None) -> Path:
    return (results_dir or RESULTS_DIR) / "smoke_started.json"


def counts_as_run(record: dict | None) -> bool:
    """A complete record: not void and not stopped on cost."""
    return (record is not None and not is_void_record(record) and not record.get("stopped_on_api_error")
            and not record.get("stopped_on_cost") and record.get("review") == "pending")


def counted_run(run_no: int, results_dir: Path | None = None) -> dict | None:
    """The record that counts as run `run_no`: the first unless it is void, else the re-run's."""
    for rerun in (False, True):
        record = read_record(result_path(run_no, results_dir, rerun=rerun))
        if counts_as_run(record):
            return record
    return None


def ledger(results_dir: Path | None = None) -> dict:
    """Phase 8 spend so far: `cost_usd` of every smoke and run record under results/, and for each
    marker with no readable record the cap it was started with (the spend is unknown)."""
    results_dir = results_dir or RESULTS_DIR
    entries = {}
    if results_dir.exists():
        for marker in sorted(results_dir.glob("*_started.json")):
            stem = marker.name[:-len("_started.json")]
            cap = SMOKE_CAP_USD if stem == "smoke" else RUN_CAP_USD
            record = read_record(results_dir / f"{stem}.json")
            cost = record.get("cost_usd") if record else None
            entries[stem] = float(cost) if isinstance(cost, (int, float)) else cap
        for path in sorted(results_dir.glob("*.json")):
            if re.fullmatch(r"smoke|e2e_run\d+(_rerun)?", path.stem) and path.stem not in entries:
                record = read_record(path)
                cost = record.get("cost_usd") if record else None
                entries[path.stem] = float(cost) if isinstance(cost, (int, float)) else RUN_CAP_USD
    return {"entries": entries, "spent_usd": round(sum(entries.values()), 6)}


def check_ledger(results_dir: Path | None, cap_usd: float) -> float:
    spent = ledger(results_dir)["spent_usd"]
    if spent + cap_usd > LEDGER_CAP_USD + 1e-9:
        raise GuardError(f"the Phase 8 ledger is ${spent:.4f}; this call series may cost up to ${cap_usd:.2f}, "
                         f"which would pass the ${LEDGER_CAP_USD:.2f} cap (D-133). Kene and the overseer decide")
    return spent


def check_ready(ctx: Context, *, cap_usd: float) -> dict:
    """The checks every smoke and final run needs before any model call (Section 7.3): all five pins,
    no TAXY_ environment variable, config_p8.json frozen, pinned and equal to the live configuration,
    the ledger, a clean tree and a database that answers. Returns what the record needs."""
    require_pins(ctx.pins, PINS)
    environ = os.environ if ctx.environ is None else ctx.environ
    taxy = sorted(k for k in environ if k.startswith("TAXY_"))
    if taxy:
        raise GuardError(f"unset these environment variables before a paid run: {', '.join(taxy)}")
    verify_input_hashes(ctx.paths, ctx.pins)
    config_path = ctx.paths["config_p8"]
    if not config_path.exists() or sha256_file(config_path) != ctx.pins["config_p8"]:
        raise GuardError(f"{config_path.name} does not match the pinned sha256 {ctx.pins['config_p8']}")
    frozen = json.loads(config_path.read_text(encoding="utf-8"))
    comparable = {k: v for k, v in frozen.items() if k not in exit_check.FREEZE_FIELDS}
    cases = ctx.cases()
    live = ctx.live()
    check_p7_base(live)
    if comparable != expected_config([c["id"] for c in cases], ctx.pins, live):
        raise GuardError(f"the live configuration, the pins or the caps differ from {config_path.name}")
    spent = check_ledger(ctx.results_dir, cap_usd)
    state = (ctx.git or git_state)()
    if not state["clean"]:
        raise GuardError("the working tree is not clean (outside eval/e2e/results); commit first (Section 7.3)")
    (ctx.db or db_check)()
    return {"frozen": frozen, "cases": cases, "ledger_before_usd": spent, "commit": state["commit"]}


def first_stop_kind(record: dict | None) -> str | None:
    return ((record or {}).get("stopped_on_api_error") or {}).get("kind")


def check_run_allowed(run_no: int, ctx: Context | None = None, *, rerun: bool = False) -> dict:
    """The frozen configuration, cases and ledger for run `run_no`, or GuardError before any model call:
    two runs at most (D-137); each once (a marker counts as a run); run 2 only after a run 1 that
    counts; the one re-run only when the first record is a complete void record and no re-run exists."""
    ctx = ctx or Context()
    require_pins(ctx.pins, PINS)
    results_dir = ctx.results_dir
    if run_no not in RUN_NUMBERS:
        raise GuardError("the final evaluation is two runs at most (D-137): run 1 or run 2")
    first = result_path(run_no, results_dir)
    if rerun:
        if result_path(run_no, results_dir, rerun=True).exists() or marker_path(run_no, results_dir, rerun=True).exists():
            raise GuardError(f"the one re-run allowed after a void run {run_no} has already been made")
        if not first.exists():
            raise GuardError(f"a re-run of run {run_no} needs a first run that stopped on an API error; there is none")
        if not is_void_record(read_record(first)):
            raise GuardError(f"run {run_no}'s record is not a complete void record (stopped_on_api_error), "
                             "so it cannot be re-run (D-128)")
        if first_stop_kind(read_record(first)) == "unexpected":
            raise GuardError(f"run {run_no} stopped on an unexpected (non-API) error, which may be a system defect "
                             "that belongs in the result; it is not re-run automatically. Kene and the overseer decide")
    elif first.exists() or marker_path(run_no, results_dir).exists():
        raise GuardError(f"end-to-end run {run_no} has already been started; a run is never repeated "
                         "(after a void run, use --rerun-after-void once)")
    if run_no == 2 and counted_run(1, results_dir) is None:
        raise GuardError("run 2 needs a complete run 1 (a void run does not count)")
    return check_ready(ctx, cap_usd=RUN_CAP_USD)


def check_smoke_allowed(ctx: Context | None = None) -> dict:
    ctx = ctx or Context()
    require_pins(ctx.pins, PINS)
    if smoke_path(ctx.results_dir).exists() or smoke_marker_path(ctx.results_dir).exists():
        raise GuardError("the smoke run has already been made; it is run once (Section 7.5)")
    return check_ready(ctx, cap_usd=SMOKE_CAP_USD)


# The stop (Section 7.4) --------------------------------------------------------------------------


def error_log(exc: BaseException) -> dict:
    """An error as fields, by type and redacted message only (a database or unexpected error gives
    its type: the message may hold a host name or other detail)."""
    if isinstance(exc, APIError):
        return exc.as_log()
    message = redact(str(exc)) if isinstance(exc, EmbeddingError) else None
    return {"error_type": type(exc).__name__, "status_code": getattr(exc, "status_code", None),
            "api_error_type": None, "message": message, "request_id": None}


class RunStop(run_adversarial.APIStop):
    """The one stop of a run: the first error of any kind wins, and `kind` says which."""

    def __init__(self):
        super().__init__()
        self.kind: str | None = None

    def record(self, exc, kind: str = "api") -> None:  # type: ignore[override]
        with self._lock:
            if self.error is None:
                self.error, self.kind = exc, kind

    def log(self) -> dict:
        return {"kind": self.kind, "error": error_log(self.error)}


class KindStop:
    """A view of the shared stop for one model client, so the record names which client failed."""

    def __init__(self, stop: RunStop, kind: str):
        self.stop, self.kind = stop, kind

    @property
    def stopped(self) -> bool:
        return self.stop.stopped

    def record(self, exc) -> None:
        self.stop.record(exc, self.kind)


class StopOnEmbeddingError:
    """The query embedding client with the run's stop: an EmbeddingError stops the run, and every
    later call fails at once, so no embedding call is made after the first error."""

    def __init__(self, client, stop: RunStop):
        self.client, self.stop = client, stop

    def __getattr__(self, name):  # model, dims, tokens_used ...
        return getattr(self.__dict__["client"], name)

    def embed_query(self, text: str):
        if self.stop.stopped:
            raise EmbeddingError("not called: the run stopped at an earlier error")
        try:
            return self.client.embed_query(text)
        except EmbeddingError as exc:
            self.stop.record(exc, "embedding")
            raise

    def embed_documents(self, texts):
        if self.stop.stopped:
            raise EmbeddingError("not called: the run stopped at an earlier error")
        try:
            return self.client.embed_documents(texts)
        except EmbeddingError as exc:
            self.stop.record(exc, "embedding")
            raise


# Running -----------------------------------------------------------------------------------------


def default_clients() -> dict:
    """The live model clients (paid). Imported here so that tests, which pass fakes, never build them."""
    return exit_check.default_clients()


def live_resources(stop: RunStop):
    """The live retriever (with the stopped embedding client), corpus store and a close function.
    Connects on the first case, inside the stop's reach."""
    conn = connect()
    config = gen.retrieval_config()
    retriever = Retriever(conn, config, client=StopOnEmbeddingError(get_client(config["model"]), stop))
    return retriever, gen.DbCorpusStore(conn), conn.close


def default_answer(question: str, config, clients: dict, retriever, store):
    return gen.generate(question, retriever=retriever, store=store, config=config, llm=clients["llm"],
                        verifier=clients["verifier"], tagger=clients["tagger"])


def case_row(case: dict, answer, latency_s: float) -> dict:
    """One case of a run record (Section 3.3). Facts only: no verdict and no automatic check."""
    v = answer.verification
    return {"id": case["id"], "subset": case["subset"], "category": case["category"], "question": case["question"],
            "status": answer.status, "refusal_reason": answer.refusal_reason, "flags": answer.flags,
            "notes": [n.ref for n in answer.notes],
            "claims": [{"no": i, "text": c.text, "chunk_ids": c.chunk_ids} for i, c in enumerate(answer.claims, 1)],
            "gaps": answer.gaps,
            "computations": answer.computations,
            "passages": [{"label": p.label, "chunk_id": p.chunk_id, "reasons": p.reasons} for p in answer.passages],
            "verification": None if v is None else v.summary,
            "tool_uses": [{k: u[k] for k in ("turn", "name", "input", "is_error", "limit_hit", "calculation_id")}
                          for u in (answer.tools or {}).get("tool_uses", [])],
            "disclosures": [d.ref for d in answer.disclosures],
            "rendered": answer.rendered, "cost_usd": round(answer.total_cost_usd, 6),
            "latency_s": round(latency_s, 3), "record_id": answer.record_id, "error": answer.error}


def projected_over_cap(spent: float, costs: list[float], cap: float = RUN_CAP_USD,
                       floor: float = MIN_CASE_ESTIMATE_USD) -> bool:
    return spent + max([floor, *costs]) > cap


def execute(cases: list[dict], *, config, cap_usd: float, floor: float, clients: dict | None = None,
            answer_fn=None, resources=None, echo: Callable[[str], None] = print) -> dict:
    """Answers the cases in order and returns the pieces of a record: rows, spend, the API stop (or
    None) and the cost stop (or None). The generation, verifier and tagger clients, the query
    embedding and the database share one stop; a case that raises is not written."""
    stop = RunStop()
    raw = clients if clients is not None else default_clients()
    wrapped = {name: run_adversarial.StopOnAPIError(client, KindStop(stop, name)) for name, client in raw.items()}
    answer_fn = answer_fn or default_answer
    resources = resources or live_resources
    rows, costs, spent, cost_stop, api_stop, opened = [], [], 0.0, None, None, None

    def stopped(case_id: str) -> dict:
        return {"case": case_id, "cases_written": len(rows), "cases_planned": len(cases), **stop.log()}

    try:
        for case in cases:
            if projected_over_cap(spent, costs, cap_usd, floor):
                cost_stop = {"before": case["id"], "spent_usd": round(spent, 6), "cap_usd": cap_usd}
                break
            try:
                if opened is None:
                    opened = resources(stop)
                retriever, store, _ = opened
                started = time.perf_counter()
                answer = answer_fn(case["question"], config, wrapped, retriever, store)
                latency = time.perf_counter() - started
            except psycopg.Error as exc:
                stop.record(exc, "database")
            except EmbeddingError as exc:
                stop.record(exc, "embedding")
            except Exception as exc:  # noqa: BLE001 - a crash must leave a record (the run has spent money)
                stop.record(exc, "unexpected")
            else:
                rows.append(case_row(case, answer, latency))
                costs.append(answer.total_cost_usd)
                spent += answer.total_cost_usd
                echo(f"{case['id']}: {answer.status}, ${answer.total_cost_usd:.4f}")
            if stop.stopped:
                api_stop = stopped(case["id"])
                break
    finally:
        if opened is not None:
            try:
                opened[2]()
            except Exception:  # noqa: BLE001 - closing a connection must not hide the record
                pass
    return {"rows": rows, "spent": spent, "api_stop": api_stop, "cost_stop": cost_stop}


def build_record(parts: dict, *, header: dict, review_ok: str) -> dict:
    record = dict(header) | {"cost_usd": round(parts["spent"], 6)}
    if parts["api_stop"] is not None:  # void (D-128): the run does not count and its answers are never reviewed
        record |= {"review": "void", "stopped_on_api_error": parts["api_stop"], "stopped_on_cost": parts["cost_stop"]}
    elif parts["cost_stop"] is not None:  # incomplete: no verdict, work stops for Kene and the overseer
        record |= {"review": "incomplete", "stopped_on_api_error": None, "stopped_on_cost": parts["cost_stop"]}
    else:
        record |= {"review": review_ok, "stopped_on_api_error": None, "stopped_on_cost": None}
    return record | {"cases": parts["rows"]}


def generation_config(ctx: Context) -> gen.GenerationConfig:
    return gen.GenerationConfig(tools=gen.ToolsConfig(
        enabled=True, rates=taxcalc.load_rates(), config_file=rel(ctx.paths["config_p8"]),
        config_sha256=ctx.pins["config_p8"]))


def write_marker(path: Path, **fields) -> None:
    write_record(path, {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields})


def run(run_no: int, *, rerun: bool = False, ctx: Context | None = None, clients: dict | None = None,
        answer_fn=None, resources=None, echo: Callable[[str], None] = print) -> dict:
    """A final run, or with `rerun` the one re-run after a void run. Guards first, then the marker,
    then the first paid call."""
    ctx = ctx or Context()
    ready = check_run_allowed(run_no, ctx, rerun=rerun)
    config = generation_config(ctx)  # builds no client
    write_marker(marker_path(run_no, ctx.results_dir, rerun=rerun), run=run_no, rerun_after_void=rerun,
                 config_sha256=ctx.pins["config_p8"], commit=ready["commit"], cap_usd=RUN_CAP_USD)
    started = datetime.now(timezone.utc)
    parts = execute(ready["cases"], config=config, cap_usd=RUN_CAP_USD, floor=MIN_CASE_ESTIMATE_USD, clients=clients,
                    answer_fn=answer_fn, resources=resources, echo=echo)
    header = {"run": run_no, "rerun_after_void": rerun, "decision": DECISION,
              "config": {"file": rel(ctx.paths["config_p8"]), "sha256": ctx.pins["config_p8"],
                         "frozen_at": ready["frozen"]["frozen_at"]},
              "inputs": {n: ctx.pins[n] for n in INPUT_PINS}, "commit": ready["commit"],
              "case_ids": [c["id"] for c in ready["cases"]], "started": started.isoformat(),
              "finished": datetime.now(timezone.utc).isoformat(), "cap_usd": RUN_CAP_USD,
              "ledger_before_usd": ready["ledger_before_usd"]}
    record = build_record(parts, header=header, review_ok="pending")
    write_record(result_path(run_no, ctx.results_dir, rerun=rerun), record)
    return record


def smoke(*, ctx: Context | None = None, cases: list[dict] | None = None, clients: dict | None = None,
          answer_fn=None, resources=None, echo: Callable[[str], None] = print) -> dict:
    """The smoke run (Section 7.5): 3 tuning cases through the live path, once, cap $0.05."""
    ctx = ctx or Context()
    ready = check_smoke_allowed(ctx)
    cases = cases if cases is not None else smoke_cases()
    config = generation_config(ctx)
    write_marker(smoke_marker_path(ctx.results_dir), smoke=True, config_sha256=ctx.pins["config_p8"],
                 commit=ready["commit"], cap_usd=SMOKE_CAP_USD)
    started = datetime.now(timezone.utc)
    parts = execute(cases, config=config, cap_usd=SMOKE_CAP_USD, floor=0.0, clients=clients, answer_fn=answer_fn,
                    resources=resources, echo=echo)
    header = {"smoke": True, "decision": DECISION,
              "config": {"file": rel(ctx.paths["config_p8"]), "sha256": ctx.pins["config_p8"]},
              "commit": ready["commit"], "case_ids": [c["id"] for c in cases], "started": started.isoformat(),
              "finished": datetime.now(timezone.utc).isoformat(), "cap_usd": SMOKE_CAP_USD,
              "ledger_before_usd": ready["ledger_before_usd"]}
    record = build_record(parts, header=header, review_ok="pending")
    write_record(smoke_path(ctx.results_dir), record)
    return record


# Status and command line -------------------------------------------------------------------------


def status(ctx: Context | None = None) -> dict:
    """Free. Pins (set or not), whether each file matches its pin, the freeze, the records and the
    ledger. Prints booleans, counts and hashes only."""
    ctx = ctx or Context()
    files = {}
    for name, path in ctx.paths.items():
        pin = ctx.pins.get(name)
        files[name] = {"exists": path.exists(), "pinned": pin,
                       "matches_pin": bool(pin) and path.exists() and sha256_file(path) == pin}
    out = {"files": files, "missing_pins": missing_pins(ctx.pins, PINS), "runs": {},
           "smoke": {"started": smoke_marker_path(ctx.results_dir).exists(), "recorded": smoke_path(ctx.results_dir).exists()},
           "ledger": ledger(ctx.results_dir) | {"cap_usd": LEDGER_CAP_USD}}
    for n in RUN_NUMBERS:
        out["runs"][n] = {"started": marker_path(n, ctx.results_dir).exists(),
                          "result": result_path(n, ctx.results_dir).exists(),
                          "void": is_void_record(read_record(result_path(n, ctx.results_dir))),
                          "rerun_started": marker_path(n, ctx.results_dir, rerun=True).exists(),
                          "rerun_result": result_path(n, ctx.results_dir, rerun=True).exists(),
                          "counts": counted_run(n, ctx.results_dir) is not None}
    return out


def main(argv=None, *, ctx: Context | None = None, run_fn=None, smoke_fn=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="free: pins, freeze, records and the ledger")
    group.add_argument("--freeze", action="store_true", help="free: write config_p8.json once")
    group.add_argument("--smoke", action="store_true", help="PAID: the smoke run, once, cap $0.05")
    group.add_argument("--run", type=int, choices=RUN_NUMBERS, help="PAID: final run N, cap $1.10")
    ap.add_argument("--rerun-after-void", action="store_true",
                    help="PAID: with --run N, the one re-run after a void run N (D-128)")
    args = ap.parse_args(argv)
    if args.rerun_after_void and not args.run:
        ap.error("--rerun-after-void goes with --run only")
    try:
        if args.status:
            print(json.dumps(status(ctx), indent=1))
            return 0
        if args.freeze:
            print(f"{CONFIG.name} sha256 {freeze_config(ctx)}")
            return 0
        if args.smoke:
            record, label = (smoke_fn or (lambda: smoke(ctx=ctx)))(), "smoke run"
        else:
            record = (run_fn or (lambda n, rerun: run(n, rerun=rerun, ctx=ctx)))(args.run, rerun=args.rerun_after_void)
            label = f"run {args.run}{' (re-run after void)' if args.rerun_after_void else ''}"
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if stop := record["stopped_on_api_error"]:
        error = stop["error"]
        print(f"{label} STOPPED at an error during {stop['case']} ({stop['kind']}; {stop['cases_written']} of "
              f"{stop['cases_planned']} cases written): {error['error_type']} {error['status_code']} "
              f"{error['api_error_type']}: {error['message']}. The record is void (D-128)."
              + (" It stopped on an unexpected error, so it is not re-run automatically: Kene and the overseer decide."
                 if stop["kind"] == "unexpected" else ""), file=sys.stderr)
        return 3
    if cost := record["stopped_on_cost"]:
        print(f"{label} STOPPED on cost before {cost['before']}: ${cost['spent_usd']:.4f} of ${cost['cap_usd']:.2f}. "
              "The record is incomplete and has no verdict; Kene and the overseer decide.", file=sys.stderr)
        return 4
    print(f"{label}: {len(record['cases'])} cases, cost ${record['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
