# Documentation Index

Nigerian SME Tax Advisory Copilot: a grounded retrieval-augmented generation (RAG) system over
Nigerian tax statutes. This is a research and reference tool, not a substitute for a licensed
tax professional.

Every document in `docs/` is listed here with a one-line purpose. Update this index whenever a
document is added, moved or renamed.

## Current documents

| Document | Purpose |
|---|---|
| [00_planning/implementation_plan.md](00_planning/implementation_plan.md) | Phased implementation plan (Phases 0 to 12, gates, stack, repository structure, evaluation method), converted to Markdown. |
| [00_planning/decision_log.md](00_planning/decision_log.md) | Decisions (D-xx), corrections to the plan (C-xx) and data-quality issues (DQ-xx). |
| [phase00_setup/model_selection.md](phase00_setup/model_selection.md) | Sourced LLM and embedding comparison, cost per query, and recommendation (D-03). |
| [phase01_corpus/scope_verification.md](phase01_corpus/scope_verification.md) | Primary-source check of the Nigeria Tax Act 2025 (assent, commencement, repeals, later changes) and of the gazette and National Assembly text dispute; verdict on D-01. |
| [phase01_corpus/v1_corpus_definition.md](phase01_corpus/v1_corpus_definition.md) | Exact in-scope chapters, parts and sections of both Acts, which text is cited (D-09), known gaps and source files. |
| [phase02_chunking/chunking_design.md](phase02_chunking/chunking_design.md) | Probe-backed design for text extraction, the NTAA scan strategy, structure detection, tables, chunk schema, the discrepancy register, derived data, the fetch script, the spot-check protocol and the Phase 2b plan. |
| [phase02_chunking/proofread_report.md](phase02_chunking/proofread_report.md) | Proofreading of the six NTAA sections where the NRS print differs from the Gombe print: every difference with three readings, print differences for the register, and review steps. |
| [phase02_chunking/register_review.md](phase02_chunking/register_review.md) | Review pack and review outcome for the discrepancy register: how the rows were produced and checked, where each known item is, review steps and reply format, and every row grouped as substantive and missing content, numbering, and wording. |
| [phase02_chunking/build_notes.md](phase02_chunking/build_notes.md) | Phase 2b build record: deviations from the design, per-file parser tolerances, pinned anomalies, chunk counts and token distribution, the golden-chunk check method and new data-quality findings. |
| [phase02_chunking/spot_check.md](phase02_chunking/spot_check.md) | Method, strata, seed, pass rule and reviewers for the 20-chunk exit-gate spot-check, with its results (pending review). |
| [phase03_embedding/vector_store_design.md](phase03_embedding/vector_store_design.md) | Approved design for the vector store (D-19): schema, embedded text, index choice, loader, cache, re-embedding procedure, tests and the pinned SDK. |
| [phase03_embedding/vector_store.md](phase03_embedding/vector_store.md) | The built vector store: final schema, model and dimensions, measured cost, how to run, re-embedding procedure, smoke-check results (exit gate) and tests. |
| [phase04_retrieval/question_set.md](phase04_retrieval/question_set.md) | The labelled 50-question retrieval set (v1): schema, labelling method, wording and overlap check, coverage by category and split, the seeded split, the hit rule, gate G1 (D-20) and the frozen sha256. |
| [phase04_retrieval/retrieval_report.md](phase04_retrieval/retrieval_report.md) | Retrieval layer (section lookup, vector, keyword fusion, filters), the bge baseline, the evaluation runner and test-split guard, dev results by configuration, misses, the frozen configuration and its sha256, and the test results (gate G1). |
| [phase05_generation/generation_design.md](phase05_generation/generation_design.md) | Approved design for grounded generation (D-22 to D-29): flow and return object, prompt, claim-and-citation output format and parser checks, the disclosure rule for disputed text, the basic decline path, model settings with sourced cost per question, SDK pin and key, logging, tests, the 15-question exit check, and decisions D-22 to D-29. |
| [phase05_generation/exit_check.md](phase05_generation/exit_check.md) | Phase 5 exit check: runner, the 15 questions, reviewers, pass rule, record format, run 1 (failed) and run 2 (passed: 60 supported, 0 unsupported) with verdicts, Kene's confirmations, usage and cost, and notes for Phase 6. Exit gate passed on run 2. |
| [phase06_guardrails/guardrails_design.md](phase06_guardrails/guardrails_design.md) | Approved design for guardrails (D-30 to D-40): the claim verification pass (rule check plus Haiku 5.5 check with evidence quotes), gap checks, the refusal path and why a score threshold alone cannot refuse, the adversarial set, gate G2 with a held-out split and false-refusal limit, fixes for the Phase 5 weaknesses, sourced cost and latency, `gen-log/2`, tests, review, and decisions D-30 to D-40. |
| [phase06_guardrails/calibration_report.md](phase06_guardrails/calibration_report.md) | Verifier calibration on the frozen calibration set: method, all seven runs with settings and numbers, every miss with its cause and fix (`norm/2` evidence labels, `ver-prompt/2` and `/3`), final settings (Haiku 5.5, effort `low`), stability (0.51%), cost ($0.30) and latency; run 8 re-calibration after tuning run 1 (section 8), the offline `norm/4` re-check (section 9) and run 9, the link rule `ver-prompt/5`, which failed its gate and was not adopted (section 10, D-90). |
| [phase06_guardrails/tuning_report.md](phase06_guardrails/tuning_report.md) | Guardrail tuning runs 1 to 14: runs 1 to 3 (settings, pass counts, run 1 failures and fixes D-43 to D-46, false-refusal counts, cost, latency, notes for review); run 4 under D-61 and its failure (D-65); the gap-check re-checks (D-66 to D-68); void run 5 (C-06); runs 6 and 7, which meet the D-61 gate, with the cases passed with concern and the known limitations (D-71); section 16: runs 8 to 10 under D-84 (run 8 passes, void run 9 C-07, run 10 fails D-86) and the known limitations (D-85); section 17: run 11, which fails D-84 on ADV-085's invented link between provisions (D-89); section 18: runs 12 to 14 (runs 12 and 14 meet D-84, void run 13 C-08, ADV2-018 claim 7 passed with concern D-91), the v3 configuration freeze (D-92) and the known limitations (D-93). |
| [phase06_guardrails/g2_results.md](phase06_guardrails/g2_results.md) | Gate G2 decision run 1 (held-out part, 3 repeats): method, frozen config and result hashes, results by category, over-refusals, D-38 false refusals and their cause, ADV-071, missing-note cases (D-51), claim verdicts, removals and moves, cost and latency, owner sample, and the result (FAIL, D-50) with next steps; per-case and per-claim rows in `g2_results.csv`. |
| [phase06_guardrails/g2_results_v2.md](phase06_guardrails/g2_results_v2.md) | Gate G2 decision run 2 on adversarial_v2 (3 repeats, result: FAIL (D-72)): method, frozen config and result hashes, results by category, over-refusals, D-38 false refusals, D-57 notes not required, missing-note cases (D-51) and their causes, claims, removals and moves, cost and latency, the owner sample confirmed by Kene, and the result with next steps (D-73, D-74); per-case and per-claim rows in `g2_results_v2.csv`. |
| [phase06_guardrails/g2_results_v3.md](phase06_guardrails/g2_results_v3.md) | Gate G2 decision run 3 on adversarial_v3 (3 repeats; does not pass on D-38 (D-94)): method, frozen config and result hashes, results by category, over-refusals, D-38 false refusals and their retrieval causes, D-57 notes not required and the note tagger record, required notes (D-51), claims, removals, moves and linked-provision checks (D-89, D-91), cost and latency, the suggested owner sample, and the result; per-case and per-claim rows in `g2_results_v3.csv`. |
| [phase06_guardrails/g2_fix_design.md](phase06_guardrails/g2_fix_design.md) | Fix design after G2 decision run 1 (approved, D-54 to D-61): root causes and measured options for the retrieval misses (Act-name lookup plus one keyword slot), the evidence normaliser (`norm/5`, offline re-check), note triggers and a false-trigger limit, version-keyed decision runs, carried risks, and the re-tuning plan with cost and order of work. |
| [phase06_guardrails/g2_fix_design_2.md](phase06_guardrails/g2_fix_design_2.md) | Fix design after G2 decision run 2 (approved 2026-11-07, D-77 to D-84; section 13 is the build record; section 14 is the tagger majority build, D-86 to D-88; section 15 is the verifier link rule, measured and not adopted, D-89 and D-90): note-trigger recall measured on the 88 probes with a seeded tune/check split (wider patterns, a closed-label Haiku tagger, both), false triggers under D-57, a gap reference check replayed on saved runs, lettered section numbers, the subsection premise-correction note, the retrieval misses behind ADV2-027, 028 and 046, the adversarial_v3 decision-run plan, re-tuning, cost and the spend limit. |
| [phase06_guardrails/g2_fix_design_3.md](phase06_guardrails/g2_fix_design_3.md) | Fix design after G2 decision run 3 (approved 2026-11-09, D-98 to D-105): definition attachment and a Nigeria Tax Act commencement lookup appended after P6 (first five unchanged on dev and test), abbreviation expansion measured and rejected, the percent parser fix and `norm/6`, known limitations kept, and the tuning and decision run 4 plan within the D-97 cap. |

## Scripts and data files added by the G2 fix build

| File | Purpose |
|---|---|
| [../python/retrieval/references.py](../python/retrieval/references.py) | Shared reference parser for retrieval and the guardrails (D-54): section and Schedule references, the Act each belongs to, and the names of Acts outside the corpus. |
| [../eval/retrieval/config_v2.json](../eval/retrieval/config_v2.json) | Retrieval configuration v2 (D-54): the frozen D-21 settings plus other-Act references skipped, the Act-name lookup (at most 2 chunks) and one keyword slot (P6). |
| [../eval/retrieval/results/dev_v2.json](../eval/retrieval/results/dev_v2.json) | Dev-split retrieval result under `config_v2.json`, with the first five identical to `dev_final.json`. |
| [../eval/retrieval/check_v1_retrieval.py](../eval/retrieval/check_v1_retrieval.py) | v1 retrieval check (G2 fix design, section 2.5): needed units under D-21 and D-54 over the 34 in-scope adversarial_v1 cases; writes nothing. |
| [../eval/guardrails/recheck_evidence.py](../eval/guardrails/recheck_evidence.py) | Offline evidence re-check of `norm/5` against `norm/4` over the logged calibration, tuning and held-out runs (needs the local, git-ignored generation log). |
| [../eval/guardrails/recheck_gaps.py](../eval/guardrails/recheck_gaps.py) | Offline gap re-check (D-66, D-67): re-classifies with the live gap prompt every gap kept in tuning runs 2 to 4 and the G2 run 1 held-out repeats, without regenerating answers; paid, capped at $0.60, writes `gap_recheck_<N>.json` once. |
| [../eval/guardrails/measure_notes.py](../eval/guardrails/measure_notes.py) | Note measure (G2 fix design, section 4.3; G2 fix design 2, section 2.7): recall on the 88 note-recall probes by seeded half and by family, required notes missed and notes not required per set, for the patterns, the tagger and both; `--tagger` makes one paid Haiku call per question (about $0.02 for 257); writes only an optional labels file. |
| [../python/generation/note_tagger.py](../python/generation/note_tagger.py) | Note tagger (D-77): one Haiku 5.5 call per question choosing fixed notes from a closed label list (`tag-prompt/2` in `prompt.py`), validated in code, falling back to the pattern notes on failure. |
| [../eval/guardrails/replay_gap_references.py](../eval/guardrails/replay_gap_references.py) | Offline replay of the gap reference check (D-79) over tuning runs 6 and 7 and the v2 decision run; reproduces the 8 removals of G2 fix design 2, section 3.2; no model call, no database. |
| `eval/guardrails/config_g2_v3.json` | Frozen configuration for the adversarial_v3 decision run (D-83, D-84). Not written yet: frozen once, after two passing tuning runs, with `--freeze-config --set v3`. |
| [../tests/test_g2_fix_2.py](../tests/test_g2_fix_2.py) | Tests for G2 fix design 2 (D-77 to D-84), with fake model clients; one test needs the database. |
| [../tests/test_note_tagger_api.py](../tests/test_note_tagger_api.py) | The note tagger on 6 fixed questions against the live API (`api` marker, about $0.001). |
| [../data/curated/heading_corrections.yml](../data/curated/heading_corrections.yml) | Checked corrections to three cited section headings (DQ-18, D-60), each with its PDF page, read by the guardrails' heading loader. |
| [../tests/data/note_probes.json](../tests/data/note_probes.json) | Note-trigger probe fixtures (D-56, D-57): 18 questions that should get a note and 16 near misses that should not. |

The source-document inventory (files, hashes, provenance, snapshot date) is in
[../data/README.md](../data/README.md) and [../data/manifest.csv](../data/manifest.csv).

## Planned folder structure

Folders are created when their first document lands. Two-digit phase numbers keep them in order.

| Folder | Phase | Expected contents |
|---|---|---|
| `00_planning/` | Planning | Implementation plan (Markdown), decision log (D-xx, C-xx, DQ-xx). |
| `phase00_setup/` | 0. Environment and repository setup | Environment setup notes, LLM and embedding model choice with cost per query. |
| `phase01_corpus/` | 1. Corpus acquisition | Source-document inventory, provenance, snapshot date. |
| `phase02_chunking/` | 2. Parsing and chunking | Chunking design, 20-chunk spot-check record. |
| `phase03_embedding/` | 3. Embedding and vector store | Schema, embedding model notes, re-embedding procedure. |
| `phase04_retrieval/` | 4. Retrieval | Retrieval configuration and hit-rate report (gate G1). |
| `phase05_generation/` | 5. Grounded generation | Prompt design, citation format, 15-question review. |
| `phase06_guardrails/` | 6. Guardrails | Verification and refusal design, adversarial results (gate G2). |
| `phase07_tools/` | 7. Agentic tools | Tool specs, scope and limitations. |
| `phase08_evaluation/` | 8. Evaluation | Evaluation methodology and report (gate G3). |
| `phase09_interface/` | 9. Interface and audit log | UI notes, audit-log schema. |
| `phase10_packaging/` | 10. Documentation | Packaging and reproduction notes. |
| `phase11_deployment/` | 11. Deployment | Docker, Render, Supabase setup, rate limit, cold-start notes. |
| `phase12_final_qa/` | 12. Final QA | QA checklist and sign-off. |

The plan also names three top-level reference documents, to be written as the system takes shape:
`architecture.md`, `methodology.md` and `limitations.md`.
