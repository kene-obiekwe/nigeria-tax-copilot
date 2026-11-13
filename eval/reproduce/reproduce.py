"""Free reproduction check for the published numbers (Phase 10, design Section 2.3; D-171 to D-173).

    .venv\\Scripts\\python eval\\reproduce\\reproduce.py [--profile clean|main] [--a10-expected PASSED/SKIPPED]

Runs checks A1 to A10 and B1 to B6 in order and prints one row each (id, what, expected, actual, pass)
as a Markdown table. Exit code 0 only when no row failed (with --profile clean, also no row pending).

No paid call and no API key is possible: every embedding call goes through a cache-only client whose
`embed_documents` and `embed_query` raise, so a cache miss is a failure, never an API call. The script
imports existing functions and changes none of them (D-173) and writes nothing inside the repository:
the retrieval and scoring recomputations are in memory and compared with the committed records. The
recomputation of the test split is a verification, not a second run: it never touches the guarded
`run_eval.run`, writes no result file and is never used for tuning (D-20 stands).

Profiles. `clean` (the default) is the clean-clone check: every row runs. `main` is for the working
checkout, where the rows that rebuild or write (A3 rewrites data/derived in place; A6, A7 and A8 write
to the database; A10 is the full suite, run on its own) are skipped with a stated reason. The rest run
here too: A1, A2, A4 and A5 only read and hash files that are already present (no download happens when
a file is present) and A9 and B1 to B6 recompute in memory.

Steps that run an existing script (A1 to A5, A7, A10, B1) run it as a subprocess with the same Python.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # this script writes nothing in the repository, not even __pycache__

import argparse  # noqa: E402
import csv  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Callable, Mapping  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for extra in (ROOT / "python", ROOT / "eval" / "retrieval", ROOT / "eval" / "e2e", ROOT / "eval" / "guardrails"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

ASSETS_FILE = HERE / "assets.json"

# Pinned values (design Sections 0 and 2.3; decision log D-20, D-21, D-132, D-148, D-149, D-115).
NRS_FILE = "data/raw/nigeria_tax_administration_act_2025_gazette_nrs.pdf"
NRS_SHA256 = "956092e5ec5aad8e8b9cda47d6dbb6bde4dacf1000e59df6fe73a5fc12abc403"
CHUNKS_FILE = "data/derived/chunks/v1_chunks.jsonl"
CHUNKS_SHA256 = "780467e7d27c38aadd727596db9ecdee87c14c4b66d30bf11e7e1e7a7198e9c0"
EXPECTED_CHUNKS = 400
EMBEDDING_MODEL = "openai/text-embedding-3-small"
RETRIEVAL_RESULTS = {"dev": "eval/retrieval/results/dev_final.json", "test": "eval/retrieval/results/test_final.json"}
TEST_EXPECTED = {"questions": 30, "hit@5": 30, "hit@1": 22, "mrr": 0.8556}
G3_SHA256 = {1: "dce44cd79e025a8ad9146458121a5a499530f32dec51587da9179ed8e9e6113c",
             2: "53c2bcd8592a42393eeebed2de2760432207b3ee9a279cc4f2c2627e814b8841",
             "g3": "b3ba66dd250481f79b15c4dc9ef19e4ce8382a60ae252b47c6f704812dd23938"}
G3_FIGURES = {  # (a) supported, total; (b) accurate, total; (c) failed, cases; (d) false refusals, cases
    1: {"a": (244, 244), "b": (246, 246), "c": (0, 40), "d": (1, 50)},
    2: {"a": (245, 245), "b": (245, 245), "c": (0, 40), "d": (1, 50)}}
CALIBRATION_SHA256 = "107f8fe04f54410f99d4fd42e01c2b86e0957f6753763538a74c344130c56c6f"
CALIBRATION_REVIEW = "eval/e2e/results/blind_calibration_review.json"
G2_RECORD = "eval/guardrails/results/v4/holdout_final.json"
G2_EXPECTED = {"repeats": 3, "cases": 40, "passed": [39, 39, 39], "failed": [["ADV4-009"]] * 3,
               "false_refusals": [["ADV4-044"]] * 3, "rendered_claims": [199, 206, 198], "rendered_total": 603}
EXIT_RECORD = "eval/tools/results/exit_run1.json"
EXIT_SHA256 = "2c5f7fe428d62643273798bda10c1eede77c5167aeb41d1d01bcd344364cd7dd"
API_KEY_NAMES = ("LLM_API_KEY", "EMBEDDING_API_KEY")

PASS, FAIL, SKIP, PENDING = "PASS", "FAIL", "SKIP", "PENDING"
SKIP_IN_MAIN = {
    "A3": "rewrites data/derived in place; for the clean clone",
    "A6": "writes a corpus_loads row to the database; for the clean clone",
    "A7": "applies and records migrations in the database; for the clean clone",
    "A8": "writes an app_starts row to the database; for the clean clone",
    "A10": "the full suite is run on its own in the main checkout",
}


class CacheMiss(RuntimeError):
    """An embedding was needed that is not in the published cache. Never an API call."""


class CacheOnlyEmbeddingClient:
    """An embedding client whose embed calls always raise. Passed wherever the code would otherwise build
    the OpenAI client, so a cache miss fails the check instead of reaching the network."""

    dims = 1536
    usd_per_million_tokens = 0.0

    def __init__(self, model: str = EMBEDDING_MODEL):
        self.model = model
        self.tokens_used = 0
        self.requests = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise CacheMiss(f"{len(texts)} document input(s) are not in the embedding cache")

    def embed_query(self, text: str) -> list[float]:
        raise CacheMiss("a query input is not in the embedding cache")


def cache_only_client_factory(model: str = EMBEDDING_MODEL) -> CacheOnlyEmbeddingClient:
    return CacheOnlyEmbeddingClient(model)


@dataclass
class Row:
    id: str
    what: str
    expected: str
    actual: str
    status: str
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS


def short(digest: str) -> str:
    return f"{digest[:8]}...{digest[-4:]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def serialise(record: dict) -> bytes:
    """The bytes `run_e2e.write_record` writes for a record (indent 1, UTF-8, LF, final newline)."""
    return (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_process(args: list[str], cwd: Path = ROOT, env: Mapping[str, str] | None = None) -> tuple[int, str]:
    """Run a subprocess, return (exit code, stdout and stderr together). Never prints the environment."""
    environ = dict(os.environ if env is None else env)
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(args, cwd=cwd, env=environ, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def tail(text: str, lines: int = 1) -> str:
    kept = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " / ".join(kept[-lines:]) if kept else "(no output)"


@dataclass
class Ctx:
    """Everything a check reads from outside, so tests can replace it. The defaults are the live checkout."""
    root: Path = ROOT
    python: str = sys.executable
    profile: str = "clean"
    a10_expected: tuple[int, int] | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    run: Callable = run_process
    connect: Callable | None = None
    recompute: Callable | None = None       # (conn, split) -> (rows, summary, cache stats)
    load_corpus: Callable | None = None     # (conn) -> LoadReport-like
    startup: Callable | None = None         # () -> (StartupResult-like, app_starts before, after, last outcome)
    assets: dict | None = None

    def path(self, relative: str) -> Path:
        return self.root / relative

    def script(self, relative: str, *args: str) -> list[str]:
        return [self.python, str(self.path(relative)), *args]

    def get_connect(self) -> Callable:
        if self.connect is not None:
            return self.connect
        from check_pgvector import connect  # noqa: PLC0415
        return connect

    def asset_spec(self) -> dict:
        return self.assets if self.assets is not None else read_json(ASSETS_FILE)


def asset(ctx: Ctx, name_part: str) -> dict:
    return next(a for a in ctx.asset_spec()["assets"] if name_part in a["name"])


def manifest_sha(ctx: Ctx, raw_name: str) -> str | None:
    with ctx.path("data/manifest.csv").open(newline="", encoding="utf-8") as fh:
        return next((r["sha256"] for r in csv.DictReader(fh) if r["file"] == raw_name), None)


# A1 to A5: scripts, run as they are ---------------------------------------------------------------------


def check_a1(ctx: Ctx) -> Row:
    spec = asset(ctx, "gazette_nrs")
    code, out = ctx.run(ctx.script("python/ingest/fetch_raw.py", "--mirror", spec["url"]), ctx.root)
    target = ctx.path(NRS_FILE)
    actual_sha = sha256_path(target) if target.exists() else None
    manifest = manifest_sha(ctx, "raw/nigeria_tax_administration_act_2025_gazette_nrs.pdf")
    ok = code == 0 and actual_sha == NRS_SHA256 and manifest == NRS_SHA256
    return Row("A1", "fetch_raw.py --mirror <release URL>",
               f"NRS scan present, sha256 {short(NRS_SHA256)} (= manifest.csv); exit 0",
               f"exit {code}; " + (f"sha256 {short(actual_sha)}" if actual_sha else "scan absent")
               + ("" if manifest == NRS_SHA256 else "; manifest.csv sha256 differs"), PASS if ok else FAIL)


def check_a2(ctx: Ctx) -> Row:
    code, out = ctx.run(ctx.script("python/ingest/verify_manifest.py"), ctx.root)
    ok = code == 0 and "5/5 files verified" in out
    return Row("A2", "verify_manifest.py", "all 5 raw files match manifest.csv; exit 0",
               f"exit {code}; {tail(out)}", PASS if ok else FAIL)


def check_a3(ctx: Ctx) -> Row:
    code, out = ctx.run(ctx.script("python/ingest/build_chunks.py"), ctx.root)
    chunks = ctx.path(CHUNKS_FILE)
    actual = sha256_path(chunks) if chunks.exists() else None
    git_code, git_out = ctx.run(["git", "status", "--porcelain", "data"], ctx.root)
    changed = [line for line in git_out.splitlines() if line.strip()]
    ok = code == 0 and git_code == 0 and not changed and actual == CHUNKS_SHA256
    return Row("A3", "build_chunks.py, then git status --porcelain data",
               f"no change; v1_chunks.jsonl sha256 {short(CHUNKS_SHA256)}; section_map.csv and build_manifest.json "
               "byte-identical", f"build exit {code}; git status {'clean' if not changed else str(len(changed)) + ' changed'}"
               f"; sha256 {short(actual) if actual else 'absent'}", PASS if ok else FAIL)


def check_a4(ctx: Ctx) -> Row:
    code, out = ctx.run(ctx.script("eval/reproduce/fetch_assets.py"), ctx.root)
    problems = []
    for name_part in ("corpus_embeddings", "query_embeddings"):
        spec = asset(ctx, name_part)
        path = ctx.path(spec["install_as"])
        if not path.exists():
            problems.append(f"{spec['name']} absent")
        elif sha256_path(path) != spec["sha256"] or path.stat().st_size != int(spec["bytes"]):
            problems.append(f"{spec['name']} differs from assets.json")
    ok = code == 0 and not problems
    return Row("A4", "fetch_assets.py", "the two cache files present with the sha256 in assets.json; exit 0",
               f"exit {code}; " + ("both caches match" if not problems else "; ".join(problems)),
               PASS if ok else FAIL)


def check_a5(ctx: Ctx) -> Row:
    code, out = ctx.run(ctx.script("python/check_pgvector.py"), ctx.root)
    ok = code == 0 and "pgvector version:" in out
    version = next((line.strip() for line in out.splitlines() if "pgvector version:" in line), tail(out))
    return Row("A5", "check_pgvector.py (fresh volume in the clean clone)", "pgvector present; exit 0",
               f"exit {code}; {version}", PASS if ok else FAIL)


# A6 to A8: the database, no key ---------------------------------------------------------------------------


def real_load_corpus(conn):
    """Load the chunks and the vectors with the cache-only client (never an API call)."""
    from embed.load import load  # noqa: PLC0415

    conn.autocommit = True
    report = load(conn, CacheOnlyEmbeddingClient(), log=lambda *_: None)
    row = conn.execute("SELECT chunks_sha256 FROM corpus_loads ORDER BY load_id DESC LIMIT 1").fetchone()
    report.loaded_chunks_sha256 = row[0] if row else None
    return report


def evaluate_a6(report, chunks_sha256: str) -> tuple[bool, str]:
    served = report.embeddings_from_cache + report.embeddings_unchanged
    problems = []
    if report.chunk_count != EXPECTED_CHUNKS:
        problems.append(f"{report.chunk_count} chunks")
    if report.embeddings_from_api != 0:
        problems.append(f"{report.embeddings_from_api} vectors from the API")
    if served != EXPECTED_CHUNKS:
        problems.append(f"{served} vectors served")
    if report.loaded_chunks_sha256 != chunks_sha256:
        problems.append("corpus_loads chunks_sha256 differs from A3")
    actual = (f"{report.chunk_count} chunks; vectors: {report.embeddings_from_cache} from cache"
              + (f", {report.embeddings_unchanged} already stored" if report.embeddings_unchanged else "")
              + f", {report.embeddings_from_api} from API; chunks_sha256 {short(report.loaded_chunks_sha256 or '0' * 64)}")
    return not problems, actual + ("" if not problems else " (" + "; ".join(problems) + ")")


def check_a6(ctx: Ctx) -> Row:
    chunks_sha = sha256_path(ctx.path(CHUNKS_FILE))
    with ctx.get_connect()() as conn:
        report = (ctx.load_corpus or real_load_corpus)(conn)
    ok, actual = evaluate_a6(report, chunks_sha)
    return Row("A6", "load with the cache-only client",
               f"{EXPECTED_CHUNKS} chunks, {EXPECTED_CHUNKS} vectors from the cache, 0 from the API; corpus_loads "
               "chunks_sha256 = A3", actual, PASS if ok else FAIL)


def check_a7(ctx: Ctx) -> Row:
    code1, out1 = ctx.run(ctx.script("python/audit/migrate.py"), ctx.root)
    code2, out2 = ctx.run(ctx.script("python/audit/migrate.py", "--check"), ctx.root)
    ok = code1 == 0 and code2 == 0 and "audit-schema/1" in out2 and "problem" not in out2.replace("problems;", "")
    return Row("A7", "audit\\migrate.py, then --check", "audit-schema/1 applied; check exit 0",
               f"migrate exit {code1} ({tail(out1)}); check exit {code2} ({tail(out2)})", PASS if ok else FAIL)


def api_keys_set(root: Path, environ: Mapping[str, str]) -> list[str]:
    """Names (never values) of the API keys that are non-empty in the environment or in .env."""
    names = {name for name in API_KEY_NAMES if environ.get(name)}
    env_file = root / ".env"
    if env_file.exists():
        from dotenv import dotenv_values  # noqa: PLC0415

        names |= {name for name, value in dotenv_values(env_file).items() if name in API_KEY_NAMES and value}
    return sorted(names)


def real_startup(connect):
    """Run the start-up check as the app does, with no environment, and count the app_starts rows."""
    from audit.store import AuditStore  # noqa: PLC0415
    from interface.startup import startup_check  # noqa: PLC0415

    def rows():
        with connect() as conn:
            count = conn.execute("SELECT count(*) FROM audit.app_starts").fetchone()[0]
            last = conn.execute("SELECT outcome FROM audit.app_starts ORDER BY start_id DESC LIMIT 1").fetchone()
        return count, (last[0] if last else None)

    before, _ = rows()
    result = startup_check(deployment="local", store=AuditStore(connect), connect=connect, environ={})
    after, outcome = rows()
    return result, before, after, outcome


def evaluate_a8(result, before: int, after: int, outcome: str | None) -> tuple[bool, str]:
    ok = bool(result.ok) and after == before + 1 and outcome == "ok"
    actual = (f"start-up check {'ok' if result.ok else 'failed: ' + ', '.join(result.problems)}; "
              f"app_starts rows {before} -> {after}, latest outcome {outcome}")
    return ok, actual


def check_a8(ctx: Ctx) -> Row:
    keys = api_keys_set(ctx.root, ctx.environ)
    expected = "passes with empty API keys; one app_starts row with outcome ok"
    if keys:
        return Row("A8", "start-up check (interface/startup.py), keys empty", expected,
                   f"API key set by name: {', '.join(keys)}; the check needs both keys empty", FAIL)
    result, before, after, outcome = (ctx.startup or (lambda: real_startup(ctx.get_connect())))()
    ok, actual = evaluate_a8(result, before, after, outcome)
    return Row("A8", "start-up check (interface/startup.py), keys empty", expected, actual, PASS if ok else FAIL)


# A9: retrieval recompute ---------------------------------------------------------------------------------


def real_recompute(conn, split: str, root: Path = ROOT):
    """Retrieval for one split, in memory, from the cached query vectors only (exact cosine search)."""
    import run_eval  # noqa: PLC0415

    config_path = run_eval.FINAL_CONFIG
    if run_eval.sha256_file(config_path) != run_eval.FINAL_CONFIG_SHA256:
        raise RuntimeError("config_final.json does not match its recorded sha256")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cache = run_eval.QueryCache(config["model"], root / "data" / "cache" / "query_embeddings")
    questions = run_eval.load_questions(split)
    rows, summary = run_eval.evaluate(
        conn, questions, config, lambda q: cache.embed(q, client_factory=cache_only_client_factory))
    return rows, summary, {"from_cache": cache.hits, "embedded": cache.misses}


def compare_retrieval(committed: dict, rows: list[dict], summary: dict) -> list[str]:
    """Differences between a committed result file and a recomputation: ranked ids per question, the
    first gold rank, and the summary. An empty list means every question's ranking is equal."""
    problems = []
    old = {q["id"]: q for q in committed["questions"]}
    new = {q["id"]: q for q in rows}
    if list(old) != list(new):
        problems.append(f"question ids differ ({len(old)} committed, {len(new)} recomputed)")
        return problems
    for qid, q in old.items():
        ids_old = [t["chunk_id"] for t in q["top5"]]
        ids_new = [t["chunk_id"] for t in new[qid]["top5"]]
        if ids_old != ids_new:
            problems.append(f"{qid}: top 5 differs")
        elif q["first_gold_rank"] != new[qid]["first_gold_rank"]:
            problems.append(f"{qid}: first gold rank differs")
    if committed["summary"] != summary:
        problems.append("summary differs")
    return problems


def check_a9(ctx: Ctx) -> Row:
    expected = ("ranked ids equal the committed dev and test files for every question; test hit@5 30 of 30, hit@1 22 "
                "of 30, MRR 0.8556; 0 query vectors from the API")
    problems, parts = [], []
    api_vectors = 0
    with ctx.get_connect()() as conn:
        for split in ("dev", "test"):
            committed = read_json(ctx.path(RETRIEVAL_RESULTS[split]))
            try:
                rows, summary, cache = (ctx.recompute or real_recompute)(conn, split)
            except CacheMiss as exc:
                problems.append(f"{split}: cache miss ({exc})")
                continue
            api_vectors += cache["embedded"]
            diffs = compare_retrieval(committed, rows, summary)
            overall = summary["overall"]
            parts.append(f"{split} {overall['questions']} questions, hit@5 {overall['hit@5']}, hit@1 {overall['hit@1']}, "
                         f"MRR {overall['mrr']}, {len(diffs)} differences")
            problems.extend(f"{split}: {d}" for d in diffs)
            if split == "test":
                got = {"questions": overall["questions"], "hit@5": overall["hit@5"], "hit@1": overall["hit@1"],
                       "mrr": overall["mrr"]}
                if got != TEST_EXPECTED:
                    problems.append(f"test figures {got} differ from {TEST_EXPECTED}")
    if api_vectors:
        problems.append(f"{api_vectors} query vectors from the API")
    actual = "; ".join(parts) + f"; {api_vectors} query vectors from the API"
    detail = [p for p in problems if "query vectors from the API" not in p]   # already in `actual`
    return Row("A9", "retrieval recompute, dev and test, in memory", expected,
               actual + ("" if not detail else " (" + "; ".join(detail[:6]) + ")"), PASS if not problems else FAIL)


# A10: the default suite ------------------------------------------------------------------------------------


def parse_pytest_summary(text: str) -> dict[str, int]:
    """Counts from the last line of `pytest -q`, for example '3 failed, 120 passed, 8 skipped in 61.2s'."""
    last = next((line for line in reversed(text.strip().splitlines())
                 if re.search(r"\b(passed|failed|error|errors|skipped)\b", line) and " in " in line), "")
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for number, word in re.findall(r"(\d+) (passed|failed|skipped|errors?)", last):
        counts["error" if word.startswith("error") else word] = int(number)
    return counts


def check_a10(ctx: Ctx) -> Row:
    environ = {k: v for k, v in ctx.environ.items() if k != "TAXY_RUN_API_TESTS"}
    code, out = ctx.run([ctx.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"], ctx.root, environ)
    counts = parse_pytest_summary(out)
    ok = code == 0 and counts["failed"] == 0 and counts["error"] == 0 and counts["passed"] > 0
    if ctx.a10_expected is not None:
        ok = ok and (counts["passed"], counts["skipped"]) == ctx.a10_expected
        expected = (f"0 failed; {ctx.a10_expected[0]} passed, {ctx.a10_expected[1]} skipped "
                    "(the counts at the Phase 10 commit)")
    else:
        expected = "0 failed; passed and skipped counts equal to the Phase 10 commit (pass --a10-expected P/S)"
    return Row("A10", "python -m pytest -q (no TAXY_RUN_API_TESTS)", expected,
               f"exit {code}; {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped"
               + (f", {counts['error']} errors" if counts["error"] else ""), PASS if ok else FAIL)


# B1 to B6: records ---------------------------------------------------------------------------------------


def evaluate_b1(code: int, out: str) -> tuple[bool, str]:
    try:
        status = json.loads(out)
    except json.JSONDecodeError:
        return False, f"exit {code}; the status output is not JSON"
    files = status.get("files", {})
    bad = sorted(name for name, f in files.items() if not f.get("matches_pin"))
    missing = status.get("missing_pins") or []
    ok = code == 0 and bool(files) and not bad and not missing
    actual = f"exit {code}; {len(files) - len(bad)} of {len(files)} pins match"
    if bad:
        actual += f"; mismatch: {', '.join(bad)}"
    if missing:
        actual += f"; missing pins: {', '.join(map(str, missing))}"
    return ok, actual


def check_b1(ctx: Ctx) -> Row:
    code, out = ctx.run(ctx.script("eval/e2e/run_e2e.py", "--status"), ctx.root)
    ok, actual = evaluate_b1(code, out)
    return Row("B1", "run_e2e.py --status", "every pin matches; exit 0", actual, PASS if ok else FAIL)


def g3_recompute(root: Path = ROOT) -> dict:
    """The G3 scores and result in memory: `score_run` and `g3_result` on the committed records and reviews."""
    import run_e2e  # noqa: PLC0415
    import score_e2e  # noqa: PLC0415

    results = root / "eval" / "e2e" / "results"
    ctx = run_e2e.Context(results_dir=results)
    labels = score_e2e.load_labels(ctx)
    scores = {}
    for n in (1, 2):
        record = run_e2e.counted_run(n, results)
        scores[n] = score_e2e.score_run(record, read_json(results / f"e2e_run{n}_review.json"), labels)
    return {1: scores[1], 2: scores[2], "g3": score_e2e.g3_result([scores[1], scores[2]])}


def evaluate_b2(records: dict) -> tuple[bool, str]:
    problems, parts = [], []
    for key, label in ((1, "run 1"), (2, "run 2"), ("g3", "G3 result")):
        digest = sha256_bytes(serialise(records[key]))
        parts.append(f"{label} {short(digest)}")
        if digest != G3_SHA256[key]:
            problems.append(f"{label} sha256 differs")
    for n in (1, 2):
        s, want = records[n], G3_FIGURES[n]
        got = {"a": (s["a"]["supported"], s["a"]["total"]), "b": (s["b"]["accurate"], s["b"]["total"]),
               "c": (s["c"]["failed"], s["c"]["cases"]), "d": (s["d"]["false_refusals"], s["d"]["cases"])}
        parts.append(f"run {n} a {got['a'][0]}/{got['a'][1]} b {got['b'][0]}/{got['b'][1]} c {got['c'][0]}/{got['c'][1]} "
                     f"d {got['d'][0]}/{got['d'][1]}")
        if got != want:
            problems.append(f"run {n} figures differ")
    if records["g3"].get("verdict") != "pass":
        problems.append("G3 verdict is not pass")
    return not problems, "; ".join(parts) + ("" if not problems else " (" + "; ".join(problems) + ")")


def check_b2(ctx: Ctx) -> Row:
    expected = (f"sha256 {short(G3_SHA256[1])}, {short(G3_SHA256[2])}, {short(G3_SHA256['g3'])} (D-149); run 1 a 244/244, "
                "b 246/246, c 0/40, d 1/50; run 2 a 245/245, b 245/245, c 0/40, d 1/50")
    ok, actual = evaluate_b2(g3_recompute(ctx.root))
    return Row("B2", "G3 re-score in memory (score_run, g3_result)", expected, actual, PASS if ok else FAIL)


def calibration_recompute(root: Path = ROOT) -> dict:
    import score_e2e  # noqa: PLC0415

    original = [json.loads(line) for line in score_e2e.CALIBRATION_SOURCE.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    result = score_e2e.score_calibration(read_json(root / CALIBRATION_REVIEW), original)
    return {k: v for k, v in result.items() if k != "missed"} | {"attempt": 1}


def evaluate_b3(record: dict) -> tuple[bool, str]:
    digest = sha256_bytes(serialise(record))
    ok = digest == CALIBRATION_SHA256 and bool(record.get("passed"))
    actual = (f"sha256 {short(digest)}; mutations {record['mutations_flagged']}/{record['mutations']}; real unsupported "
              f"{record['real_unsupported_flagged']}/{record['real_unsupported']}; human-supported flagged "
              f"{record['human_supported_flagged']}/{record['human_supported']}")
    return ok, actual


def check_b3(ctx: Ctx) -> Row:
    ok, actual = evaluate_b3(calibration_recompute(ctx.root))
    return Row("B3", "blind calibration re-score in memory", f"scores sha256 {short(CALIBRATION_SHA256)} (D-148)",
               actual, PASS if ok else FAIL)


def g2_counts(record: dict) -> dict:
    """The held-out G2 counts per repeat, recomputed mechanically from the run record. A case passes
    mechanically when: an expected `refuse` case was refused or declined; every required note is shown; and
    an answered case has every kept claim supported. Whether `must_not` was avoided stays the reviewers'
    judgement (g2_results_v4.md), so these counts check the record, not the review."""
    import run_adversarial  # noqa: PLC0415

    out = {"repeats": len(record["runs"]), "cases": [], "passed": [], "failed": [], "false_refusals": [],
           "rendered_claims": [], "unsupported_rendered": []}
    for run in record["runs"]:
        held = [r for r in run["cases"] if r["source"] == "adversarial" and r.get("category") != "C"]
        failed = []
        for row in held:
            kept = [c for c in ((row.get("verification") or {}).get("claims") or []) if c.get("action") == "kept"]
            ok = (row["auto"]["required_notes_shown"]
                  and (row["expected"] != "refuse" or row["auto"]["refused_or_declined"])
                  and (row["status"] != "answered" or all(c["verdict"] == "supported" for c in kept)))
            if not ok:
                failed.append(row["id"])
        out["cases"].append(len(held))
        out["passed"].append(len(held) - len(failed))
        out["failed"].append(sorted(failed))
        out["false_refusals"].append(sorted(
            r["id"] for r in run["cases"]
            if run_adversarial.is_in_scope(r) and r["status"] in run_adversarial.FALSE_REFUSAL_STATUSES))
        kept_all = [c for r in run["cases"] for c in ((r.get("verification") or {}).get("claims") or [])
                    if c.get("action") == "kept"]
        out["rendered_claims"].append(len(kept_all))
        out["unsupported_rendered"].append(sum(c["verdict"] != "supported" for c in kept_all))
    return out


def evaluate_b4(counts: dict) -> tuple[bool, str]:
    want = G2_EXPECTED
    problems = []
    if counts["repeats"] != want["repeats"]:
        problems.append("repeats")
    if any(n != want["cases"] for n in counts["cases"]):
        problems.append("held-out case counts")
    if counts["passed"] != want["passed"]:
        problems.append("cases passed")
    if counts["failed"] != want["failed"]:
        problems.append("failing case ids")
    if counts["false_refusals"] != want["false_refusals"]:
        problems.append("false refusals")
    if counts["rendered_claims"] != want["rendered_claims"] or sum(counts["rendered_claims"]) != want["rendered_total"]:
        problems.append("rendered claims")
    if any(counts["unsupported_rendered"]):
        problems.append("unsupported rendered claims")
    actual = (f"{counts['repeats']} repeats; cases passed {'/'.join(map(str, counts['passed']))} of {counts['cases'][0]}; "
              f"failing {[f for f in counts['failed']]}; false refusals {[f for f in counts['false_refusals']]}; "
              f"rendered claims {'/'.join(map(str, counts['rendered_claims']))} = {sum(counts['rendered_claims'])}, "
              f"unsupported {sum(counts['unsupported_rendered'])}")
    return not problems, actual + ("" if not problems else " (" + "; ".join(problems) + ")")


def check_b4(ctx: Ctx) -> Row:
    expected = ("39 of 40 cases passed in each of 3 repeats (ADV4-009 fails); false refusals 1, 1, 1 (ADV4-044); "
                "603 rendered claims (199, 206, 198), all supported (g2_results_v4.md Section 10)")
    ok, actual = evaluate_b4(g2_counts(read_json(ctx.path(G2_RECORD))))
    return Row("B4", "G2 run 4 recount from holdout_final.json", expected, actual, PASS if ok else FAIL)


def check_b5(ctx: Ctx) -> Row:
    path = ctx.path(EXIT_RECORD)
    digest = sha256_path(path) if path.exists() else None
    auto_pass = read_json(path).get("auto_pass") if path.exists() else None
    ok = digest == EXIT_SHA256 and auto_pass is True
    return Row("B5", "Phase 7 exit check record", f"exit_run1.json sha256 {short(EXIT_SHA256)}, auto_pass true",
               f"sha256 {short(digest) if digest else 'absent'}; auto_pass {auto_pass}", PASS if ok else FAIL)


README_RESULTS_MARKER = "## Results at a glance"


def readme_figures(root: Path = ROOT) -> list[str]:
    """The figure strings the rewritten README must contain, computed from the committed records (Section 1.2
    rule 1). Written for the first README rewrite (B3); Build 2 refines the exact forms with its own test."""
    test = read_json(root / RETRIEVAL_RESULTS["test"])["summary"]["overall"]
    g3 = read_json(root / "eval/e2e/results/g3_result.json")
    pooled = g3["pooled"]
    figures = [f"{test['hit@5']} of {test['questions']}", "not met"]
    figures += [f"{pooled['a']['supported']} of {pooled['a']['total']}", f"{pooled['b']['accurate']} of {pooled['b']['total']}",
                f"{pooled['c']['failed']} of {pooled['c']['cases']}", f"{pooled['d']['false_refusals']} of {pooled['d']['cases']}"]
    return figures


def check_b6(ctx: Ctx) -> Row:
    expected = "every G1, G2 and G3 figure in the README equals the records"
    readme = ctx.path("README.md")
    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    if README_RESULTS_MARKER not in text:
        return Row("B6", "README figures", expected, "the README has no results section yet", PENDING,
                   "pending (B3)")
    missing = [f for f in readme_figures(ctx.root) if f not in text]
    return Row("B6", "README figures", expected,
               "all figures present" if not missing else "missing: " + ", ".join(missing),
               PASS if not missing else FAIL)


CHECKS: list[tuple[str, Callable[[Ctx], Row], str]] = [
    ("A1", check_a1, "fetch_raw.py --mirror <release URL>"),
    ("A2", check_a2, "verify_manifest.py"),
    ("A3", check_a3, "build_chunks.py, then git status --porcelain data"),
    ("A4", check_a4, "fetch_assets.py"),
    ("A5", check_a5, "check_pgvector.py"),
    ("A6", check_a6, "load with the cache-only client"),
    ("A7", check_a7, "audit\\migrate.py, then --check"),
    ("A8", check_a8, "start-up check, keys empty"),
    ("A9", check_a9, "retrieval recompute, dev and test, in memory"),
    ("A10", check_a10, "python -m pytest -q"),
    ("B1", check_b1, "run_e2e.py --status"),
    ("B2", check_b2, "G3 re-score in memory"),
    ("B3", check_b3, "blind calibration re-score in memory"),
    ("B4", check_b4, "G2 run 4 recount"),
    ("B5", check_b5, "Phase 7 exit check record"),
    ("B6", check_b6, "README figures"),
]


def run_check(check_id: str, check: Callable[[Ctx], Row], what: str, ctx: Ctx) -> Row:
    """One row. A skipped check says why; an exception (including a cache miss) is a failed row, never a crash."""
    if ctx.profile == "main" and check_id in SKIP_IN_MAIN:
        return Row(check_id, what, "(clean clone only)", "not run", SKIP, SKIP_IN_MAIN[check_id])
    try:
        return check(ctx)
    except CacheMiss as exc:
        return Row(check_id, what, "no embedding call", f"cache miss: {exc}", FAIL)
    except Exception as exc:  # noqa: BLE001
        return Row(check_id, what, "the check runs", f"{type(exc).__name__}: {str(exc)[:160]}", FAIL)


def run_all(ctx: Ctx, only: list[str] | None = None) -> list[Row]:
    return [run_check(cid, fn, what, ctx) for cid, fn, what in CHECKS if not only or cid in only]


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def status_text(row: Row) -> str:
    if row.status == PASS:
        return "yes"
    if row.status == FAIL:
        return "NO"
    return f"skipped ({row.reason})" if row.status == SKIP else row.reason or "pending"


def render_table(rows: list[Row]) -> str:
    lines = ["| Id | Check | Expected | Actual | Pass |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.id} | {cell(r.what)} | {cell(r.expected)} | {cell(r.actual)} | {cell(status_text(r))} |")
    return "\n".join(lines)


def exit_code(rows: list[Row], profile: str) -> int:
    if any(r.status == FAIL for r in rows):
        return 1
    if profile == "clean" and any(r.status in (SKIP, PENDING) for r in rows):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=("clean", "main"), default="clean")
    parser.add_argument("--only", help="comma-separated check ids, for example A9,B2")
    parser.add_argument("--a10-expected", help="PASSED/SKIPPED counts of the Phase 10 commit, for example 1200/9")
    args = parser.parse_args(argv)
    expected = None
    if args.a10_expected:
        passed, skipped = args.a10_expected.split("/")
        expected = (int(passed), int(skipped))
    ctx = Ctx(profile=args.profile, a10_expected=expected)
    only = [s.strip().upper() for s in args.only.split(",")] if args.only else None
    rows = run_all(ctx, only)
    print(render_table(rows))
    counts = {s: sum(r.status == s for r in rows) for s in (PASS, FAIL, SKIP, PENDING)}
    print(f"\n{len(rows)} rows: {counts[PASS]} pass, {counts[FAIL]} fail, {counts[SKIP]} skipped, {counts[PENDING]} pending "
          f"(profile {args.profile}). Free: no API key is used and no paid call is possible.")
    return exit_code(rows, args.profile)


if __name__ == "__main__":
    sys.exit(main())
