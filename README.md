# nigeria-tax-copilot

## Nigerian SME Tax Reference

> **Disclaimer.** This is a research and reference tool. It is **not tax advice** and is not a
> substitute for a licensed tax professional. It must not be used for an actual filing or compliance
> decision. Answers come only from the Nigeria Tax Act 2025 and selected parts of the Nigeria Tax
> Administration Act 2025 (Official Gazette text), as a dated snapshot taken on 2026-10-18; later
> changes are not included. **Personal income tax is out of scope**: questions about it, about PAYE
> and about an individual's tax bands and rates are declined. It is not designed to process real
> taxpayer data.

A grounded retrieval-augmented generation (RAG) system over Nigerian tax statutes, aimed at questions
from small and medium enterprises. It retrieves the relevant statutory passages first, answers only from
those passages with section-level citations, checks each statement against the cited text, declines when
the corpus does not cover the question, and does any arithmetic in code rather than in the language model.

## Status

**Phases 1 to 9 are done** (corpus, chunking, embedding, retrieval, generation, guardrails, tools,
evaluation, interface). **Phase 10 (documentation and packaging) is in progress.** The app has not been
deployed: there is no live demo yet, and the live demo comes in Phase 11. Gate G2 was **not met**; see
Results at a glance.

## Results at a glance

| Gate | What it measures | Result | Figures | Record |
|---|---|---|---|---|
| G1 retrieval | The right statute section is among the top 5 passages, on 30 held-out questions | **passed** | hit@5 30 of 30; hit@1 22 of 30; MRR 0.8556 | [`test_final.json`](eval/retrieval/results/test_final.json) |
| G2 adversarial guardrails | Hostile and tricky questions, on a held-out set, three repeats | **not met** | Decision runs 1 to 4 all failed. Run 4: 39 of 40 cases passed in each repeat (ADV4-009 failed), one false refusal in each repeat (ADV4-044), all 603 rendered claims supported by their cited text | [G2 results, run 4](docs/phase06_guardrails/g2_results_v4.md) |
| G3 end to end | The shipped system on 90 held-out cases, run twice; each run must meet all four thresholds | **passed** | Run 1: (a) 244 of 244, (b) 246 of 246, (c) 0 of 40, (d) 1 of 50. Run 2: (a) 245 of 245, (b) 245 of 245, (c) 0 of 40, (d) 1 of 50 | [`g3_result.json`](eval/e2e/results/g3_result.json) |
| Phase 7 exit check | 14 new questions on the calculator and section lookup tools | **passed** on run 1 | Every shown figure equals the hand-worked figure; every kept claim supported | [`exit_run1.json`](eval/tools/results/exit_run1.json) |

G3 metrics: (a) shown items supported by a passage they cite (threshold 95%); (b) shown citations that
point to a section stating what the claim says (95%); (c) must-refuse cases answered (none allowed, out of
40); (d) in-scope questions falsely refused (at most 10%). The reviewer was a calibrated model session,
not an independent person, and the sets are small. G2 and G3 measure different things on different sets:
G3 is a held-out measurement of the shipped system, and G2 closed as not met. Details are under Evaluation
results and Known limitations.

## The problem

A small company in Nigeria that wants to know what the 2025 tax Acts say has to read long statutes. The
Nigeria Tax Act 2025 commenced on 1 January 2026. Two official-source texts of each Act are in circulation
and they differ in provisions that matter to companies (for example the small-company threshold), so a
reader can be given the wrong text without knowing it. A wrong answer has a cost: a missed deadline, a
wrongly claimed exemption or a wrong rate. See the [scope verification](docs/phase01_corpus/scope_verification.md)
and the data-quality entries DQ-01 to DQ-05 in the [decision log](docs/00_planning/decision_log.md).

## Why retrieval rather than a plain model

A plain language model answers from what it learned. It gives no citation, no snapshot date and no way to
check a sentence against the law, and it can state a section that does not exist. This system works the
other way round:

- **Retrieve first.** The relevant sections are found in the corpus before anything is written.
- **Cite.** Every claim names the retrieved passage it rests on, and the page shows the statute text.
- **Verify.** A second model checks each claim against the text it cites; unsupported claims are removed.
- **Decline.** When the corpus does not cover the question, or the section is outside the corpus, the system
  says so instead of guessing.
- **Calculate in code.** Tax arithmetic is done by a calculator with a pinned rate table, never by the model.

The design documents are the [generation design](docs/phase05_generation/generation_design.md), the
[guardrails design](docs/phase06_guardrails/guardrails_design.md) and the
[tools design](docs/phase07_tools/tools_design.md).

## Scope and snapshot date

The corpus is a dated snapshot taken on **2026-10-18**:

- **Nigeria Tax Act 2025:** the company income tax and value added tax parts: Chapter One, the company parts
  of Chapter Two (including chargeable gains, the company rate and the development levy), Chapter Six
  (VAT), Parts I and IV of Chapter Eight (exemptions) and Chapter Nine (general provisions and
  definitions), with the First (Part I), Third, Eleventh and Thirteenth Schedules.
- **Nigeria Tax Administration Act 2025:** selected procedural parts: registration, returns, assessment,
  objections, payment, penalties, rulings and offences of general application.

The cited text is the Official Gazette; the National Assembly text is kept as a comparison copy, and an
answer on a disputed provision says that the two texts differ (D-09). The exact in-scope sections are in the
[v1 corpus definition](docs/phase01_corpus/v1_corpus_definition.md), and the source files, sizes and hashes
are in the [data inventory](data/README.md).

## What it does not cover

- **Personal income tax is out of scope**: PAYE, an individual's tax bands and reliefs, and sole traders and
  business names whose profits are taxed as the owner's income (D-64).
- Sections outside the v1 corpus, for example Sections 13 and 58 of the Nigeria Tax Act and the Fourth
  Schedule; questions that name them are declined.
- State and local taxes and levies, stamp duties, customs and excise, petroleum taxes, regulations and other
  subsidiary legislation, and guidance notes.
- Tax periods before 2026, and any change to the law after 2026-10-18.
- Any connection to a filing system or to the Nigeria Revenue Service.
- Memory: each question is answered on its own.

## Architecture

The diagram and the stage-by-stage description are in the [architecture page](docs/reference/architecture.md);
GitHub draws the diagram from its Mermaid source ([`diagrams/architecture.mmd`](diagrams/architecture.mmd)).
In short:

- **Corpus build** (offline, deterministic): five pinned source PDFs, a re-read of the scanned Administration
  Act, text and structure extraction, 400 chunks under a 450-token cap, a register of differences between
  the two official texts, then embedding with OpenAI `text-embedding-3-small` into a pgvector database
  ([chunking design](docs/phase02_chunking/chunking_design.md),
  [vector store](docs/phase03_embedding/vector_store.md)).
- **Retrieval:** vector search plus exact section lookup, top 5 passages, with one keyword slot, a
  commencement lookup and one definition attachment; a relevance floor refuses off-topic questions
  ([retrieval report](docs/phase04_retrieval/retrieval_report.md)).
- **Generation:** Claude Sonnet 5.5 writes claims that cite passage labels, using a bounded tool loop with a
  section-lookup tool and a Decimal calculator ([tools design](docs/phase07_tools/tools_design.md)).
- **Guardrails:** a pre-check on the question, a rule check and a Claude Haiku 5.5 claim verifier with
  evidence quotes, a gap check, scope notes and disclosures for disputed text
  ([guardrails design](docs/phase06_guardrails/guardrails_design.md)).
- **Interface and audit:** a Streamlit page with citation chips and a section reader, a personal-data screen,
  and an audit log of every question, projected from the generation log; the answer card is shown only if it
  equals the logged text ([interface design](docs/phase09_interface/interface_design.md)).

## How to run

Tested on Windows 11 with PowerShell, Python 3.12 and Docker Desktop. Other platforms are untested. After the
clone, run every command from the repository root.

1. Clone the repository and open PowerShell in the new folder:

   ```powershell
   git clone https://github.com/kene-obiekwe/nigeria-tax-copilot.git
   ```

   ```powershell
   Set-Location nigeria-tax-copilot
   ```

2. Create the virtual environment (Python 3.12):

   ```powershell
   python -m venv .venv
   ```

3. Install the pinned dependencies:

   ```powershell
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

4. Create your `.env` from the template, then open it and type your own `POSTGRES_PASSWORD`. The password is
   yours to choose; never commit `.env`. The two API keys are needed only to ask questions or to re-embed;
   the free reproduction below needs neither:

   ```powershell
   Copy-Item .env.example .env
   ```

5. Start the database (a pgvector container on host port 5434) and wait for `healthy`:

   ```powershell
   docker compose up -d
   ```

   ```powershell
   docker compose ps
   ```

6. Fetch the one source file that is not committed (the 27.7 MB scan of the Nigeria Tax Administration Act)
   and check all raw files against the manifest. If the Nigeria Revenue Service link is unavailable, the
   same file is a release asset of tag `corpus-2026-10-18`; pass its address with `--mirror`
   ([data inventory](data/README.md)):

   ```powershell
   .venv\Scripts\python python\ingest\fetch_raw.py
   ```

   ```powershell
   .venv\Scripts\python python\ingest\verify_manifest.py
   ```

7. Download the two embedding caches from the same release (checked by sha256 against
   [`eval/reproduce/assets.json`](eval/reproduce/assets.json)):

   ```powershell
   .venv\Scripts\python eval\reproduce\fetch_assets.py
   ```

8. Load the corpus into the database. This is idempotent and uses the cached vectors first, so with the caches
   installed it makes no embedding call; it needs `EMBEDDING_API_KEY` in `.env` only because the client
   refuses to start without one (the key-free load is part of the reproduction check):

   ```powershell
   .venv\Scripts\python python\embed\load.py --model openai/text-embedding-3-small
   ```

9. Create the audit schema, then check it:

   ```powershell
   .venv\Scripts\python python\audit\migrate.py
   ```

   ```powershell
   .venv\Scripts\python python\audit\migrate.py --check
   ```

10. Start the app and open the address it prints. Asking a question calls the language-model and embedding
    services and needs both keys in `.env`:

    ```powershell
    .venv\Scripts\streamlit run app\main.py
    ```

11. To look at a past query, run the local log viewer (it only reads):

    ```powershell
    .venv\Scripts\streamlit run app\review.py
    ```

On first load the app checks, without any paid call, that it runs the shipped configuration and the expected
corpus and audit schema. If the check fails the page shows "This service is not available right now" and no
question box. Before the first question the page asks you to tick a consent box (see Privacy).

## How to reproduce

The reproduction is free: one command, no API key and no paid call. It rebuilds the corpus chunks and
checks them byte for byte, loads the vector store from the published cached vectors, recomputes retrieval
for the dev and test questions and compares every ranking with the committed files, re-scores the committed
evaluation records and compares the scores with their pinned hashes, and checks the figures in this README
against the records. After steps 1 to 5 above (with both API keys left empty), run:

```powershell
.venv\Scripts\python eval\reproduce\reproduce.py --profile clean
```

It prints one row per check (id, what, expected, actual, pass) and exits 0 only when every row passes.
What each check proves, and what a failure means, is in [docs/reference/reproduce.md](docs/reference/reproduce.md).

Two things to know before you run it:

- **Stop any other copy of this stack first.** `docker-compose.yml` fixes the container name
  `taxcopilot-pgvector` and host port 5434, so two copies cannot run at the same time (run
  `docker compose down` in the other folder; its data volume is kept).
- **Test counts.** Check A10 requires 0 failed tests. To also compare the counts, add `--a10-expected`. The
  counts depend on what you installed: with `requirements.txt` only, expect `--a10-expected 1864/31` (1864
  passed, 31 skipped). One test (`tests/test_retrieval.py:401`) needs `requirements-eval.txt` and is skipped
  without it; with that file installed (as in the main checkout) the counts are 1865 passed, 30 skipped.

What is reproduced is every **number**, exactly. The system's **answers** are not regenerated: they came from
paid, non-deterministic model calls, and what the system showed is committed and pinned instead. A paid
reproduction of regenerated answers on a sample of the evaluation questions against the live service is
planned for Phase 12. OCR of the scanned Administration Act can optionally be re-run in its own environment
(about 60 minutes); its committed output is byte-identical across runs ([data inventory](data/README.md) and
[chunking design](docs/phase02_chunking/chunking_design.md)).

## Evaluation results

Gate G3 (D-134, D-137): on a held-out end-to-end set run twice with the shipped configuration, each run must
meet all four thresholds on its own. Both did. Intervals are Wilson 95% intervals; the pooled column is
reported, not gated.

| | Metric | Threshold | Run 1 | Run 2 | Pooled (not gated) |
|---|---|---|---|---|---|
| (a) | Groundedness: shown items supported | at least 95% | 244 of 244 (100%; 98.5% to 100%) | 245 of 245 (100%; 98.5% to 100%) | 489 of 489 (99.2% to 100%) |
| (b) | Citation accuracy: shown citations accurate | at least 95% | 246 of 246 (100%; 98.5% to 100%) | 245 of 245 (100%; 98.5% to 100%) | 491 of 491 (99.2% to 100%) |
| (c) | Must-refuse cases answered | none | 0 of 40 (upper bound 8.8%) | 0 of 40 (upper bound 8.8%) | 0 of 80 (upper bound 4.6%) |
| (d) | False refusals of in-scope questions | at most 5 of 50 | 1 of 50 (2.0%; 0.4% to 10.5%) | 1 of 50 (2.0%; 0.4% to 10.5%) | 2 of 100 (2.0%; 0.6% to 7.0%) |

Retrieval on the shipped configuration: the first five passages held a gold unit for 30 of 30 questions of
the retrieval test split (the same split as G1, so not a new held-out result) and for 19 of 20 of the new
in-scope questions, in each run. The one false refusal is the same question in both runs (a football-club
question; a retrieval miss).

Caveats, in plain words:

- The reviewer is a calibrated model session, not an independent person. It can share blind spots with the
  model that wrote the claims, so a result of 100% is a statement about this reviewer's reading.
- The retrieval test questions are easier than unseen questions, because retrieval was known to find a gold
  unit for each. The 20 new in-scope questions are the better guide, and 20 is a small number.
- Zero of 40 must-refuse cases answered bounds the true leak rate only to about 8.8% per run; it is a check
  for systematic leaks, not a proof of a low rate.
- G2 was not met, and the test split and the end-to-end set are now spent: any further measurement needs a
  fresh held-out set.

Spend, from the phase reports: embedding the corpus $0.00126; the G2 tuning and decision runs about $6.29 of
an $8.00 cap; Phase 7 $0.4611 of $2.00; the Phase 8 evaluation $1.6527 of $3.00; Phase 9 $0.2010 of
$0.40. Method, per-subset results and every limitation: the
[evaluation report](docs/phase08_evaluation/evaluation_report.md).

## Known limitations

The main one for users: **personal income tax is out of scope**. The full list, with sources, is in
[docs/reference/limitations.md](docs/reference/limitations.md). The ones to know first:

- Sections outside the v1 corpus, other taxes, subsidiary legislation, periods before 2026 and anything after
  2026-10-18 are declined or answered with a note.
- Two failure classes from the G2 run are carried: subsidiary legislation named "Regulations" gets no scope
  note (ADV4-009), and exported-services questions miss Section 187 and the exported-service definition
  (ADV4-044).
- Some answers are incomplete: a question on the Thirteenth Schedule lists 2 of its 6 activities, a company
  levy calculation is declined without gross turnover, and a sporting-activities exemption was missed by
  retrieval.
- The personal-data screen blocks email addresses, phone numbers and long digit runs; it does not detect names
  or addresses.
- Built and tested on Windows 11 with PowerShell only.

## Disclaimer

This is a research and reference tool. It is **not tax, legal or financial advice** and is not a substitute
for a licensed tax professional. It must not be used for an actual filing or compliance decision. The corpus
is the Nigeria Tax Act 2025 and selected procedural parts of the Nigeria Tax Administration Act 2025, as a
dated snapshot of 18 October 2026. **Personal income tax is out of scope:** questions about it, about PAYE and
about an individual's tax bands and rates are declined. The figures in this README describe this system on
these question sets; they are not a guarantee about any other question.

## Privacy

**Privacy.** The public demo is run by Kene Christopher Obiekwe for research and demonstration. It stores each
question, the answer and how it was checked (the statute passages retrieved and each statement's verification
result) for 90 days, so that any answer can be reviewed, and then deletes them. It does so with your consent:
before your first question the page asks you to tick a box confirming that you understand this, and nothing
is sent or stored until you do. The app does not store IP addresses, names or email addresses; the hosting
platform keeps its own request logs. If you sign in, it stores a one-way key derived from your sign-in account
so it can show you your own past questions; you can delete them at any time from "My questions". Questions are
sent outside Nigeria: to Anthropic (Claude) to write and check the answer, to OpenAI to search the statutes,
and to the hosting providers Render and Supabase; Google handles sign-in if you use it. Their own data
policies apply: [Anthropic](https://www.anthropic.com/legal/privacy),
[OpenAI](https://openai.com/policies/privacy-policy), [Render](https://render.com/privacy),
[Supabase](https://supabase.com/privacy) and [Google](https://policies.google.com/privacy). These providers
process data outside Nigeria, where the law may not protect it in the same way. The service makes no decision
about you; it returns reference text from the statutes. Do not enter real personal or taxpayer data; the service is not intended for children. You may ask for access to, correction or
deletion of your data, or object to its use, by writing to taxy.copilot@gmail.com and, for an anonymous
question, quoting its reference; you may also complain to the Nigeria Data Protection Commission. You can withdraw your consent at any time by
writing to taxy.copilot@gmail.com; your stored questions are then deleted.

The page shows this acknowledgement above the question box until it is ticked, once per browser session: "Your
question and the answer are stored for 90 days and sent outside Nigeria to the providers named in Privacy
details, so that the answer can be written and checked. You can withdraw your consent at any time by writing to
taxy.copilot@gmail.com; your stored questions are then deleted." The box reads "I understand and agree. I will not
enter real personal or taxpayer details." The tick itself is not stored. Run locally, the app keeps questions
in your own database and nothing is deleted automatically.

This text is a plain statement of what the app does. It is not legal advice and does not say that any law
applies or is met.

## Licence and usage

The code and the project's own documents are under the MIT licence ([LICENSE](LICENSE)). The statute texts in
`data/raw/` and their transcriptions (the chunks, OCR text and proofread text under `data/derived/` and
`data/curated/`) are **not licensed by the project**: they remain the work of their official publishers and
are redistributed with their source and hash for verification only. See [NOTICE.md](NOTICE.md).

This project is for research and demonstration. It is not an interface to any filing system or to the Nigeria
Revenue Service, and it is not designed to process real taxpayer data: users are asked not to enter it, and a
screen blocks email addresses, phone numbers and long numbers before anything is sent or stored.

## Documentation

See the [documentation index](docs/README.md). The three reference pages are the
[architecture](docs/reference/architecture.md), the [known limitations](docs/reference/limitations.md) and the
[reproduction guide](docs/reference/reproduce.md); the evaluation method is in the
[evaluation report](docs/phase08_evaluation/evaluation_report.md), and decisions and corrections are in the
[decision log](docs/00_planning/decision_log.md).
