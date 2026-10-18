# Decision Log

Decisions (D-xx), corrections to the original plan (C-xx) and data-quality issues (DQ-xx). Earlier
entries are never edited silently; add a new entry that supersedes them.

## Decisions

| ID | Date | Decision | Reason |
|---|---|---|---|
| D-01 | 2026-10-18 | v1 statutory scope is the Nigeria Tax Act 2025 (company income tax and VAT parts), replacing the plan's Companies Income Tax Act, VAT Act and Finance Acts. The Nigeria Tax Administration Act 2025 is added only if needed. Phase 1 must confirm commencement and obtain official text from a primary source before any acquisition. | The Nigeria Tax Act 2025 (in force 1 January 2026) consolidates and repeals the older statutes the plan names. Grounding answers in repealed law would be wrong. |
| D-02 | 2026-10-18 | Local pgvector runs in Docker (image `pgvector/pgvector:pg17`, host port 5434). The existing PostgreSQL 17 (5433) and PostgreSQL 15 (5432) services are left untouched. | pgvector is not installed on either local server, and compiling it on Windows needs VS Build Tools and is fragile. Docker also mirrors the deployed environment. |
| D-03 | 2026-10-18 | The hosted LLM and the embedding model are chosen from a sourced comparison ([model selection](../phase00_setup/model_selection.md)). **Approved 2026-10-18:** Claude Sonnet 5.5 for generation, Claude Haiku 5.5 for the verification pass, `text-embedding-3-small` for embeddings. The embedding choice stays provisional until Phase 4 hit-rate results; the verification model is revisited in Phase 6. | Budget and per-query cost should be understood before heavy experimentation (plan Phase 0). |
| D-04 | 2026-10-18 | The original planning `.docx` moves to `.agents/planning/` (not committed). A cleaned Markdown conversion lives at [implementation_plan.md](implementation_plan.md), with non-technical framing removed. | The repository should read as a professional project. The original is kept unchanged for provenance (sha256 `665433b9…2b7abb`). |
| D-05 | 2026-10-18 | Repository name is `ng-tax-advisory-copilot`. No remote has been created yet. | Name suggested in the plan. GitHub username still to be supplied. |

## Corrections to the original plan

| ID | Date | Correction | Affects |
|---|---|---|---|
| C-01 | 2026-10-18 | Corpus scope changes from CITA / VAT Act / Finance Acts to the Nigeria Tax Act 2025 (see D-01). The original wording is preserved in the plan with a pointer to this entry. | Plan sections 3.1, Phase 1, section 11. |
| C-02 | 2026-10-18 | The plan says Docker is used for deployment only, not in local development. Local pgvector now runs in Docker (see D-02). | Plan section 5 (Containerization), Phase 0. |

## Data-quality issues

| ID | Date | Issue | Status |
|---|---|---|---|
| | | | |
