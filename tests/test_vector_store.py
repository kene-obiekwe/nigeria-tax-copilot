"""Vector store (Phase 3, D-19): schema, chunk and register rows against their files, numbering
normalisation, loader idempotence and the cache, and secret handling. No API key needed: vectors
come from a fake client. Each test runs in a throwaway schema (conftest.db_schema)."""
import hashlib
import json
from decimal import Decimal

import httpx2
import pytest

from embed import clients, load

CHUNKS = load.read_chunks()
REGISTER = load.read_register()
DUMMY_KEY = "sk-test-dummy-0123456789abcdefDUMMYKEY"


class FakeClient:
    """Deterministic vectors from the input's sha256; counts every call."""

    model = "test/fake-8"
    dims = 8
    usd_per_million_tokens = 0.0

    def __init__(self):
        self.tokens_used = 0
        self.requests = 0
        self.inputs = []

    def _vector(self, text):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255 + 0.01 for b in digest[: self.dims]]

    def embed_documents(self, texts):
        self.requests += 1
        self.tokens_used += sum(len(t.split()) for t in texts)
        self.inputs.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class FailingClient(FakeClient):
    def embed_documents(self, texts):
        raise AssertionError("the API client must not be called")


def run_load(conn, tmp_path, client=None, **kwargs):
    return load.load(conn, client, cache_dir=tmp_path / "cache", log=lambda *_: None, **kwargs)


def rows(conn, sql):
    cur = conn.execute(sql)
    names = [d.name for d in cur.description]
    return {r[0]: dict(zip(names, r)) for r in cur.fetchall()}


# Schema ------------------------------------------------------------------------------------


def test_schema_tables_keys_and_checks(db_schema, tmp_path):
    run_load(db_schema, tmp_path)
    tables = {r[0] for r in db_schema.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
    assert tables == {"corpus_chunks", "chunk_embeddings", "discrepancy_register", "corpus_loads"}
    columns = [r[0] for r in db_schema.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'corpus_chunks' ORDER BY ordinal_position")]
    assert columns == load.CHUNK_COLUMNS
    columns = [r[0] for r in db_schema.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 'discrepancy_register' ORDER BY ordinal_position")]
    assert columns == load.REGISTER_COLUMNS
    embedding_type = db_schema.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'").fetchone()[0]
    assert embedding_type == "vector"  # no fixed width: several models share the table
    pk = db_schema.execute(
        "SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_constraint c "
        "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.conrelid = 'chunk_embeddings'::regclass AND c.contype = 'p'").fetchone()[0]
    assert pk == ["chunk_id", "model"]
    fks = {r[0]: r[1] for r in db_schema.execute(
        "SELECT conrelid::regclass::text, confdeltype FROM pg_constraint WHERE contype = 'f' "
        "AND connamespace = current_schema()::regnamespace")}
    assert fks == {"chunk_embeddings": "c", "discrepancy_register": "a"}  # cascade / no action
    # The width check rejects a vector whose length disagrees with dims.
    with pytest.raises(Exception, match="check"):
        db_schema.execute("INSERT INTO chunk_embeddings VALUES (%s, 'x', 3, '[1,2]', 'h', now())",
                          (CHUNKS[0]["chunk_id"],))


def test_schema_is_idempotent(db_schema):
    load.apply_schema(db_schema)
    load.apply_schema(db_schema)


# Chunks and register against their files ---------------------------------------------------


def test_every_chunk_column_equals_the_jsonl(db_schema, tmp_path):
    run_load(db_schema, tmp_path)
    stored = rows(db_schema, "SELECT * FROM corpus_chunks")
    assert len(CHUNKS) == 400 and len(stored) == 400
    for chunk in CHUNKS:
        row = stored[chunk["chunk_id"]]
        chapter, part, schedule = chunk["chapter"] or {}, chunk["part"] or {}, chunk["schedule"] or {}
        expected = {
            "chunk_id": chunk["chunk_id"], "act_id": chunk["act_id"], "act_title": chunk["act_title"],
            "unit_type": chunk["unit_type"], "citation": chunk["citation"],
            "chapter_printed": chapter.get("number"), "chapter_title": chapter.get("title"),
            "part_printed": part.get("number"), "part_title": part.get("title"),
            "schedule_printed": schedule.get("number"), "schedule_title": schedule.get("title"),
            "schedule_part": schedule.get("part"), "section": chunk["section"],
            "subsections": chunk["subsections"], "na_section": chunk["na_section"],
            "heading": chunk["heading"], "heading_source": chunk["heading_source"],
            "context_header": chunk["context_header"], "text": chunk["text"],
            "text_source": chunk["text_source"], "source_file": chunk["source"]["file"],
            "source_sha256": chunk["source"]["sha256"], "source_pages": chunk["source"]["pages"],
            "page_labels": chunk["page_labels"], "cross_refs": chunk["cross_refs"],
            "discrepancy_flag": chunk["discrepancy"]["flag"],
            "register_ids": chunk["discrepancy"]["register_ids"],
            "corpus_snapshot": chunk["corpus_snapshot"], "edition_date": chunk["edition_date"],
            "tokens_cl100k": chunk["tokens_cl100k"], "tokens_with_header": chunk["tokens_with_header"],
            "tokens_bge_with_header": chunk["tokens_bge_with_header"], "text_sha256": chunk["text_sha256"],
        }
        actual = {k: v for k, v in row.items() if not k.endswith("_no")}
        actual["corpus_snapshot"] = actual["corpus_snapshot"].isoformat()
        actual["edition_date"] = actual["edition_date"].isoformat()
        assert actual == expected, chunk["chunk_id"]


PRINTED_NUMBERS = {
    "chapter": {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "NINE": 9, "2": 2, "6": 6, "8": 8},
    "part": {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VIII": 8, "IX": 9, "X": 10, "1": 1},
    "schedule": {"First": 1, "Third": 3, "Eleventh": 11, "Thirteenth": 13},
}


def test_numbering_normalisation_covers_every_printed_form(db_schema, tmp_path):
    run_load(db_schema, tmp_path)
    for kind, mapping in PRINTED_NUMBERS.items():
        seen = dict(db_schema.execute(
            f"SELECT DISTINCT {kind}_printed, {kind}_no FROM corpus_chunks "
            f"WHERE {kind}_printed IS NOT NULL").fetchall())
        assert seen == mapping, kind
    assert load.chapter_no(None) is None
    with pytest.raises(ValueError):
        load.part_no("IIX")


def test_register_rows_equal_the_csv_and_join_to_their_chunks(db_schema, tmp_path):
    run_load(db_schema, tmp_path)
    stored = rows(db_schema, "SELECT * FROM discrepancy_register")
    assert len(REGISTER) == 274 and len(stored) == 274
    for row in REGISTER:
        actual = stored[row["register_id"]]
        for column in load.REGISTER_COLUMNS:
            value = actual[column]
            if isinstance(value, Decimal):
                value = f"{value:.3f}"
            elif column == "reviewed_on":
                value = value.isoformat()
            assert (value if value is not None else "") == row[column], (row["register_id"], column)
    # Disclosure join (Phase 5): each flagged chunk gets exactly the rows that cite it.
    joined = db_schema.execute(
        "SELECT c.chunk_id, array_agg(r.register_id ORDER BY r.register_id) FROM corpus_chunks c "
        "JOIN discrepancy_register r ON r.register_id = ANY(c.register_ids) GROUP BY c.chunk_id").fetchall()
    flagged = {c["chunk_id"]: sorted(c["discrepancy"]["register_ids"]) for c in CHUNKS if c["discrepancy"]["flag"]}
    assert dict(joined) == flagged
    cited = dict(db_schema.execute(
        "SELECT register_id, cited_unit FROM discrepancy_register WHERE cited_unit IS NOT NULL").fetchall())
    assert len(cited) == 273
    assert all(cited[rid] == chunk_id for chunk_id, rids in flagged.items() for rid in rids)
    assert stored["DR-000"]["cited_unit"] is None


def test_validation_stops_before_any_write(db_schema, tmp_path):
    bad = [dict(c) for c in CHUNKS]
    bad[1] = dict(bad[1], chunk_id=bad[0]["chunk_id"])
    path = tmp_path / "bad.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in bad), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate chunk_id"):
        run_load(db_schema, tmp_path, chunks_path=path)
    assert db_schema.execute("SELECT to_regclass(current_schema() || '.corpus_chunks')").fetchone()[0] is None


# Loader idempotence and the cache ----------------------------------------------------------


def xmins(conn, table, key):
    return dict(conn.execute(f"SELECT {key}, xmin::text FROM {table}").fetchall())


def test_second_run_makes_no_calls_and_changes_no_rows(db_schema, tmp_path):
    client = FakeClient()
    first = run_load(db_schema, tmp_path, client)
    assert (first.chunks_inserted, first.embeddings_from_api, first.api_requests) == (400, 400, 4)
    assert db_schema.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 400
    assert client.inputs == [load.embedding_input(c) for c in CHUNKS]
    before = [xmins(db_schema, "corpus_chunks", "chunk_id"),
              xmins(db_schema, "chunk_embeddings", "chunk_id"),
              xmins(db_schema, "discrepancy_register", "register_id")]
    calls = client.requests
    second = run_load(db_schema, tmp_path, client)
    assert client.requests == calls
    assert (second.chunks_inserted, second.chunks_updated, second.chunks_deleted,
            second.register_changed) == (0, 0, 0, 0)
    assert (second.embeddings_unchanged, second.embeddings_from_cache, second.embeddings_from_api) == (400, 0, 0)
    after = [xmins(db_schema, "corpus_chunks", "chunk_id"),
             xmins(db_schema, "chunk_embeddings", "chunk_id"),
             xmins(db_schema, "discrepancy_register", "register_id")]
    assert after == before
    assert db_schema.execute("SELECT count(*) FROM corpus_loads").fetchone()[0] == 2


def test_stored_input_hash_and_vector(db_schema, tmp_path):
    client = FakeClient()
    run_load(db_schema, tmp_path, client)
    chunk = CHUNKS[0]
    text = load.embedding_input(chunk)
    assert text == chunk["context_header"] + "\n\n" + chunk["text"]
    digest, dims, vector = db_schema.execute(
        "SELECT input_sha256, dims, embedding::text FROM chunk_embeddings WHERE chunk_id = %s",
        (chunk["chunk_id"],)).fetchone()
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest() and dims == 8
    assert json.loads(vector) == pytest.approx(client._vector(text), rel=1e-6)


def test_editing_one_chunk_re_embeds_only_that_chunk(db_schema, tmp_path):
    client = FakeClient()
    run_load(db_schema, tmp_path, client)
    edited = [dict(c) for c in CHUNKS]
    text = edited[7]["text"] + " (edited)"
    edited[7] = dict(edited[7], text=text, text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    path = tmp_path / "edited.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in edited), encoding="utf-8")
    client.inputs.clear()
    report = run_load(db_schema, tmp_path, client, chunks_path=path)
    assert (report.chunks_inserted, report.chunks_updated, report.chunks_deleted) == (0, 1, 0)
    assert (report.embeddings_unchanged, report.embeddings_from_api) == (399, 1)
    assert client.inputs == [load.embedding_input(edited[7])]


def test_removed_chunk_is_deleted_with_its_vector(db_schema, tmp_path):
    run_load(db_schema, tmp_path, FakeClient())
    unflagged = next(i for i, c in enumerate(CHUNKS) if not c["discrepancy"]["flag"])
    kept = CHUNKS[:unflagged] + CHUNKS[unflagged + 1:]
    path = tmp_path / "fewer.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in kept), encoding="utf-8")
    report = run_load(db_schema, tmp_path, FakeClient(), chunks_path=path)
    assert report.chunks_deleted == 1
    assert db_schema.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 399


def test_full_cache_means_no_api_call(db_schema, tmp_path):
    run_load(db_schema, tmp_path, FakeClient())
    db_schema.execute("DELETE FROM chunk_embeddings")
    report = run_load(db_schema, tmp_path, FailingClient())
    assert (report.embeddings_from_cache, report.embeddings_from_api, report.api_tokens) == (400, 0, 0)
    assert db_schema.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 400


def test_wrong_width_vector_is_rejected(db_schema, tmp_path):
    class WideClient(FakeClient):
        def _vector(self, text):
            return super()._vector(text) + [0.5]

    with pytest.raises(ValueError, match="wrong width"):
        run_load(db_schema, tmp_path, WideClient())
    assert db_schema.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 0


def test_two_models_share_the_table_and_drop_model(db_schema, tmp_path):
    class Fake4(FakeClient):
        model, dims = "test/fake-4", 4

    run_load(db_schema, tmp_path, FakeClient())
    run_load(db_schema, tmp_path, Fake4())
    counts = dict(db_schema.execute("SELECT model, count(*) FROM chunk_embeddings GROUP BY model").fetchall())
    assert counts == {"test/fake-8": 400, "test/fake-4": 400}
    assert load.drop_model(db_schema, "test/fake-4") == 400
    assert db_schema.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 400


# Secrets -----------------------------------------------------------------------------------


def test_loader_output_and_cache_never_hold_the_key(db_schema, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMBEDDING_API_KEY", DUMMY_KEY)
    report = run_load(db_schema, tmp_path, FakeClient())
    load.print_report(report)
    cache_text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "cache").iterdir())
    assert DUMMY_KEY not in capsys.readouterr().out
    assert DUMMY_KEY not in cache_text
    dump = db_schema.execute("SELECT string_agg(t::text, '') FROM corpus_loads t").fetchone()[0]
    assert DUMMY_KEY not in dump


def test_provider_error_does_not_quote_the_key():
    def handler(request):
        key = request.headers["authorization"].removeprefix("Bearer ")
        return httpx2.Response(401, json={"error": {"message": f"Incorrect API key provided: {key}",
                                                    "type": "invalid_request_error"}})

    client = clients.OpenAIEmbeddingClient(
        DUMMY_KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), max_retries=0)
    with pytest.raises(clients.EmbeddingError) as info:
        client.embed_documents(["text"])
    assert str(info.value) == "AuthenticationError (HTTP 401)"
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert DUMMY_KEY not in repr(client)


def test_missing_key_is_reported_without_a_value(monkeypatch):
    monkeypatch.setattr(clients, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    with pytest.raises(clients.EmbeddingError, match="EMBEDDING_API_KEY is not set"):
        clients.OpenAIEmbeddingClient()

