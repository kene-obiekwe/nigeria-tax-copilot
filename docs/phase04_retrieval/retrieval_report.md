# Retrieval Layer and Hit-Rate Report (Phase 4b)

Status: **configuration frozen on 2026-11-02 from dev results. Single test run made on
2026-11-02: hit@5 30 of 30, gate G1 (D-20) passed.** Results in heading 7.

Question set: [questions_v1.jsonl](../../eval/retrieval/questions_v1.jsonl), sha256
`48daa9fa13afdec2183b62185dbae20400ec0f3ef9d677623c7ac6199987ba45` (see the
[question set](question_set.md)). Headings 1 to 6 use only the 20 dev questions. The 30 test questions were not embedded, queried or read until the single test run in heading 7.

## 1. Components

`python/retrieval/retrieve.py`: `retrieve(question, k=5, model=..., filters=None)` returns ranked
chunks, each with a score, its cosine distance to the question and the reasons it was returned
(for example `section lookup: Section 66, Nigeria Tax Administration Act 2025 (cosine distance
0.3730)` or `vector rank 2 (cosine distance 0.5289)`).

| Component | What it does |
|---|---|
| Exact section lookup | A question that names a section ("section 148 of the Nigeria Tax Act", "s. 66 of the NTAA", "sections 20 and 21") gets that section's chunks first, nearest to the question first. Each reference takes the Act named after it, else the Act named before it. Where **no Act is named**, the section is looked up in both Acts and the chunks of both are ordered together by cosine distance, so the closer Act leads and the other still shows. A section outside the v1 corpus is ignored. Numbers are gazette numbers (D-09). |
| Vector search | Exact cosine distance over one model's vectors, no ANN index (D-19). With lookup and keyword search off, this is the Phase 3 search exactly (a test checks it). |
| Keyword search (optional) | PostgreSQL full text (`to_tsvector('english', ...)` over the context header and text) gives the stemmed lexemes, scored with BM25 (k1 1.2, b 0.75, the textbook defaults, not tuned). BM25 replaces `ts_rank`, which has no inverse document frequency and lets common words such as "tax" decide the order. |
| Fusion | Vector and keyword candidates (top 50 of each) merged by reciprocal rank fusion, rrf_k 60 (the published default). Ties go to the better vector rank, then the chunk id. |
| Metadata filter (optional) | `filters` limits every component: `act`, `chapter`, `part`, `unit_type`, or `tax_area` (`vat`, `income`, `administration`; NTA Chapter 9, which holds the Section 202 definitions, belongs to both NTA areas). Filters are never inferred from the question; no dev result justified it. |

Schedules are not looked up by name ("the Eleventh Schedule"): no dev question named one, and
vector search found both dev schedule questions in the top 2.

## 2. Embedding models

| Model | Dimensions | Where it runs | Query input | Corpus cost |
|---|---|---|---|---|
| `text-embedding-3-small` (D-03) | 1536 | OpenAI API | the question | loaded in Phase 3 |
| `BAAI/bge-small-en-v1.5` (D-03 baseline) | 384 | this machine, CPU | the model card's retrieval instruction plus the question | free; 59,382 tokens, no chunk over 512 |

The bge client (`python/embed/clients.py`) loads the weights at a pinned Hugging Face revision
(`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`), normalises the vectors and refuses an input over
512 tokens instead of truncating it. Its packages are in `requirements-eval.txt` (torch 2.14.1
CPU, sentence-transformers 5.7.0, transformers 4.51.3 to keep the `tokenizers==0.21.0` pin),
which the deployed image does not install. The corpus was embedded with the existing loader:
`python\embed\load.py --model BAAI/bge-small-en-v1.5`.

Dev query embeddings: 20 OpenAI requests, 349 tokens ($0.000007); bge 20 local queries, free.
Both are cached in `data/cache/query_embeddings/` (git-ignored), keyed by the sha256 of the query
input, with no question text.

## 3. Evaluation runner

`eval/retrieval/run_eval.py --split dev --config <file>` retrieves the top 20 chunks per question,
takes the rank of the first chunk matching a gold unit (the `smoke_query.matches` rule), and
writes `eval/retrieval/results/<split>_<config name>.json`: per question the gold units, that
rank, hit@1, hit@3, hit@5, the reciprocal rank within the top 5, and the top 5 with scores and
reasons; then the summary, overall and by category. Result files hold question ids, never
question text. MRR is the mean reciprocal rank within the top 5 (0 when no gold unit is in the
top 5).

**Test-split guard.** Without `--final` only the dev rows are kept as the file is read.
`--split test` needs `--final`; `--final` runs only `eval/retrieval/config_final.json`, refuses if
its sha256 differs from the hash recorded in `run_eval.py` and below, and refuses if
`results/test_final.json` already exists (the file is opened for exclusive creation). Every
refusal happens before any question is loaded or embedded.

## 4. Dev results (20 questions)

Configurations are in `eval/retrieval/configs/`; all use 50 candidates and rrf_k 60.

| Config | Model | Lookup | Keyword | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|---|---|
| `openai_vector` | OpenAI | no | no | 14 | 17 | 18 (90%) | 0.779 |
| `bge_vector` | bge | no | no | 10 | 16 | 17 (85%) | 0.646 |
| **`openai_section`** | OpenAI | yes | no | **16** | **19** | **20 (100%)** | **0.879** |
| `openai_section_keyword` | OpenAI | yes | yes | 14 | 17 | 19 (95%) | 0.789 |
| `bge_section` | bge | yes | no | 11 | 17 | 18 (90%) | 0.696 |
| `bge_section_keyword` (best bge) | bge | yes | yes | 13 | 18 | 19 (95%) | 0.771 |

Chosen configuration by category (2 dev questions each):

| Category | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| `cit_rates_levy` | 2 | 2 | 2 | 1.000 |
| `deductions_gains` | 1 | 2 | 2 | 0.667 |
| `vat` | 1 | 2 | 2 | 0.750 |
| `exemptions_incentives` | 2 | 2 | 2 | 1.000 |
| `definitions` | 2 | 2 | 2 | 1.000 |
| `schedules` | 1 | 2 | 2 | 0.750 |
| `ntaa_registration_returns` | 2 | 2 | 2 | 1.000 |
| `ntaa_assessment_objection` | 2 | 2 | 2 | 1.000 |
| `ntaa_payment_penalties` | 1 | 1 | 2 | 0.625 |
| `section_reference` | 2 | 2 | 2 | 1.000 |

## 5. Dev misses (gold unit not in the top 5)

| Config | Question | Gold rank | Reason |
|---|---|---|---|
| `openai_vector` | Q047 (Section 192, NTA) | not in top 20 | The question has no topic words, only "section 192 ... about", so it lands on generic "tax" definitions and Section 194. Fixed by section lookup. |
| `openai_vector` | Q050 (Section 66, NTAA) | not in top 20 | Same: generic "tax authority" definitions. Fixed by section lookup. |
| `bge_vector`, `bge_section` | Q007 (deducting regulator fines) | 6 | "Fines" pulls in NTAA offence and company provisions (Sections 105 and 107 on failing to deduct or remit tax, Section 53); the Section 21 list of deductions not allowed is sixth. |
| `bge_vector`, `bge_section` | Q041 (tax on a share of sales) | 7 | NTA company-income sections (8, 11, 10) outrank NTAA Section 38 (deemed profit or income assessment); the question does not use statute wording. |
| `bge_vector` | Q047 (Section 192, NTA) | 6 | No topic words; fixed by section lookup in `bge_section`. |
| `openai_section_keyword` | Q041 | 13 | Vector search ranks Section 38 first, but BM25 favours NTA Sections 11, 8 and 4, which share "profit", "sale" and "tax" with the question. Fusion pushes the right answer out. |
| `bge_section_keyword` | Q041 | not in top 20 | The same keyword pull, on a weaker vector ranking. |

The chosen configuration has no dev miss. Its four questions not at rank 1: Q007 at 3 (two Section 20
"deductions allowed" chunks rank above Section 21, "deductions not allowed"), Q015 at 2 (Section 155 on collection
of VAT by persons other than the supplier, above Section 151 on taxable supply by non-residents), Q029 at 2 (First Schedule paragraphs 21
to 22 above the rate table), Q043 at 4 (the Section 41 objection chunks above Section 49 on
payment pending objection).

## 6. Chosen configuration

`eval/retrieval/config_final.json`: `text-embedding-3-small`, exact section lookup on, keyword
search off, no filter, 50 candidates, top 5.

sha256 `6ce743b317a58260f7eb397c7a6e9a7e14afab86cbde2dbc296273131c9e3684`

Reasons:

1. **Best on dev and simplest near the best.** It is the top configuration (20 of 20). The only
   others within one question (19 of 20) both add keyword fusion, a component more. With 20 dev
   questions one question is 5 points, so the 1-question margin is not evidence that keyword
   fusion hurts in general, but it gives no evidence that it helps.
2. **Section lookup is cheap and needed.** It fixes the two section-reference questions, which
   vector search alone cannot answer, and changes nothing for questions that name no section.
3. **Keyword fusion off.** The question-set wording rule bans five-word runs from the statute, so
   keyword search has little to match by design, and on dev it pulled in sections that share
   common words (Q041). It stays in the code as an option for users who quote statute wording.
4. **OpenAI over bge.** bge is lower in every like-for-like pair (17 vs 18 vector only, 18 vs 20
   with lookup) and would put torch in the deployed image. The D-03 embedding choice is kept; no
   model change.

Rerunning the frozen file on dev (`--split dev --final`) gives per-question results identical to
`openai_section` (`results/dev_final.json`).

## 7. Test results

Run once on 2026-11-02, with the frozen configuration (sha256 `6ce743b3…3684`) and nothing
changed afterwards:

```
.venv\Scripts\python eval\retrieval\run_eval.py --split test --final
```

Output: `eval/retrieval/results/test_final.json`. Query embeddings: 30 OpenAI requests, 511
tokens (none from cache).

**Gate G1 (D-20, hit@5 of at least 27 of 30): PASS, 30 of 30 (100%).**

| Measure | Test (30) |
|---|---|
| hit@1 | 22 (73%) |
| hit@3 | 30 (100%) |
| hit@5 | 30 (100%) |
| MRR (top 5) | 0.856 |

By category:

| Category | Questions | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| `cit_rates_levy` | 3 | 3 | 3 | 3 | 1.000 |
| `deductions_gains` | 4 | 2 | 4 | 4 | 0.708 |
| `vat` | 4 | 4 | 4 | 4 | 1.000 |
| `exemptions_incentives` | 4 | 4 | 4 | 4 | 1.000 |
| `definitions` | 3 | 2 | 3 | 3 | 0.833 |
| `schedules` | 2 | 1 | 2 | 2 | 0.750 |
| `ntaa_registration_returns` | 3 | 3 | 3 | 3 | 1.000 |
| `ntaa_assessment_objection` | 2 | 0 | 2 | 2 | 0.500 |
| `ntaa_payment_penalties` | 2 | 0 | 2 | 2 | 0.417 |
| `section_reference` | 3 | 3 | 3 | 3 | 1.000 |

Per question (rank of the first chunk matching a gold unit):

| Id | hit@5 | Rank | Id | hit@5 | Rank | Id | hit@5 | Rank |
|---|---|---|---|---|---|---|---|---|
| Q001 | yes | 1 | Q017 | yes | 1 | Q033 | yes | 1 |
| Q002 | yes | 1 | Q020 | yes | 1 | Q036 | yes | 1 |
| Q005 | yes | 1 | Q021 | yes | 1 | Q037 | yes | 1 |
| Q006 | yes | 1 | Q022 | yes | 1 | Q038 | yes | 2 |
| Q008 | yes | 3 | Q023 | yes | 1 | Q040 | yes | 2 |
| Q009 | yes | 2 | Q024 | yes | 1 | Q042 | yes | 2 |
| Q011 | yes | 1 | Q027 | yes | 2 | Q045 | yes | 3 |
| Q012 | yes | 1 | Q028 | yes | 1 | Q046 | yes | 1 |
| Q013 | yes | 1 | Q031 | yes | 2 | Q048 | yes | 1 |
| Q016 | yes | 1 | Q032 | yes | 1 | Q049 | yes | 1 |

**Misses: none.** No test question has its gold unit outside the top 5. The eight questions not
at rank 1, with what ranked above the gold chunk:

| Id | Gold | Rank | Ranked above it |
|---|---|---|---|
| Q008 | `nta2025:s21(p)` | 3 | `nta2025:s156`, `nta2025:s187` |
| Q009 | `nta2025:s34` | 2 | `nta2025:s47` |
| Q027 | `nta2025:s202:def:year-of-assessment` | 2 | `ntaa2025:s147:def:calendar-year` |
| Q031 | `nta2025:sch11` or `nta2025:s186` | 2 | `nta2025:s187` |
| Q038 | `ntaa2025:s35` | 2 | `ntaa2025:s34` |
| Q040 | `ntaa2025:s41(5)-(8)` | 2 | `ntaa2025:s41(1)-(4)` (the other part of the same section) |
| Q042 | `ntaa2025:s65` | 2 | `ntaa2025:s49(1)-(6)` |
| Q045 | `ntaa2025:s107` | 3 | `ntaa2025:s105`, `ntaa2025:s104` |

The three section-reference questions (Q046, Q048, Q049) were answered at rank 1 by the section
lookup. Thirty questions are still a small sample: the pass margin (30 against 27) is clear, but
a 100% result does not mean retrieval never misses. hit@1 (22 of 30) shows that the right chunk
is often second or third, so Phase 5 should pass all five chunks to generation.

## 8. How to check

From the repository root, with the database up and `requirements-eval.txt` installed for bge:

```
.venv\Scripts\python eval\retrieval\run_eval.py --split dev --config eval\retrieval\configs\openai_section.json
.venv\Scripts\python -m pytest -q tests\test_retrieval.py
```

`tests/test_retrieval.py` covers section-reference parsing, fusion order and ties, every filter
across every component, the lookup with and without an Act named, BM25 against a rare term, the
bge client, the guard rules, a refused run loading nothing, and that no test-split question text
is in a Phase 4b file and no test question has been embedded before the final run.
