# Vector Store (Phase 3)

Status: **built and loaded on 2026-11-02; exit gate passed.** Built to the approved
[design](vector_store_design.md) (D-19).

## 1. What is loaded

| Item | Value |
|---|---|
| Database | `pgvector/pgvector:0.8.7-pg17` container, port 5434 (D-02) |
| Chunks | 400 rows in `corpus_chunks` from `data/derived/chunks/v1_chunks.jsonl` (sha256 `780467e7…98e9c0`) |
| Register | 274 rows in `discrepancy_register` from `data/curated/discrepancy_register.csv` |
| Embedding model | `text-embedding-3-small` (stored as `openai/text-embedding-3-small`), 1536 dimensions (D-03, provisional until Phase 4) |
| Vectors | 400 rows in `chunk_embeddings`, 3.4 MB in the database |
| Embedded text | `context_header + "\n\n" + text` |
| Search | exact cosine distance (`<=>`), no ANN index |
| Local cache | `data/cache/embeddings/openai__text-embedding-3-small.jsonl`, 12.5 MB, git-ignored |
| Client | `openai==3.35.1` |

## 2. Schema

Defined in `python/embed/schema.sql`. Every statement is idempotent and uses only core
PostgreSQL types plus `vector`, so the same file runs on Supabase.

- **`corpus_chunks`**: one row per chunk, every JSONL field. Chapter, part and schedule numbers
  are kept as printed (`chapter_printed`, `part_printed`, `schedule_printed`) next to a
  normalised whole number (`chapter_no`, `part_no`, `schedule_no`). The cited gazette mixes
  forms, for example `NINE` and `2` for chapters and `VIII` and `1` for parts; both become
  integers. Indexed on `(act_id, section)` for Phase 4 exact section lookup.
- **`chunk_embeddings`**: primary key `(chunk_id, model)`, with `dims`, `embedding`
  (`vector` with no fixed width, checked against `dims`), `input_sha256` and `embedded_at`. A
  second model adds rows; the schema does not change. Deleting a chunk deletes its vectors.
- **`discrepancy_register`**: the 17 CSV columns, empty cells stored as NULL. `cited_unit`
  references `corpus_chunks` (NULL only for the DR-000 rule row). Phase 5 disclosure:
  `JOIN discrepancy_register r ON r.register_id = ANY(c.register_ids)`.
- **`corpus_loads`**: one row per loader run, with the sha256 of the chunks file, register and
  build manifest, row counts, and the embedding counts and tokens.

## 3. How to run

From the repository root, with the database up (`docker compose up -d`):

1. Load and embed: `.venv\Scripts\python python\embed\load.py`
2. Smoke check: `.venv\Scripts\python python\retrieval\smoke_query.py`
3. Tests: `.venv\Scripts\python -m pytest -q`. The two `api` tests run when
   `EMBEDDING_API_KEY` is set in `.env` and are skipped, with that reason, when it is not.

The loader reads the key from `.env` and never prints it. Provider errors are reported by type
and HTTP status only, because their messages can quote part of the key.

## 4. Cost

Measured from the API's own `usage` figures on 2026-11-02, at $0.02 per 1M tokens:

| Run | Requests | Tokens | Cost |
|---|---|---|---|
| Full corpus embedding (400 chunks, batches of 100) | 4 | 63,059 | $0.00126 |
| Second run, nothing changed | 0 | 0 | $0 |
| Smoke check (8 questions) | 8 | 76 | $0.000002 |

The API token count equals the corpus's `tokens_with_header` total (63,059).

## 5. Re-embedding procedure

**Corpus change** (a new Finance Act, a re-gazetted text, or a chunking fix):

1. Rebuild the chunks and register with the Phase 2 scripts, then run `pytest`.
2. Run `python\embed\load.py`. It inserts new chunks, updates changed ones, deletes removed ones
   (with their vectors) and syncs the register in one transaction. Only chunks whose input sha256
   changed are re-embedded, from the cache where it has them and from the API otherwise. It prints
   inserted, updated and deleted counts, and the tokens and cost.
3. Re-run the smoke check and the Phase 4 evaluation, then record the change in the decision log.
   A new gazette edition gets a new corpus entry (D-09).

One corpus edition lives in a database at a time. An earlier edition can be rebuilt from git
(the chunks file) plus the cache.

**Model change:**

1. Add a client class to `python/embed/clients.py` that has `model`, `dims`,
   `embed_documents` and `embed_query`, and register it in `CLIENTS`.
2. Run `python\embed\load.py --model <model>`. Its rows sit beside the existing model's, so
   Phase 4 can compare both.
3. To retire a model: `python\embed\load.py --drop-model <model>`.

**Supabase (Phase 11):** set `DATABASE_URL` and run the loader. With the cache present, it loads
the same vectors as the local database and makes no API call.

## 6. Smoke check

The questions and expected sections were fixed before any result was seen, and nothing was tuned
to them. Only question 1 gates; Phase 4 does the measured evaluation. Results are from
2026-11-02, cosine distance in brackets.

| # | Question | Expected | Rank | Top 5 |
|---|---|---|---|---|
| 1 | Which supplies are exempt from VAT? (**gate**) | nta2025 s186 | **1** | `nta2025:s186` (0.289), `nta2025:s187` (0.347), `nta2025:sch11` (0.440), `nta2025:s189:def:basic-food-items(a)-(k)` (0.488), `nta2025:s188` (0.500) |
| 2 | What is the VAT rate? | nta2025 s148 | 1 | `nta2025:s148` (0.432), `nta2025:s144` (0.526), `nta2025:s149` (0.536), `nta2025:s145` (0.544), `nta2025:s187` (0.546) |
| 3 | What tax rate does a small company pay? | nta2025 s56 or s202 "small company" | 1 | `nta2025:s56` (0.389), `nta2025:s57` (0.487), `nta2025:s202:def:small-company` (0.492), `ntaa2025:s11(1)-(2)` (0.569), `ntaa2025:s147:def:small-business` (0.574) |
| 4 | What is the development levy on companies? | nta2025 s59 | 1 | `nta2025:s59` (0.341), `nta2025:s165` (0.538), `nta2025:s202:def:economic-development-incentive-certificate` (0.575), `nta2025:s56` (0.577), `nta2025:s202:def:priority-company` (0.599) |
| 5 | Are basic food items zero-rated for VAT? | nta2025 s187 | 2 | `nta2025:s189:def:basic-food-items(a)-(k)` (0.450), `nta2025:s187` (0.467), `nta2025:s186` (0.517), `nta2025:sch11` (0.568), `nta2025:s189:def:basic-food-items(l)-(p)` (0.574) |
| 6 | How do I object to a tax assessment and how long do I have? | ntaa2025 s41 | 1 | `ntaa2025:s41(1)-(4)` (0.402), `ntaa2025:s41(5)-(8)` (0.426), `ntaa2025:s43` (0.486), `ntaa2025:s36` (0.491), `ntaa2025:s49(1)-(6)` (0.495) |
| 7 | What is the penalty for failing to register for tax? | ntaa2025 s100 | 1 | `ntaa2025:s100` (0.340), `ntaa2025:s105` (0.411), `ntaa2025:s104` (0.416), `ntaa2025:s101` (0.422), `ntaa2025:s106` (0.432) |
| 8 | What is the penalty for failing to file tax returns? | ntaa2025 s101 | 1 | `ntaa2025:s101` (0.372), `ntaa2025:s100` (0.471), `ntaa2025:s108` (0.481), `ntaa2025:s104` (0.486), `ntaa2025:s105` (0.487) |

**Exit gate: passed.** `nta2025:s186` ranks first for question 1. All eight expected sections
are in the top 5. Eight questions are a smoke check, not a measurement: the hit-rate that gate G1
needs comes from the labelled Phase 4 test set.

## 7. Tests

`tests/test_vector_store.py` (16 tests) needs the database but no API key. Each test runs in a
throwaway schema that is dropped afterwards, and a fake client supplies deterministic vectors.
The tests cover:

- the schema: tables, columns, keys, the width check, and that it can be applied twice
- every chunk column against the JSONL, for all 400 chunks
- every printed chapter, part and schedule form, against its normalised number
- the 274 register rows against the CSV, and the disclosure join
- validation stopping before any write
- a second run making no calls and changing no rows (row versions compared)
- a one-chunk edit re-embedding only that chunk
- removed chunks being deleted
- a full cache making no client call
- wrong-width vectors being rejected
- two models sharing the table, and `--drop-model`
- the key never reaching output, the cache or `corpus_loads`, and provider errors never quoting
  it

`tests/test_smoke_query.py` (2 tests, marker `api`) calls the real API: a 1536-dimension check,
and the question 1 exit gate on a fresh load into a throwaway schema.
