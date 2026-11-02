# Vector Store Design (Phase 3a)

Status: **approved 2026-11-02 (D-19), option (a) on every point; built as recorded in
[vector_store.md](vector_store.md).** Inputs: `data/derived/chunks/v1_chunks.jsonl` (400
chunks, 63,059 cl100k tokens including context headers, largest 445) and
`data/curated/discrepancy_register.csv` (274 rows, DR-000 to DR-273). Database: the
`pgvector/pgvector:0.8.7-pg17` container on port 5434 (D-02), later Supabase. Embedding model:
`text-embedding-3-small`, 1536 dimensions (D-03, provisional until Phase 4).

## 1. Schema

Four tables. The SQL uses only core PostgreSQL types (`text`, `smallint`, `integer`, `date`,
`timestamptz`, `boolean`, `text[]`, `jsonb`) plus pgvector's `vector`, so the same script runs on
Supabase.

**`corpus_chunks`**: one row per chunk, every JSONL field kept.

| Column | Type | Notes |
|---|---|---|
| `chunk_id` | text, primary key | e.g. `nta2025:s56` |
| `act_id`, `act_title`, `unit_type`, `citation` | text, not null | as in the JSONL |
| `chapter_printed`, `chapter_title` | text | as printed: the gazette mixes words and digits (`NINE`, `2`, `8`) |
| `chapter_no` | smallint | normalised: `NINE` and `9` both become 9; null for schedules |
| `part_printed`, `part_title` | text | as printed (`VIII`, and one `1`) |
| `part_no` | smallint | normalised from Roman numerals or digits |
| `schedule_printed`, `schedule_title`, `schedule_part` | text | e.g. `Thirteenth` |
| `schedule_no` | smallint | normalised: `Thirteenth` becomes 13 |
| `section`, `na_section` | text | as printed, kept as text to match the JSONL (all digits today; null for schedule chunks) |
| `subsections`, `page_labels` | text[] | |
| `heading`, `heading_source`, `context_header`, `text`, `text_source` | text | `text` stays verbatim |
| `source_file`, `source_sha256` | text | from `source` |
| `source_pages` | integer[] | 1-based PDF pages |
| `cross_refs` | jsonb | list of `{raw, act_id, target}` |
| `discrepancy_flag` | boolean, not null | |
| `register_ids` | text[] | DR ids, as in the JSONL |
| `corpus_snapshot`, `edition_date` | date | |
| `tokens_cl100k`, `tokens_with_header`, `tokens_bge_with_header` | integer | |
| `text_sha256` | text | |

Indexes: `act_id`, and `(act_id, section)` for the Phase 4 exact section lookup.

**`chunk_embeddings`**: one row per chunk per model.

| Column | Type | Notes |
|---|---|---|
| `chunk_id` | text | references `corpus_chunks`, `ON DELETE CASCADE` |
| `model` | text | e.g. `openai/text-embedding-3-small`; later `baai/bge-small-en-v1.5` |
| `dims` | smallint | 1536 or 384; a check constraint requires `vector_dims(embedding) = dims` |
| `embedding` | vector | no fixed width, so 1536 and 384 vectors share the table |
| `input_sha256` | text | sha256 of the exact UTF-8 string sent to the model |
| `embedded_at` | timestamptz | when the vector was produced (kept from the cache, not reset on reload) |

Primary key `(chunk_id, model)`. A second model is new rows, not a schema change.

**`discrepancy_register`**: all 17 CSV columns, `register_id` as primary key. `cited_unit`
references `corpus_chunks` (null only for the DR-000 rule row). Each register row names exactly
one chunk and each flagged chunk lists exactly its rows, so the Phase 5 disclosure query is a
plain join on `register_id = ANY(register_ids)`.

**`corpus_loads`**: one row per load run: sha256 of the chunks file, the register and the build
manifest, chunk and register counts, snapshot date, models embedded, tokens sent, and time. Phase
9 audit log rows can then name the exact corpus that answered a query.

## 2. What is embedded

`context_header + "\n\n" + text`, the string the 450-token cap was measured on (D-12). The header
carries the act, chapter, part, section number and marginal note. Many chunks need it to be found
at all: 198 of the 400 are single definitions and some sections are under 40 tokens. `text` alone
would lose that signal; adding the citation or other fields would only repeat what the header
already says. The largest input is 445 cl100k and 447 bge tokens, so neither model truncates.
Questions are embedded as typed, with no prefix. bge's query prefix is a Phase 4 concern, handled
inside its client.

## 3. Index

**Exact search, no ANN index**, cosine distance (`<=>`). At 400 rows a sequential scan over 1536
dimensions takes a few milliseconds and finds the true nearest neighbours every time. HNSW is
approximate: it would add a small recall error to the Phase 4 measurement and gain nothing at
this size. Every query filters `WHERE model = …`.

At Supabase, nothing changes while the corpus stays this size. If it ever reaches tens of
thousands of rows, add one HNSW index per model, written as an expression index with a cast and a
model filter, for example `USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE model =
'openai/text-embedding-3-small'`. Queries then use the same cast. Portability notes: Supabase
installs pgvector in its `extensions` schema, so the setup script uses
`CREATE EXTENSION IF NOT EXISTS vector` and never schema-qualifies the type; table SQL uses no
PostgreSQL 17-only features.

## 4. Loader

`python/embed/load.py`, run as `.venv\Scripts\python python\embed\load.py --model openai/text-embedding-3-small`.

1. **Validate before writing.** 400 unique chunk ids, 274 unique register ids, every chunk
   `register_ids` entry exists, every non-empty `cited_unit` is a chunk, and every chapter, part
   and schedule number normalises. Any failure stops the run before a write.
2. **Chunks and register in one transaction.** Upsert chunks (`INSERT … ON CONFLICT DO UPDATE`),
   delete chunks no longer in the file (their vectors go with them), replace the register rows.
3. **Embed only what changed.** For each chunk, build the input string and its sha256. Skip it if
   `chunk_embeddings` already holds that hash for that model. Otherwise take the vector from the
   local cache (Section 5), and call the API only for hashes the cache lacks. A second run with no
   changes makes no API call and changes no rows.
4. **Batching, retries and limits.** Batches of 100 inputs, so a full run is 4 requests (the API
   accepts far more per request; smaller batches keep a failure cheap). The SDK's own retry
   handles 429, 5xx and timeouts with exponential backoff (`max_retries=5`, 60-second timeout).
   Each response is checked: same count and order as the inputs, 1536 dimensions. A full run is
   about 63,500 tokens, far below any per-minute limit.
5. **Cost per full run:** about 63,500 tokens (63,059 plus the separators) at $0.02 per 1M tokens
   is **about $0.0013**. The loader logs the `usage` token count from each response, and the
   actual figure goes in `vector_store.md`.
6. **Secrets.** The key is read from `.env` (`EMBEDDING_API_KEY`) with python-dotenv and passed
   straight to the client. It is never printed, logged, put in an exception message or written to
   the cache or `corpus_loads`.

## 5. Vectors outside the database

A git-ignored local cache, `data/cache/embeddings/<model>.jsonl`, one line per vector:
`{input_sha256, model, dims, embedded_at, embedding}`. It is keyed by input hash, not chunk id, so
a renamed chunk with unchanged text costs nothing. It is not committed: as JSON it is about 12 MB
per OpenAI run (over the 10 MB rule), and it is rebuilt for about a tenth of a cent. It still
matters, for two reasons. The API does not return bit-identical vectors on every call, so the
cache pins the exact vectors behind the Phase 4 numbers. And loading Supabase from the same cache
makes the live vectors identical to the local ones, which the Phase 12 check relies on. Whether
to publish it as a release asset is a Phase 10 question.

## 6. Re-embedding procedure

**Corpus change** (a new Finance Act, a re-gazetted text, or a chunking fix):

1. Rebuild the chunks and register with the Phase 2 scripts; run `pytest`.
2. Run the loader. It upserts changed chunks, deletes removed ones and re-embeds only inputs whose
   hash changed. It prints new, changed, unchanged and deleted counts, and the tokens sent.
3. Re-run the smoke check and the Phase 4 evaluation; record the change in the decision log.
   A new gazette edition gets a new corpus entry (D-09).

One corpus edition lives in a database at a time. Earlier editions stay reproducible from git
(the chunks file) plus the cache.

**Model change:**

1. Add a client class behind the embedding interface (`python/embed/clients.py`).
2. Run the loader with `--model <new>`. It adds rows; the old model's rows stay, so Phase 4 can
   compare both. A different width needs no schema change.
3. Once a model is dropped: `load.py --drop-model <old>`. If an HNSW index exists, create one for
   the new model.

## 7. Tests

**Run without the API key** (Docker on; they fail, not skip, if the database is down). Each test
run uses a throwaway schema, dropped afterwards, so it never touches the loaded data.

- Schema: tables, columns, types, primary and foreign keys, the dims check, the extension.
- Chunks: 400 rows, every column equal to its JSONL field for every chunk.
- Normalisation: the full mapping of printed chapter, part and schedule numbers seen in the file.
- Register: 274 rows equal to the CSV; every chunk `register_ids` entry resolves; the join returns
  each flagged chunk's rows.
- Loader, with a fake embedding client giving deterministic vectors: a second run makes zero
  client calls and changes no rows; editing one chunk's text re-embeds exactly that chunk; with a
  full cache, a client that fails when called is never called; a wrong-width vector is rejected.
- Secrets: with a dummy key in the environment, the loader's output and the cache never contain
  it.

**Need the key** (marker `api`): the smoke check. Question 1 asserts `nta2025:s186` in the top 5;
the other seven are reported, not asserted.

## 8. Dependencies

Add `openai==3.35.1` to `requirements.txt`: uploaded to PyPI on 2026-10-18, the newest release
at least two weeks old on 2026-11-02 (3.36.0 followed on 2026-10-21). It pulls in `httpx2`,
`jiter`, `pydantic`, `sniffio` and `typing-extensions`; the step B install checks they do not
conflict with streamlit's. No other new package is needed: `psycopg` and `pgvector` are already
pinned. bge's `sentence-transformers` and PyTorch wait for Phase 4.
