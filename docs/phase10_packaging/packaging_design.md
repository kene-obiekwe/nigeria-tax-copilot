# Phase 10 Design: Documentation, Disclaimers and Packaging

**Status:** approved by Kene 2026-11-13 (D-171 to D-185 and C-13). Design only: no code, no paid call.
Every decision was approved as recommended except D-177, where Kene chose option (b), Mermaid only; the
approved options are marked in Section 9 and recorded in the
[decision log](../00_planning/decision_log.md).
**Baseline:** commit aff858f (Phase 9 closed, exit gate passed).
**Plan reference:** [implementation plan](../00_planning/implementation_plan.md), Phase 10: "A third party could
clone the repository, follow the README, rebuild the corpus and vector store, and reproduce the evaluation
report's numbers."

> This project is a research and reference tool, not tax advice. Nothing in this design is legal advice
> either: Section 4 lists what the privacy wording must be checked against and what Kene must decide; it
> does not decide whether the project complies with any law.

## 0. Inputs this design rests on

Facts checked in the repository on 2026-11-13. The build sessions re-check each one before relying on it.

| Fact | Value | Where |
|---|---|---|
| Raw files | 5 PDFs; 4 committed, the 27.7 MB NRS scan git-ignored, with its URL and sha256 pinned | `data/manifest.csv`, D-11 |
| Fetch script | `python/ingest/fetch_raw.py` already takes `--mirror URL`, tried only when the source URL fails, and checks size and sha256 before keeping a file | `fetch_raw.py` docstring |
| Chunk build | `python/ingest/build_chunks.py` writes no time; two builds from the same inputs are byte-identical. `v1_chunks.jsonl` sha256 `780467e7d27c38aadd727596db9ecdee87c14c4b66d30bf11e7e1e7a7198e9c0`, 400 chunks, unchanged since commit f330db9 (2026-11-02) | `data/derived/chunks/build_manifest.json` |
| Build inputs | `build_manifest.json` records the sha256 of `data/manifest.csv` among its inputs, so **editing `manifest.csv` changes the build manifest** | `build_chunks.py`, `input_files()` |
| OCR | the NTAA re-OCR runs in `.venv-ocr` (about 60 minutes); its output is committed in `data/derived/ocr/ntaa_nrs/` and is byte-identical across runs | D-12, D-13 |
| Embedding cache | `data/cache/` is git-ignored. Corpus vectors: `openai__text-embedding-3-small.jsonl` (400 rows, 12.5 MB). Query vectors: `query_embeddings/openai__text-embedding-3-small.jsonl` (516 rows, 16.1 MB; input sha256 and vector, no question text). D-19 point 5 left a release asset of the cache to Phase 10 | `.gitignore`, D-19 |
| Embedding cost | the full corpus embedding costs $0.00126 (63,059 tokens) | [vector store](../phase03_embedding/vector_store.md), Section 4 |
| Embedding client | `OpenAIEmbeddingClient` raises if `EMBEDDING_API_KEY` is empty, even when every vector is cached. `load(conn, client=None)` loads chunks without vectors; `QueryCache.embed()` builds a client only on a cache miss and takes a `client_factory` | `python/embed/clients.py`, `load.py`, `eval/retrieval/run_eval.py` |
| G1 | test split run once with `config_final.json` (sha256 `6ce743b3...3684`): hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556; `--split test --final` refuses once `test_final.json` exists | `eval/retrieval/results/test_final.json`, D-20, D-21 |
| G2 | not met: decision runs 1 to 4 all failed (D-50, D-72, D-94, D-115); run 4: 39 of 40 cases in every repeat (ADV4-009), 1 false refusal in each repeat (ADV4-044), all 603 rendered claims supported; about $6.29 of $8.00 | [G2 results v4](../phase06_guardrails/g2_results_v4.md), Sections 10 and 11 |
| G3 | passed (D-149); score files and verdict pinned by sha256 (`dce44cd7...`, `53c2bcd8...`, `b3ba66dd...`); the scorer is free, writes each file once and refuses to overwrite | [evaluation report](../phase08_evaluation/evaluation_report.md), Sections 1 and 7 |
| Paid evaluation | `run_e2e.py` refuses a further run once its markers exist; `e2e_v1` and the test split are spent (D-133, D-135) | evaluation report, Section 7 point 4 |
| Docker | `docker-compose.yml` fixes `container_name: taxcopilot-pgvector` and host port 5434; volume `pgdata` | `docker-compose.yml` |
| Licence | `LICENSE` (MIT, D-07) and `NOTICE.md` exist | repository root |
| README | still says "Status: Phase 1"; has the disclaimer, a G3 box, scope, out-of-scope, run-the-app | `README.md` |
| UI privacy text | `PRIVACY_LINE` and `PRIVACY_DETAILS` in `app/copy.py`, the D-167 wording | `app/copy.py`, interface design Section 6.4 |
| Repository | private (D-08); `gh` is not installed on the project laptop; Node 24 and npx are | `git remote -v`, shell |
| History scan | no agent file, `.env` or secrets file in any of the 72 commits; key-shaped strings appear only as fake keys in redaction tests (`test_audit_write.py`, `test_e2e.py`, `test_generation.py`, `test_tools_exit_check.py`); every commit carries Kene's personal email as author address | `git log --all`, `git grep` over all commits |

## 1. README rewrite

The README is the entry point for a reader who has never seen the project. It states the capability and
the limits with the same care, and every number in it is copied from a committed file named below, never
retyped from memory. Model and vendor names appear as factual references only (D-06).

### 1.1 Outline and order

| # | Section | What it says | Facts it must cite | Source in the repository |
|---|---|---|---|---|
| 0 | Title and disclaimer box | "Nigerian SME Tax Reference" as the product name under the repository name; the short disclaimer (not tax advice, not for filing, dated snapshot, personal income tax out of scope) | snapshot 2026-10-18 | `DISCLAIMER` in `python/generation/generate.py`; D-10, D-64, D-167 |
| 1 | Status | one line: phases done, what is next, whether a live demo exists | phases 0 to 10 complete; deployment is Phase 11; no live URL yet | decision log; Section 7.1 of this design |
| 2 | Results at a glance | a 4-row table: G1, G2, G3, Phase 7 exit check, each with result and a link; **G2 reads "not met"** in the same type as the passes | G1 30 of 30; G2 not met (runs 1 to 4); G3 pass with the four run figures; Phase 7 passed on run 1 | `test_final.json`; `g2_results_v4.md` Section 11; `g3_result.json`; `exit_run1_review.json` |
| 3 | The problem | SMEs need to know what the 2025 Acts say; the text is long, two official prints disagree, and a wrong answer has a cost | the Acts commenced 2026-01-01; two texts in circulation (DQ-01 to DQ-05) | `scope_verification.md`; decision log DQ rows |
| 4 | Why retrieval rather than a plain model | a plain model answers from what it learned, with no citation, no snapshot date and no way to check a sentence; here every claim cites a retrieved section, is checked against that text, the system declines when the corpus does not cover the question, and arithmetic is done in code | the design rules: retrieve first, cite, verify, refuse, calculate in code | `generation_design.md`, `guardrails_design.md`, `tools_design.md` |
| 5 | Scope and snapshot date | the exact v1 corpus: which Act, which chapters and parts; the cited text is the Official Gazette; the National Assembly text is a comparison copy | snapshot 2026-10-18; NTA in-scope parts; NTAA in-scope sections; D-09 | `v1_corpus_definition.md`; `data/manifest.csv` `in_scope_parts` |
| 6 | What it does not cover | personal income tax and PAYE first; sections outside v1 (examples: NTA Sections 13 and 58, Fourth Schedule); state and local levies, stamp duties, customs and excise, petroleum taxes, subsidiary legislation; periods before 2026; changes after the snapshot; any filing system; each question answered on its own | D-64; evaluation report Section 6 | evaluation report Section 6; interface design Section 3.7 |
| 7 | Architecture | the diagram (Section 3) and one short paragraph per stage, each linking its design document | stage list, models (D-03), retrieval config (D-21), guardrails, tools, audit log | `diagrams/`; phase designs |
| 8 | How to run | prerequisites (Windows 11 and PowerShell as tested; Python 3.12; Docker Desktop); clone; venv; install; `.env` (the reader types their own password); start the database; fetch and verify raw files; load the corpus; migrate the audit schema; run the app; the viewer. A note that other platforms are untested | commands as in CLAUDE.md "How to run", plus `migrate.py` | CLAUDE.md run lines; interface design Section 4.2 |
| 9 | How to reproduce | the free reproduction (Section 2): one command after setup, its expected output table and what "pass" means; what cannot be reproduced for free and why (paid, non-deterministic, spent sets) | the expected values of Section 2.3 | this design, Section 2; `eval/reproduce/` (new) |
| 10 | Evaluation results | the G3 table (run 1, run 2, pooled, Wilson intervals); hit@5 on T and N; the honest caveats in plain words: the reviewer is a calibrated model session, T has a known retrieval bias, N is 20 questions, 0 of 40 bounds the leak rate only to about 8.8%; G2 not met and why; spend per phase | figures from D-149 and evaluation report Sections 1, 3 and 6.1; G2 Section 11 | `g3_result.json`, `e2e_run{1,2}_scores.json`, `evaluation_report.md`, `g2_results_v4.md` |
| 11 | Known limitations | the consolidated list of Section 7.3, short, with a link to `docs/reference/limitations.md` | ADV4-009, ADV4-044, N05, Q032, N17, Phase 9 notes | Section 7.3 |
| 12 | Disclaimer | the full text: research and reference, not tax, legal or financial advice, not a substitute for a licensed professional, not for filing or compliance decisions, dated snapshot, the figures describe this system on these questions only | evaluation report disclaimer block | `evaluation_report.md` lines 7 to 13 |
| 13 | Privacy | the Section 4 text, as approved | retention 90 days (D-159); recipients; no IP, name or email stored by the app | interface design Section 6; Section 4 here |
| 14 | Licence and usage | code MIT; statute texts not licensed by the project; research and demonstration only; not a filing interface; not designed for real taxpayer data | D-07; Section 5 | `LICENSE`, `NOTICE.md` |
| 15 | Documentation | link to the docs index and the three reference pages | | `docs/README.md` |

The current G3 box at the top is replaced by row 2, so that G2 "not met" sits next to the passes rather
than further down.

### 1.2 Rules for the README text

1. Every figure links to the file it comes from. A build test (Section 8, step B4) reads the README and
   checks the G1, G3 and G2 figures against `test_final.json`, the two score files and `g3_result.json`, so
   the README cannot drift from the records.
2. No career or portfolio framing (CLAUDE.md Section 1). No mention of how the project was built.
3. The word "advisory" appears only in the repository name (D-08).
4. "Section" is written in full.
5. Commands are PowerShell, one per fenced block.

## 2. Exit gate made testable

### 2.1 What "reproduce" means

Each number is in one of three classes. The class decides what reproduction means.

| Class | Numbers | Reproduce means | Cost |
|---|---|---|---|
| **A. Deterministic build** | raw-file hashes; the 400 chunks and their sha256; the vector store contents; the audit schema; the start-up check; the test suite | **exact**: the same bytes or the same hashes | free |
| **A. Deterministic retrieval** | G1 hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556 on the test split; the dev results of `config_final.json` | **exact**: every question's ranked chunk ids equal the committed result file, recomputed from the published cached vectors with exact cosine search (no ANN index, D-19) | free |
| **B. Model-dependent, re-scored** | G3 (a) to (d) per run and pooled; Phase 8 hit@5 on T and N; the blind calibration scores; the G2 run 4 counts; the Phase 7 exit check and Phase 9 live check spend | **exact re-scoring of the committed records**: the free scorers, run on the committed run and review files, give byte-identical score files (sha256 as in D-148 and D-149) or equal counts | free |
| **C. Model-dependent, regenerated** | new answers to the same questions | not part of the Phase 10 gate (Section 2.4) | paid |

So the third party reproduces every **number** in the report exactly. They do not regenerate the
**answers**: those came from paid, non-deterministic model calls, and the records of what the system
showed are committed and pinned. The README says this in one plain paragraph.

### 2.2 Exact retrieval needs the cached vectors

`text-embedding-3-small` vectors are not guaranteed to be bit-identical between calls, so a reader who
re-embeds the corpus and the 516 query inputs may see a different rank where two chunks score close.
Exact retrieval reproduction therefore needs the vectors that produced the committed results. They are
published as GitHub Release assets with their sha256 (D-172, Section 6), and the corpus is loaded from
them with no API call. Re-embedding stays possible as a documented alternative with a tolerance:

| Path | Key needed | Cost | Pass |
|---|---|---|---|
| **Published caches (recommended, the gate path)** | none | $0 | every question's top-5 ids and order equal `test_final.json` and `dev_final.json` |
| Re-embed (alternative, not the gate) | `EMBEDDING_API_KEY` | about $0.0013 for the corpus plus under $0.0001 for queries | test hit@5 at least 27 of 30 (G1 still met); every rank difference is listed, none hidden |

### 2.3 The checks and what a pass looks like

A new free script, `eval/reproduce/reproduce.py`, runs the checks in order after the README setup steps
and prints one row per check (id, what, expected, actual, pass). It writes nothing inside the repository
and makes no paid call: it uses a cache-only embedding client whose `embed_documents` and `embed_query`
raise, so a cache miss is a failure, never an API call. It imports the existing functions and changes
none of them (D-173).

| Id | Check | Expected |
|---|---|---|
| A1 | `python\ingest\fetch_raw.py --mirror <release URL>` | NRS scan present, sha256 `956092e5...c403`; exit 0 |
| A2 | `python\ingest\verify_manifest.py` | all 5 raw files match `manifest.csv` |
| A3 | `python\ingest\build_chunks.py`, then `git status --porcelain data` | no change: `v1_chunks.jsonl` sha256 `780467e7...e9c0`, `section_map.csv` and `build_manifest.json` byte-identical |
| A4 | `eval\reproduce\fetch_assets.py` | the two cache files present with the sha256 in `eval/reproduce/assets.json` |
| A5 | `python\check_pgvector.py` on a fresh volume | pgvector present |
| A6 | load with the cache-only client | 400 chunks, 400 vectors from the cache, 0 from the API; the `corpus_loads` row's `chunks_sha256` equals A3 |
| A7 | `python\audit\migrate.py`, then `--check` | `audit-schema/1` applied; check exit 0 |
| A8 | the start-up check (`python/interface/startup.py`) with empty API keys | passes; one `app_starts` row with status ok |
| A9 | retrieval recompute, dev and test, in memory | ranked ids equal the committed files for every question; test hit@5 30 of 30, hit@1 22 of 30, MRR 0.8556; 0 query vectors from the API |
| A10 | `python -m pytest -q` (no `TAXY_RUN_API_TESTS`) | 0 failed; passed and skipped counts equal to the counts taken at the Phase 10 commit (recorded in the check record) |
| B1 | `eval\e2e\run_e2e.py --status` | every pin matches |
| B2 | G3 re-score in memory (`score_run` and `g3_result` on the committed records and reviews) | serialised bytes give sha256 `dce44cd7...`, `53c2bcd8...`, `b3ba66dd...` (D-149); run 1 a 244/244, b 246/246, c 0/40, d 1/50; run 2 a 245/245, b 245/245, c 0/40, d 1/50 |
| B3 | blind calibration re-score in memory | scores sha256 `107f8fe0...6c6f` (D-148) |
| B4 | G2 run 4 recount from `eval/guardrails/results/v4/holdout_final.json` | 39 of 40 cases passed in each of 3 repeats; false refusals 1, 1, 1 (ADV4-044); 603 rendered claims; the counts of `g2_results_v4.md` Section 10 |
| B5 | Phase 7 exit check record | `exit_run1.json` sha256 `2c5f7fe4...cd7dd`, `auto_pass` true |
| B6 | README figures | every G1, G2 and G3 figure in the README equals the records (the same check as the build test) |

**A pass** is every row passing, in a clean clone, with no API key in `.env`, and no step the README
leaves out or gets wrong. Any README step that had to be changed or added to get through is a failure:
the README is fixed and the affected checks are run again from that step.

OCR is not re-run in the gate: it needs a second venv and about 60 minutes, and its committed output is
pinned by `ocr_manifest.json`. The README lists it as an optional deeper check (D-13 commands), with the
expectation "output byte-identical".

### 2.4 The paid rerun: options and recommendation

| Option | What | Cost | Problem |
|---|---|---|---|
| (a) **Re-score only (recommended)** | Classes A and B above | $0 | the answers themselves are not regenerated |
| (b) Capped rerun of `e2e_v1` | one more run on the frozen `config_p8.json` into a separate results folder (the runner needs a new mode, since it refuses a third run), cap $1.00; tolerance: (c) 0 of 40, (d) at most 5 of 50, status equal to run 1 on at least 85 of 90 cases | about $0.82, plus a full review session for (a) and (b) | the set is spent (D-133, D-135), so it measures nothing held-out; (a) and (b) need a new model review of about 245 items, which is the expensive part; a new runner mode touches pinned evaluation code |
| (c) Rerun on a new held-out set | a fresh set and two runs | about $1.65 plus label and review sessions | a new evaluation, not a reproduction |

**Recommendation: (a).** It reproduces every published number exactly for $0. A regenerated answer
can only be compared with a tolerance, and the plan already has a paid check of regenerated answers where
it matters: Phase 12 repeats a sample of evaluation questions against the live URL (plan Section 10, "the
live deployment reproduces the Phase 8 evaluation results on a spot-check sample"). The README states
that Phase 12 check as the paid reproduction once it exists.

### 2.5 Who runs the clean-clone check

| Option | Who | For | Against |
|---|---|---|---|
| (a) **A NEW implementation session (recommended)** | a Sonnet high session started in a fresh folder outside the project folder; Kene types the database password; the overseer re-checks | nothing of the project's state is in its context, so it follows only the README; its log is the evidence | same laptop: Docker image and Python install are shared |
| (b) Kene by hand | Kene | a person following the README | long; hard to record every output |
| (c) An outside person | someone Kene trusts | a true third party | needs a public repo and their time; not under the project's control |

**Recommendation: (a)**, run like this:

1. The repository is public first (D-175) and the release assets exist (Section 6), so the clone and the
   downloads work **without any GitHub credentials**, as for a stranger.
2. Folder: `C:\Users\DELL\Documents\taxy-clean-check` (outside the project folder). Clone over HTTPS.
3. Fresh venv from `C:\Python312\python.exe`; `pip install --no-cache-dir -r requirements.txt`, so no
   wheel comes from the laptop's pip cache. The eval-only and OCR requirements are not installed.
4. Fresh `.env` from `.env.example`; Kene types `POSTGRES_PASSWORD`; **both API keys stay empty**, which
   proves no paid call is possible.
5. Fresh database volume: in the main checkout `docker compose down` (the main `pgdata` volume is kept;
   the fixed container name would otherwise clash); in the clean folder
   `docker compose -p taxyclean up -d`, which creates the volume `taxyclean_pgdata`.
6. Follow the README literally, then run `eval\reproduce\reproduce.py`.
7. Afterwards, with Kene's yes: `docker compose -p taxyclean down -v` in the clean folder (removes only
   the throwaway volume), then `docker compose up -d` in the main checkout.
8. The session writes `docs/phase10_packaging/clean_clone_check.md` **in the main checkout**: every
   command, its exit code and the key output lines, the reproduce table, `python --version`,
   `pip freeze` of the clean venv, the Docker image digest, and every README deviation with its fix.

The shared Docker image and Python install are accepted and stated in the record: the image is pinned by
tag (`pgvector/pgvector:0.8.7-pg17`) and the record gives its digest.

## 3. Architecture diagram

### 3.1 Format

| Option | For | Against |
|---|---|---|
| (a) **Mermaid source plus a rendered SVG (recommended)** | GitHub renders Mermaid in Markdown; the source diffs as text; the SVG works anywhere | rendering the SVG needs `@mermaid-js/mermaid-cli`, which downloads a headless Chromium (about 150 MB) once |
| (b) Mermaid only | nothing to install | outside GitHub the reader sees source text |
| (c) Hand-drawn SVG or draw.io | full control of layout | drifts from the code silently; binary-like diffs |

The design recommended (a). **Approved: (b), Mermaid only (D-177, Kene 2026-11-13)**, which differs
from that recommendation: no SVG is rendered, so no mermaid-cli and no Chromium download.

Files: `diagrams/architecture.mmd` is the one source, and the same Mermaid block goes in
`docs/reference/architecture.md`, which GitHub renders. A short `diagrams/README.md` says that the `.mmd`
file is the source and where it is rendered. A drift test checks that the block in `architecture.md`
equals `architecture.mmd` and that every file path named in a node exists. Outside GitHub a reader sees
the Mermaid source text; this is accepted.

### 3.2 Stages, matching the code

The build session draws these stages and checks each against the module before drawing it. Names in
brackets are the modules.

**Corpus build (offline, deterministic, run once per snapshot)**

1. Raw sources: five PDFs pinned in `data/manifest.csv` [`ingest/fetch_raw.py`, `ingest/verify_manifest.py`].
2. NTAA text: re-OCR of the NRS scan in its own venv [`ingest/ocr_ntaa.py`], aligned to the Gombe print with
   proofread sections [`ingest/align_ntaa.py`, `data/curated/ntaa_nrs_proofread/`].
3. Text and structure: pdfplumber character-geometry split of header, body and margin notes
   [`ingest/extract.py`, `ingest/parse_structure.py`, `ingest/scope.py`, `ingest/tables.yml`].
4. Chunks and section map: 400 chunks under a 450-token cap with context headers
   [`ingest/chunker.py`, `ingest/section_map.py`, `ingest/build_chunks.py`].
5. Discrepancy register between the gazette and National Assembly texts
   [`ingest/build_register.py`, `data/curated/register_decisions.yml`].
6. Embedding and load: `text-embedding-3-small`, cache, pgvector tables `corpus_chunks`,
   `chunk_embeddings`, `corpus_loads` [`embed/load.py`, `embed/clients.py`, `embed/schema.sql`].

**One question (the app)**

7. Page and start-up check: fails closed unless the shipped configuration, corpus and audit schema match
   [`app/main.py`, `interface/startup.py`].
8. Personal-data screen (hard block, D-160) and admission with a receipt row before any paid call
   [`interface/screen.py`, `interface/service.py`, `interface/limits.py`].
9. Pre-check on the question: section and Schedule references, other Acts, foreign law, scope notes
   [`generation/guardrails.py` `pre_check`]; the note tagger runs alongside (Claude Haiku 5.5, three votes)
   [`generation/note_tagger.py`].
10. Retrieval: vector top 5 plus exact section lookup, one keyword slot, the commencement lookup and one
    definition attachment (P1 to P8) [`retrieval/retrieve.py`, `retrieval/references.py`,
    `retrieval/commencement.py`, `retrieval/definitions.py`]; then the relevance floor (refuses as
    `low_relevance`).
11. Generation: Claude Sonnet 5.5 writes claims that cite passage labels, with the bounded tool loop
    [`generation/generate.py`, `generation/prompt.py`, `generation/llm.py`]; tools: section lookup
    (passages P9 onward) and the Decimal calculator with the pinned rate table [`tools/lookup.py`,
    `tools/taxcalc.py`, `data/curated/calc_rates.yml`].
12. Verification: the rule check and the Claude Haiku 5.5 claim verifier with evidence quotes; the gap
    check; claims removed or moved to gaps [`generation/verify.py`].
13. Disclosures for disputed text and rendering with notes, sources and the disclaimer
    [`generation/disclosure.py`, `generate.render`].
14. Audit: the `gen-log/3` record (local JSONL) projected into the `audit` schema; the answer card shown
    only if it equals the rendered text [`generation/audit_log.py`, `audit/project.py`, `audit/store.py`,
    `app/cards.py`].
15. Review (local only, never deployed): the log viewer and CLIs on a read-only role [`app/review.py`,
    `audit/report.py`, `audit/show_query.py`, `audit/compare_log.py`].

Refusal exits are drawn as side branches: personal data blocked (8), pre-check refusals such as
`no_such_section`, `section_outside_v1` and `foreign_law` (9), `low_relevance` (10), `not_covered` from the
model (11), and the error and withheld paths (14).

## 4. Privacy section and the Nigeria Data Protection Act 2023

### 4.1 What this section does and does not do

It lists the points of the Nigeria Data Protection Act 2023 (NDPA) and the Nigeria Data Protection
Commission's General Application and Implementation Directive 2025 (GAID) that the wording should be
checked against, says what the current wording covers, and flags what Kene must decide. It reaches no
conclusion on whether the NDPA applies or is met. Section numbers come from secondary summaries and the
Act's table of contents; **the build session checks each one against the gazetted text** before any is
quoted, and Kene may want a qualified person to read the final text.

### 4.2 Checklist

| # | NDPA point (to confirm) | What the project does now | Gap in the Section 6.4 wording | Kene decides |
|---|---|---|---|---|
| P1 | Application and who the controller is (Part I; definitions, s65) | the public demo is run by Kene as an individual | the README names no controller | whether the README names Kene (or a project name) as the operator of the demo |
| P2 | Principles: lawful, fair, transparent, purpose-limited, minimal, accurate, storage-limited, secure, accountable (s24) | data minimised (no IP, name or email kept by the app; HMAC user key; hard-block screen); purpose stated; 90-day retention | none on substance; the wording should name the purpose once ("so that any answer can be reviewed") — it does | — |
| P3 | Lawful basis (s25) and conditions for consent (s26) | none stated; using the demo implies acceptance at most | **no lawful basis is stated** | the basis: (a) consent, shown as an explicit acknowledgement before the first question ("I understand my question is stored for 90 days and sent to the providers named"); or (b) legitimate interests, stated in the notice. (a) is the more conservative engineering choice and costs one UI control; it is not a legal conclusion |
| P4 | Information to the data subject (s27; GAID Article 27) | the UI line and "Privacy details" popover; the README paragraph | missing from the README draft: the operator's identity and contact; the hosting providers (Render, Supabase) and the sign-in provider (Google), which the UI popover already names; the rights in P5; that providing data is optional; that no automated decision with legal effect is made | approve the amended text in Section 4.3 |
| P5 | Data-subject rights: access, rectification, erasure, restriction, objection, withdrawal of consent, portability, complaint to the Commission (about s34 to s38) | signed-in users can see and delete their history; anonymous users can ask for a reference to be erased (Kene runs `erase_query`) | only deletion is mentioned; no complaint route | which rights the notice lists (recommend all, with one contact), and the response time to commit to (confirm the period the Act or GAID sets) |
| P6 | Cross-border transfer (Part VIII, s41 to s43) | questions go to Anthropic and OpenAI and, when hosted, to Render, Supabase and Google, all outside Nigeria | **transfer outside Nigeria is not stated** | the basis for transfer, and whether to state the countries or regions (Phase 11 fixes the Render and Supabase regions) |
| P7 | Security (s39) and breach notification to the Commission (s40; about 72 hours) | RLS, SECURITY DEFINER functions, no PUBLIC execute, owner-only erase and purge, provider encryption | not a notice item; needs a Phase 11 runbook entry | the Phase 11 runbook records who notifies and how |
| P8 | Registration as a data controller of major importance (s44; GAID Article 9 and the Commission's guidance notice) | a small demo | none | whether the demo falls in any category; recommend checking the GAID thresholds before the public link opens (Phase 11) |
| P9 | Impact assessment for high-risk processing (s28) | low volume, no profiling | none | whether one is needed (likely not; confirm) |
| P10 | Sensitive personal data (s30) and children (s31) | the screen blocks emails, phone numbers and long digit runs; names, addresses and health or religious details are not detected | the notice does not say the service is not meant for children | whether to add "not intended for children" (recommend yes) |
| P11 | Pseudonymous data is still personal data | the signed-in key is an HMAC of issuer and subject | "one-way key" is accurate; the text must not imply the key is anonymous | — |
| P12 | Processors and their terms | provider API policies "apply" | the notice should not promise what the providers do; it should link their policies | — |
| P13 | Platform logs | the app never reads IP addresses; Render keeps its own request logs | "It does not store IP addresses" should be scoped to the app | approve "The app does not store ...; the hosting platform keeps its own request logs" |

The same review also corrects two statements outside the privacy text that become inaccurate once a
public demo stores questions:

- `NOTICE.md` and the README disclaimer say the project "does not collect, store or process real taxpayer
  data". A public demo stores whatever a user types for 90 days. Proposed: "It is not designed to process
  real taxpayer data. Users are asked not to enter it, and a screen blocks email addresses, phone numbers
  and long numbers before anything is sent or stored."
- The UI popover and the README must say the same things. Any change to `app/copy.py` is a UI text change,
  not a change to the measured system (the generation path and `config_p7.json` are untouched), and the
  existing AppTest text checks are updated with it.

### 4.3 Proposed README privacy text (amended from interface design Section 6.4)

Square brackets mark what Kene decides.

> **Privacy.** The public demo is run by [operator name] for research and demonstration. It stores each
> question, the answer and how it was checked (the statute passages retrieved and each statement's
> verification result) for 90 days, so that any answer can be reviewed, and then deletes them.
> It does so with your consent: before your first question the page asks you to tick a box confirming that
> you understand this, and nothing is sent or stored until you do. The app does not store IP addresses, names or email
> addresses; the hosting platform keeps its own request logs. If you sign in, it stores a one-way key
> derived from your sign-in account so it can show you your own past questions; you can delete them at any
> time from "My questions". Questions are sent outside Nigeria: to Anthropic (Claude) to write and check
> the answer, to OpenAI to search the statutes, and to the hosting providers Render and Supabase; Google
> handles sign-in if you use it. Their own data policies apply [links]. Do not enter real personal or
> taxpayer data; the service is not intended for children. You may ask for access to, correction or
> deletion of your data, or object to its use, by writing to [contact route] and, for an anonymous
> question, quoting its reference; you may also complain to the Nigeria Data Protection Commission.

Lawful basis (P3): **consent, approved by Kene 2026-11-13 (D-180 (a))**. `[operator name]` (Kene's
name as on the GitHub profile) and `[contact route]` (the project mailbox, D-181) stay placeholders until
Kene types them in chat at Build 2.

**UI acknowledgement (D-180 (a)).** Shown above the question box until it is ticked, once per browser
session; the question box's submit button stays disabled until then, and nothing is sent or stored before
it. The tick itself is not stored.

> Your question and the answer are stored for 90 days and sent outside Nigeria to the providers named in
> Privacy details, so that the answer can be written and checked.
>
> ☐ I understand and agree. I will not enter real personal or taxpayer details.

### 4.4 Deletion and contact route (D-167 open item)

| Option | For | Against |
|---|---|---|
| (a) **A dedicated project mailbox (recommended)** | private; one address for every request and for P1; Kene's personal address stays out of the public repository | Kene creates and watches one more mailbox |
| (b) Kene's personal email | nothing to set up | publishes a personal address in a public repository |
| (c) A GitHub issue | no new account | public: a request links the requester's GitHub account to a reference; not everyone has an account |
| (d) A web form (for example Google Forms) | structured requests | adds another processor and another notice item |

**Recommendation: (a). Approved: (a) (D-181, Kene 2026-11-13).** Kene creates the mailbox (the build sessions never create accounts) and gives
the address in chat; it goes into the README and the UI popover.

## 5. Licence and usage note

| Item | Options | Recommendation |
|---|---|---|
| Code | (a) keep MIT (D-07); (b) Apache-2.0 (patent grant); (c) a non-commercial licence | **(a)**: already in place; simple; no reason found to change |
| Project-written documents and evaluation sets | (a) covered by the MIT licence with the code; (b) CC BY 4.0 for `docs/` and `eval/` sets | **(a)**: one licence, already stated; no separate document licence needed for a project this size |
| Statute texts (`data/raw/`) and their transcriptions (`data/derived/` chunks, OCR text, proofread text) | (a) not licensed by the project: "remain the work of their official publishers", redistributed with source and hash for verification; (b) stop redistributing raw PDFs and keep only fetch-by-URL | **(a)** as `NOTICE.md` already says, extended to name the derived files. (b) would not remove the four PDFs already in history and the chunks contain the text anyway. **Kene to confirm** that redistributing the official texts is acceptable; this design does not decide the copyright status of Nigerian enactments |
| Embedding vectors (release assets) | stated as derived from the statute texts by the OpenAI embeddings API, published for reproduction only | state it in the release notes and `NOTICE.md` |
| Usage | research and demonstration; not tax, legal or financial advice; not an interface to any filing system or to the Nigeria Revenue Service; not designed for real taxpayer data (Section 4.2 wording) | in `NOTICE.md` and README section 14 |

## 6. D-11: NRS scan backup on a GitHub Release

`gh` is not installed, so Kene does this in the GitHub web page. The assets in one release:
`nigeria_tax_administration_act_2025_gazette_nrs.pdf` (D-11) and, under D-172, the two
`openai__text-embedding-3-small.jsonl` cache files (corpus and query), renamed
`corpus_embeddings_openai_text-embedding-3-small.jsonl` and
`query_embeddings_openai_text-embedding-3-small.jsonl` so the two names differ.

**Before** (build session, free): copy the two cache files to a scratch folder, record their sha256 and
sizes in `eval/reproduce/assets.json` with the release URLs, and print the sha256 of the NRS scan (it must
equal `956092e5...c403` in `manifest.csv`). The cache files are snapshots: the query cache must not grow
between this step and the upload (no eval script runs in between).

**Steps for Kene:**

1. On github.com open the repository, then "Releases", then "Draft a new release".
2. "Choose a tag": type `corpus-2026-10-18`, choose "Create new tag on publish", target `main` (the Phase
   10 commit).
3. Title: `Corpus snapshot 2026-10-18: source scan and embedding caches`.
4. Description: paste the text the build session gives (file names, sha256, sizes, the NRS source URL,
   "published for reproduction; the statute text remains the work of its official publisher").
5. Drag in the three files from the paths the build session gives.
6. Leave "Set as a pre-release" off; click "Publish release".
7. On each asset, right-click and copy the link; paste the three links in chat.

**Hash check** (build session, after the links are in chat, once the repository is public):
`fetch_raw.py --dest <scratch folder> --mirror <link>` cannot prove the mirror while the NRS source URL
still works, because it tries the source first. So the session downloads each asset to a scratch folder
with `fetch_assets.py --dest <scratch folder>` (the same streamed sha256 and size check) and compares
against `manifest.csv` and `assets.json`. A mismatch: the asset is deleted from the release and uploaded
again.

**Where the mirror URL is written:** `data/README.md`, the README "How to run" step and `assets.json`.
**Not `data/manifest.csv`**: it is a chunk-build input (Section 0), so editing it would change
`build_manifest.json` and break check A3.

## 7. Carried items

### 7.1 README Status line

At the Phase 10 commit: **"Status: Phases 0 to 10 complete (corpus, retrieval, generation, guardrails,
tools, evaluation, interface, packaging). Not yet deployed: the live demo comes in Phase 11. Gate G2 was
not met; see Results."** Phase 11 replaces the second sentence with the live URL and the cold-start note.

### 7.2 Documentation index and reference pages

The plan (Section 7) names `docs/architecture.md`, `docs/methodology.md` and `docs/limitations.md` at the
top of `docs/`. CLAUDE.md asks for sub-folders by phase or topic. Proposed (D-178):

- `docs/reference/architecture.md`: the diagram, the stage table of Section 3.2 with links.
- `docs/reference/limitations.md`: the one consolidated list of Section 7.3.
- `docs/reference/reproduce.md`: the long form of README section 9 and the check table of Section 2.3.
- No separate `methodology.md`: the evaluation method is `evaluation_report.md` Section 2 and the phase
  designs; the docs index points there. Recorded as a correction (C-13) to the plan's file list.
- `docs/README.md`: rows for this design, the clean-clone record, the three reference pages,
  `diagrams/`, `eval/reproduce/`; the "Planned folder structure" line about the three top-level documents
  is replaced by the C-13 note; the Phase 10 folder row stays.

### 7.3 Known limitations for the README and `limitations.md`

Grouped, user-facing first, each with its source:

1. **Personal income tax, PAYE and an individual's bands and rates are out of scope**, as are sections
   outside v1 and other taxes (D-64, D-10; evaluation report Section 6).
2. The corpus is a snapshot of 2026-10-18; later amendments are not included.
3. G2 not met, carried limitations: subsidiary legislation named "Regulations" gets no scope note
   (ADV4-009); exported-services questions miss Section 187 and the exported-service definition
   (ADV4-044) (D-115).
4. Phase 8: N05 retrieval miss (Section 163(1)(r), sporting activities); Q032 lists 2 of the 6
   Thirteenth Schedule activities; the N17 levy calculation is declined without gross turnover; Q009 and
   N11 incomplete; the reviewer is a calibrated model session, not an independent person; T retrieval
   bias; 0 of 40 bounds the leak rate only to about 8.8% (evaluation report Section 6.1).
5. Carried from Phases 6 and 7 (evaluation report Sections 6.3 and 6.4): true claims sometimes removed as
   bridging facts; gaps with legal content but no citation field; the gap reference check binding a
   section to the wrong Act; invented provision links; foreign-law wordings the pre-check misses; tools can
   be skipped; interest is never computed.
6. Phase 9 notes: the personal-data screen does not detect names or addresses; each question is answered
   on its own (no memory); the log viewer is local only; the low-relevance refusal still runs the note
   tagger (a small Haiku cost, recorded in the log); sign-in, limits and the cost cap are built but switched
   on only in Phase 11 (D-162, D-163); rotating the HMAC secret detaches signed-in histories.
7. Platforms: built and tested on Windows 11 with PowerShell; other platforms untested.

## 8. Build plan

### 8.1 Steps

| Step | What | Files | Who | Cost |
|---|---|---|---|---|
| 0 | Kene decides D-171 to D-185 | decision log rows (written in step B1) | Kene, overseer | $0 |
| B1 | Reproduction tooling: `eval/reproduce/assets.json`, `fetch_assets.py` (reuses `fetch_raw.download`), `reproduce.py` (checks A1 to B6, cache-only client, writes nothing in the repository); tests with fakes; the asset snapshot of Section 6 "Before"; decision rows D-171 to D-185 and C-13 | `eval/reproduce/*`, `tests/test_reproduce.py`, `docs/00_planning/decision_log.md` | NEW session "Phase 10 Build 1" | $0 |
| B2 | Diagram (D-177 (b), Mermaid only): `diagrams/architecture.mmd`, `diagrams/README.md`, the same block in `docs/reference/architecture.md`; drift test (block equals the `.mmd` file; every path in a node exists); no SVG | `diagrams/*`, `docs/reference/architecture.md`, `tests/test_docs.py` | same session as B1 (second prompt) | $0 |
| — | **Stop: overseer review of B1 and B2; commit on Kene's go** | | overseer | |
| B3 | Words: README rewrite (Section 1), `docs/reference/limitations.md` and `reproduce.md`, privacy text (Section 4.3) in README and `app/copy.py` with AppTest updates, the consent acknowledgement of Section 4.3 (D-180 (a)), `NOTICE.md`, `data/README.md` mirror line, docs index; the README-figures test | `README.md`, `NOTICE.md`, `app/copy.py`, `app/main.py` (the acknowledgement), `tests/test_app.py`, `tests/test_docs.py`, `docs/reference/*`, `docs/README.md`, `data/README.md` | NEW session "Phase 10 Build 2" | $0 |
| — | **Stop: overseer review of B3, including every privacy sentence against Section 4; commit on Kene's go; push on Kene's yes** | | overseer | |
| K1 | Pre-publication check: the history scan of Section 0 re-run at the new HEAD; Kene makes the repository public (D-175) | — | overseer runs the scan; Kene flips the setting | $0 |
| K2 | The GitHub Release (Section 6), then the hash check of the three assets | `eval/reproduce/assets.json` URLs if they differ | Kene, then the Build 2 session | $0 |
| C1 | Clean-clone check (Section 2.5), README fixes if any, the check record | `docs/phase10_packaging/clean_clone_check.md`, `README.md` fixes | NEW session "Phase 10 clean-clone check" | $0 |
| — | **Stop: overseer verifies the record (re-runs `reproduce.py` in the main checkout, reads the deviations), Kene's go to commit; Phase 10 exit gate** | | overseer | |

### 8.2 Sessions, models and effort

| Session | Steps | Model and effort | Why |
|---|---|---|---|
| Phase 10 Build 1 | B1, then B2 | Sonnet, high | multi-file tooling and tests from a fixed spec (CLAUDE.md Section 4, row 3); the exact-compare logic must not touch pinned code; B2 is a Mermaid source file, one document and a drift test (D-177 (b)) |
| Phase 10 Build 2 | B3, K2 hash check | Sonnet, high | many documents with figures checked by tests; the privacy text is fixed by Section 4.3 and Kene's decisions, so no legal design is left to the session |
| Phase 10 clean-clone check | C1 | Sonnet, high | follows the README literally and records evidence; a new session has no project context, which is the point |
| Overseer | reviews | Opus, medium or high | verification |

**Spend: $0.** No step calls a paid API: the clean clone has no keys, and the reproduction is cache-only.
The only downloads are the release assets and the Python packages of the clean venv.

### 8.3 What can go wrong

| Risk | Mitigation |
|---|---|
| A clean clone hits a test that needs the eval-only packages (bge) | A10 reports it; the fix is a test-only skip when the package is missing, never installing `requirements-eval.txt` into the check venv |
| The start-up check needs a key even though it calls nothing paid | A8 shows it; the fix is in the check's own code only if it builds a client eagerly; reported as a deviation |
| A transitive package resolves differently in the fresh venv | A3 and A10 catch a behaviour change; `pip freeze` is in the record; a lock file is left to the Phase 11 Dockerfile (D-183) |
| Docker container name clash | the main container is stopped first (Section 2.5 step 5) |
| The query cache grows after the asset snapshot | no eval script runs between the snapshot and the upload; `assets.json` pins the sha256, so a different file fails A4 |
| The README and the records drift later | the README-figures test (B3) runs in the normal suite |

## 9. Decisions

Next free ids checked in the decision log on 2026-11-13: D-170, C-12 and DQ-19 are the last in use.

| Id | Decision | Options | Recommendation | Approved (Kene 2026-11-13) |
|---|---|---|---|---|
| D-171 | What "reproduce" means for the Phase 10 exit gate | (a) classes A (exact rebuild and retrieval) and B (exact re-scoring of committed records), free; paid regeneration left to the Phase 12 live spot check; (b) as (a) plus one capped rerun of `e2e_v1` (cap $1.00) with the tolerance of Section 2.4; (c) a new held-out set and two runs | **(a)**: every published number reproduced exactly for $0; a rerun on a spent set measures nothing held-out and needs a full new review | (a) |
| D-172 | How exact retrieval is reproduced | (a) publish the corpus and query embedding caches as release assets pinned by sha256; re-embedding documented as an alternative with the Section 2.2 tolerance; (b) re-embed only, tolerance hit@5 at least 27 of 30; (c) commit the caches to git (28.6 MB) | **(a)**: exact, no key, no cost; completes D-19 point 5; (c) breaks the 10 MB rule of D-11 | (a) |
| D-173 | Where the reproduction code goes | (a) new files under `eval/reproduce/` only, importing existing functions; a cache-only embedding client; recomputing the test split in memory and comparing is a check, not a second run, and writes nothing; no change under `python/` or to existing `eval/` files; (b) add `--verify` modes to `run_eval.py` and `score_e2e.py`; (c) make the embedding client lazy in `python/embed/` | **(a)**: no pinned code changes; one command for the whole check | (a): recomputing the spent test split in memory and comparing it with `test_final.json` is a verification, not a second run; it writes nothing and is never used for tuning (D-20 stands) |
| D-174 | Who runs the clean-clone check and how | (a) a NEW session in a folder outside the project, unauthenticated HTTPS clone of the public repo, fresh venv with `--no-cache-dir`, empty API keys, fresh compose project `taxyclean`, record in `clean_clone_check.md`, overseer re-checks; (b) Kene by hand; (c) an outside person | **(a)** | (a) |
| D-175 | Repository visibility | (a) public after step B3 is committed and pushed and the pre-publication scan is clean, before the release and the clean-clone check; (b) private until Phase 12, clean-clone check with Kene's credentials; (c) public now | **(a)**: a third party and the release mirror need a public repository; the README is ready first | (a) |
| D-176 | Author email in commit history | (a) keep history; set the GitHub noreply address for future commits and turn on GitHub's "block command line pushes that expose my email"; (b) rewrite history to the noreply address before going public | **(a)**: (b) changes every commit hash, and the decision log and reports cite hashes (8ff3b16, bd59e73, a6b6c21 and others); Kene decides whether the address being public is acceptable | (a): Kene accepts that the existing author address stays visible in the history once public |
| D-177 | Diagram format | (a) Mermaid source plus an SVG rendered by pinned mermaid-cli, with a drift test; (b) Mermaid only; (c) hand-drawn SVG | **(a)** | **(b), Mermaid only**: differs from the recommended (a); no SVG, no mermaid-cli, no Chromium download |
| D-178 | Reference documents | (a) `docs/reference/{architecture,limitations,reproduce}.md`, no separate methodology page, C-13 records the change to the plan's file list; (b) the three plan files at the top of `docs/`; (c) everything in the README | **(a)**: follows CLAUDE.md Section 2; one place each | (a), with C-13 |
| D-179 | README outline | (a) Section 1.1 order, with G2 "not met" in the results-at-a-glance table at the top and a README-figures test; (b) keep the current README and add sections | **(a)** | (a) |
| D-180 | Privacy text and lawful basis | (a) the Section 4.3 text, in README and `app/copy.py`, with consent shown as an explicit acknowledgement before the first question; (b) Section 4.3 with legitimate interests stated, no new control; (c) the Section 6.4 text unchanged | **(a) or (b) is Kene's call** (not a legal conclusion); the design recommends (a) as the more conservative and cheap option; (c) leaves P3, P4, P6 and P13 open | (a): consent, as an explicit acknowledgement before the first question (Section 4.3) |
| D-181 | Deletion and contact route | (a) a dedicated project mailbox Kene creates; (b) Kene's personal email; (c) GitHub issues; (d) a web form | **(a)** | (a): a dedicated project mailbox Kene creates; the address is given at Build 2 |
| D-182 | Licence and usage | (a) code and project-written files MIT (D-07); statute texts and transcriptions not licensed by the project; NOTICE and README wording corrected to "not designed to process real taxpayer data"; Kene confirms redistribution of the official texts; (b) add CC BY 4.0 for docs; (c) stop redistributing raw PDFs | **(a)** | (a): Kene confirms that redistributing the official texts is acceptable |
| D-183 | Dependency lock | (a) no lock file in Phase 10; the clean-clone record keeps `pip freeze`; the Phase 11 Dockerfile decides a lock; (b) add `requirements.lock.txt` now from a fresh venv | **(a)**: direct pins plus pdfplumber's exact `pdfminer.six==20231228` requirement already fix the text-extraction stack (Pillow is unpinned but only renders images); the image build is where a lock pays | (a) |
| D-184 | The GitHub Release (D-11) | (a) one release `corpus-2026-10-18` with the NRS scan and the two cache files, made by Kene in the web page, hash-checked after upload, mirror URL in `data/README.md`, the README and `assets.json`, never in `manifest.csv`; (b) the NRS scan only | **(a)** | (a) |
| D-185 | NDPA follow-ups outside the README | (a) Phase 11 checks the GAID registration categories (P8) before the public link opens and adds breach-notification and erasure-request steps to the runbook (P5, P7); the "not intended for children" line goes in now (P10); (b) all deferred to Phase 12 | **(a)** | (a) |
| C-13 | Correction to the plan's file list | the plan's `docs/architecture.md`, `docs/methodology.md` and `docs/limitations.md` become `docs/reference/architecture.md`, `docs/reference/limitations.md` and `docs/reference/reproduce.md`; the methodology is the evaluation report Section 2 | with D-178 | approved with D-178 (a) |

## 10. Sources for Section 4

Secondary summaries used to locate the NDPA and GAID points; the build session checks the gazetted text
before quoting any section:

- Nigeria Data Protection Act 2023, text hosted by DataGuidance and KPMG Nigeria.
- Nigeria Data Protection Commission, General Application and Implementation Directive 2025 (ndpc.gov.ng).
- Law-firm summaries of the Act and the GAID (Mondaq, DLA Piper Privacy Matters).
