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
| [phase06_guardrails/calibration_report.md](phase06_guardrails/calibration_report.md) | Verifier calibration on the frozen calibration set: method, all seven runs with settings and numbers, every miss with its cause and fix (`norm/2` evidence labels, `ver-prompt/2` and `/3`), final settings (Haiku 5.5, effort `low`), stability (0.51%), cost ($0.30) and latency; run 8 re-calibration after tuning run 1 (section 8) and the offline `norm/4` re-check (section 9). |
| [phase06_guardrails/tuning_report.md](phase06_guardrails/tuning_report.md) | Guardrail tuning runs 1 and 2 (tune part, dev, G01 to G05; held-out part never opened): settings, pass counts, run 1 failures and the decisions that fixed them (D-43 to D-46), run 2 changes, false-refusal counts (D-38), cost, latency and notes for review. |
| [phase06_guardrails/g2_results.md](phase06_guardrails/g2_results.md) | Gate G2 decision run 1 (held-out part, 3 repeats): method, frozen config and result hashes, results by category, over-refusals, D-38 false refusals and their cause, ADV-071, missing-note cases (D-51), claim verdicts, removals and moves, cost and latency, owner sample, and the result (FAIL, D-50) with next steps; per-case and per-claim rows in `g2_results.csv`. |

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
