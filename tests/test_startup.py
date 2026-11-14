"""The start-up check (interface design, Sections 3.8, 10 and 13; D-154).

Reads the real corpus tables (read only) and writes app_starts rows to a throwaway audit schema on the
local container; fails, not skips, when the database is down. No paid call. The AppTest case "on
failure the app shows no question box" needs app/main.py and is in Build B (tests/test_app.py)."""
import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from audit_testlib import audit_db, no_paid_clients  # noqa: F401
from check_pgvector import connect
from interface import startup
from tools import taxcalc

ROOT = Path(__file__).resolve().parents[1]
FAKE_HOSTED_ENV = {name: "set" for name in startup.HOSTED_REQUIRED_ENV}


def check(db, **kwargs):
    kwargs.setdefault("deployment", "local")
    return startup.startup_check(store=db.store, connect=connect, names=db.names, **kwargs)


def start_rows(db):
    return db.conn.execute(f"SELECT outcome, problems, config_file, config_sha256, corpus_snapshot::text, "
                           f"audit_schema, app_version FROM {db.q('app_starts')} ORDER BY start_id").fetchall()


def test_the_pinned_sha_is_the_e2e_constant():
    sys.path.insert(0, str(ROOT / "eval" / "e2e"))  # as tests/test_e2e.py imports it
    import run_e2e as module
    assert startup.P7_CONFIG_SHA256 == module.P7_CONFIG_SHA256
    assert startup.P7_CONFIG_FILE == module.P7_CONFIG.relative_to(ROOT).as_posix()


def test_the_check_passes_on_the_repository(audit_db):
    result = check(audit_db)
    assert (result.ok, result.problems) == (True, [])
    assert result.start_id is not None and result.corpus_snapshot == startup.EXPECTED_SNAPSHOT
    assert start_rows(audit_db) == [("ok", [], "eval/tools/config_p7.json", startup.P7_CONFIG_SHA256, "2026-10-18",
                                     "audit-schema/2", "app/1")]


def test_the_shipped_config_names_config_p7():
    config = startup.shipped_config()
    assert startup.is_shipped(config)
    assert config.tools.enabled and config.tools.config_file == "eval/tools/config_p7.json"
    for changed in (dataclasses.replace(config, model="claude-opus-5-5"), dataclasses.replace(config, verify=False),
                    dataclasses.replace(config, tools=dataclasses.replace(config.tools, max_model_calls=4)),
                    dataclasses.replace(config, tools=dataclasses.replace(config.tools, config_sha256="0" * 64)),
                    startup.GenerationConfig()):
        assert not startup.is_shipped(changed)


def test_a_tampered_config_p7_fails_closed(audit_db, tmp_path):
    copy = tmp_path / "config_p7.json"
    data = json.loads(startup.P7_CONFIG.read_text(encoding="utf-8"))
    data["tools"]["max_model_calls"] = 4
    copy.write_text(json.dumps(data, indent=2), encoding="utf-8")
    result = check(audit_db, config_path=copy)
    assert not result.ok and {"config_p7_sha256", "live_config"} <= set(result.problems)
    ((outcome, problems, *_),) = start_rows(audit_db)
    assert outcome == "failed" and problems == result.problems


def test_a_changed_rate_file_fails_closed(audit_db, tmp_path):
    copy = tmp_path / "calc_rates.yml"
    shutil.copy(taxcalc.RATES, copy)
    copy.write_text(copy.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    result = check(audit_db, rates_path=copy)
    assert not result.ok and {"rate_table", "live_config"} <= set(result.problems)


def test_a_wrong_corpus_hash_fails_closed(audit_db, tmp_path):
    copy = tmp_path / "v1_chunks.jsonl"
    copy.write_bytes(startup.CHUNKS.read_bytes() + b"\n")
    result = check(audit_db, chunks_path=copy)
    assert result.problems == ["corpus_chunks_sha256"]


def test_a_missing_embedding_row_fails_closed(audit_db):
    def probe(conn):
        figures = startup.corpus_probe(conn)
        return figures | {"embedding_count": figures["embedding_count"] - 1,
                          "embedded_chunks": figures["embedded_chunks"] - 1}
    assert check(audit_db, probe=probe).problems == ["corpus_embeddings"]


def test_a_wrong_snapshot_or_chunk_count_fails_closed(audit_db):
    def probe(conn):
        return startup.corpus_probe(conn) | {"snapshot": "2026-10-19", "chunk_count": 399}
    assert set(check(audit_db, probe=probe).problems) == {"corpus_snapshot", "corpus_chunk_count", "disclaimer_date"}


def test_a_wrong_schema_version_fails_closed(audit_db):
    audit_db.conn.execute(f"DELETE FROM {audit_db.q('schema_migrations')} WHERE version = 3")
    assert check(audit_db).problems == ["audit_schema_version"]


def test_a_public_execute_grant_fails_closed(audit_db):
    audit_db.conn.execute(f"GRANT EXECUTE ON FUNCTION {audit_db.names.schema}.purge_expired(integer) TO PUBLIC")
    # through PUBLIC, the app and reviewer roles could then run it too: all three are reported
    assert check(audit_db).problems == ["audit_public_execute", "audit_app_grants", "audit_review_execute"]


def test_a_wrong_generation_config_fails_closed(audit_db):
    config = dataclasses.replace(startup.shipped_config(), effort="low")
    assert check(audit_db, config=config).problems == ["generation_config"]


def test_hosted_checks_names_only(audit_db, caplog):
    assert check(audit_db, deployment="hosted", environ=FAKE_HOSTED_ENV).ok
    missing = dict(FAKE_HOSTED_ENV, LLM_API_KEY="")
    assert check(audit_db, deployment="hosted", environ=missing).problems == ["hosted_env"]
    jsonl = dict(FAKE_HOSTED_ENV, TAXY_JSONL_DIR="logs/generation")
    with caplog.at_level("ERROR", logger="taxy.startup"):
        assert check(audit_db, deployment="hosted", environ=jsonl).problems == ["hosted_jsonl"]
    assert "start-up check failed: hosted_jsonl" in caplog.text and "logs/generation" not in caplog.text
    assert check(audit_db, deployment="local", environ={}).ok  # the local app needs none of them


def test_database_down_fails_closed():
    class DownStore:
        def record_start(self, **kwargs):
            raise OSError("connection refused")

    def down():
        raise OSError("connection refused")

    result = startup.startup_check(deployment="local", store=DownStore(), connect=down)
    assert not result.ok and result.problems == ["database", "app_starts_write"] and result.start_id is None


def test_an_unknown_deployment_fails_closed(audit_db):
    assert "deployment" in check(audit_db, deployment="production").problems


def test_the_startup_module_imports_without_eval_only_or_ocr_packages():
    """Section 13: the start-up path (with run_exit_check and run_adversarial) must not need an
    eval-only or OCR package, so it runs in the deployed image built from requirements.txt alone."""
    code = (
        "import sys, importlib.abc\n"
        "BLOCKED = ('sentence_transformers', 'transformers', 'torch', 'sklearn', 'scipy', 'huggingface_hub',\n"
        "           'safetensors', 'rapidocr_onnxruntime', 'onnxruntime', 'cv2')\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BLOCKED:\n"
        "            raise ImportError('blocked eval-only package ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(ROOT / 'python')!r})\n"
        "from interface import startup\n"
        "config = startup.load_exit_check().current_config(startup.taxcalc.load_rates())\n"
        "assert startup.is_shipped(startup.shipped_config())\n"
        "print('ok', sorted(k for k in sys.modules if k.split('.')[0] in BLOCKED))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "ok []"
