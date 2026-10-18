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

The Nigeria Tax Act 2025 (company income tax and VAT parts). The Nigeria Tax Administration Act
2025 is added only if needed. Scope is confirmed against primary sources in Phase 1. See
[decision log](docs/00_planning/decision_log.md) (D-01, C-01).

Out of scope: personal income tax, state-level taxes, real-time tracking of legislative change,
model fine-tuning, and any integration with real filing systems.

## Status

**Phase 0**: environment and repository setup. No corpus, retrieval or generation code yet.

## Documentation

See the [documentation index](docs/README.md).
