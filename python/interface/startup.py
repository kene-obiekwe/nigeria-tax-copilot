"""Proving at start-up that the app runs the shipped configuration (interface design, Section 3.8; D-154).

    result = startup_check(deployment="local", store=AuditStore(connect), connect=connect)
    if not result.ok: show UNAVAILABLE_TEXT and keep the question box disabled

Once per process, before the question box is enabled. Every check is free (no model call and no
embedding call):

1. `config_p7_sha256`: eval/tools/config_p7.json has its pinned sha256.
2. `live_config`: `run_exit_check.current_config()` (the hashes of the prompts, schemas, tool
   definitions, policies, retrieval configuration and policy, rate table, model ids, efforts and tagger
   votes, recomputed from the code) equals config_p7.json without its freeze fields, exactly as
   `run_e2e.check_p7_base()` compares them. `rate_table`: the pinned rate file loads.
3. `generation_config`: the `GenerationConfig` the app will pass is the shipped one: tools on, every
   other limit and model at its default, config_file and config_sha256 naming config_p7.
4. Corpus (`corpus_*`, `disclaimer_date`): the latest corpus load has snapshot 2026-10-18 and the
   committed chunk and register hashes; 400 chunks; 400 embeddings for text-embedding-3-small at 1,536
   dimensions; the date inside DISCLAIMER is the snapshot date.
5. `audit_*`: the audit schema is at audit-schema/2, the recorded migration hashes match the files and
   the privileges are as designed (`migrate.check_schema`).
6. Hosted only: `hosted_env` (the key variables are present: checked by name and non-empty only, never
   read out) and `hosted_jsonl` (no JSONL directory is configured).

The result goes into an `app_starts` row. If any check fails the app fails closed, and the failed check
names, with no values, go to the server log and to `app_starts.problems`. If the database cannot even
record the start, `app_starts_write` is added and the app still fails closed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from audit.migrate import SCHEMA_LABEL, Names, check_schema  # noqa: E402
from embed.clients import OpenAIEmbeddingClient  # noqa: E402
from embed.load import BUILD_MANIFEST, CHUNKS, REGISTER  # noqa: E402
from generation.generate import DISCLAIMER, GenerationConfig, ToolsConfig  # noqa: E402
from tools import taxcalc  # noqa: E402

LOG = logging.getLogger("taxy.startup")
APP_VERSION = "app/1"
P7_CONFIG = ROOT / "eval" / "tools" / "config_p7.json"
P7_CONFIG_FILE = "eval/tools/config_p7.json"
P7_CONFIG_SHA256 = "e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25"  # = run_e2e.P7_CONFIG_SHA256
COMPARE_SKIP = ("frozen_at", "decision", "exit_questions")  # as run_e2e.check_p7_base()
EXPECTED_SNAPSHOT = "2026-10-18"
EXPECTED_CHUNKS = 400
EMBEDDING_MODEL = OpenAIEmbeddingClient.model
EMBEDDING_DIMS = OpenAIEmbeddingClient.dims
DEPLOYMENTS = ("local", "hosted", "test")
HOSTED_REQUIRED_ENV = ("DATABASE_URL", "LLM_API_KEY", "EMBEDDING_API_KEY", "TAXY_USER_KEY_SECRET")
JSONL_ENV = "TAXY_JSONL_DIR"
UNAVAILABLE_TEXT = "This service is not available right now. Please try again later."
_DISCLAIMER_DATE = re.compile(r"corpus snapshot (\d{1,2} [A-Z][a-z]+ \d{4})")
_EXIT_CHECK_MODULE = "e2e_tools_run_exit_check"  # the name run_e2e.py loads it under


@dataclass
class StartupResult:
    ok: bool
    problems: list[str]
    start_id: int | None
    corpus_snapshot: str | None = None
    info: dict = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_exit_check():
    """`eval/tools/run_exit_check.py`, imported as run_e2e.py imports it (the Phase 5 runner is
    `run_exit_check` in sys.modules). Neither it nor run_adversarial needs an eval-only package at
    import time."""
    if _EXIT_CHECK_MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(_EXIT_CHECK_MODULE, ROOT / "eval" / "tools" / "run_exit_check.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_EXIT_CHECK_MODULE] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[_EXIT_CHECK_MODULE]
            raise
    return sys.modules[_EXIT_CHECK_MODULE]


def shipped_config(rates: taxcalc.RateTable | None = None) -> GenerationConfig:
    """The configuration the app passes to generate(): the frozen Phase 7 system, as measured in Phase 8."""
    return GenerationConfig(tools=ToolsConfig(enabled=True, rates=rates or taxcalc.load_rates(),
                                              config_file=P7_CONFIG_FILE, config_sha256=P7_CONFIG_SHA256))


def is_shipped(config: GenerationConfig) -> bool:
    tools = config.tools
    if not (tools.enabled and tools.config_file == P7_CONFIG_FILE and tools.config_sha256 == P7_CONFIG_SHA256):
        return False
    if tools.rates is None or tools.rates.sha256 != taxcalc.RATES_SHA256:
        return False
    plain_tools = dataclasses.replace(tools, enabled=False, rates=None, config_file=None, config_sha256=None)
    return plain_tools == ToolsConfig() and dataclasses.replace(config, tools=ToolsConfig()) == GenerationConfig()


def corpus_probe(conn) -> dict:
    """Read-only figures from the corpus tables (public) for check 4."""
    load = conn.execute("SELECT load_id, corpus_snapshot, chunks_sha256, register_sha256 FROM corpus_loads "
                        "ORDER BY load_id DESC LIMIT 1").fetchone()
    chunks = conn.execute("SELECT count(*) FROM corpus_chunks").fetchone()[0]
    embeddings = conn.execute(
        "SELECT count(*), min(dims), max(dims), count(DISTINCT e.chunk_id) FILTER (WHERE c.chunk_id IS NOT NULL) "
        "FROM chunk_embeddings e LEFT JOIN corpus_chunks c ON c.chunk_id = e.chunk_id WHERE e.model = %s",
        (EMBEDDING_MODEL,)).fetchone()
    return {"load_id": None if load is None else load[0],
            "snapshot": None if load is None else load[1].isoformat(),
            "chunks_sha256": None if load is None else load[2], "register_sha256": None if load is None else load[3],
            "chunk_count": chunks, "embedding_count": embeddings[0], "embedding_min_dims": embeddings[1],
            "embedding_max_dims": embeddings[2], "embedded_chunks": embeddings[3]}


def disclaimer_date(text: str = DISCLAIMER) -> date | None:
    m = _DISCLAIMER_DATE.search(text)
    return datetime.strptime(m.group(1), "%d %B %Y").date() if m else None


def _check(problems: list[str], name: str, test: Callable[[], bool]) -> None:
    """Run one check; a check that raises fails (its name only, never the exception text)."""
    try:
        ok = bool(test())
    except Exception:  # noqa: BLE001 - fail closed
        ok = False
    if not ok:
        problems.append(name)


def config_checks(problems: list[str], config: GenerationConfig, config_path: Path, rates_path: Path) -> None:
    _check(problems, "config_p7_sha256", lambda: sha256_file(config_path) == P7_CONFIG_SHA256)
    rates: list = []
    _check(problems, "rate_table", lambda: rates.append(taxcalc.load_rates(rates_path)) or True)

    def live() -> bool:
        frozen = json.loads(Path(config_path).read_text(encoding="utf-8"))
        comparable = {k: v for k, v in frozen.items() if k not in COMPARE_SKIP}
        live_config = load_exit_check().current_config(rates[0])
        return comparable == {k: v for k, v in live_config.items() if k not in COMPARE_SKIP}

    _check(problems, "live_config", live)
    _check(problems, "generation_config", lambda: is_shipped(config))


def corpus_checks(problems: list[str], corpus: dict, chunks_path: Path, register_path: Path) -> None:
    _check(problems, "corpus_load", lambda: corpus["load_id"] is not None)
    manifest = lambda: json.loads(Path(BUILD_MANIFEST).read_text(encoding="utf-8"))["corpus_snapshot"]  # noqa: E731
    _check(problems, "corpus_snapshot", lambda: corpus["snapshot"] == EXPECTED_SNAPSHOT == manifest())
    _check(problems, "corpus_chunks_sha256", lambda: corpus["chunks_sha256"] == sha256_file(chunks_path))
    _check(problems, "corpus_register_sha256", lambda: corpus["register_sha256"] == sha256_file(register_path))
    _check(problems, "corpus_chunk_count", lambda: corpus["chunk_count"] == EXPECTED_CHUNKS)
    _check(problems, "corpus_embeddings", lambda: (corpus["embedding_count"], corpus["embedded_chunks"],
                                                   corpus["embedding_min_dims"], corpus["embedding_max_dims"])
           == (EXPECTED_CHUNKS, EXPECTED_CHUNKS, EMBEDDING_DIMS, EMBEDDING_DIMS))
    _check(problems, "disclaimer_date", lambda: disclaimer_date() == date.fromisoformat(corpus["snapshot"]))


def hosted_checks(problems: list[str], environ: Mapping[str, str]) -> None:
    """Names and non-emptiness only: no value is read out, compared, logged or returned."""
    if not all(environ.get(name) for name in HOSTED_REQUIRED_ENV):
        problems.append("hosted_env")
    if environ.get(JSONL_ENV):
        problems.append("hosted_jsonl")


def git_commit(deployment: str, environ: Mapping[str, str]) -> str | None:
    if deployment == "hosted":
        return environ.get("RENDER_GIT_COMMIT") or None
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _streamlit_version() -> str:
    try:
        return importlib.metadata.version("streamlit")
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def startup_check(*, deployment: str, store, connect: Callable, environ: Mapping[str, str] = os.environ,
                  names: Names = Names(), config: GenerationConfig | None = None, config_path: Path = P7_CONFIG,
                  rates_path: Path = taxcalc.RATES, chunks_path: Path = CHUNKS, register_path: Path = REGISTER,
                  probe: Callable = corpus_probe) -> StartupResult:
    """Run checks 1 to 6, record the app_starts row, and return the result (fail closed)."""
    problems: list[str] = []
    if deployment not in DEPLOYMENTS:
        problems.append("deployment")
    if config is None:
        try:
            config = shipped_config(taxcalc.load_rates(rates_path))
        except Exception:  # noqa: BLE001
            config = GenerationConfig()
    config_checks(problems, config, config_path, rates_path)

    corpus: dict = {}
    try:
        with connect() as conn:
            corpus = probe(conn)
            corpus_checks(problems, corpus, chunks_path, register_path)
            try:
                problems.extend(check_schema(conn, names))
            except Exception:  # noqa: BLE001
                problems.append("audit_schema")
    except Exception:  # noqa: BLE001 - the database is down or unreadable
        problems.append("database")
    if deployment == "hosted":
        hosted_checks(problems, environ)

    problems = list(dict.fromkeys(problems))
    start_id = None
    try:
        start_id = store.record_start(
            deployment=deployment if deployment in DEPLOYMENTS else "test", git_commit=git_commit(deployment, environ),
            app_version=APP_VERSION, audit_schema=SCHEMA_LABEL, python_version=platform.python_version(),
            streamlit_version=_streamlit_version(), config_file=P7_CONFIG_FILE, config_sha256=P7_CONFIG_SHA256,
            corpus_load_id=corpus.get("load_id"), corpus_snapshot=corpus.get("snapshot"),
            chunks_sha256=corpus.get("chunks_sha256"), outcome="failed" if problems else "ok", problems=problems)
    except Exception:  # noqa: BLE001
        problems.append("app_starts_write")
    if problems:
        LOG.error("start-up check failed: %s", ", ".join(problems))
    return StartupResult(not problems, problems, start_id, corpus.get("snapshot"),
                         {"config_sha256": P7_CONFIG_SHA256, "corpus_load_id": corpus.get("load_id")})
