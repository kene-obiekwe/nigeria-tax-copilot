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
| [phase02_chunking/build_notes.md](phase02_chunking/build_notes.md) | Phase 2b build record: deviations from the design, per-file parser tolerances, pinned anomalies and new data-quality findings. |

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
