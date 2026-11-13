# How to reproduce the published numbers

> This project is a research and reference tool, not tax advice. This page says how a reader checks the
> numbers in the [README](../../README.md) and the [evaluation report](../phase08_evaluation/evaluation_report.md)
> for free, what each check proves, and what is not reproduced.

The design is in the [packaging design](../phase10_packaging/packaging_design.md), Section 2 (D-171 to D-173).

## 1. What "reproduce" means here

Each published number is in one of three classes, and the class decides what reproduction means.

| Class | Numbers | Reproduce means | Cost |
|---|---|---|---|
| **A. Deterministic build** | raw-file hashes; the 400 chunks and their sha256; the vector store contents; the audit schema; the start-up check; the test suite | the same bytes or the same hashes | free |
| **A. Deterministic retrieval** | gate G1 on the test split (hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556); the dev results of the frozen configuration | every question's ranked chunk ids equal the committed result file, recomputed from the published cached vectors with exact cosine search | free |
| **B. Model-dependent, re-scored** | gate G3 per run and pooled; the blind calibration scores; the G2 run 4 counts; the Phase 7 exit check | the free scorers, run on the committed run and review files, give byte-identical score files (or equal counts) | free |
| **C. Model-dependent, regenerated** | new answers to the same questions | **not part of this reproduction** | paid |

So a reader reproduces every **number** exactly. A reader does not regenerate the **answers**: they came from
paid, non-deterministic model calls, and the records of what the system showed are committed and pinned
instead (D-171). Regenerating answers can only be compared with a tolerance, and the plan has that paid check
for Phase 12, on a sample of the evaluation questions against the live service.

## 2. Steps

The check runs in a **clean clone**, with both API keys empty, which proves that no paid call is possible.

1. Clone the repository, create the virtual environment and install `requirements.txt`, create `.env` from
   `.env.example` and type your own `POSTGRES_PASSWORD`, and start the database: steps 1 to 5 of "How to run"
   in the [README](../../README.md). Leave `LLM_API_KEY` and `EMBEDDING_API_KEY` empty.
2. Run the check:

   ```powershell
   .venv\Scripts\python eval\reproduce\reproduce.py --profile clean
   ```

3. Read the table. It has one row per check with its id, what it checks, what is expected, what was found and
   whether it passed. The exit code is 0 only when no row failed and none was skipped or pending.

The script downloads two kinds of file when they are missing: the scanned source file of the Nigeria Tax
Administration Act, and the two embedding cache files. Both come from the release of tag
`corpus-2026-10-18` and are kept only when their size and sha256 match
[`eval/reproduce/assets.json`](../../eval/reproduce/assets.json) (and, for the scan, `data/manifest.csv`).

The script writes nothing inside the repository except what the rebuild step itself rewrites (check A3 rewrites
`data/derived` in place and then requires `git status` to show no change). It never calls a paid service:
every embedding call goes through a client whose calls raise, so a cache miss is a failed row, not an API call.
To compare the passed and skipped test counts of the Phase 10 commit, add
`--a10-expected PASSED/SKIPPED`.

## 3. What each check proves

| Id | Check | What a pass proves |
|---|---|---|
| A1 | `fetch_raw.py --mirror <release URL>` | the scanned source file is present and its sha256 equals the one pinned in `data/manifest.csv` |
| A2 | `verify_manifest.py` | all 5 raw files match the manifest in size and sha256 |
| A3 | `build_chunks.py`, then `git status --porcelain data` | the chunk build is deterministic: `v1_chunks.jsonl` (400 chunks, sha256 `780467e7...e9c0`), `section_map.csv` and `build_manifest.json` come out byte-identical |
| A4 | `eval\reproduce\fetch_assets.py` | the two cache files are present with the sha256 recorded in `assets.json` |
| A5 | `check_pgvector.py` | the database in the container has the pgvector extension |
| A6 | load with the cache-only client | 400 chunks and 400 vectors loaded from the cache, none from the API, and the load record's chunk hash equals the one from A3 |
| A7 | `audit\migrate.py`, then `--check` | the `audit-schema/1` migrations apply and the schema check exits 0 |
| A8 | the start-up check, keys empty | the app's start-up check passes with no API key and records one `ok` row |
| A9 | retrieval recompute, dev and test, in memory | every question's ranked ids equal the committed `dev_final.json` and `test_final.json`; test hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556; no query vector from the API |
| A10 | `python -m pytest -q` (without `TAXY_RUN_API_TESTS`) | the test suite has 0 failures |
| B1 | `eval\e2e\run_e2e.py --status` | every pinned evaluation input and record matches its pin |
| B2 | G3 re-score in memory | the re-computed score files and G3 verdict have the sha256 values of D-149; run 1 (a) 244 of 244, (b) 246 of 246, (c) 0 of 40, (d) 1 of 50; run 2 (a) 245 of 245, (b) 245 of 245, (c) 0 of 40, (d) 1 of 50 |
| B3 | blind calibration re-score | the calibration scores have the sha256 of D-148 |
| B4 | G2 run 4 recount from `holdout_final.json` | 39 of 40 cases passed in each of 3 repeats (ADV4-009 fails), 1 false refusal in each (ADV4-044), 603 rendered claims all supported |
| B5 | Phase 7 exit check record | `exit_run1.json` has its pinned sha256 and `auto_pass` true |
| B6 | README figures | every G1, G2 and G3 figure in the README equals the committed records, so the README cannot drift from them |

A pass is every row passing. B4 recounts the mechanical part of G2 from the run record; whether a case avoided
what it must not say stays the reviewers' judgement, recorded in the
[G2 results](../phase06_guardrails/g2_results_v4.md).

## 4. Reproducing in the working checkout

`--profile main` is for a checkout that already holds the built database: it skips the rows that rebuild or
write (A3, A6, A7, A8) and the full suite (A10, run on its own), and runs the rest, which only read files or
recompute in memory.

```powershell
.venv\Scripts\python eval\reproduce\reproduce.py --profile main
```

## 5. Why the cached vectors

`text-embedding-3-small` vectors are not guaranteed to be bit-identical between calls, so a reader who
re-embeds the corpus and the 516 query inputs may see a different rank where two chunks score close. Exact
retrieval reproduction therefore uses the vectors that produced the committed results, published with their
sha256 (D-172).

| Path | Key needed | Cost | Pass |
|---|---|---|---|
| **Published caches (the path above)** | none | $0 | every question's top 5 ids and order equal the committed files |
| Re-embed (alternative, not the check) | `EMBEDDING_API_KEY` | about $0.0013 for the corpus plus under $0.0001 for queries | test hit@5 of at least 27 of 30 (gate G1 still met); list every rank difference |

## 6. What is not reproduced

- **OCR of the scanned Administration Act.** It needs a second virtual environment and about 60 minutes, and
  its committed output is pinned by `ocr_manifest.json`. It runs in its own environment (`.venv-ocr`, created
  from [`requirements-ocr.txt`](../../requirements-ocr.txt); never installed into the main environment) with
  `python\ingest\ocr_ntaa.py` (D-13, [build notes](../phase02_chunking/build_notes.md)); the expectation is
  output that is byte-identical.
- **Regenerated answers** (class C above). The evaluation runner refuses a further run once its markers exist,
  and the test split and the end-to-end set are spent (D-133, D-135).
- **A new held-out measurement.** That would be a new evaluation, not a reproduction.
