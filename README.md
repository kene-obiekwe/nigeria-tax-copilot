# Nigerian SME Tax Advisory Copilot

> **Disclaimer.** This is a research and reference tool. It is **not tax advice** and is not a
> substitute for a licensed tax professional. It must not be used for an actual filing or
> compliance decision. It holds **no real taxpayer data**; all test scenarios use synthetic figures.

A grounded retrieval-augmented generation (RAG) system over Nigerian tax statutes, aimed at
questions from small and medium enterprises. It retrieves the relevant statutory passages first,
answers only from those passages with section-level citations, verifies each claim against the
cited text, refuses when the corpus does not cover the question, and does any arithmetic in code
rather than in the language model.

## v1 scope

The Nigeria Tax Act 2025 (company income tax and VAT parts) and selected procedural parts of the
Nigeria Tax Administration Act 2025 (registration, returns, assessment, payment, penalties),
as a dated snapshot taken on 2026-10-18. Two official texts of each Act are in circulation; the
corpus cites the Official Gazette and discloses where the National Assembly text differs. See
[scope verification](docs/phase01_corpus/scope_verification.md), the
[data inventory](data/README.md) and the [decision log](docs/00_planning/decision_log.md)
(D-01, C-01, D-09, D-10).

Out of scope: personal income tax, state-level taxes, real-time tracking of legislative change,
model fine-tuning, and any integration with real filing systems.

## Status

**Phase 1**: corpus acquired and hash-pinned. No parsing, retrieval or generation code yet.

## Documentation

See the [documentation index](docs/README.md).
