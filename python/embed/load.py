"""Load the chunks and the discrepancy register into pgvector and embed the chunks (Phase 3, D-19).

    .venv\\Scripts\\python python\\embed\\load.py [--model openai/text-embedding-3-small]
    .venv\\Scripts\\python python\\embed\\load.py --drop-model <model>

1. Validate data/derived/chunks/v1_chunks.jsonl and data/curated/discrepancy_register.csv
   (unique ids, every register link resolves, every chapter, part and schedule number
   normalises). Nothing is written if a check fails.
2. In one transaction: create the schema if needed (schema.sql), insert new chunks, update
   changed ones, delete chunks no longer in the file (their vectors go with them) and bring the
   register in line with the CSV. Unchanged rows are not touched.
3. Embed `context_header + "\n\n" + text`. A chunk whose input sha256 is already stored for the
   model is skipped; otherwise the vector comes from the git-ignored cache
   (data/cache/embeddings/<model>.jsonl), and only inputs missing from the cache go to the API,
   in batches of 100. New vectors are appended to the cache after each batch.
4. Record the run in corpus_loads and print the counts, the tokens the API reported and the cost.

The API key is never printed (clients.py).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from check_pgvector import connect  # noqa: E402
from embed.clients import DEFAULT_MODEL, EmbeddingClient, get_client  # noqa: E402

CHUNKS = ROOT / "data" / "derived" / "chunks" / "v1_chunks.jsonl"
BUILD_MANIFEST = ROOT / "data" / "derived" / "chunks" / "build_manifest.json"
REGISTER = ROOT / "data" / "curated" / "discrepancy_register.csv"
CACHE_DIR = ROOT / "data" / "cache" / "embeddings"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
BATCH_SIZE = 100

CHUNK_COLUMNS = [
    "chunk_id", "act_id", "act_title", "unit_type", "citation",
    "chapter_printed", "chapter_no", "chapter_title", "part_printed", "part_no", "part_title",
    "schedule_printed", "schedule_no", "schedule_title", "schedule_part",
    "section", "subsections", "na_section", "heading", "heading_source", "context_header", "text",
    "text_source", "source_file", "source_sha256", "source_pages", "page_labels", "cross_refs",
    "discrepancy_flag", "register_ids", "corpus_snapshot", "edition_date", "tokens_cl100k",
    "tokens_with_header", "tokens_bge_with_header", "text_sha256",
]
REGISTER_COLUMNS = [
    "register_id", "act_id", "cited_unit", "gazette_section", "comparison_text",
    "comparison_section", "type", "cited_reading", "comparison_reading", "similarity", "auto_class",
    "cited_pages", "comparison_pages", "refs", "reviewed_by", "reviewed_on", "notes",
]

# Printed numbering forms. The gazette prints some chapters in words and some in digits.
WORDS = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN",
         "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN"]
ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth",
            "Tenth", "Eleventh", "Twelfth", "Thirteenth", "Fourteenth", "Fifteenth"]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV",
         "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]


def number_from_printed(printed: str | None, forms: list[str]) -> int | None:
    """Whole number for a printed chapter, part or schedule number: digits or one of `forms`."""
    if printed is None:
        return None
    value = printed.strip()
    if value.isdigit():
        return int(value)
    lookup = {form.upper(): i for i, form in enumerate(forms, start=1)}
    if value.upper() not in lookup:
        raise ValueError(f"unrecognised printed number {printed!r}")
    return lookup[value.upper()]


def chapter_no(printed):
    return number_from_printed(printed, WORDS)


def part_no(printed):
    return number_from_printed(printed, ROMAN)


def schedule_no(printed):
    return number_from_printed(printed, ORDINALS)


def embedding_input(chunk: dict) -> str:
    """The exact string embedded for a chunk (design section 2)."""
    return chunk["context_header"] + "\n\n" + chunk["text"]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_chunks(path: Path = CHUNKS) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_register(path: Path = REGISTER) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != REGISTER_COLUMNS:
            raise ValueError(f"unexpected register columns: {reader.fieldnames}")
        return list(reader)


def chunk_row(chunk: dict) -> tuple:
    chapter, part, schedule = chunk["chapter"] or {}, chunk["part"] or {}, chunk["schedule"] or {}
    return (
        chunk["chunk_id"], chunk["act_id"], chunk["act_title"], chunk["unit_type"], chunk["citation"],
        chapter.get("number"), chapter_no(chapter.get("number")), chapter.get("title"),
        part.get("number"), part_no(part.get("number")), part.get("title"),
        schedule.get("number"), schedule_no(schedule.get("number")), schedule.get("title"),
        schedule.get("part"),
        chunk["section"], list(chunk["subsections"]), chunk["na_section"], chunk["heading"],
        chunk["heading_source"], chunk["context_header"], chunk["text"], chunk["text_source"],
        chunk["source"]["file"], chunk["source"]["sha256"], list(chunk["source"]["pages"]),
        list(chunk["page_labels"]), json.dumps(chunk["cross_refs"], ensure_ascii=False),
        chunk["discrepancy"]["flag"], list(chunk["discrepancy"]["register_ids"]),
        date.fromisoformat(chunk["corpus_snapshot"]), date.fromisoformat(chunk["edition_date"]),
        chunk["tokens_cl100k"], chunk["tokens_with_header"], chunk["tokens_bge_with_header"],
        chunk["text_sha256"],
    )


def register_row(row: dict) -> tuple:
    values = {key: (value if value != "" else None) for key, value in row.items()}
    if values["similarity"] is not None:
        values["similarity"] = Decimal(values["similarity"])
    values["reviewed_on"] = date.fromisoformat(values["reviewed_on"])
    return tuple(values[column] for column in REGISTER_COLUMNS)


def validate(chunks: list[dict], register: list[dict]) -> None:
    """Raise ValueError before any write if the inputs do not fit together."""
    ids = [chunk["chunk_id"] for chunk in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate chunk_id in the chunks file")
    register_ids = [row["register_id"] for row in register]
    if len(set(register_ids)) != len(register_ids):
        raise ValueError("duplicate register_id in the register")
    known_chunks, known_rows = set(ids), set(register_ids)
    for chunk in chunks:
        chunk_row(chunk)  # every printed number must normalise
        if sha256_text(chunk["text"]) != chunk["text_sha256"]:
            raise ValueError(f"{chunk['chunk_id']}: text_sha256 does not match text")
        missing = set(chunk["discrepancy"]["register_ids"]) - known_rows
        if missing:
            raise ValueError(f"{chunk['chunk_id']}: register ids not in the register: {sorted(missing)}")
        if chunk["discrepancy"]["flag"] != bool(chunk["discrepancy"]["register_ids"]):
            raise ValueError(f"{chunk['chunk_id']}: discrepancy flag disagrees with its register ids")
    for row in register:
        if row["cited_unit"] and row["cited_unit"] not in known_chunks:
            raise ValueError(f"{row['register_id']}: cited_unit {row['cited_unit']!r} is not a chunk")


class EmbeddingCache:
    """Append-only JSONL of vectors keyed by input sha256, one file per model (git-ignored)."""

    def __init__(self, model: str, directory: Path = CACHE_DIR):
        self.path = directory / (model.replace("/", "__") + ".jsonl")
        self.records: dict[str, dict] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        record = json.loads(line)
                        self.records[record["input_sha256"]] = record

    def get(self, input_sha256: str) -> dict | None:
        return self.records.get(input_sha256)

    def add(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
                self.records[record["input_sha256"]] = record


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


@dataclass
class LoadReport:
    chunk_count: int = 0
    register_count: int = 0
    chunks_inserted: int = 0
    chunks_updated: int = 0
    chunks_deleted: int = 0
    register_changed: int = 0
    model: str | None = None
    embeddings_unchanged: int = 0
    embeddings_from_cache: int = 0
    embeddings_from_api: int = 0
    api_tokens: int = 0
    api_requests: int = 0
    cost_usd: float = 0.0


def apply_schema(conn) -> None:
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def _sync_rows(conn, table: str, key: str, columns: list[str], rows: list[tuple], casts: dict) -> tuple[int, int, set]:
    """Insert new and update changed rows of `table`; return (inserted, updated, stale keys)."""
    select = ", ".join(f"{c}::text" if c in casts else c for c in columns)
    existing = {row[0]: row for row in conn.execute(f"SELECT {select} FROM {table}").fetchall()}

    def comparable(row):
        return tuple(json.dumps(json.loads(v), sort_keys=True) if columns[i] in casts and v is not None
                     else v for i, v in enumerate(row))

    placeholders = ", ".join(f"%s::{casts[c]}" if c in casts else "%s" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
    sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({key}) DO UPDATE SET {updates}")
    inserted = updated = 0
    for row in rows:
        old = existing.get(row[0])
        if old is not None and comparable(old) == comparable(row):
            continue
        conn.execute(sql, row)
        if old is None:
            inserted += 1
        else:
            updated += 1
    return inserted, updated, set(existing) - {row[0] for row in rows}


def load(conn, client: EmbeddingClient | None = None, *, chunks_path: Path = CHUNKS,
         register_path: Path = REGISTER, manifest_path: Path = BUILD_MANIFEST,
         cache_dir: Path = CACHE_DIR, log=print) -> LoadReport:
    """Bring the database in line with the files; embed with `client` if one is given."""
    chunks = read_chunks(chunks_path)
    register = read_register(register_path)
    validate(chunks, register)
    report = LoadReport(chunk_count=len(chunks), register_count=len(register))

    chunk_rows = [chunk_row(chunk) for chunk in chunks]
    register_rows = [register_row(row) for row in register]
    with conn.transaction():
        apply_schema(conn)
        # Order for the cited_unit foreign key: drop register rows no longer in the CSV, write
        # the chunks, write the register (every cited chunk now exists), then drop stale chunks
        # (validate() guarantees no register row still cites one).
        reg_deleted = conn.execute(
            "DELETE FROM discrepancy_register WHERE NOT (register_id = ANY(%s))",
            ([row["register_id"] for row in register],)).rowcount
        report.chunks_inserted, report.chunks_updated, stale = _sync_rows(
            conn, "corpus_chunks", "chunk_id", CHUNK_COLUMNS, chunk_rows, {"cross_refs": "jsonb"})
        reg_inserted, reg_updated, _ = _sync_rows(
            conn, "discrepancy_register", "register_id", REGISTER_COLUMNS, register_rows, {})
        report.register_changed = reg_inserted + reg_updated + reg_deleted
        if stale:
            conn.execute("DELETE FROM corpus_chunks WHERE chunk_id = ANY(%s)", (sorted(stale),))
        report.chunks_deleted = len(stale)

    if client is not None:
        _embed(conn, client, chunks, report, EmbeddingCache(client.model, cache_dir), log)

    conn.execute(
        "INSERT INTO corpus_loads (corpus_snapshot, chunks_sha256, register_sha256, "
        "build_manifest_sha256, chunk_count, register_count, chunks_inserted, chunks_updated, "
        "chunks_deleted, model, embeddings_unchanged, embeddings_from_cache, embeddings_from_api, "
        "api_tokens) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (date.fromisoformat(chunks[0]["corpus_snapshot"]), sha256_file(chunks_path),
         sha256_file(register_path), sha256_file(manifest_path), report.chunk_count,
         report.register_count, report.chunks_inserted, report.chunks_updated, report.chunks_deleted,
         report.model, report.embeddings_unchanged, report.embeddings_from_cache,
         report.embeddings_from_api, report.api_tokens))
    return report


def _embed(conn, client: EmbeddingClient, chunks: list[dict], report: LoadReport,
           cache: EmbeddingCache, log) -> None:
    report.model = client.model
    stored = dict(conn.execute("SELECT chunk_id, input_sha256 FROM chunk_embeddings WHERE model = %s",
                               (client.model,)).fetchall())
    todo = []  # (chunk_id, input, input_sha256)
    for chunk in chunks:
        text = embedding_input(chunk)
        digest = sha256_text(text)
        if stored.get(chunk["chunk_id"]) == digest:
            report.embeddings_unchanged += 1
        else:
            todo.append((chunk["chunk_id"], text, digest))

    missing = list({digest: text for _, text, digest in todo if cache.get(digest) is None}.items())
    report.embeddings_from_cache = sum(1 for _, _, digest in todo if cache.get(digest) is not None)
    tokens_before, requests_before = client.tokens_used, client.requests
    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start:start + BATCH_SIZE]
        vectors = client.embed_documents([text for _, text in batch])
        now = datetime.now(timezone.utc).isoformat()
        cache.add([{"input_sha256": digest, "model": client.model, "dims": client.dims,
                    "embedded_at": now, "embedding": vector}
                   for (digest, _), vector in zip(batch, vectors)])
        log(f"  embedded {start + len(batch)}/{len(missing)} via API")
    report.embeddings_from_api = len(todo) - report.embeddings_from_cache
    report.api_tokens = client.tokens_used - tokens_before
    report.api_requests = client.requests - requests_before
    report.cost_usd = report.api_tokens * client.usd_per_million_tokens / 1_000_000

    with conn.transaction():
        for chunk_id, _, digest in todo:
            record = cache.get(digest)
            if record["dims"] != client.dims or len(record["embedding"]) != client.dims:
                raise ValueError(f"{chunk_id}: cached vector has the wrong width for {client.model}")
            conn.execute(
                "INSERT INTO chunk_embeddings (chunk_id, model, dims, embedding, input_sha256, embedded_at) "
                "VALUES (%s, %s, %s, %s::vector, %s, %s) ON CONFLICT (chunk_id, model) DO UPDATE SET "
                "dims = EXCLUDED.dims, embedding = EXCLUDED.embedding, "
                "input_sha256 = EXCLUDED.input_sha256, embedded_at = EXCLUDED.embedded_at",
                (chunk_id, client.model, client.dims, vector_literal(record["embedding"]), digest,
                 datetime.fromisoformat(record["embedded_at"])))


def drop_model(conn, model: str) -> int:
    return conn.execute("DELETE FROM chunk_embeddings WHERE model = %s", (model,)).rowcount


def print_report(report: LoadReport, log=print) -> None:
    log(f"chunks: {report.chunk_count} in file; {report.chunks_inserted} inserted, "
        f"{report.chunks_updated} updated, {report.chunks_deleted} deleted")
    log(f"register: {report.register_count} rows; {report.register_changed} changed")
    if report.model:
        log(f"embeddings ({report.model}): {report.embeddings_unchanged} unchanged, "
            f"{report.embeddings_from_cache} from cache, {report.embeddings_from_api} from API")
        log(f"API: {report.api_requests} requests, {report.api_tokens} tokens reported, "
            f"cost ${report.cost_usd:.6f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL, help="embedding model key")
    parser.add_argument("--drop-model", help="delete every vector of this model and stop")
    parser.add_argument("--no-embed", action="store_true", help="load chunks and register only")
    args = parser.parse_args(argv)
    with connect() as conn:
        conn.autocommit = True
        if args.drop_model:
            print(f"deleted {drop_model(conn, args.drop_model)} vectors of {args.drop_model}")
            return 0
        client = None if args.no_embed else get_client(args.model)
        print_report(load(conn, client))
    return 0


if __name__ == "__main__":
    sys.exit(main())
