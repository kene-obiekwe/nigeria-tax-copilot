"""Retrieval evaluation against the labelled question set (Phase 4b, gate G1, D-20).

    .venv\\Scripts\\python eval\\retrieval\\run_eval.py --split dev --config eval\\retrieval\\configs\\<name>.json
    .venv\\Scripts\\python eval\\retrieval\\run_eval.py --split test --final

For each question of one split: retrieve the top 20 chunks with the given configuration, find
the rank of the first chunk that matches a gold unit (smoke_query.matches), and score hit@1,
hit@3, hit@5 and the reciprocal rank within the top 5 (MRR). Writes
eval/retrieval/results/<split>_<config name>.json with the per-question results (ids, ranks,
top 5 with reasons; no question text) and the summary, overall and by category.

Query vectors are cached by model in data/cache/query_embeddings/ (git-ignored), keyed by the
sha256 of the exact query input, so a repeat run makes no embedding call.

Test-split guard: `--split test` needs `--final`, which runs only eval/retrieval/config_final.json
and refuses if its sha256 differs from FINAL_CONFIG_SHA256 (recorded in the retrieval report) or
if a test result file already exists. The test split runs once. Without `--final`, only the dev
split is loaded; test questions are dropped as the file is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.clients import get_client  # noqa: E402
from embed.load import CHUNKS, sha256_file, sha256_text  # noqa: E402
from retrieval.retrieve import Retriever  # noqa: E402
from retrieval.smoke_query import matches  # noqa: E402

QUESTIONS = HERE / "questions_v1.jsonl"
QUESTIONS_SHA256 = "48daa9fa13afdec2183b62185dbae20400ec0f3ef9d677623c7ac6199987ba45"
FINAL_CONFIG = HERE / "config_final.json"
FINAL_CONFIG_SHA256 = "6ce743b317a58260f7eb397c7a6e9a7e14afab86cbde2dbc296273131c9e3684"  # recorded in the report
RESULTS_DIR = HERE / "results"
TEST_RESULT = RESULTS_DIR / "test_final.json"
QUERY_CACHE_DIR = ROOT / "data" / "cache" / "query_embeddings"
SPLITS = ("dev", "test")
K = 5
DEPTH = 20
G1_MIN_HITS, G1_QUESTIONS = 27, 30


class GuardError(RuntimeError):
    """The run would break the test-split rule; nothing has been embedded or retrieved."""


def load_questions(split: str, path: Path = QUESTIONS, expected_sha256: str = QUESTIONS_SHA256) -> list[dict]:
    """The questions of one split only. Rows of the other split are discarded unread."""
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    if sha256_file(path) != expected_sha256:
        raise GuardError(f"{path.name} does not match its frozen sha256")
    kept = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                if row["split"] == split:
                    kept.append(row)
    return kept


def check_guard(split: str, final: bool, config_path: Path | None, *, final_config: Path = FINAL_CONFIG,
                final_sha256: str | None = FINAL_CONFIG_SHA256, test_result: Path = TEST_RESULT) -> Path:
    """Return the configuration file to run, or raise GuardError before any work is done."""
    if split == "test" and not final:
        raise GuardError("--split test needs --final (the frozen configuration, run once)")
    if not final:
        if config_path is None:
            raise GuardError("--config is required without --final")
        return config_path
    if config_path is not None and config_path.resolve() != final_config.resolve():
        raise GuardError(f"--final runs only {final_config.name}; do not pass another --config")
    if not final_sha256:
        raise GuardError("no frozen configuration hash is recorded yet")
    if not final_config.exists() or sha256_file(final_config) != final_sha256:
        raise GuardError(f"{final_config.name} does not match the recorded sha256 {final_sha256}")
    if split == "test" and test_result.exists():
        raise GuardError(f"{test_result.name} already exists: the test split has been run")
    return final_config


class QueryCache:
    """Query vectors by model, keyed by sha256 of the exact query input (git-ignored, no text)."""

    def __init__(self, model: str, directory: Path = QUERY_CACHE_DIR):
        self.model = model
        self.path = directory / (model.replace("/", "__") + ".jsonl")
        self.records: dict[str, list[float]] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        record = json.loads(line)
                        self.records[record["input_sha256"]] = record["embedding"]
        self.hits = self.misses = 0
        self._client = None

    @staticmethod
    def key(client_or_cls, question: str) -> str:
        return sha256_text(getattr(client_or_cls, "query_prefix", "") + question)

    def embed(self, question: str, client_factory=get_client) -> list[float]:
        from embed.clients import CLIENTS

        digest = self.key(CLIENTS.get(self.model), question)
        if digest in self.records:
            self.hits += 1
            return self.records[digest]
        if self._client is None:
            self._client = client_factory(self.model)
        vector = self._client.embed_query(question)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"input_sha256": digest, "model": self.model, "dims": len(vector),
                                 "embedded_at": datetime.now(timezone.utc).isoformat(),
                                 "embedding": vector}) + "\n")
        self.records[digest] = vector
        self.misses += 1
        return vector

    @property
    def tokens_used(self) -> int:
        return self._client.tokens_used if self._client else 0


def first_gold_rank(chunk_ids: list[str], gold_units: list[str]) -> int | None:
    return next((rank for rank, chunk_id in enumerate(chunk_ids, start=1)
                 if any(matches(chunk_id, unit) for unit in gold_units)), None)


def score(rows: list[dict]) -> dict:
    n = len(rows)
    out = {"questions": n}
    for k in (1, 3, 5):
        hits = sum(1 for r in rows if r[f"hit@{k}"])
        out[f"hit@{k}"] = hits
        out[f"hit@{k}_rate"] = round(hits / n, 4) if n else None
    out["mrr"] = round(sum(r["rr"] for r in rows) / n, 4) if n else None
    return out


def evaluate(conn, questions: list[dict], config: dict, embed) -> tuple[list[dict], dict]:
    retriever = Retriever(conn, config)
    rows = []
    for q in questions:
        results = retriever.retrieve(q["question"], k=DEPTH, query_vector=embed(q["question"]))
        ids = [r.chunk_id for r in results]
        rank = first_gold_rank(ids, q["gold_units"])
        rows.append({
            "id": q["id"], "category": q["category"], "gold_units": q["gold_units"],
            "first_gold_rank": rank,
            **{f"hit@{k}": rank is not None and rank <= k for k in (1, 3, 5)},
            "rr": round(1 / rank, 4) if rank is not None and rank <= K else 0.0,
            "top5": [{"rank": r.rank, "chunk_id": r.chunk_id, "score": round(r.score, 6),
                      "distance": None if r.distance is None else round(r.distance, 6),
                      "reasons": r.reasons} for r in results[:K]],
        })
    by_category = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    summary = {"overall": score(rows), "by_category": {c: score(r) for c, r in sorted(by_category.items())}}
    return rows, summary


def run(split: str, config_path: Path | None, *, final: bool, conn=None, cache_dir: Path = QUERY_CACHE_DIR,
        results_dir: Path = RESULTS_DIR, **guard) -> dict:
    """Check the guard, run one split and write its result file. `guard` overrides the guard's
    paths and hash (tests only)."""
    config_path = check_guard(split, final, config_path, **guard).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    questions = load_questions(split)
    cache = QueryCache(config["model"], cache_dir)
    if conn is None:
        with connect() as own:
            rows, summary = evaluate(own, questions, config, cache.embed)
    else:
        rows, summary = evaluate(conn, questions, config, cache.embed)
    record = {
        "split": split, "final": final, "config_name": config["name"], "config": config,
        "config_file": config_path.relative_to(ROOT).as_posix() if config_path.is_relative_to(ROOT) else config_path.name,
        "config_sha256": sha256_file(config_path), "questions_sha256": sha256_file(QUESTIONS),
        "chunks_sha256": sha256_file(CHUNKS), "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": K, "depth": DEPTH,
        "query_embeddings": {"from_cache": cache.hits, "embedded": cache.misses, "tokens": cache.tokens_used},
        "summary": summary, "questions": rows,
    }
    if split == "test":
        hits = summary["overall"]["hit@5"]
        record["g1"] = {"rule": f"hit@5 >= {G1_MIN_HITS} of {G1_QUESTIONS}", "hits": hits,
                        "passed": summary["overall"]["questions"] == G1_QUESTIONS and hits >= G1_MIN_HITS}
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / ("test_final.json" if split == "test" else f"{split}_{config['name']}.json")
    with out.open("x" if split == "test" else "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    record["output"] = out
    return record


def print_summary(record: dict) -> None:
    def line(label, s):
        return (f"{label:<28} {s['questions']:>3}  hit@1 {s['hit@1']:>2}  hit@3 {s['hit@3']:>2}  "
                f"hit@5 {s['hit@5']:>2} ({s['hit@5_rate']:.0%})  MRR {s['mrr']:.3f}")
    print(f"{record['split']} split, config {record['config_name']} (sha256 {record['config_sha256'][:12]}...)")
    print(line("overall", record["summary"]["overall"]))
    for category, s in record["summary"]["by_category"].items():
        print(line(f"  {category}", s))
    for row in record["questions"]:
        if not row["hit@5"]:
            got = f"first gold at rank {row['first_gold_rank']}" if row["first_gold_rank"] else f"no gold in top {DEPTH}"
            print(f"  miss {row['id']} ({row['category']}): {got}; top 5 {[t['chunk_id'] for t in row['top5']]}")
    q = record["query_embeddings"]
    print(f"query embeddings: {q['from_cache']} from cache, {q['embedded']} embedded, {q['tokens']} tokens")
    if "g1" in record:
        print(f"G1 ({record['g1']['rule']}): {'PASS' if record['g1']['passed'] else 'FAIL'} "
              f"with {record['g1']['hits']}")
    print(f"written: {Path(record['output']).relative_to(ROOT).as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--final", action="store_true", help="run the frozen config_final.json")
    args = parser.parse_args(argv)
    try:
        record = run(args.split, args.config, final=args.final)
    except GuardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print_summary(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
