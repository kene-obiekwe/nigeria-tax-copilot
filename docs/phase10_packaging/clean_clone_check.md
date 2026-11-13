# Clean-clone check

Date: 2026-11-13 (walk-through ended 19:40 +01:00)
Walker: a separate session acting as a third party, using only the public README of
https://github.com/kene-obiekwe/nigeria-tax-copilot and the docs it links to.
Folder: the clean folder (empty before the clone)
Clone HEAD: `97187547c50208a42f5f0d3756ed18548ffb1213` ("chore(phase10): mark the corpus release as published and checked", 2026-11-13 16:14:14 +0100)
Python: `python --version` in the clean venv gives `Python 3.12.2` (venv built from `C:\Python312\python.exe`)
Result: reproduce.py exits 1 with 14 of 16 rows passing. Rows A8 and A10 fail. Both failures have one cause
(see Deviation D1). Nothing in the repository was edited or fixed.

Rules kept: no GitHub credentials, no API keys (`LLM_API_KEY` and `EMBEDDING_API_KEY` empty in the clean
`.env`), no paid call, the database password was typed by Kene and never printed, requirements-eval.txt and
requirements-ocr.txt were not installed, no repository file was edited, nothing was committed.

## Commands, exit codes and key output

| # | Command (run in the clean folder unless stated) | Exit | Key output |
|---|---|---|---|
| 1 | `git clone https://github.com/kene-obiekwe/nigeria-tax-copilot.git .` (`GIT_TERMINAL_PROMPT=0`) | 0 | HEAD 97187547c50208a42f5f0d3756ed18548ffb1213 |
| 2 | `C:\Python312\python.exe -m venv .venv` | 0 | `.venv\Scripts\python --version`: Python 3.12.2 |
| 3 | `.venv\Scripts\python -m pip install --no-cache-dir -r requirements.txt` | 0 | "Successfully installed" 70 packages; pip printed a notice "A new release of pip is available: 24.0 -> 26.3" |
| 4 | `.venv\Scripts\python -m pip freeze` | 0 | 70 lines (full list below); identical when repeated after the reproduction and the test runs |
| 5 | `Copy-Item .env.example .env` | 0 | Kene then typed `POSTGRES_PASSWORD`. Checked only that it is non-empty; `LLM_API_KEY` and `EMBEDDING_API_KEY` length 0 |
| 6 | in the main checkout: `docker compose ps`, then `docker compose down` | 0 | `taxcopilot-pgvector` was Up 5 hours (healthy); stopped and removed; network `taxy_default` removed; volume `taxy_pgdata` still listed |
| 7 | `docker compose -p taxyclean up -d` | 0 | volume `taxyclean_pgdata` and network `taxyclean_default` created; container `taxcopilot-pgvector` started |
| 8 | `docker compose -p taxyclean ps` (polled) | 0 | `Up 6 seconds (healthy)`, `0.0.0.0:5434->5432/tcp` |
| 9 | `docker image inspect pgvector/pgvector:0.8.7-pg17` | 0 | see Image digest below; the image was already in the local cache, so no pull happened |
| 10 | `.venv\Scripts\python eval\reproduce\reproduce.py --profile clean --a10-expected 1861/30` | 1 | stderr `start-up check failed: live_config`; `16 rows: 14 pass, 2 fail, 0 skipped, 0 pending (profile clean)`; table below |
| 11 | `git status --porcelain` after the reproduction and after the test runs | 0 | empty (no file changed in the clone) |
| 12 | `.venv\Scripts\python -m pytest -q -rfs -p no:cacheprovider` (repeat of A10, to capture failure detail) | 1 | `52 failed, 1808 passed, 31 skipped in 438.46s`; same counts as A10 |
| 13 | diagnostic script (scratchpad, read-only) comparing `config_p7.json` against `run_exit_check.current_config()` | 1 | `GuardError: the live guardrails configuration differs from config_g2_v4.json in ['disclosure_policy_sha256'], which is not a named override` |
| 14 | `Get-FileHash` and a CRLF conversion in memory of `data\curated\disclosure_policy.yml` | 0 | see D1 |
| 15 | diagnostic: copy of the clone (without `.venv`) in the scratchpad, only `data\curated\disclosure_policy.yml` converted to CRLF; `pytest -q` on `tests/test_e2e.py tests/test_g2_fix_3.py tests/test_startup.py tests/test_tools_exit_check.py` | 0 | `217 passed, 3 skipped` |
| 16 | same copy, full `pytest -q -rs -p no:cacheprovider` | 0 | `1860 passed, 31 skipped in 492.57s`, 0 failed |

Commands 10 to 12 ran against the clean database (`taxyclean_pgdata`). Commands 13 to 16 changed nothing in the
clone; 15 and 16 ran in a scratch copy. The README's "How to run" steps 6 to 11 were not run as separate
commands (the brief limited the walk-through to steps 1 to 5 and the reproduce section); reproduce.py ran the
fetch, verify, cache download, load and audit migration itself (rows A1 to A7).

## Image digest

- Image: `pgvector/pgvector:0.8.7-pg17`
- Image id: `sha256:151aa1c2e849e7828ed8aef2183ae15f692cbe887d2f28921d8b44b26e154d7e`
- Repo digest: `pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`

## Full reproduce table (profile clean, `--a10-expected 1861/30`)

| Id | Check | Expected | Actual | Pass |
|---|---|---|---|---|
| A1 | fetch_raw.py --mirror <release URL> | NRS scan present, sha256 956092e5...c403 (= manifest.csv); exit 0 | exit 0; sha256 956092e5...c403 | yes |
| A2 | verify_manifest.py | all 5 raw files match manifest.csv; exit 0 | exit 0; 5/5 files verified | yes |
| A3 | build_chunks.py, then git status --porcelain data | no change; v1_chunks.jsonl sha256 780467e7...e9c0; section_map.csv and build_manifest.json byte-identical | build exit 0; git status clean; sha256 780467e7...e9c0 | yes |
| A4 | fetch_assets.py | the two cache files present with the sha256 in assets.json; exit 0 | exit 0; both caches match | yes |
| A5 | check_pgvector.py (fresh volume in the clean clone) | pgvector present; exit 0 | exit 0; pgvector version: 0.8.7 | yes |
| A6 | load with the cache-only client | 400 chunks, 400 vectors from the cache, 0 from the API; corpus_loads chunks_sha256 = A3 | 400 chunks; vectors: 400 from cache, 0 from API; chunks_sha256 780467e7...e9c0 | yes |
| A7 | audit\migrate.py, then --check | audit-schema/1 applied; check exit 0 | migrate exit 0 (schema audit: audit-schema/1; versions [1, 2, 3]); check exit 0 (schema audit: audit-schema/1; versions [1, 2, 3]) | yes |
| A8 | start-up check (interface/startup.py), keys empty | passes with empty API keys; one app_starts row with outcome ok | start-up check failed: live_config; app_starts rows 0 -> 1, latest outcome failed | NO |
| A9 | retrieval recompute, dev and test, in memory | ranked ids equal the committed dev and test files for every question; test hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556; 0 query vectors from the API | dev 20 questions, hit@5 20, hit@1 16, MRR 0.8792, 0 differences; test 30 questions, hit@5 30, hit@1 22, MRR 0.8556, 0 differences; 0 query vectors from the API | yes |
| A10 | python -m pytest -q (no TAXY_RUN_API_TESTS) | 0 failed; 1861 passed, 30 skipped (the counts at the Phase 10 commit) | exit 1; 1808 passed, 52 failed, 31 skipped | NO |
| B1 | run_e2e.py --status | every pin matches; exit 0 | exit 0; 5 of 5 pins match | yes |
| B2 | G3 re-score in memory (score_run, g3_result) | sha256 dce44cd7...113c, 53c2bcd8...8841, b3ba66dd...3938 (D-149); run 1 a 244/244, b 246/246, c 0/40, d 1/50; run 2 a 245/245, b 245/245, c 0/40, d 1/50 | run 1 dce44cd7...113c; run 2 53c2bcd8...8841; G3 result b3ba66dd...3938; run 1 a 244/244 b 246/246 c 0/40 d 1/50; run 2 a 245/245 b 245/245 c 0/40 d 1/50 | yes |
| B3 | blind calibration re-score in memory | scores sha256 107f8fe0...6c6f (D-148) | sha256 107f8fe0...6c6f; mutations 60/60; real unsupported 2/2; human-supported flagged 1/131 | yes |
| B4 | G2 run 4 recount from holdout_final.json | 39 of 40 cases passed in each of 3 repeats (ADV4-009 fails); false refusals 1, 1, 1 (ADV4-044); 603 rendered claims (199, 206, 198), all supported (g2_results_v4.md Section 10) | 3 repeats; cases passed 39/39/39 of 40; failing [['ADV4-009'], ['ADV4-009'], ['ADV4-009']]; false refusals [['ADV4-044'], ['ADV4-044'], ['ADV4-044']]; rendered claims 199/206/198 = 603, unsupported 0 | yes |
| B5 | Phase 7 exit check record | exit_run1.json sha256 2c5f7fe4...d7dd, auto_pass true | sha256 2c5f7fe4...d7dd; auto_pass True | yes |
| B6 | README figures | every G1, G2 and G3 figure in the README equals the records | all 25 figures present; no unexpected count | yes |

`16 rows: 14 pass, 2 fail, 0 skipped, 0 pending (profile clean). Free: no API key is used and no paid call is possible.`

## A10 comparison

Expected at the Phase 10 commit: 1861 passed, 30 skipped, 0 failed (total 1891).
Clean clone: 1808 passed, 52 failed, 31 skipped (total 1891). The same counts came from the run inside
reproduce.py and from the separate pytest run.

### The 52 failures

All 52 are caused by D1 (below). By file:

| File | Failed | Mechanism |
|---|---|---|
| tests/test_e2e.py | 36 | `e2e_tools_run_exit_check.GuardError: the live guardrails configuration differs from config_g2_v4.json in ['disclosure_policy_sha256']` |
| tests/test_startup.py | 10 | the start-up check reports `live_config` as a failed check, so each test that expects a pass or a specific single failed check sees an extra `live_config` entry (examples: `At index 0 diff: 'live_config' != 'database'`; `Left contains one more item: 'app_starts_write'`); this includes `test_the_check_passes_on_the_repository` and `test_the_startup_module_imports_without_eval_only_or_ocr_packages` |
| tests/test_tools_exit_check.py | 5 | `tools_run_exit_check.GuardError`, same message as test_e2e |
| tests/test_g2_fix_3.py | 1 | `test_v4_entry_is_pinned_and_frozen` fails on the same pinned-hash chain |

Evidence that D1 is the single cause: in a scratch copy of the clone where only `data\curated\disclosure_policy.yml`
was converted from LF to CRLF, the same four files give 217 passed, 3 skipped, and the full suite gives
0 failed (command 16).

### The skip difference (31 against 30)

The skipped test that differs is `tests/test_retrieval.py:401`
(`test_bge_client_embeds_normalised_vectors`, reason "requirements-eval.txt is not installed"). It needs the
`sentence_transformers` package, which is in `requirements-eval.txt` and not in `requirements.txt`. The brief for
this check said to install nothing but `requirements.txt`, so that test skips here and presumably passed at the
Phase 10 commit in an environment that had `requirements-eval.txt` installed. With D1 removed and no eval
packages, the measured result is 1860 passed, 31 skipped, 0 failed: one pass fewer and one skip more than
1861/30, for this reason alone. I did not install `requirements-eval.txt` to confirm that the count then becomes
1861/30.

The other 30 skips match the expected 30 and are listed here for the record:
- 1 `test_api_guard.py:41` (paid API test, needs `TAXY_RUN_API_TESTS=1`)
- 1 `test_app_live.py`, 3 `test_generation_api.py`, 1 + 4 `test_note_tagger_api.py`, 5 + 6 `test_verification_api.py` (`LLM_API_KEY` not set): 20 tests
- 2 `test_smoke_query.py`, 1 + 1 `test_tools_api.py` (`EMBEDDING_API_KEY` not set): 4 tests
- 1 `test_e2e.py:93` (every pin is set), 1 `test_e2e.py:1214` and 1 `test_refusal.py:1102` (overseer holdout checks), 1 `test_retrieval.py:512` (final test run made), 1 `test_tools_exit_check.py:147` (config_p7.json is frozen): 5 tests
- Total 1 + 20 + 4 + 5 = 30. Together with `test_retrieval.py:401` this makes 31.

## Deviations

### D1. A clean clone does not match a pinned hash of `data/curated/disclosure_policy.yml` (blocks A8 and A10)

- What failed: reproduce.py row A8 (`start-up check failed: live_config`; one `app_starts` row written with outcome `failed`) and row A10 (52 test failures). Because A8 and A10 fail, reproduce.py exits 1 although the README says it "exits 0 only when every row passes".
- Exact error (from `run_exit_check.current_config()`): `the live guardrails configuration differs from config_g2_v4.json in ['disclosure_policy_sha256'], which is not a named override`.
- Cause: `eval/guardrails/config_g2_v4.json` pins `disclosure_policy_sha256` as
  `8f2316b6de8198e3b0985b1c6ce33823d90cf179cb7e1da5100a3d275db1b36c`. The committed file
  `data/curated/disclosure_policy.yml` has LF line endings (6598 bytes, 125 LF, 0 CR; `git ls-files --eol` shows
  `i/lf w/lf`, as `.gitattributes` sets `* text=auto eol=lf`) and hashes to
  `b11cd28b109ff698d67487ae67429e31bf0e9a6ecfe8163bad26475024c3c6f5`. The pinned value is what that same
  content gives with CRLF line endings (computed in memory; it equals the pin exactly). So the pin was taken over a
  CRLF working copy, while Git stores and checks out LF. The same hash is computed by `sha256_file` in
  `eval/guardrails/run_adversarial.py:314` on the raw bytes. `.gitattributes` was added at commit ae703f3
  (2026-10-18), which predates the pin, so the mismatch is in the file's working-copy bytes on the author's
  machine, not in the repository history. I did not look inside the main checkout to confirm this.
- What I did: nothing to fix it. I recorded the failure, found the cause with read-only diagnostics and a
  scratch copy, and left the clone unchanged (`git status --porcelain` empty).
- Consequence for the Phase 10 gate: the clean-clone check does not pass as the repository stands. The shipped
  app's own start-up check, as the README describes it ("On first load the app checks ... that it runs the
  shipped configuration"), would also fail on a clean clone and show "This service is not available right now"
  with no question box.
- Not checked: whether any other pinned hash depends on CRLF bytes. Every other reproduce row and the rest of
  the test suite passed, so none showed up in this walk-through.

### D2. A10 counts differ from 1861/30 for a second reason: `requirements-eval.txt` (1 test)

- One test (`tests/test_retrieval.py::test_bge_client_embeds_normalised_vectors`) is skipped without
  `requirements-eval.txt`, so a clean install of `requirements.txt` alone cannot give 1861 passed and 30 skipped.
  Details in the A10 comparison. Neither the README nor `docs/reference/reproduce.md` says that the A10 expected
  counts assume `requirements-eval.txt` is installed; `docs/reference/reproduce.md` only says the test suite has
  0 failures. If the reproduction guide intends a clean clone to reach exactly 1861/30, it needs to say so.

### D3. The README does not say the clean stack conflicts with another copy of the same compose file

- `docker-compose.yml` fixes `container_name: taxcopilot-pgvector` and host port 5434. A second checkout started
  with a different compose project name (`-p taxyclean`) therefore cannot run at the same time as the main checkout
  stack; the main stack had to be stopped first. The README says nothing about this. It matters only to someone
  who already has the project running; it did not affect a first-time reader.

### Observations that are not README errors

- The README step 1 clones into a new folder and then runs `Set-Location nigeria-tax-copilot`. This walk-through
  cloned into the already-empty target folder with `.` instead, as the brief set up.
- The README uses `python -m venv .venv` and `pip install -r requirements.txt`. This walk-through used
  `C:\Python312\python.exe -m venv .venv` and added `--no-cache-dir`, as the brief set. Neither changes what is installed.
- The pgvector image was already in the local Docker cache, so the first-time image pull was not exercised.
- README "How to run" steps 6 to 11 and the Streamlit app were not run on their own (see the note after the
  command table). reproduce.py covered the fetch, verify, cache download, load and audit migration (A1 to A7)
  and the start-up check (A8).
- pip printed a notice about a newer pip version on stderr; PowerShell showed it as an error, but the install
  exit code was 0.

## `pip freeze` of the clean venv

```
altair==6.3.0
annotated-types==0.8.0
anthropic==2.3.0
anyio==4.16.0
attrs==26.1.0
certifi==2026.10.23
cffi==2.1.1
charset-normalizer==3.5.2
click==8.5.1
colorama==0.4.6
cryptography==50.0.2
docstring_parser==0.18.0
filelock==4.2.10
fsspec==2026.9.0
h11==0.16.0
httpcore2==2.16.0
httpx2==2.16.0
huggingface_hub==0.36.2
idna==3.20
iniconfig==2.3.1
itsdangerous==2.2.0
Jinja2==3.1.6
jiter==0.19.0
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
MarkupSafe==3.0.4
narwhals==2.28.0
numpy==2.5.4
openai==3.35.1
opentelemetry-api==1.45.2
packaging==26.3
pandas==3.1.1
pdfminer.six==20231228
pdfplumber==0.11.4
pgvector==0.5.1
pillow==12.3.0
pluggy==1.7.0
protobuf==7.36.2
psycopg==3.3.6
psycopg-binary==3.3.6
pyarrow==26.0.1
pycparser==3.12
pydantic==2.14.3
pydantic_core==2.50.4
pydeck==0.9.3
Pygments==2.21.0
pypdfium2==4.30.0
pytest==9.1.1
python-dateutil==4.0.0
python-dotenv==1.2.4
python-multipart==0.0.33
PyYAML==6.0.2
referencing==0.37.0
regex==2026.11.9
requests==2.34.2
rpds-py==2026.9.1
sniffio==1.3.1
starlette==1.10.0
streamlit==1.66.0
tiktoken==0.8.0
tokenizers==0.21.0
tqdm==4.70.1
truststore==0.10.5
typing-inspection==0.4.5
typing_extensions==4.16.0
tzdata==2026.6
urllib3==2.8.0
uvicorn==0.54.0
watchdog==6.0.0
websockets==17.4
```

## State at the end of the walk-through

- Clean clone: the clean folder, HEAD unchanged, `git status --porcelain` empty. The folder and its `.venv` were left in place.

## Cleanup (after Kene's yes, 2026-11-13)

| Command | Exit | Key output |
|---|---|---|
| `docker compose -p taxyclean down -v` (clean folder) | 0 | container `taxcopilot-pgvector` stopped and removed; volume `taxyclean_pgdata` and network `taxyclean_default` removed; `docker volume ls` still lists `taxy_pgdata` |
| `docker compose up -d` (main checkout) | 0 | network `taxy_default` and container `taxcopilot-pgvector` created and started; `docker compose ps` shows `Up 6 seconds (healthy)` on `0.0.0.0:5434->5432/tcp` |

The main checkout database is running and healthy on its original volume `taxy_pgdata`, which was never removed.


---

## Run 2

Date: 2026-11-13 (walk-through ended 20:46 +01:00)
Walker: the same separate session as run 1, using only the public README of
https://github.com/kene-obiekwe/nigeria-tax-copilot and the docs it links to.
Folder: `run2` inside the clean folder (a new, empty folder; it sits inside the run 1 clone folder, so the run 1 clone shows `run2/` as untracked)
Clone HEAD: `0e9b42c14d04ff3ec2d75c8f2825cef6da9105e7` ("fix(phase10): store the disclosure policy bytes that its pinned hash covers", 2026-11-13 20:22:56 +0100); this is the required 0e9b42c
Python: `python --version` in the clean venv gives `Python 3.12.2` (venv built from `C:\Python312\python.exe`)
Result: reproduce.py exits 0, 16 of 16 rows pass, A10 gives 1864 passed, 0 failed, 31 skipped. D1 no longer occurs.

Rules kept: no GitHub credentials, no API keys (`LLM_API_KEY` and `EMBEDDING_API_KEY` empty in the run2 `.env`), no paid call,
the database password was typed by Kene and never printed, only `requirements.txt` installed, no repository
file edited, nothing committed.

### Commands, exit codes and key output

| # | Command (run in `run2` unless stated) | Exit | Key output |
|---|---|---|---|
| 1 | `git clone https://github.com/kene-obiekwe/nigeria-tax-copilot.git .` (`GIT_TERMINAL_PROMPT=0`) | 0 | HEAD 0e9b42c14d04ff3ec2d75c8f2825cef6da9105e7 |
| 2 | `C:\Python312\python.exe -m venv .venv` | 0 | Python 3.12.2 |
| 3 | `.venv\Scripts\python -m pip install --no-cache-dir -r requirements.txt` | 0 | "Successfully installed" 70 packages |
| 4 | `.venv\Scripts\python -m pip freeze` | 0 | 70 lines; byte-identical to the run 1 freeze, and unchanged when repeated after the reproduction |
| 5 | `Copy-Item .env.example .env` | 0 | Kene then typed `POSTGRES_PASSWORD`; checked only that it is non-empty; both API keys empty |
| 6 | in the main checkout: `docker compose ps`, then `docker compose down` | 0 | `taxcopilot-pgvector` was Up 26 minutes (healthy); stopped and removed; network `taxy_default` removed; volume `taxy_pgdata` still listed (no `-v`) |
| 7 | `docker volume ls` filtered for `taxyclean_pgdata` | 0 | 0 matches, so the volume is new |
| 8 | `docker compose -p taxyclean up -d` | 0 | volume `taxyclean_pgdata` and network `taxyclean_default` created; container `taxcopilot-pgvector` started |
| 9 | `docker compose -p taxyclean ps` (polled) | 0 | `Up 6 seconds (healthy)`, `0.0.0.0:5434->5432/tcp` |
| 10 | `docker volume inspect taxyclean_pgdata` | 0 | `taxyclean_pgdata created 2026-11-13T19:34:28Z` (a fresh volume for this run) |
| 11 | `docker image inspect pgvector/pgvector:0.8.7-pg17` | 0 | see Image digest below; the image was already in the local cache, so no pull happened |
| 12 | `.venv\Scripts\python eval\reproduce\reproduce.py --profile clean --a10-expected 1864/31` | 0 | `16 rows: 16 pass, 0 fail, 0 skipped, 0 pending (profile clean)`; table below. It ran longer than 10 minutes because of A10. |
| 13 | `git rev-parse HEAD` and `git status --porcelain` after the reproduction | 0 | HEAD unchanged; status empty (no file changed in the clone) |

Order of steps: the brief had the main checkout database stopped before the clean one started, so step 5 of the
README's "How to run" (`docker compose up -d`) was run as `docker compose -p taxyclean up -d` after that stop. As in run 1, the
README's "How to run" steps 6 to 11 and the Streamlit app were not run as separate commands; reproduce.py ran
the fetch, verify, cache download, load and audit migration itself (rows A1 to A7) and the start-up check (A8).

### Image digest

- Image: `pgvector/pgvector:0.8.7-pg17`
- Image id: `sha256:151aa1c2e849e7828ed8aef2183ae15f692cbe887d2f28921d8b44b26e154d7e`
- Repo digest: `pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d` (the same as run 1)

### Full reproduce table (profile clean, `--a10-expected 1864/31`)

| Id | Check | Expected | Actual | Pass |
|---|---|---|---|---|
| A1 | fetch_raw.py --mirror <release URL> | NRS scan present, sha256 956092e5...c403 (= manifest.csv); exit 0 | exit 0; sha256 956092e5...c403 | yes |
| A2 | verify_manifest.py | all 5 raw files match manifest.csv; exit 0 | exit 0; 5/5 files verified | yes |
| A3 | build_chunks.py, then git status --porcelain data | no change; v1_chunks.jsonl sha256 780467e7...e9c0; section_map.csv and build_manifest.json byte-identical | build exit 0; git status clean; sha256 780467e7...e9c0 | yes |
| A4 | fetch_assets.py | the two cache files present with the sha256 in assets.json; exit 0 | exit 0; both caches match | yes |
| A5 | check_pgvector.py (fresh volume in the clean clone) | pgvector present; exit 0 | exit 0; pgvector version: 0.8.7 | yes |
| A6 | load with the cache-only client | 400 chunks, 400 vectors from the cache, 0 from the API; corpus_loads chunks_sha256 = A3 | 400 chunks; vectors: 400 from cache, 0 from API; chunks_sha256 780467e7...e9c0 | yes |
| A7 | audit\migrate.py, then --check | audit-schema/1 applied; check exit 0 | migrate exit 0 (schema audit: audit-schema/1; versions [1, 2, 3]); check exit 0 (schema audit: audit-schema/1; versions [1, 2, 3]) | yes |
| A8 | start-up check (interface/startup.py), keys empty | passes with empty API keys; one app_starts row with outcome ok | start-up check ok; app_starts rows 0 -> 1, latest outcome ok | yes |
| A9 | retrieval recompute, dev and test, in memory | ranked ids equal the committed dev and test files for every question; test hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556; 0 query vectors from the API | dev 20 questions, hit@5 20, hit@1 16, MRR 0.8792, 0 differences; test 30 questions, hit@5 30, hit@1 22, MRR 0.8556, 0 differences; 0 query vectors from the API | yes |
| A10 | python -m pytest -q (no TAXY_RUN_API_TESTS) | 0 failed; 1864 passed, 31 skipped (the counts at the Phase 10 commit) | exit 0; 1864 passed, 0 failed, 31 skipped | yes |
| B1 | run_e2e.py --status | every pin matches; exit 0 | exit 0; 5 of 5 pins match | yes |
| B2 | G3 re-score in memory (score_run, g3_result) | sha256 dce44cd7...113c, 53c2bcd8...8841, b3ba66dd...3938 (D-149); run 1 a 244/244, b 246/246, c 0/40, d 1/50; run 2 a 245/245, b 245/245, c 0/40, d 1/50 | run 1 dce44cd7...113c; run 2 53c2bcd8...8841; G3 result b3ba66dd...3938; run 1 a 244/244 b 246/246 c 0/40 d 1/50; run 2 a 245/245 b 245/245 c 0/40 d 1/50 | yes |
| B3 | blind calibration re-score in memory | scores sha256 107f8fe0...6c6f (D-148) | sha256 107f8fe0...6c6f; mutations 60/60; real unsupported 2/2; human-supported flagged 1/131 | yes |
| B4 | G2 run 4 recount from holdout_final.json | 39 of 40 cases passed in each of 3 repeats (ADV4-009 fails); false refusals 1, 1, 1 (ADV4-044); 603 rendered claims (199, 206, 198), all supported (g2_results_v4.md Section 10) | 3 repeats; cases passed 39/39/39 of 40; failing [['ADV4-009'], ['ADV4-009'], ['ADV4-009']]; false refusals [['ADV4-044'], ['ADV4-044'], ['ADV4-044']]; rendered claims 199/206/198 = 603, unsupported 0 | yes |
| B5 | Phase 7 exit check record | exit_run1.json sha256 2c5f7fe4...d7dd, auto_pass true | sha256 2c5f7fe4...d7dd; auto_pass True | yes |
| B6 | README figures | every G1, G2 and G3 figure in the README equals the records | all 25 figures present; no unexpected count | yes |

16 rows: 16 pass, 0 fail, 0 skipped, 0 pending (profile clean). Free: no API key is used and no paid call is possible.

### A10 counts

Expected (README, `requirements.txt` only): 1864 passed, 31 skipped, 0 failed. Actual: 1864 passed, 0 failed,
31 skipped (exit 0). They match exactly. Against run 1 (1808 passed, 52 failed, 31 skipped at 97187547), the 52
failures are gone, and the 31 skips are the same set (the one skip that needs `requirements-eval.txt` is
`tests/test_retrieval.py:401`, which the README now names). Row A8 also passes now (`start-up check ok`, one
`app_starts` row with outcome `ok`), which was the other D1 symptom.

### Run 1 deviations, status in this run

- D1 (`disclosure_policy_sha256` mismatch on a clean clone): not reproduced; A8 and A10 pass.
- D2 (A10 counts depend on `requirements-eval.txt`): the README now says to expect 1864/31 with `requirements.txt` only; the counts matched.
- D3 (the clean stack conflicts with another copy of the compose file): the README now has a "Stop any other copy of this stack first" note. See observation O1 on where it is placed.

### Deviations in this run

None. No README step or linked-doc step was wrong or missing, and none needed a workaround.

### Observations (not counted as deviations)

- O1: the "Stop any other copy of this stack first" note is in the "How to reproduce" section, after step 5 of "How to run"
  (`docker compose up -d`). A reader who follows the README in order and still has another copy running would hit the
  container name and port 5434 conflict at step 5 before reaching the note. I followed the brief's order (stop the main stack
  first) and never hit the error, and a reader with no other copy running is not affected, so I did not count it as a deviation.
  If the Overseer counts it, the gate result below changes to NOT MET.
- O2: as in run 1, README step 1 clones into a new `nigeria-tax-copilot` folder and runs `Set-Location nigeria-tax-copilot`; this run cloned
  into the empty `run2` folder with `.` instead, and used `C:\Python312\python.exe` and `--no-cache-dir` as the brief set.
- O3: the pgvector image was already in the local Docker cache, so a first-time image pull was not exercised.

### `pip freeze` of the run 2 venv

```
altair==6.3.0
annotated-types==0.8.0
anthropic==2.3.0
anyio==4.16.0
attrs==26.1.0
certifi==2026.10.23
cffi==2.1.1
charset-normalizer==3.5.2
click==8.5.1
colorama==0.4.6
cryptography==50.0.2
docstring_parser==0.18.0
filelock==4.2.10
fsspec==2026.9.0
h11==0.16.0
httpcore2==2.16.0
httpx2==2.16.0
huggingface_hub==0.36.2
idna==3.20
iniconfig==2.3.1
itsdangerous==2.2.0
Jinja2==3.1.6
jiter==0.19.0
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
MarkupSafe==3.0.4
narwhals==2.28.0
numpy==2.5.4
openai==3.35.1
opentelemetry-api==1.45.2
packaging==26.3
pandas==3.1.1
pdfminer.six==20231228
pdfplumber==0.11.4
pgvector==0.5.1
pillow==12.3.0
pluggy==1.7.0
protobuf==7.36.2
psycopg==3.3.6
psycopg-binary==3.3.6
pyarrow==26.0.1
pycparser==3.12
pydantic==2.14.3
pydantic_core==2.50.4
pydeck==0.9.3
Pygments==2.21.0
pypdfium2==4.30.0
pytest==9.1.1
python-dateutil==4.0.0
python-dotenv==1.2.4
python-multipart==0.0.33
PyYAML==6.0.2
referencing==0.37.0
regex==2026.11.9
requests==2.34.2
rpds-py==2026.9.1
sniffio==1.3.1
starlette==1.10.0
streamlit==1.66.0
tiktoken==0.8.0
tokenizers==0.21.0
tqdm==4.70.1
truststore==0.10.5
typing-inspection==0.4.5
typing_extensions==4.16.0
tzdata==2026.6
urllib3==2.8.0
uvicorn==0.54.0
watchdog==6.0.0
websockets==17.4
```

### Cleanup

Done after Kene's yes (2026-11-13):

| Command | Exit | Key output |
|---|---|---|
| `docker compose -p taxyclean down -v` (in `run2`) | 0 | container `taxcopilot-pgvector` stopped and removed; volume `taxyclean_pgdata` and network `taxyclean_default` removed; `docker volume ls` still lists `taxy_pgdata` |
| `docker compose up -d` (main checkout) | 0 | network `taxy_default` and container `taxcopilot-pgvector` created and started; `docker compose ps` shows `Up 6 seconds (healthy)` on `0.0.0.0:5434->5432/tcp` |

The main checkout database is running and healthy on its original volume `taxy_pgdata`, which was never removed. The `run2` folder and its `.venv` were left in place.

Gate result: PASSED

## After run 2

Observation O1 was fixed in the README after the run, by moving the "Stop any other copy of this stack first"
note into "How to run" step 5, before `docker compose up -d` (a one-line pointer stays in "How to reproduce").
No check depends on it, so there was no rerun. The Phase 10 exit gate passed on this run (D-188).
