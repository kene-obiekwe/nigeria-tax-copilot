r"""Hosted retrieval equals local retrieval, for every dev and test question (deployment design Section 9.1; $0).

    .venv\Scripts\python eval\deploy\with_hosted.py app eval\deploy\compare_retrieval.py

`with_hosted.py app` sets `DATABASE_URL` to the hosted app login (`taxy_app_login` through the session pooler).
This script also opens the local container (the `POSTGRES_*` settings of `.env`, never `DATABASE_URL`) and runs the
same retrieval on both, twice per configuration:

- `eval/retrieval/config_final.json` (top 5, the G1 record) and
- `eval/retrieval/config_v3.json` (top 8, the shipped retrieval),

over the dev and test splits of `questions_v1.jsonl`. The query vectors come from the published query cache
through the cache-only client: a cache miss fails the run, and nothing is ever sent to an API.

It is a verification, not a run: it writes no result file in the repository (D-173 precedent; the test split
stays spent, D-20). It prints no connection string, no password and no question text.

Pass (exit 0): for every question of every configuration and split, the ranked chunk ids (the whole retrieved
list, not only the top 5) are equal hosted and local, the two `run_eval.evaluate()` summaries are equal, and the
test split with `config_final.json` reproduces hit@5 30 of 30, hit@1 22 of 30 and MRR 0.8556 on the hosted side.
It also reports the largest absolute distance difference; above 1e-6 is a warning, not a failure, while the ids
are equal. Fail (exit 1): any difference. Exit 2: a missing connection or a query-cache miss.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

CONFIGS = ("config_final.json", "config_v3.json")
SPLITS = ("dev", "test")
TEST_EXPECTED = {"questions": 30, "hit@5": 30, "hit@1": 22, "mrr": 0.8556}  # the G1 record (config_final, test)
DISTANCE_WARNING = 1e-6
FIGURES_CONFIG = "config_final.json"


def load_script(name: str, relative: str):
    """Import a script of `eval/` by path (as tests/test_reproduce.py does)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class Result:
    config: str
    split: str
    questions: int
    differences: list[str] = field(default_factory=list)
    max_distance_difference: float = 0.0
    figures: dict | None = None  # hosted overall summary of the split


def ranked_lists(conn, questions: list[dict], config: dict, embed, depth: int) -> dict[str, list[tuple[str, float | None]]]:
    """The whole retrieved list (chunk id, distance) of each question."""
    from retrieval.retrieve import Retriever  # noqa: PLC0415

    retriever = Retriever(conn, config)
    out = {}
    for q in questions:
        results = retriever.retrieve(q["question"], k=depth, query_vector=embed(q["question"]))
        out[q["id"]] = [(r.chunk_id, r.distance) for r in results]
    return out


def compare_rankings(local: dict, hosted: dict) -> tuple[list[str], float]:
    """(differences, largest absolute distance difference over equal positions). A difference names the
    question id and the first rank where the two lists part; no text."""
    diffs, largest = [], 0.0
    if list(local) != list(hosted):
        return [f"question ids differ ({len(local)} local, {len(hosted)} hosted)"], largest
    for qid, left in local.items():
        right = hosted[qid]
        left_ids, right_ids = [c for c, _ in left], [c for c, _ in right]
        if left_ids != right_ids:
            first = next((i for i, (a, b) in enumerate(zip(left_ids, right_ids), 1) if a != b),
                         min(len(left_ids), len(right_ids)) + 1)
            diffs.append(f"{qid}: ranked ids differ from rank {first} ({len(left_ids)} local, {len(right_ids)} hosted)")
            continue
        for (_, dl), (_, dh) in zip(left, right):
            if dl is not None and dh is not None:
                largest = max(largest, abs(dl - dh))
    return diffs, largest


def figures_problems(summary: dict, split: str, config_name: str) -> list[str]:
    if split != "test" or config_name != FIGURES_CONFIG:
        return []
    overall = summary["overall"]
    got = {"questions": overall["questions"], "hit@5": overall["hit@5"], "hit@1": overall["hit@1"], "mrr": overall["mrr"]}
    return [] if got == TEST_EXPECTED else [f"hosted test figures {got} differ from {TEST_EXPECTED}"]


def compare(local_conn, hosted_conn, *, run_eval, cache_factory, configs=CONFIGS, splits=SPLITS) -> list[Result]:
    """Run every configuration and split on both connections and compare."""
    import json  # noqa: PLC0415

    results = []
    for name in configs:
        config = json.loads((ROOT / "eval" / "retrieval" / name).read_text(encoding="utf-8"))
        cache = cache_factory(config["model"])
        embed = lambda q, cache=cache: cache.embed(q, client_factory=cache.client_factory)  # noqa: E731
        for split in splits:
            questions = run_eval.load_questions(split)
            _, local_summary = run_eval.evaluate(local_conn, questions, config, embed)
            _, hosted_summary = run_eval.evaluate(hosted_conn, questions, config, embed)
            local = ranked_lists(local_conn, questions, config, embed, run_eval.DEPTH)
            hosted = ranked_lists(hosted_conn, questions, config, embed, run_eval.DEPTH)
            diffs, largest = compare_rankings(local, hosted)
            if local_summary != hosted_summary:
                diffs.append("the evaluate() summaries differ")
            diffs += figures_problems(hosted_summary, split, name)
            results.append(Result(name, split, len(questions), diffs, largest, hosted_summary["overall"]))
    return results


def render(results: list[Result]) -> list[str]:
    lines = []
    for r in results:
        figures = "" if not r.figures else (f"; hosted hit@5 {r.figures['hit@5']}, hit@1 {r.figures['hit@1']}, "
                                            f"MRR {r.figures['mrr']}")
        warn = " WARNING: above 1e-6" if r.max_distance_difference > DISTANCE_WARNING else ""
        lines.append(f"{r.config} {r.split}: {r.questions} questions, {len(r.differences)} differences, largest "
                     f"distance difference {r.max_distance_difference:.2e}{warn}{figures}")
        lines += [f"  {d}" for d in r.differences[:10]]
    return lines


def connect_local():
    """The local container from the `POSTGRES_*` settings of `.env`, never from `DATABASE_URL`."""
    import psycopg  # noqa: PLC0415
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(ROOT / ".env")
    return psycopg.connect(host=os.getenv("POSTGRES_HOST", "localhost"), port=int(os.getenv("POSTGRES_PORT", "5434")),
                           user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
                           dbname=os.environ["POSTGRES_DB"], connect_timeout=5)


def connect_hosted():
    import psycopg  # noqa: PLC0415

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set: run this through with_hosted.py app")
    return psycopg.connect(url, connect_timeout=5)


def make_cache_factory(run_eval, reproduce):
    def factory(model: str):
        cache = run_eval.QueryCache(model)
        cache.client_factory = reproduce.cache_only_client_factory
        return cache
    return factory


def main() -> int:
    run_eval = load_script("run_eval", "eval/retrieval/run_eval.py")
    reproduce = load_script("reproduce_for_compare", "eval/reproduce/reproduce.py")
    try:
        with connect_local() as local, connect_hosted() as hosted:
            results = compare(local, hosted, run_eval=run_eval, cache_factory=make_cache_factory(run_eval, reproduce))
    except reproduce.CacheMiss as exc:
        print(f"query-cache miss ({exc}); nothing was sent to an API")
        return 2
    except Exception as exc:  # noqa: BLE001 - the type only: a message can carry a host or a user name
        print(f"compare_retrieval.py: failed ({type(exc).__name__})")
        return 2
    for line in render(results):
        print(line)
    failed = [r for r in results if r.differences]
    print("PASS: hosted retrieval equals local" if not failed else f"FAIL: {len(failed)} configuration(s) differ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
