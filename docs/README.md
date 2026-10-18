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
