# Nigerian SME Tax Advisory Copilot

> **Disclaimer.** This is a research and reference tool. It is **not tax advice** and is not a
> substitute for a licensed tax professional. It must not be used for an actual filing or
> compliance decision. It holds **no real taxpayer data**; all test scenarios use synthetic figures.

> **Evaluation (gate G3, passed 2026-11-12).** On a held-out end-to-end set of 90 cases, run twice with
> the shipped configuration, both runs met every threshold: groundedness 244 of 244 and 245 of 245
> shown items supported (threshold 95%); citation accuracy 246 of 246 and 245 of 245 (95%); 0 of 40
> must-refuse cases answered in each run; false refusals 1 of 50 in each run (at most 5 of 50).
> Judged by a calibrated model session, not an independent person, on a small set; personal income
> tax is out of scope. Method, intervals and limitations:
> [evaluation report](docs/phase08_evaluation/evaluation_report.md).

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

## Run the app

The Streamlit app answers one question at a time from the Nigeria Tax Act 2025 and the selected parts of the
Nigeria Tax Administration Act 2025, with citations and the cited statute text. From the repository root, in
PowerShell:

1. Start the database: `docker compose up -d`, then `docker compose ps` (expect `healthy` on port 5434).
2. Activate the environment: `.venv\Scripts\Activate.ps1`.
3. Start the app: `.venv\Scripts\streamlit run app\main.py`, then open the local address it prints.

On the first page load the app checks, without any paid call, that it runs the shipped configuration and the
expected corpus and audit schema. If the check fails the page shows "This service is not available right now"
and no question box. Asking a question calls the language-model and embedding services and needs the keys in
`.env` (see `.env.example`). The local app keeps each question, answer and its checks in the `audit` schema of
the project database, and the full request log in `logs/generation/` (git-ignored).

To look at a past query, run the local viewer with `.venv\Scripts\streamlit run app\review.py`, or print one
with `.venv\Scripts\python python\audit\show_query.py Q-xxxxxxxx`. Both read the audit log only. The viewer
reads chunk text through a read-only role; the owner grants it once with
`.venv\Scripts\python python\audit\grant_review.py --apply` (run without `--apply` it only prints the
statements). See the [interface design](docs/phase09_interface/interface_design.md).

## Documentation

See the [documentation index](docs/README.md).
