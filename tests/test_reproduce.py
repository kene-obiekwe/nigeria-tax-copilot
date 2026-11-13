"""The Phase 10 reproduction tooling (design Sections 2.3 and 6; D-171 to D-173). Free: fakes only, no key
and no API call. Covers the cache-only embedding client, `fetch_assets.py`, every check of `reproduce.py`
(its pass and its fail path), the row output, and that `reproduce.py` writes nothing in the repository."""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from embed import load

ROOT = Path(__file__).resolve().parents[1]


def import_script(name: str, relative: str):
    """Import a script by path. `reproduce.py` turns bytecode writing off at import; restore it after."""
    saved = sys.dont_write_bytecode
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


rp = import_script("reproduce_script", "eval/reproduce/reproduce.py")
fa = import_script("fetch_assets_script", "eval/reproduce/fetch_assets.py")

NAME = "payload.bin"
PAYLOAD = b"synthetic release asset " * 40
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# The cache-only client -----------------------------------------------------------------------------------


def test_cache_only_client_raises_on_every_embed_call():
    client = rp.CacheOnlyEmbeddingClient()
    with pytest.raises(rp.CacheMiss):
        client.embed_documents(["one", "two"])
    with pytest.raises(rp.CacheMiss):
        client.embed_query("question")
    assert (client.requests, client.tokens_used) == (0, 0)


def test_cache_only_client_has_the_interface_the_code_reads():
    client = rp.cache_only_client_factory()
    assert client.model == "openai/text-embedding-3-small"
    assert client.usd_per_million_tokens == 0.0


def test_query_cache_hit_needs_no_client_and_a_miss_raises_without_writing(tmp_path):
    import run_eval

    path = tmp_path / "openai__text-embedding-3-small.jsonl"
    known = "What is the VAT rate?"
    key = run_eval.QueryCache.key(None, known)
    path.write_text(json.dumps({"input_sha256": key, "embedding": [0.5, 0.25]}) + "\n", encoding="utf-8")
    before = path.read_bytes()
    cache = run_eval.QueryCache("openai/text-embedding-3-small", tmp_path)
    assert cache.embed(known, client_factory=rp.cache_only_client_factory) == [0.5, 0.25]
    with pytest.raises(rp.CacheMiss):
        cache.embed("a question that is not cached", client_factory=rp.cache_only_client_factory)
    assert path.read_bytes() == before            # a miss never appends
    assert (cache.hits, cache.misses) == (1, 0)   # and is never counted as an API vector


def test_the_loader_with_the_cache_only_client_never_reaches_an_embedding_call(db_schema, tmp_path):
    chunks = load.read_chunks()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    class Small(rp.CacheOnlyEmbeddingClient):
        dims = 8

    # An empty cache: the first missing vector is a failure, not an API call.
    with pytest.raises(rp.CacheMiss):
        load.load(db_schema, Small(), cache_dir=cache_dir, log=lambda *_: None)
    # A complete cache: every vector comes from it, none from the API.
    lines = [json.dumps({"input_sha256": load.sha256_text(load.embedding_input(c)), "model": Small().model, "dims": 8,
                         "embedded_at": "2026-11-13T00:00:00+00:00", "embedding": [0.1 * (i % 5 + 1)] * 8})
             for i, c in enumerate(chunks)]
    (cache_dir / "openai__text-embedding-3-small.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = load.load(db_schema, Small(), cache_dir=cache_dir, log=lambda *_: None)
    assert (report.chunk_count, report.embeddings_from_cache, report.embeddings_from_api) == (400, 400, 0)


def test_real_recompute_raises_a_cache_miss_and_builds_no_api_client(tmp_path, monkeypatch):
    from check_pgvector import connect
    from embed import clients

    def forbidden(*args, **kwargs):
        raise AssertionError("the OpenAI client must not be built")

    monkeypatch.setattr(clients.OpenAIEmbeddingClient, "__init__", forbidden)
    monkeypatch.setattr(clients, "get_client", forbidden)
    with connect() as conn:
        with pytest.raises(rp.CacheMiss):
            rp.real_recompute(conn, "dev", root=tmp_path)    # an empty cache folder: the first question misses
    assert not list(tmp_path.rglob("*.jsonl"))


# fetch_assets.py -----------------------------------------------------------------------------------------


def asset_entry(**over):
    entry = {"name": NAME, "install_as": f"data/cache/{NAME}", "sha256": PAYLOAD_SHA, "bytes": len(PAYLOAD),
             "url": "https://example.invalid/release/" + NAME}
    return entry | over


def fake_fetch(data=PAYLOAD, calls=None):
    def fetch(url, part, cap):
        if calls is not None:
            calls.append(url)
        part.write_bytes(data)
        return len(data), sha(data)
    return fetch


def test_assets_json_matches_the_manifest_and_the_release_plan():
    spec = fa.load_assets()
    assets = spec["assets"]
    assert fa.manifest_mismatches(assets) == []
    assert len(assets) == 3 and len({a["name"] for a in assets}) == 3
    assert spec["release"]["tag"] == "corpus-2026-10-18"
    for a in assets:
        assert len(a["sha256"]) == 64 and a["bytes"] > 0
        assert a["url"] == spec["release"]["base_url"] + a["name"]
    caches = {a["name"]: a for a in assets if a["install_as"].startswith("data/cache/")}
    assert set(caches) == {"corpus_embeddings_openai_text-embedding-3-small.jsonl",
                           "query_embeddings_openai_text-embedding-3-small.jsonl"}
    assert (caches["corpus_embeddings_openai_text-embedding-3-small.jsonl"]["rows"],
            caches["query_embeddings_openai_text-embedding-3-small.jsonl"]["rows"]) == (400, 516)


def test_manifest_mismatch_is_reported():
    nrs = next(a for a in fa.load_assets()["assets"] if "gazette_nrs" in a["name"])
    assert fa.manifest_mismatches([nrs | {"sha256": "0" * 64}]) == [f"{nrs['name']}: sha256 differs from the manifest"]
    assert fa.manifest_mismatches([nrs | {"bytes": 1}]) == [f"{nrs['name']}: size differs from the manifest"]
    assert "not in the manifest" in fa.manifest_mismatches([nrs | {"manifest_file": "raw/none.pdf"}])[0]


def test_fetch_downloads_verifies_and_installs(tmp_path):
    calls = []
    ok = fa.fetch_all([asset_entry()], base=tmp_path, fetch=fake_fetch(calls=calls), log=lambda *_: None)
    target = tmp_path / "data" / "cache" / NAME
    assert ok and target.read_bytes() == PAYLOAD and calls == ["https://example.invalid/release/" + NAME]
    assert not target.with_name(NAME + ".part").exists()


def test_fetch_present_file_is_not_downloaded_again(tmp_path):
    target = tmp_path / "data" / "cache" / NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)
    calls, lines = [], []
    assert fa.fetch_all([asset_entry()], base=tmp_path, fetch=fake_fetch(calls=calls), log=lines.append)
    assert calls == [] and lines == [f"present {NAME}"]


def test_fetch_wrong_existing_file_is_an_error_and_is_not_overwritten(tmp_path):
    target = tmp_path / "data" / "cache" / NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"something else")
    calls, lines = [], []
    assert not fa.fetch_all([asset_entry()], base=tmp_path, fetch=fake_fetch(calls=calls), log=lines.append)
    assert target.read_bytes() == b"something else" and calls == [] and "not overwritten" in lines[0]


def test_fetch_content_mismatch_leaves_no_file_and_no_part(tmp_path):
    ok = fa.fetch_all([asset_entry()], base=tmp_path, fetch=fake_fetch(b"truncated"), log=lambda *_: None)
    target = tmp_path / "data" / "cache" / NAME
    assert not ok and not target.exists() and not target.with_name(NAME + ".part").exists()


def test_fetch_size_mismatch_with_the_right_hash_is_a_failure(tmp_path):
    ok, message = fa.fetch_asset(asset_entry(bytes=len(PAYLOAD) + 1), tmp_path / NAME, fetch=fake_fetch())
    assert not ok and "content mismatch" in message and not (tmp_path / NAME).exists()


def test_fetch_network_error_is_a_failure(tmp_path):
    def broken(url, part, cap):
        part.write_bytes(b"x")
        raise OSError("network down")

    ok, message = fa.fetch_asset(asset_entry(), tmp_path / NAME, fetch=broken)
    assert not ok and "OSError" in message and not (tmp_path / NAME).exists()
    assert not (tmp_path / (NAME + ".part")).exists()


def test_fetch_dest_mode_is_flat_and_leaves_the_repository_alone(tmp_path):
    base, dest = tmp_path / "repo", tmp_path / "scratch"
    assert fa.fetch_all([asset_entry()], base=base, dest=dest, fetch=fake_fetch(), log=lambda *_: None)
    assert (dest / NAME).read_bytes() == PAYLOAD and not base.exists()


def test_fetch_only_selects_by_name_and_an_empty_selection_fails(tmp_path):
    entries = [asset_entry(), asset_entry(name="other.bin", install_as="data/cache/other.bin", url="https://x.invalid/o")]
    calls = []
    assert fa.fetch_all(entries, base=tmp_path, only=["other"], fetch=fake_fetch(calls=calls), log=lambda *_: None)
    assert calls == ["https://x.invalid/o"]
    assert not fa.fetch_all(entries, base=tmp_path, only=["nothing"], fetch=fake_fetch(), log=lambda *_: None)


def test_fetch_stops_before_any_download_when_the_manifest_disagrees(tmp_path):
    nrs = next(a for a in fa.load_assets()["assets"] if "gazette_nrs" in a["name"])
    calls = []
    ok = fa.fetch_all([nrs | {"sha256": "0" * 64}], base=tmp_path, fetch=fake_fetch(calls=calls), log=lambda *_: None)
    assert not ok and calls == []


# Rows ---------------------------------------------------------------------------------------------------


class FakeRun:
    """Stands in for subprocess runs: (exit code, output) by a keyword in the command line."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.commands = []

    def __call__(self, args, cwd=None, env=None):
        self.commands.append(list(args))
        return self.answers.pop(0)


def make_root(tmp_path, *, nrs=b"nrs scan bytes", manifest_sha=None):
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "nigeria_tax_administration_act_2025_gazette_nrs.pdf").write_bytes(nrs)
    (tmp_path / "data" / "manifest.csv").write_text(
        "file,sha256\nraw/nigeria_tax_administration_act_2025_gazette_nrs.pdf," + (manifest_sha or sha(nrs)) + "\n",
        encoding="utf-8")
    return tmp_path


def ctx_for(tmp_path, *answers, **kwargs):
    return rp.Ctx(root=tmp_path, python="python", run=FakeRun(*answers), **kwargs)


def test_a1_pass_and_fail_paths(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(rp, "NRS_SHA256", sha(b"nrs scan bytes"))
    assets = {"assets": [{"name": "gazette_nrs.pdf", "url": "https://x.invalid/nrs.pdf"}]}
    ctx = ctx_for(root, (0, "present"), assets=assets)
    assert rp.check_a1(ctx).status == rp.PASS
    assert "--mirror" in ctx.run.commands[0] and "https://x.invalid/nrs.pdf" in ctx.run.commands[0]
    assert rp.check_a1(ctx_for(root, (1, "FAIL"), assets=assets)).status == rp.FAIL                  # script failed
    monkeypatch.setattr(rp, "NRS_SHA256", "0" * 64)
    assert rp.check_a1(ctx_for(root, (0, "present"), assets=assets)).status == rp.FAIL               # wrong hash


def test_a1_fails_when_the_manifest_hash_differs_or_the_scan_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "NRS_SHA256", sha(b"nrs scan bytes"))
    assets = {"assets": [{"name": "gazette_nrs.pdf", "url": "u"}]}
    root = make_root(tmp_path, manifest_sha="1" * 64)
    assert rp.check_a1(ctx_for(root, (0, ""), assets=assets)).status == rp.FAIL
    (root / "data" / "raw" / "nigeria_tax_administration_act_2025_gazette_nrs.pdf").unlink()
    row = rp.check_a1(ctx_for(root, (0, ""), assets=assets))
    assert row.status == rp.FAIL and "absent" in row.actual


def test_a2_pass_and_fail_paths(tmp_path):
    assert rp.check_a2(ctx_for(tmp_path, (0, "5/5 files verified\n"))).status == rp.PASS
    assert rp.check_a2(ctx_for(tmp_path, (1, "FAIL x: missing\n4/5 files verified\n"))).status == rp.FAIL
    assert rp.check_a2(ctx_for(tmp_path, (0, "4/5 files verified\n"))).status == rp.FAIL


def test_a3_pass_and_fail_paths(tmp_path, monkeypatch):
    chunks = tmp_path / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
    chunks.parent.mkdir(parents=True)
    chunks.write_bytes(b"chunk bytes")
    monkeypatch.setattr(rp, "CHUNKS_SHA256", sha(b"chunk bytes"))
    assert rp.check_a3(ctx_for(tmp_path, (0, "400 chunks"), (0, ""))).status == rp.PASS
    assert rp.check_a3(ctx_for(tmp_path, (0, "400 chunks"), (0, " M data/derived/chunks/v1_chunks.jsonl\n"))).status == rp.FAIL
    assert rp.check_a3(ctx_for(tmp_path, (1, "error"), (0, ""))).status == rp.FAIL
    monkeypatch.setattr(rp, "CHUNKS_SHA256", "0" * 64)
    assert rp.check_a3(ctx_for(tmp_path, (0, "400 chunks"), (0, ""))).status == rp.FAIL


def cache_assets(tmp_path, corpus=b"corpus vectors", query=b"query vectors"):
    spec = {"assets": []}
    for part, data, name in (("corpus_embeddings", corpus, "c.jsonl"), ("query_embeddings", query, "q.jsonl")):
        path = tmp_path / "data" / "cache" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        spec["assets"].append({"name": f"{part}_x", "install_as": f"data/cache/{name}", "sha256": sha(data),
                               "bytes": len(data)})
    return spec


def test_a4_pass_and_fail_paths(tmp_path):
    spec = cache_assets(tmp_path)
    assert rp.check_a4(ctx_for(tmp_path, (0, "present"), assets=spec)).status == rp.PASS
    assert rp.check_a4(ctx_for(tmp_path, (1, "FAIL"), assets=spec)).status == rp.FAIL
    (tmp_path / "data" / "cache" / "q.jsonl").write_bytes(b"grown query cache")
    row = rp.check_a4(ctx_for(tmp_path, (0, "present"), assets=spec))
    assert row.status == rp.FAIL and "query_embeddings_x differs" in row.actual
    (tmp_path / "data" / "cache" / "c.jsonl").unlink()
    assert "corpus_embeddings_x absent" in rp.check_a4(ctx_for(tmp_path, (0, ""), assets=spec)).actual


def test_a5_pass_and_fail_paths(tmp_path):
    assert rp.check_a5(ctx_for(tmp_path, (0, "pgvector version: 0.8.7\n"))).status == rp.PASS
    assert rp.check_a5(ctx_for(tmp_path, (1, "OperationalError"))).status == rp.FAIL
    assert rp.check_a5(ctx_for(tmp_path, (0, "nothing useful"))).status == rp.FAIL


def load_report(**over):
    base = dict(chunk_count=400, embeddings_from_cache=400, embeddings_unchanged=0, embeddings_from_api=0,
                loaded_chunks_sha256="a" * 64)
    return SimpleNamespace(**(base | over))


def test_a6_evaluator_pass_and_fail_paths():
    assert rp.evaluate_a6(load_report(), "a" * 64)[0]
    assert rp.evaluate_a6(load_report(embeddings_from_cache=0, embeddings_unchanged=400), "a" * 64)[0]   # a re-run
    for bad in (dict(chunk_count=399), dict(embeddings_from_api=1), dict(embeddings_from_cache=399),
                dict(loaded_chunks_sha256="b" * 64)):
        ok, actual = rp.evaluate_a6(load_report(**bad), "a" * 64)
        assert not ok and "(" in actual


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a6_check_uses_the_injected_loader_and_the_chunks_hash(tmp_path, monkeypatch):
    chunks = tmp_path / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
    chunks.parent.mkdir(parents=True)
    chunks.write_bytes(b"chunk bytes")
    ctx = rp.Ctx(root=tmp_path, connect=FakeConn,
                 load_corpus=lambda conn: load_report(loaded_chunks_sha256=sha(b"chunk bytes")))
    assert rp.check_a6(ctx).status == rp.PASS
    ctx.load_corpus = lambda conn: load_report()
    assert rp.check_a6(ctx).status == rp.FAIL


def test_a7_pass_and_fail_paths(tmp_path):
    ok = ctx_for(tmp_path, (0, "applied: [1, 2, 3]"), (0, "schema audit: audit-schema/1; versions [1, 2, 3]"))
    assert rp.check_a7(ok).status == rp.PASS
    assert rp.check_a7(ctx_for(tmp_path, (1, "error"), (0, "audit-schema/1"))).status == rp.FAIL
    assert rp.check_a7(ctx_for(tmp_path, (0, "applied"), (1, "schema audit: problems\n  problem: x"))).status == rp.FAIL
    assert rp.check_a7(ctx_for(tmp_path, (0, "applied"), (0, "schema audit: problems"))).status == rp.FAIL


def test_a8_evaluator_pass_and_fail_paths():
    good = SimpleNamespace(ok=True, problems=[])
    assert rp.evaluate_a8(good, 0, 1, "ok")[0]
    assert not rp.evaluate_a8(SimpleNamespace(ok=False, problems=["database"]), 0, 1, "failed")[0]
    assert not rp.evaluate_a8(good, 0, 0, "ok")[0]          # no row written
    assert not rp.evaluate_a8(good, 0, 2, "ok")[0]          # more than one row
    assert not rp.evaluate_a8(good, 0, 1, "failed")[0]


def test_a8_refuses_to_run_with_an_api_key_set_and_never_prints_it(tmp_path):
    secret = "sk-test-do-not-print-0123456789"
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=x\nEMBEDDING_API_KEY=" + secret + "\nLLM_API_KEY=\n", encoding="utf-8")
    called = []
    ctx = rp.Ctx(root=tmp_path, environ={}, startup=lambda: called.append(1))
    row = rp.check_a8(ctx)
    assert row.status == rp.FAIL and "EMBEDDING_API_KEY" in row.actual and secret not in (row.actual + row.expected)
    assert called == []
    assert rp.api_keys_set(tmp_path, {"LLM_API_KEY": "k"}) == ["EMBEDDING_API_KEY", "LLM_API_KEY"]


def test_a8_check_runs_with_empty_keys(tmp_path):
    good = SimpleNamespace(ok=True, problems=[])
    ctx = rp.Ctx(root=tmp_path, environ={}, startup=lambda: (good, 3, 4, "ok"))
    assert rp.check_a8(ctx).status == rp.PASS
    ctx.startup = lambda: (good, 3, 3, "ok")
    assert rp.check_a8(ctx).status == rp.FAIL


def ranked(ids, rank):
    return {"first_gold_rank": rank, "top5": [{"chunk_id": i} for i in ids]}


def retrieval_pair():
    committed = {"questions": [{"id": "Q1", **ranked(["a", "b"], 1)}, {"id": "Q2", **ranked(["c", "d"], 2)}],
                 "summary": {"overall": {"hit@5": 2}}}
    rows = [{"id": "Q1", **ranked(["a", "b"], 1)}, {"id": "Q2", **ranked(["c", "d"], 2)}]
    return committed, rows, {"overall": {"hit@5": 2}}


def test_compare_retrieval_pass_and_every_fail_path():
    committed, rows, summary = retrieval_pair()
    assert rp.compare_retrieval(committed, rows, summary) == []
    swapped = [rows[0], {"id": "Q2", **ranked(["d", "c"], 2)}]                  # same set, different order
    assert rp.compare_retrieval(committed, swapped, summary) == ["Q2: top 5 differs"]
    moved = [rows[0], {"id": "Q2", **ranked(["c", "d"], 3)}]
    assert rp.compare_retrieval(committed, moved, summary) == ["Q2: first gold rank differs"]
    assert rp.compare_retrieval(committed, rows, {"overall": {"hit@5": 1}}) == ["summary differs"]
    assert "question ids differ" in rp.compare_retrieval(committed, rows[:1], summary)[0]


def a9_root(tmp_path, committed):
    for split in ("dev", "test"):
        path = tmp_path / rp.RETRIEVAL_RESULTS[split]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(committed), encoding="utf-8")
    return tmp_path


def test_a9_check_pass_and_fail_paths(tmp_path, monkeypatch):
    committed, rows, summary = retrieval_pair()
    root = a9_root(tmp_path, committed)
    overall = {"questions": 30, "hit@5": 30, "hit@1": 22, "mrr": 0.8556}
    good = {"overall": overall}
    committed["summary"] = good
    a9_root(tmp_path, committed)

    def recompute(conn, split, stats=None):
        return rows, good, stats or {"from_cache": 30, "embedded": 0}

    ctx = rp.Ctx(root=root, connect=FakeConn, recompute=recompute)
    assert rp.check_a9(ctx).status == rp.PASS
    ctx.recompute = lambda conn, split: recompute(conn, split, {"from_cache": 29, "embedded": 1})
    row = rp.check_a9(ctx)
    assert row.status == rp.FAIL and "2 query vectors from the API" in row.actual    # 1 per split
    ctx.recompute = lambda conn, split: ([rows[0], {"id": "Q2", **ranked(["d", "c"], 2)}], good, {"embedded": 0})
    assert rp.check_a9(ctx).status == rp.FAIL
    weak = {"overall": overall | {"hit@5": 29}}
    committed["summary"] = weak
    a9_root(tmp_path, committed)
    ctx.recompute = lambda conn, split: (rows, weak, {"embedded": 0})
    assert rp.check_a9(ctx).status == rp.FAIL                                  # equal to the file but not to G1


def test_a9_check_turns_a_cache_miss_into_a_failed_row(tmp_path):
    committed, rows, summary = retrieval_pair()
    root = a9_root(tmp_path, committed)

    def miss(conn, split):
        raise rp.CacheMiss("a query input is not in the embedding cache")

    row = rp.check_a9(rp.Ctx(root=root, connect=FakeConn, recompute=miss))
    assert row.status == rp.FAIL and "cache miss" in row.actual


def test_pytest_summary_parsing():
    assert rp.parse_pytest_summary("....\n3 failed, 120 passed, 8 skipped in 61.2s") == {
        "passed": 120, "failed": 3, "skipped": 8, "error": 0}
    assert rp.parse_pytest_summary("120 passed in 3.1s")["skipped"] == 0
    assert rp.parse_pytest_summary("1 error in 0.5s")["error"] == 1
    assert rp.parse_pytest_summary("no summary here")["passed"] == 0


def test_a10_pass_fail_and_expected_counts(tmp_path):
    ctx = ctx_for(tmp_path, (0, "120 passed, 8 skipped in 60s"), a10_expected=(120, 8))
    row = rp.check_a10(ctx)
    assert row.status == rp.PASS
    assert "-p" in ctx.run.commands[0] and "no:cacheprovider" in ctx.run.commands[0]
    assert rp.check_a10(ctx_for(tmp_path, (0, "119 passed, 8 skipped in 60s"), a10_expected=(120, 8))).status == rp.FAIL
    assert rp.check_a10(ctx_for(tmp_path, (1, "2 failed, 118 passed in 60s"))).status == rp.FAIL
    assert rp.check_a10(ctx_for(tmp_path, (0, "120 passed, 8 skipped in 60s"))).status == rp.PASS     # no expected counts


def test_a10_never_passes_the_paid_test_flag(tmp_path):
    seen = {}

    def run(args, cwd=None, env=None):
        seen["env"] = env
        return 0, "1 passed in 1s"

    rp.check_a10(rp.Ctx(root=tmp_path, run=run, environ={"TAXY_RUN_API_TESTS": "1", "PATH": "x"}))
    assert "TAXY_RUN_API_TESTS" not in seen["env"] and seen["env"]["PATH"] == "x"


def status_json(**over):
    status = {"files": {"e2e_v1": {"matches_pin": True}, "config_p8": {"matches_pin": True}}, "missing_pins": []}
    return json.dumps(status | over)


def test_b1_pass_and_fail_paths(tmp_path):
    assert rp.check_b1(ctx_for(tmp_path, (0, status_json()))).status == rp.PASS
    bad_files = {"e2e_v1": {"matches_pin": True}, "config_p8": {"matches_pin": False}}
    row = rp.check_b1(ctx_for(tmp_path, (0, status_json(files=bad_files))))
    assert row.status == rp.FAIL and "config_p8" in row.actual
    assert rp.check_b1(ctx_for(tmp_path, (0, status_json(missing_pins=["x"])))).status == rp.FAIL
    assert rp.check_b1(ctx_for(tmp_path, (2, "refused"))).status == rp.FAIL
    assert rp.check_b1(ctx_for(tmp_path, (0, status_json(files={})))).status == rp.FAIL


def fake_score(a, b, c, d, run):
    return {"run": run, "a": {"supported": a[0], "total": a[1]}, "b": {"accurate": b[0], "total": b[1]},
            "c": {"failed": c[0], "cases": c[1]}, "d": {"false_refusals": d[0], "cases": d[1]}}


def test_b2_evaluator_pass_and_fail_paths(monkeypatch):
    records = {1: fake_score((3, 3), (4, 4), (0, 2), (0, 5), 1), 2: fake_score((2, 2), (2, 2), (0, 2), (1, 5), 2),
               "g3": {"verdict": "pass"}}
    monkeypatch.setattr(rp, "G3_SHA256", {k: sha(rp.serialise(v)) for k, v in records.items()})
    monkeypatch.setattr(rp, "G3_FIGURES", {1: {"a": (3, 3), "b": (4, 4), "c": (0, 2), "d": (0, 5)},
                                           2: {"a": (2, 2), "b": (2, 2), "c": (0, 2), "d": (1, 5)}})
    assert rp.evaluate_b2(records)[0]
    changed = records | {1: fake_score((3, 3), (4, 4), (0, 2), (0, 6), 1)}
    ok, actual = rp.evaluate_b2(changed)
    assert not ok and "run 1 sha256 differs" in actual and "run 1 figures differ" in actual
    assert not rp.evaluate_b2(records | {"g3": {"verdict": "stop"}})[0]


def test_b3_evaluator_pass_and_fail_paths(monkeypatch):
    record = {"mutations_flagged": 60, "mutations": 60, "real_unsupported_flagged": 2, "real_unsupported": 2,
              "human_supported_flagged": 1, "human_supported": 131, "passed": True, "attempt": 1}
    monkeypatch.setattr(rp, "CALIBRATION_SHA256", sha(rp.serialise(record)))
    assert rp.evaluate_b3(record)[0]
    assert not rp.evaluate_b3(record | {"human_supported_flagged": 2})[0]       # the bytes change
    monkeypatch.setattr(rp, "CALIBRATION_SHA256", sha(rp.serialise(record | {"passed": False})))
    assert not rp.evaluate_b3(record | {"passed": False})[0]                    # right bytes, targets not met


def g2_case(case_id, *, category="A1", expected="refuse", status="refused", notes_ok=True, declined=True,
            source="adversarial", claims=()):
    return {"id": case_id, "source": source, "category": category, "expected": expected, "status": status,
            "auto": {"required_notes_shown": notes_ok, "refused_or_declined": declined},
            "verification": {"claims": [{"verdict": v, "action": a} for v, a in claims]}}


def g2_record(repeat_cases):
    return {"runs": [{"repeat": i, "cases": cases} for i, cases in enumerate(repeat_cases, 1)]}


def test_g2_counts_mechanical_rules():
    cases = [
        g2_case("ADV-1"),                                                                      # refused: passes
        g2_case("ADV-2", expected="flag", status="not_covered", notes_ok=False, declined=True),   # note missing: fails
        g2_case("ADV-3", expected="refuse", status="answered", declined=False),                # answered a refuse case
        g2_case("ADV-4", expected="answer_supported", status="answered", claims=[("supported", "kept")]),
        g2_case("ADV-5", expected="answer_supported", status="answered",
                claims=[("supported", "kept"), ("unsupported", "kept")]),                      # an unsupported rendered claim
        g2_case("ADV-6", expected="answer_supported", status="answered",
                claims=[("supported", "kept"), ("unsupported", "removed")]),                   # removed: not rendered
        g2_case("CTRL-1", category="C", expected="answer_supported", status="not_covered"),    # false refusal (control)
        g2_case("DEV-1", source="dev", category=None, expected="answer_supported", status="answered",
                claims=[("supported", "kept")]),
    ]
    counts = rp.g2_counts(g2_record([cases]))
    assert counts["cases"] == [6] and counts["passed"] == [3]
    assert counts["failed"] == [["ADV-2", "ADV-3", "ADV-5"]]
    assert counts["false_refusals"] == [["CTRL-1"]]
    assert counts["rendered_claims"] == [5] and counts["unsupported_rendered"] == [1]


def test_b4_evaluator_pass_and_fail_paths(monkeypatch):
    good = {"repeats": 3, "cases": [40, 40, 40], "passed": [39, 39, 39], "failed": [["ADV4-009"]] * 3,
            "false_refusals": [["ADV4-044"]] * 3, "rendered_claims": [199, 206, 198], "unsupported_rendered": [0, 0, 0]}
    assert rp.evaluate_b4(good)[0]
    for key, value in (("repeats", 2), ("cases", [40, 40, 39]), ("passed", [39, 39, 38]),
                       ("failed", [["ADV4-009"], ["ADV4-009"], ["ADV4-010"]]),
                       ("false_refusals", [["ADV4-044"], [], ["ADV4-044"]]),
                       ("rendered_claims", [199, 206, 197]), ("unsupported_rendered", [0, 1, 0])):
        ok, actual = rp.evaluate_b4(good | {key: value})
        assert not ok and "(" in actual, key


def test_b5_pass_and_fail_paths(tmp_path, monkeypatch):
    path = tmp_path / rp.EXIT_RECORD
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"auto_pass": True}), encoding="utf-8")
    monkeypatch.setattr(rp, "EXIT_SHA256", sha(path.read_bytes()))
    ctx = rp.Ctx(root=tmp_path)
    assert rp.check_b5(ctx).status == rp.PASS
    path.write_text(json.dumps({"auto_pass": False}), encoding="utf-8")
    assert rp.check_b5(ctx).status == rp.FAIL                                        # hash changed
    monkeypatch.setattr(rp, "EXIT_SHA256", sha(path.read_bytes()))
    assert rp.check_b5(ctx).status == rp.FAIL                                        # right hash, auto_pass false
    path.unlink()
    assert "absent" in rp.check_b5(ctx).actual


def records_for_readme(tmp_path):
    test = tmp_path / rp.RETRIEVAL_RESULTS["test"]
    test.parent.mkdir(parents=True)
    test.write_text(json.dumps({"summary": {"overall": {"hit@5": 30, "questions": 30}}}), encoding="utf-8")
    g3 = tmp_path / "eval" / "e2e" / "results" / "g3_result.json"
    g3.parent.mkdir(parents=True)
    g3.write_text(json.dumps({"pooled": {"a": {"supported": 489, "total": 489}, "b": {"accurate": 491, "total": 491},
                                         "c": {"failed": 0, "cases": 80}, "d": {"false_refusals": 2, "cases": 100}}}),
                  encoding="utf-8")


def test_b6_pending_until_the_readme_has_results_then_checks_the_figures(tmp_path):
    ctx = rp.Ctx(root=tmp_path)
    row = rp.check_b6(ctx)
    assert row.status == rp.PENDING and row.reason == "pending (B3)"                  # no README at all
    (tmp_path / "README.md").write_text("# Title\n\nStatus: Phase 1\n", encoding="utf-8")
    assert rp.check_b6(ctx).status == rp.PENDING                                      # an old README
    records_for_readme(tmp_path)
    figures = " ".join(rp.readme_figures(tmp_path))
    assert figures == "30 of 30 not met 489 of 489 491 of 491 0 of 80 2 of 100"
    (tmp_path / "README.md").write_text(f"{rp.README_RESULTS_MARKER}\n\n{figures}\n", encoding="utf-8")
    assert rp.check_b6(ctx).status == rp.PASS
    (tmp_path / "README.md").write_text(f"{rp.README_RESULTS_MARKER}\n\n30 of 30, 489 of 489\n", encoding="utf-8")
    row = rp.check_b6(ctx)
    assert row.status == rp.FAIL and "not met" in row.actual and "30 of 30" not in row.actual


# The run, the table and the exit code --------------------------------------------------------------------


def test_main_profile_skips_with_a_stated_reason_and_clean_runs_everything(tmp_path):
    ran = []

    def fake(ctx):
        ran.append(1)
        return rp.Row("A3", "x", "y", "z", rp.PASS)

    row = rp.run_check("A3", fake, "w", rp.Ctx(profile="main"))
    assert row.status == rp.SKIP and "clean clone" in row.reason and ran == []
    assert rp.run_check("A3", fake, "w", rp.Ctx(profile="clean")).status == rp.PASS
    assert rp.run_check("A9", lambda c: rp.Row("A9", "x", "y", "z", rp.PASS), "w", rp.Ctx(profile="main")).status == rp.PASS
    assert set(rp.SKIP_IN_MAIN) == {"A3", "A6", "A7", "A8", "A10"}                    # A9 and B1 to B5 always run


def test_a_raised_check_is_a_failed_row_not_a_crash():
    def boom(ctx):
        raise RuntimeError("database down")

    def miss(ctx):
        raise rp.CacheMiss("not cached")

    row = rp.run_check("A9", boom, "what", rp.Ctx())
    assert row.status == rp.FAIL and "RuntimeError: database down" in row.actual
    row = rp.run_check("A9", miss, "what", rp.Ctx())
    assert row.status == rp.FAIL and "cache miss" in row.actual


def test_checks_are_in_the_design_order():
    assert [c[0] for c in rp.CHECKS] == [f"A{i}" for i in range(1, 11)] + [f"B{i}" for i in range(1, 7)]


def test_table_has_one_row_per_check_and_escapes_pipes():
    rows = [rp.Row("A1", "a | b", "x\ny", "z", rp.PASS), rp.Row("A3", "w", "e", "n", rp.SKIP, "because"),
            rp.Row("B6", "w", "e", "n", rp.PENDING, "pending (B3)"), rp.Row("B1", "w", "e", "n", rp.FAIL)]
    lines = rp.render_table(rows).splitlines()
    assert lines[0] == "| Id | Check | Expected | Actual | Pass |" and len(lines) == 2 + 4
    assert "a \\| b" in lines[2] and "x y" in lines[2] and lines[2].endswith("| yes |")
    assert lines[3].endswith("| skipped (because) |") and lines[4].endswith("| pending (B3) |")
    assert lines[5].endswith("| NO |")


def test_exit_code_rules():
    ok, skip, pend, fail = (rp.Row("X", "w", "e", "a", s) for s in (rp.PASS, rp.SKIP, rp.PENDING, rp.FAIL))
    assert rp.exit_code([ok], "clean") == 0
    assert rp.exit_code([ok, skip, pend], "main") == 0
    assert rp.exit_code([ok, skip], "clean") == 1 and rp.exit_code([ok, pend], "clean") == 1
    assert rp.exit_code([ok, fail], "main") == 1


def test_a10_expected_option_is_parsed(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(rp, "run_all", lambda ctx, only=None: seen.update(ctx=ctx, only=only) or [])
    assert rp.main(["--profile", "main", "--a10-expected", "1200/9", "--only", "a9,b2"]) == 0
    assert seen["ctx"].a10_expected == (1200, 9) and seen["ctx"].profile == "main" and seen["only"] == ["A9", "B2"]


def git_status() -> str | None:
    done = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def test_reproduce_writes_nothing_in_the_repository():
    before = git_status()
    if before is None:
        pytest.skip("not a git checkout")
    rows = rp.run_all(rp.Ctx(profile="main", python=sys.executable), only=["B1", "B2", "B3", "B4", "B5"])
    assert [r.id for r in rows] == ["B1", "B2", "B3", "B4", "B5"]
    assert all(r.status == rp.PASS for r in rows), [(r.id, r.actual) for r in rows if r.status != rp.PASS]
    assert git_status() == before
    assert (ROOT / "eval" / "retrieval" / "results" / "test_final.json").exists()
