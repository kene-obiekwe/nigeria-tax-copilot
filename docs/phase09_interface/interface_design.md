# Interface and Audit Log Design (Phase 9)

**Status:** approved 2026-11-12 (D-150 to D-168).
**Date:** 2026-11-12. **Builds on:** the shipped configuration `eval/tools/config_p7.json` (sha256
`e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25`), measured in Phase 8 (G3 passed,
D-149). **Plan:** [implementation plan](../00_planning/implementation_plan.md), Phase 9 (and Phase 11 for
the hooks).

> **Disclaimer.** This is a research and reference tool. It is not tax advice and is not a substitute for a
> licensed tax professional. It must not be used for an actual filing or compliance decision.

This design covers the Streamlit interface, the audit log in PostgreSQL, the reviewer's log viewer,
privacy and retention for a public link, access tiers and sign-in, the hooks for the Phase 11 rate limit
and cost cap, error and timeout handling, tests, the exit-gate procedure and the build plan. One rule runs
through the whole design: **the interface shows only what the pipeline already renders and checks.** It
adds no model behaviour, and it changes nothing in generation, guardrails, tools or the frozen
configuration. Nothing under `python/generation/`, `python/retrieval/` or `python/tools/` changes.

Contents: 1 Inputs. 2 Request flow. 3 User interface (a). 4 Audit log (b). 5 Log viewer (c). 6 Privacy and
retention (d). 7 Access tiers, sign-in and personal features (k). 8 Rate-limit and cost-cap hook (e).
9 Errors, timeouts and latency (f). 10 Tests (g). 11 Exit gate (h). 12 Build plan and cost (i).
13 Risks and open items. 14 Decisions (j).

## 1. Inputs this design rests on

**What exists (read for this design):**

- `generate()` in [generate.py](../../python/generation/generate.py) returns an `Answer` with `status`
  (`answered`, `not_covered`, `refused`, `error`), `claims`, `gaps`, `passages` (label, chunk id,
  citation, location, rank, score, disputed flag, reasons), `cited_chunk_ids`, `disclosures` (kind
  `register`, `note` or `hidden`), `notes`, `rendered` (the Markdown answer), `verification` (every
  claim check with verdict, reason, evidence quotes and kept citations; gap checks; removal lines),
  `computations` (records with checks K1 to K6, `shown`, `withheld_reason`), `tools` (the `gen-log/3`
  tools block), `refusal_reason`, the three cost figures and `record_id`. The refusal text itself is not a
  field: it is the first block of `rendered` for `refused` and `error` answers.
- `generate()` takes `log_dir` and appends one `gen-log/3` record per question (tools on) to
  `logs/generation/YYYY-MM-DD.jsonl` through [audit_log.py](../../python/generation/audit_log.py). The
  record holds the full requests and responses, the retrieval list with scores, distances and reasons, the
  pre-check and relevance-floor logs, the note tagger, the verification log, disclosures (including
  hidden ones), notes, `rendered`, `refusal_flag`, `error` and the tools block. It has no wall-clock
  latency for the whole question, and none is needed from it (the app measures it, Section 4.4).
- When `retriever` and `store` are not passed, `generate()` opens its own database connection and closes
  it. That is the path the app uses, so the app runs the shipped code path with no injected resources.
- A failed embedding call during retrieval is not caught inside `generate()`: it raises out, and no
  `gen-log` record is written. The app must handle that case itself (`app_error`, Section 9).
- The shipped configuration is built as `GenerationConfig(tools=ToolsConfig(enabled=True,
  rates=taxcalc.load_rates(), ...))`, the same as `eval/e2e/run_e2e.py` `generation_config()`. The live
  configuration is recomputed for free by `eval/tools/run_exit_check.py` `current_config()` and compared
  with the frozen file by `run_e2e.check_p7_base()`.
- Model settings ([llm.py](../../python/generation/llm.py)): generation `claude-sonnet-5-5`, timeout
  120 s; verifier `claude-haiku-5-5`, timeout 60 s; `MAX_RETRIES` 3 for both. In theory a slow question
  can take several minutes. In practice it does not (next bullet).
- Phase 8 ([evaluation report](../phase08_evaluation/evaluation_report.md), Section 4): latency mean
  7.9 s, median 8.3 s, 90th percentile 12.9 s, maximum 46.8 s over 180 case-runs; mean cost $0.0090 a
  question ($0.0134 answered, $0.0071 declined, $0.0002 refused).
- The guardrails policy refuses questions longer than 2,000 characters (`max_question_chars: 2000`) as
  `invalid_question`.
- The corpus: 400 chunks (198 definitions, 125 sections, 38 subsection groups, 22 Schedule paragraphs,
  15 paragraph groups, 2 Schedule tables). Chunk text averages 590 characters (maximum 2,017). Section 202
  of the Nigeria Tax Act has 120 chunks (definitions), Section 147 of the Administration Act has 55,
  Section 189 has 23. Snapshot `2026-10-18` (`data/derived/chunks/build_manifest.json`).

**The database, read for this design (free, read-only, 2026-11-12):** PostgreSQL 17.11 in the
`pgvector/pgvector:0.8.7-pg17` container on host port 5434; extension `vector` 0.8.7 in `public`; one
schema (`public`) with the four Phase 3 tables (`corpus_chunks` 400 rows, `chunk_embeddings` 800,
`discrepancy_register` 274, `corpus_loads` 3); one login role, `taxcopilot`, a superuser. The schema
comes from [schema.sql](../../python/embed/schema.sql), applied idempotently by `load.py`. No audit
table exists yet.

**Log sizes:** the 10 local `gen-log` files take 166 MB, about 55 KB per record (59 MB for 1,073 records
on 2026-11-09). Most of each record is the full prompt and the model responses.

**Streamlit 1.66.0 (pinned):** `st.chat_input`, `st.popover`, `st.dialog`, `st.fragment`,
`st.status`, `st.feedback`, `st.download_button`, `st.cache_resource`, `st.context`, and the built-in
OIDC sign-in `st.login` / `st.logout` / `st.user` are all available. Sign-in needs **Authlib**, which is
not installed. `streamlit.testing.v1.AppTest` imports.

## 2. Request flow

```
question typed
  -> (1) personal-data screen (app code, Section 3.6)          a match blocks: not sent, not stored
  -> (2) open_query(): admission + receipt row, one DB call     refused here: no paid call, no question stored
  -> (3) worker thread: generate(question, config=SHIPPED, log_dir=<fresh temp dir>)
  -> (4) read the one gen-log/3 record back from the temp dir; local only: append it to logs/generation/
  -> (5) project the record to audit rows; close_query(): one DB transaction
  -> (6) UI shows the answer only if (5) succeeded; add_delivery() records what the user saw
```

Steps 3 to 5 run in a worker thread that finishes and logs even if the browser leaves or Streamlit reruns
the script (Section 9). Steps 2, 5 and 6 are calls to database functions (Section 4.6); the app holds no
table privileges.

**Why a temporary log directory (D-150).** `generate()` already builds the complete `gen-log/3` record.
Passing a fresh `tempfile.TemporaryDirectory()` as `log_dir` gives exactly one record in one file. The app
reads it back with `audit_log.read_records()` and checks that its `record_id` equals `answer.record_id`.
That record is then the single source for the audit rows and for the answer view. This needs no change to
`python/generation/`. It also removes a race that exists today: two Streamlit sessions appending 55 KB
lines to the same day file could interleave writes. Locally the app appends the record to
`logs/generation/YYYY-MM-DD.jsonl` under a process-wide lock, so the JSONL log stays complete. On the
hosted service no JSONL is kept (D-152).

## 3. User interface (a)

### 3.1 Page layout

One page, `app/main.py`, top to bottom:

1. **Title and one line.** "Nigerian SME Tax Reference" with the line "Answers from the text of the
   Nigeria Tax Act 2025 and the Nigeria Tax Administration Act 2025, with the sections they come from."
   (wording, D-167).
2. **Status strip** (always rendered): "Corpus snapshot: 18 October 2026 · Official Gazette text". The
   date is read from the database (`corpus_loads.corpus_snapshot` through `DbCorpusStore.corpus_info()`).
   The start-up check (Section 3.8) confirms that it matches the date inside `DISCLAIMER`.
3. **Disclaimer box:** the `DISCLAIMER` constant imported from `generation.generate`, never retyped.
4. **Scope box** (collapsed after the first question, never removed): what is covered and what is not,
   with personal income tax first (Section 3.7).
5. **Privacy line** above the input: "Do not enter personal or taxpayer details." with a link to the
   privacy section (Section 6.4).
6. **Example questions by topic** (Section 7.5), shown while the session has no questions.
7. **This session's questions and answers**, each as an answer card (Section 3.2).
8. **The question box** (`st.chat_input`, `max_chars=2000`, the guardrails limit). It is disabled while a
   question is running and when the user's limit is reached. Under it: "Each question is answered on its
   own. Earlier questions in this chat are not used." The pipeline is single-turn, and a chat layout would
   otherwise suggest that it remembers.
9. **Sidebar:** sign-in or the signed-in name (Section 7.2), "My questions" when signed in (Section 7.3),
   the questions left today (Section 8), and a short form of the snapshot date and disclaimer.

**"Always visible"** is met in three places: the top of the page (items 2 to 4), the sidebar (expanded by
default on wide screens), and the end of every answer card, where the pipeline's own `rendered` text ends
with the disclaimer and snapshot date. Streamlit has no public sticky header, and the design does not
inject CSS for one.

### 3.2 The answer card

The card is built from the `gen-log/3` record and the `Answer`, not by parsing the Markdown (generation
design, Section 4.4). Order, as the pipeline renders it:

| Block | Source | Shown when |
|---|---|---|
| Status line (UI) | `status`, `refusal_reason` | always: "Answered from the corpus", "Not answered from the corpus", "Refused", "Could not answer" |
| Notes | `notes` (`Disclosure` texts) | any status (D-44); before the claims, or after the refusal text |
| Refusal or error text | first block of `rendered` | `refused`, `error` |
| Decline text | `DECLINE_TEXT` | `not_covered` |
| Claims | `claims`: text, then a citation chip for each kept chunk | `answered` |
| Removed-claims lines | `verification.removal_lines` | `answered`, when claims were removed |
| Calculation blocks | the computation records with `shown` true, in listed order (`parsed.calculations`), turned into text by the pipeline's own `calculation_lines()`; one chip for each chunk in the record's `sources` (the rate sections) | `answered` |
| Withheld-calculation lines | the same `calculation_lines()` output | `answered`, when K4 or K5 held one back |
| Gaps | `gaps` under "Not covered by the retrieved provisions:" | `answered`, `not_covered` |
| Sources | cited chunks with gazette pages, as chips | `answered` |
| Differences between official texts | disclosures with kind other than `hidden` | when any |
| Disclaimer | the end of `rendered` | always |
| Footer (UI) | reference, export, feedback, "Show as plain text" | always |

**Parity rule (D-155).** A pure function `answer_blocks(record, answer, chunks) -> list[Block]` builds the
card. Each block carries the exact text that `render()` produced for that part, plus the chunk ids its
chips open. Before showing a card, the app checks that `"\n\n".join(block.text for block in blocks) ==
answer.rendered`. If the check fails, the card falls back to `answer.rendered` shown as plain Markdown with
no chips, and the audit row gets the flag `ui_fallback`. The user therefore only ever sees text that the
pipeline rendered and logged, which is the text G3 measured. Chips are decoration on top of that text: in a
claim, the bracketed citation "[Nigeria Tax Act 2025, section 59]" is drawn as chips, and the plain-text
view shows it as printed.

**Status texts by reason** (all from the pipeline; the UI adds one hint line in a different style,
labelled as a tip):

| Status / reason | Pipeline text (verbatim) | UI hint (D-167) |
|---|---|---|
| `not_covered` | "The provisions retrieved from the corpus do not answer this question." plus checked gaps | "The gaps above say what the retrieved provisions do not contain." |
| `refused` / `invalid_question` | the pre-check text | "Ask one question about the two Acts, in under 2,000 characters." |
| `refused` / `no_such_section` | for example "The Nigeria Tax Act 2025 has no section 250. It has 203 sections." | none |
| `refused` / `section_outside_v1` | for example "Section 13 of the Nigeria Tax Act 2025 ("Employment income") is outside the corpus this tool covers (v1)." | "See what this tool covers, above." |
| `refused` / `foreign_law` | the pre-check text (D-110) | none (the text already asks the user to rephrase) |
| `refused` / `low_relevance` | "No provision in the corpus matches this question closely enough to answer it." | "Try naming the tax and what you want to know, for example VAT registration or company income tax rates." |
| `refused` / `unsupported_answer` | "The system could not check an answer to this question against the provisions it cited, so no answer is shown." | none |
| `error` / `verification_unavailable` | "The answer could not be checked, so it is not shown. Please try again later." | none |
| `error` (other) | "The system could not produce a grounded answer to this question." | "Please try again later." |
| `app_error` (Section 9) | the pipeline's `ERROR_TEXT` above, then `DISCLAIMER` | "Please try again later." |

Every card, whatever its status, shows the reference (Section 3.5) and ends with the disclaimer.

### 3.3 Opening the cited statute text (no model call)

A citation chip opens an `st.popover` with:

- the citation label (the chunk's `citation`, as the answer shows it) and its context header;
- the gazette page labels;
- "The National Assembly text differs here; see Differences between official texts." when the passage is
  marked disputed;
- the chunk text, verbatim, from `corpus_chunks.text`;
- a button **"Read the whole section"** that opens an `st.dialog` with the full unit (D-165):
  - a chunk of type `definition`: every chunk of that definition (same id up to `:def:<term>`), so a
    split definition is shown whole, not the 120 definitions of Section 202;
  - any other chunk: every non-definition chunk with the same Act and section (or the same Schedule,
    Part and paragraph), in the order of the committed `data/derived/chunks/v1_chunks.jsonl`, with the
    cited chunk highlighted;
  - the dialog says "This is the corpus text of the Official Gazette as chunked for this tool."

The chunk text comes from the same database load that the pipeline read. The audit row stores each
passage's `text_sha256` (Section 4.3), so the viewer can show whether the text has changed since.

### 3.4 Calculation blocks

These are drawn from the computation records with `shown` true and K1 to K6 passed, as `calculation_lines()`
renders them: heading "Calculation (worked out by code from the figures in the question)", then What,
From the question, Assumed, Working, Result, Not computed, the rounding line and "It is not an
assessment." Under each block there is one chip for each rate section in `sources`. A withheld
calculation shows only the pipeline's fixed line. A declined calculation shows nothing. The UI never
formats or recomputes a figure.

### 3.5 Reference and footer

Every card carries **"Reference: Q-xxxxxxxx"**: the first 8 hex characters of the app's `query_id`
(Section 4.3). The viewer resolves a prefix and refuses one that matches more than one row. The footer
also has **Export** (Section 7.5), **feedback** (Section 7.5) and **"Show as plain text"** (an expander
holding `rendered` verbatim).

### 3.6 Personal-data screen (app code, before any call)

Before anything is stored or sent, the app checks the question for an email address, a Nigerian phone
number (`+234`, `234` or `0` followed by `7`, `8` or `9`, then 9 digits) and any run of 10 or more digits
without separators that does not directly follow the ₦ sign (TIN, NIN, BVN or an account number). **A
match is a hard block (D-160):** the question
is not sent and not stored anywhere, the database included, and the only button is "Edit question". The
UI says:

> Your question may contain personal details (an email address, a phone number or a long ID or account
> number). Please remove them. This tool needs only the tax question; use made-up figures. Write amounts
> with commas or the naira sign, for example ₦1,000,000,000.
> [Edit question]

There is no "Send anyway". An amount written as 10 or more plain digits is blocked too, and the message
tells the user how to write it so that it passes. Amounts with commas (`1,000,000,000`) or with the naira
sign (`₦1000000000`) pass. The screen never stores or logs the matched text, the pattern names or the
blocked question. Every stored question has passed the screen, so no screen field is kept. The screen runs
before the pipeline and does not touch the pipeline's own pre-check.

### 3.7 Scope and out-of-scope wording (overseer open item 7, D-64)

Scope box text (D-167):

> **Covers** companies: company income tax and the development levy, VAT, and the Nigeria Tax
> Administration Act 2025 rules on registration, returns, assessment, objections, payment and penalties.
>
> **Does not cover personal income tax.** That includes sole traders and business names (their business
> profits are taxed as the owner's personal income) and PAYE (employees' tax bands and reliefs). Also not
> covered: state and local taxes and levies, stamp duties, customs and excise, petroleum taxes, regulations
> and other subsidiary legislation, and tax periods before 2026. Questions on these are declined or answered
> with a note.

The same text goes into the README in Phase 10 (or into the Phase 9 README section, Section 12, step 6).

### 3.8 Proving at start-up that the app runs the shipped configuration (D-154)

Once per process (`st.cache_resource`), before the question box is enabled, `startup_check()` runs these
checks. All are free: no model call and no embedding call.

1. `sha256(eval/tools/config_p7.json)` equals the pinned
   `e2f61fe680759c0778719c01dfc30d2a463d6f484f54e8f7d7989e5a02ceec25` (the same constant as
   `run_e2e.P7_CONFIG_SHA256`, asserted equal by a test).
2. `run_exit_check.current_config()` without `exit_questions` equals `config_p7.json` without
   `frozen_at`, `decision` and `exit_questions`, exactly as `run_e2e.check_p7_base()` compares them. This
   recomputes the hashes of the system prompts, output schemas, tool definitions, guardrails and
   disclosure policies, heading corrections, the retrieval configuration and policy, the rate table, the
   model ids, effort levels and tagger votes.
3. The `GenerationConfig` the app will pass matches: tools on, the defaults for every limit and model,
   `config_file="eval/tools/config_p7.json"`, `config_sha256` the pinned value. (Each `gen-log/3` record
   then names config_p7 in `tools.config`.)
4. Corpus: the latest `corpus_loads` row has snapshot `2026-10-18`, and its `chunks_sha256` and
   `register_sha256` equal the committed files; `corpus_chunks` has 400 rows; `chunk_embeddings` has 400
   rows for `openai/text-embedding-3-small` at 1,536 dimensions; the date in `DISCLAIMER` is the snapshot
   date.
5. The audit schema is at the expected version and the recorded migration hashes match the files
   (Section 4.2).
6. Hosted only: the key variables are present (checked by name and non-empty only, never read out or
   printed), and no JSONL directory is configured.

The result goes into an `app_starts` row (Section 4.3) with the git commit (`RENDER_GIT_COMMIT` on Render,
`git rev-parse HEAD` locally). **If any check fails, the app fails closed:** it shows "This service is not
available right now. Please try again later.", keeps the question box disabled, and writes the list of
failed check names, with no values, to the server log and to `app_starts.problems`. Phase 12 can then
confirm on the live URL that the latest `app_starts` row is `ok` with the expected config sha.

`current_config()` lives in `eval/tools/run_exit_check.py` and imports `eval/guardrails/run_adversarial.py`.
Neither needs the eval-only packages at import time (the bge client imports `sentence_transformers` only
when it is used). The app imports them the same way `run_e2e.py` does. The deployed image must therefore
contain those two modules and the frozen config files. The sealed and spent evaluation sets are left out
of the image by `.dockerignore` (Phase 11).

## 4. Audit log (b)

### 4.1 Principles

- **One question, one `query_id`, at most one `record_id`.** The app creates `query_id` (uuid4) when the
  question is admitted. `record_id` is the `gen-log/3` id from `generate()`. It is the same in the JSONL
  record, `audit.queries.record_id` and `Answer.record_id`. It is NULL only when `generate()` raised
  before writing a record (`app_error`).
- **The audit rows are a projection of the `gen-log/3` record** (D-150). A pure function
  `project(record, answer, extras) -> AuditRows` makes them. No field is computed twice in different ways.
- **The database keeps everything the exit gate needs**, which is what was retrieved and how each shown
  item was verified. It does not keep the prompt bodies or the model's raw thinking (D-152).
- **Write-once.** The app can only open a row, close it once, and add deliveries and feedback. It cannot
  edit or delete anything. Retention and erasure run only through owner functions (Section 4.6).

### 4.2 Where it lives, how it is created and versioned (D-151)

- A dedicated schema **`audit`** in the project database: the Docker container on 5434 locally, and
  Supabase in Phase 11. On Supabase, a non-`public` schema is not exposed through the Data API unless it is
  added to the exposed schemas. Every audit table also has row-level security enabled, with a `SELECT`
  policy for the reviewer role only (Section 4.6), so the API roles see nothing even if the schema is
  exposed by mistake.
- Numbered SQL migrations in `python/audit/migrations/`: `0001_audit.sql` (tables, indexes, trigger, RLS
  on), `0002_functions.sql` (the write and read functions), `0003_roles.sql` (group roles, the `PUBLIC`
  revoke and default privileges, the grants and the reviewer policies). The
  runner `python/audit/migrate.py` applies each pending file in one transaction and records its version,
  name and sha256 in `audit.schema_migrations`. If an applied file's sha256 no longer matches its record, it
  stops with an error: an applied migration is never edited, and a change is a new numbered file.
- The schema version label is **`audit-schema/1`**. `app_starts` records it, and the start-up check
  requires it.
- The migrations are templates with three names: the schema (`audit`, or a throwaway `test_audit_<hex>`
  in tests), the app role and the reviewer role. That lets tests run against the live container without
  touching the real schema, as the existing `db_schema` fixture does.
- `load.py` and `schema.sql` are not changed. The corpus tables stay in `public`.

### 4.3 Schema (`audit-schema/1`)

Names are unqualified. The migration runner sets `search_path` to the target schema.

```sql
CREATE TABLE schema_migrations (
    version     integer PRIMARY KEY,
    name        text NOT NULL,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per app process start (Section 3.8). No personal data.
CREATE TABLE app_starts (
    start_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at        timestamptz NOT NULL DEFAULT now(),
    deployment        text NOT NULL CHECK (deployment IN ('local', 'hosted', 'test')),
    git_commit        text,
    app_version       text NOT NULL,              -- 'app/1'
    audit_schema      text NOT NULL,              -- 'audit-schema/1'
    python_version    text NOT NULL,
    streamlit_version text NOT NULL,
    config_file       text NOT NULL,              -- 'eval/tools/config_p7.json'
    config_sha256     text NOT NULL,
    corpus_load_id    bigint,
    corpus_snapshot   date,
    chunks_sha256     text,
    outcome           text NOT NULL CHECK (outcome IN ('ok', 'failed')),
    problems          text[] NOT NULL DEFAULT '{}'  -- failed check names only
);

-- Signed-in users (Section 7.2). No email, no name.
CREATE TABLE users (
    user_key    text PRIMARY KEY,                 -- HMAC-SHA256(secret, issuer || '|' || subject), hex
    provider    text NOT NULL,                    -- 'google'
    tier        text NOT NULL DEFAULT 'signed_in' CHECK (tier IN ('signed_in', 'reviewer')),
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now()
);

-- One row per admitted question.
CREATE TABLE queries (
    query_id                 uuid PRIMARY KEY,
    record_id                uuid UNIQUE,         -- gen-log/3 record_id; NULL for app_error
    start_id                 bigint NOT NULL REFERENCES app_starts,
    state                    text NOT NULL CHECK (state IN ('received', 'final')),
    received_at              timestamptz NOT NULL,
    finished_at              timestamptz,
    tier                     text NOT NULL CHECK (tier IN ('local', 'anonymous', 'signed_in', 'reviewer')),
    user_key                 text REFERENCES users ON DELETE CASCADE,
    session_key              text NOT NULL,       -- random per browser session (uuid4), not personal
    question                 text NOT NULL,
    question_chars           integer NOT NULL,    -- every stored question passed the screen (D-160)
    cost_reserved_usd        numeric(8, 4) NOT NULL,
    -- outcome
    status                   text CHECK (status IN ('answered', 'not_covered', 'refused', 'error', 'app_error')),
    refusal_flag             boolean,             -- as gen-log: status in (not_covered, refused)
    refusal_reason           text,
    refusal_stage            text,                -- pre_check | relevance_floor | verification
    refusal_text             text,                -- first block of rendered, refused and error only
    error                    text,                -- pipeline error (gen-log `error`)
    app_error                text,                -- exception type and redacted message (Section 9)
    flags                    text[] NOT NULL DEFAULT '{}',  -- Answer.flags plus ui_fallback
    -- configuration and versions, from the gen-log/3 record
    log_schema               text,                -- 'gen-log/3'
    config_file              text,
    config_sha256            text,
    retrieval_config_sha256  text,
    guardrails_policy_sha256 text,
    corpus                   jsonb,               -- record.corpus (load id, snapshot, hashes)
    generator_model          text,
    prompt_version           text,                -- 'gen-prompt/9'
    schema_version           text,                -- 'gen-schema/2'
    tool_defs_version        text,                -- 'tool-defs/2'
    verifier_model           text,
    verifier_prompt_version  text,                -- 'ver-prompt/4'
    gap_prompt_version       text,                -- 'gap-prompt/3'
    tagger_model             text,
    versions                 jsonb,               -- every other version label in the record (normaliser, tagger prompt, calc)
    -- what happened
    pre_check                jsonb,               -- guardrails.pre_check
    relevance_floor          jsonb,               -- guardrails.floor
    note_tagger              jsonb,               -- status, labels, reasoning, votes; no request or response bodies
    notes                    jsonb,               -- shown notes (Disclosure dicts)
    disclosures              jsonb,               -- all, including kind 'hidden'
    parse                    jsonb,               -- parser status, rejected citations, parser flags, listed calculations
    generator_output         text,                -- the final model call's JSON text block (no thinking blocks)
    verification_summary     jsonb,               -- verification.summary and the action list
    tool_uses                jsonb,               -- tools.tool_uses (inputs, results, limits, lookups)
    model_calls              jsonb,               -- per call: kind, turn, request_id, model, stop_reason, usage, cost, latency, error
    rendered                 text,
    rendered_sha256          text,
    -- cost and time
    usage                    jsonb,
    generation_cost_usd      numeric(10, 6),
    verification_cost_usd    numeric(10, 6),
    tagger_cost_usd          numeric(10, 6),
    total_cost_usd           numeric(10, 6),
    latency_ms               integer,             -- wall clock around generate(), measured by the app
    gen_log_file             text,                -- local only: the JSONL file holding the full record
    CHECK (state = 'received' OR (status IS NOT NULL AND finished_at IS NOT NULL)),
    CHECK ((status = 'app_error') = (record_id IS NULL) OR state = 'received'),
    CHECK ((tier IN ('signed_in', 'reviewer')) = (user_key IS NOT NULL))
);
CREATE INDEX queries_received_idx ON queries (received_at);
CREATE INDEX queries_user_idx ON queries (user_key, received_at) WHERE user_key IS NOT NULL;
CREATE INDEX queries_session_idx ON queries (session_key, received_at);
CREATE INDEX queries_status_idx ON queries (status, refusal_reason);

-- Every passage shown to the model (P1 to P8 from retrieval and attachments, P9 to P14 from tools).
CREATE TABLE passages (
    query_id          uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    label             text NOT NULL,              -- 'P1' .. 'P14'
    rank              smallint NOT NULL,
    chunk_id          text NOT NULL,              -- no foreign key: a corpus reload must not touch the audit
    citation          text NOT NULL,
    context_header    text NOT NULL,
    origin            text NOT NULL CHECK (origin IN ('retrieval', 'attachment', 'lookup', 'calculation_source')),
    score             double precision,           -- NULL for tool passages
    distance          double precision,
    reasons           text[] NOT NULL,
    disputed          boolean NOT NULL,
    cited             boolean NOT NULL,           -- cited by any parsed claim
    supporting        boolean NOT NULL,           -- a kept citation of a shown claim, or a source of a shown computed item
    chunk_text_sha256 text NOT NULL,              -- corpus_chunks.text_sha256 at write time
    PRIMARY KEY (query_id, label)
);

-- Every claim the verifier checked (shown or not).
CREATE TABLE claims (
    query_id         uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    claim_no         smallint NOT NULL,
    text             text NOT NULL,
    cited_labels     text[] NOT NULL,
    cited_chunk_ids  text[] NOT NULL,
    rule             jsonb NOT NULL,              -- tokens, missing, reason, hint, normaliser
    llm_verdict      text,
    second           jsonb,                       -- D-46 second call: verdict and reason only
    verdict          text NOT NULL CHECK (verdict IN ('supported', 'unsupported', 'contradicted', 'not_a_claim', 'unverified')),
    reason           text,
    sub_reason       text,
    unsupported_part text,
    reasoning        text,
    evidence         jsonb NOT NULL,              -- quotes with the passage each names
    citations        jsonb NOT NULL,              -- per citation: supports flag
    kept_chunk_ids   text[] NOT NULL,
    action           text NOT NULL CHECK (action IN ('kept', 'removed', 'moved_not_rendered')),
    shown            boolean NOT NULL,            -- action = 'kept' and the answer's status is 'answered'
    call_error       text,
    cost_usd         numeric(10, 6),
    latency_ms       integer,
    PRIMARY KEY (query_id, claim_no)
);

-- Every gap: from the model, checked by the gap classifier or removed by a code filter.
CREATE TABLE gaps (
    query_id  uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    gap_no    smallint NOT NULL,
    text      text NOT NULL,
    origin    text,
    class     text,                               -- missing_information | legal_content | advice | disputed_flag_repeat | NULL
    kept      boolean NOT NULL,
    reason    text,
    shown     boolean NOT NULL,
    PRIMARY KEY (query_id, gap_no)
);

-- Every calculation the tool ran (computed, declined or error), with checks K1 to K6.
CREATE TABLE computations (
    query_id        uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    calc_id         text NOT NULL,                -- 'C1', 'C2'
    scenario        text NOT NULL,
    status          text NOT NULL,                -- computed | declined | error
    inputs          jsonb NOT NULL,
    input_matches   jsonb NOT NULL,
    constants       jsonb NOT NULL,               -- each with value and chunk id
    relies_on       text[] NOT NULL,
    steps           jsonb NOT NULL,
    rounding        text,
    result          text,
    result_kind     text,
    result_text     text,
    what            text,
    from_question   text[] NOT NULL,
    assumptions     text[] NOT NULL,
    working         text[] NOT NULL,
    not_computed    text[] NOT NULL,
    declined_code   text,
    declined_reason text,
    sources         text[] NOT NULL,
    listed          boolean NOT NULL,
    checks          jsonb NOT NULL,               -- K1 to K6 as logged
    shown           boolean NOT NULL,
    withheld_reason text,
    calc_version    text NOT NULL,
    rates_sha256    text NOT NULL,
    PRIMARY KEY (query_id, calc_id)
);

-- What the UI did with the answer (Section 9). Append-only.
CREATE TABLE deliveries (
    delivery_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_id       uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    at             timestamptz NOT NULL DEFAULT now(),
    outcome        text NOT NULL CHECK (outcome IN ('shown', 'shown_plain', 'withheld_log_failure', 'ui_timeout', 'shown_in_history')),
    shown_sha256   text                           -- sha256 of the text the card displayed
);

-- Per-answer feedback (Section 7.5).
CREATE TABLE feedback (
    feedback_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_id     uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    record_id    uuid,
    at           timestamptz NOT NULL DEFAULT now(),
    session_key  text NOT NULL,
    rating       smallint NOT NULL CHECK (rating IN (-1, 1)),
    reason       text CHECK (reason IN ('wrong_citation', 'missing_information', 'should_have_answered',
                                        'should_not_have_answered', 'hard_to_read', 'other')),
    UNIQUE (query_id, session_key)
);

-- Refused admissions (Section 8): counts only, no question, no key.
CREATE TABLE admission_counts (
    day     date NOT NULL,                        -- West Africa Time (Africa/Lagos)
    tier    text NOT NULL,
    reason  text NOT NULL,                        -- session_limit | daily_limit | anonymous_pool | cost_cap | unavailable
    n       integer NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tier, reason)
);
```

Row-level security is enabled on every table above. The only policy is the reviewer role's `SELECT`
policy (Section 4.6); every other non-owner role sees no rows.

### 4.4 What each field comes from

| Audit field | `gen-log/3` source (or the app) |
|---|---|
| `record_id`, `question`, `corpus` | `record_id`, `question`, `corpus` |
| `config_file`, `config_sha256` | `tools.config` (config_p7, Section 3.8 item 3) |
| `retrieval_config_sha256` | `retrieval.config_sha256` |
| `guardrails_policy_sha256`, `pre_check`, `relevance_floor`, `note_tagger` | `guardrails.*` (tagger without `request`, `response` and per-call bodies) |
| model ids and prompt labels | `tools.calls[*].request` (`prompt_version`, `schema_version`, `tool_defs_version`, `model`), `verification.*` (`verifier_model`, `prompt_version`, `gap_prompt_version`, `normaliser`), `guardrails.note_tagger.model` |
| `passages` | `retrieval.chunks` (rank, label, chunk id, score, distance, reasons) plus `tools.passages_added`; citation, header and disputed flag from `Answer.passages`; `origin` from the reasons (attachment reasons, `lookup_section:`, `calculation source:`) |
| `claims` | `verification.claims` (every check); `shown` from the final status |
| `gaps` | `verification.gaps.gaps`; with no gap check, `parsed.gaps` with `class` NULL |
| `computations` | `tools.computations` |
| `status`, `refusal_*`, `error`, `refusal_flag` | the answer status, `refusal`, `error`, `refusal_flag` |
| `notes`, `disclosures`, `rendered` | the same fields |
| `parse` | `parsed` without the claim texts (already in `claims`) |
| `generator_output` | the text block of the last call in `tools.calls[*].response.content` |
| `model_calls` | `tools.calls`, verifier and gap calls, tagger calls: request id, model, stop reason, usage, cost, latency, error; never the request or response bodies |
| costs, `usage` | `Answer.cost_usd`, `verification_cost_usd`, `tagger.cost_usd`, `total_cost_usd`, `usage` |
| `latency_ms`, `received_at`, `finished_at` | measured by the app around `generate()` |
| `chunk_text_sha256` | `corpus_chunks.text_sha256`, read at write time |
| `tier`, `user_key`, `session_key`, `cost_reserved_usd`, `start_id` | the app |

Fields whose source is a model call (model ids, prompt labels, `generator_output`, `model_calls`) are NULL
or empty for a pre-check or relevance-floor refusal, because no model call was made. The tagger fields are
still filled, because the tagger runs on every status.

**What is not stored in the database (D-152):** the request bodies (system prompts, passage blocks, tool
definitions), the model response content other than the final JSON text block (thinking blocks, tool-use
blocks; the tool inputs are in `tool_uses`), and the verifier, gap and tagger request and response bodies.
All of these are reproducible from the pinned prompt hashes, the chunk ids and the frozen configuration.
Locally they stay in full in the JSONL record named by `gen_log_file`. On the hosted service they are not
kept anywhere: the Render disk is wiped on every deploy and restart, and the prompt bodies would add about
40 KB a question of text that may echo the user's question.

**Size.** About 15 KB a question in the database: passages about 2.5 KB, claims about 7 KB (evidence and
reasoning), the rendered answer about 3 KB, the rest about 2 KB. At the order of 50 questions a day (the
Phase 11 cost cap sets the real figure), 90 days is about 4,500 rows and 70 MB, within the Supabase free
tier's 500 MB. The corpus tables are under 10 MB (the 800 embedding rows are about 3 MB of vectors).

### 4.5 When the database write fails (D-153)

The write happens twice:

1. **Receipt, before any paid call.** `open_query()` checks admission and inserts the `received` row in
   one transaction. If it fails (database down, timeout), the question is **not sent**, nothing is spent,
   and the user sees "The service could not record your question, so it was not sent. Please try again in
   a few minutes." The day's `admission_counts` row for `unavailable` is incremented if the database
   allows it.
2. **Close, after `generate()` returns.** `close_query()` writes the final row and all child rows in one
   transaction, retried once after 1 second. **If it still fails, the answer is not shown:** "An answer was
   produced but could not be recorded, so it is not shown. Please try again later. Reference Q-xxxxxxxx."
   The row stays `received`. It still counts against the cost cap at its reserved cost, and the viewer
   lists it as unfinished. Locally the full JSONL record already exists. On the hosted service the
   server log gets only the reference and the exception type.

**Why fail closed.** (i) The Phase 9 gate and the UI promise that every answer can be inspected later; an
answer shown without a row breaks that for exactly the answer a user may report. (ii) The Phase 11 cost
cap and the per-user limits are computed from these rows. An unrecorded answer is unrecorded spend, so
showing it would let a failing database switch off the cap. (iii) The loss is small: about $0.01 and a
retry, in a case that needs the database to fail between two writes a few seconds apart. Showing the
answer anyway (option b) is simpler but gives up (i) and (ii).

### 4.6 Roles and the write and read functions

- **Owner:** the migration role (`taxcopilot` locally, `postgres` on Supabase). It owns the schema, the
  tables and the functions.
- **`taxy_app` (group role, NOLOGIN):** no table privileges. `EXECUTE` only on: `record_start`,
  `open_query`, `close_query`, `add_delivery`, `add_feedback`, `touch_user`, `user_history`,
  `erase_user`, `spend_since`, `count_since`.
- **`taxy_review` (group role, NOLOGIN):** `SELECT` on every audit table and on the corpus tables.
  Nothing else: no `EXECUTE` on any audit function.
- **Owner-only maintenance:** `purge_expired` and `erase_query` are executable by the owner alone. No
  group role gets `EXECUTE` on them. The Phase 11 retention schedule runs as the owner.
- The login users that join these group roles, and their passwords, are created by Kene: locally when the
  app is first run with separate roles (optional in Phase 9; the local app may run as the owner), and on
  Supabase in Phase 11. Claude never sets or prints a password.

**Row-level security and the reviewer.** RLS is enabled on every audit table. With RLS on and no policy,
a role that holds `SELECT` still sees 0 rows, so the reviewer needs a policy. `0003_roles.sql` creates,
on every audit table, one policy `FOR SELECT TO <reviewer role> USING (true)`. No policy exists for any
other role, so every other non-owner role, the Supabase API roles included, sees nothing. RLS is not
forced (`FORCE ROW LEVEL SECURITY` is not set), so the owner, and therefore the `SECURITY DEFINER`
functions, which run as the owner, are not filtered by it. The reviewer policy changes only what the
reviewer role can read.

**Function privileges.** PostgreSQL grants `EXECUTE` on a new function to `PUBLIC` by default.
`0003_roles.sql` therefore, in this order:

1. `REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA <audit> FROM PUBLIC;`
2. `ALTER DEFAULT PRIVILEGES FOR ROLE <owner> IN SCHEMA <audit> REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;`
   so that functions added by later migrations also start with no `PUBLIC` grant;
3. `GRANT USAGE ON SCHEMA <audit>` to the app role and the reviewer role;
4. `GRANT EXECUTE ON FUNCTION <audit>.<name>(<argument types>) TO <app role>`, one statement for each of
   the ten app functions listed above, and none for any other function;
5. `GRANT SELECT ON ALL TABLES IN SCHEMA <audit> TO <reviewer role>`, and the reviewer policy on each
   table.

A later migration that adds a function grants it explicitly, or it stays owner-only.

Every function is `SECURITY DEFINER` with `SET search_path` pinned to the audit schema and `pg_catalog`:

| Function | Does |
|---|---|
| `record_start(...) -> start_id` | inserts an `app_starts` row |
| `open_query(query_id, start_id, tier, user_key, session_key, question, reserve_usd, limits jsonb) -> (admitted, reason, remaining)` | under `pg_advisory_xact_lock`, counts today's rows for the session, the user, the anonymous pool and the spend (`sum(coalesce(total_cost_usd, cost_reserved_usd))` since midnight WAT). Inserts the `received` row only if every limit allows it; otherwise increments `admission_counts` and stores nothing else |
| `close_query(query_id, payload jsonb)` | moves a `received` row to `final` and inserts its passages, claims, gaps and computations from the payload, all or nothing; raises if the row is not `received` |
| `add_delivery(query_id, outcome, shown_sha256)` | appends a delivery |
| `add_feedback(query_id, session_key, rating, reason)` | inserts or updates that session's one feedback row for the query |
| `touch_user(user_key, provider)` | inserts the user or sets `last_seen` |
| `user_history(user_key, limit, offset)` | that user's final rows: reference, time, question, status, rendered, cited passages |
| `erase_user(user_key)` | deletes the user and, by cascade, their queries and children (app role: the signed-in user's own "Delete my history") |
| `spend_since(ts)`, `count_since(kind, key, ts)` | the figures the limit display needs |
| `purge_expired(days)` (owner only) | refuses `days` below 90 (D-159); deletes `queries` older than `days` (children by cascade), users not seen for `days` with no queries left, and `admission_counts` older than 400 days |
| `erase_query(query_id)` (owner only) | deletes one query and, by cascade, its children: the deletion request for an anonymous question (Section 6.1) |

A trigger on `queries` rejects any `UPDATE` other than the `received` to `final` move and any `DELETE`
outside `erase_user`, `erase_query` and `purge_expired`. Within the app's role this makes the log append-only. It is not
tamper-proof against the owner or a database superuser, and the design does not claim that it is.

## 5. Log viewer (c)

### 5.1 What a reviewer sees

**`app/review.py`**, a separate Streamlit app run locally (`streamlit run app/review.py`) with a read-only
connection string in `REVIEW_DATABASE_URL`. It is never deployed and never part of the image. It offers:

- **Find:** by reference (`Q-` plus a prefix), `query_id` or `record_id`; or a list filtered by date, status,
  refusal reason, tier, `ui_fallback`, unfinished rows and feedback rating.
- **One query, in this order:**
  1. *Header:* time, reference, `record_id`, status and reason, tier, latency, cost, the app start (config
     sha, commit, outcome), model ids and prompt labels, and the corpus load. A red line appears if the
     config sha is not the pinned one.
  2. *Question.*
  3. *Pre-check and relevance floor:* the logged checks and the floor score against tau.
  4. *Retrieval:* every passage P1 to P14 with rank, score, distance, reasons, origin, disputed flag, cited
     and supporting marks, and the chunk text from `corpus_chunks`. "Text changed since this answer" is
     shown if the current `text_sha256` differs from `chunk_text_sha256`.
  5. *Generator output:* the parser's view (accepted and rejected citations, parser flags) and the final
     JSON text.
  6. *Verification:* for each claim, the rule check (tokens, missing), the model verdict, the second call
     if any, the final verdict and reason, the evidence quotes **highlighted inside the cited chunk text**
     (the same whitespace-collapsed match as the evidence check), the kept citations, the action and
     whether it was shown. Then each gap with its class and whether it was kept, then the action-rule summary.
  7. *Tools:* each tool use (input, result, limit hit) and each computation with K1 to K6, shown or withheld,
     and why.
  8. *Notes and disclosures:* including `hidden` disclosures, marked "not shown to the user" (D-156).
  9. *What the user saw:* `rendered`, the deliveries with their `shown_sha256` compared with
     `rendered_sha256`, and the feedback.
  10. *Local only:* a link to the JSONL record (`gen_log_file`, `record_id`) for the full request and
      response bodies.

**`python/audit/show_query.py <reference>`** prints the same report as Markdown, or JSON with `--json`.
**`python/audit/compare_log.py <reference>`** (local) checks the database rows against the JSONL record
with the same `record_id`, field by field, using the projection in reverse. The exit gate uses it
(Section 11).

### 5.2 Who may see the log on the public deployment (D-158)

**Recommended: nobody through the public app.** The deployed app has no viewer page, so there is no
reviewer login to get wrong. Reviewers are Kene and anyone Kene gives the read-only connection string
(`taxy_review`), and they run the viewer on their own machine. Enforcement: the viewer is not in the
image, the app role cannot `SELECT` any table, and the reviewer role cannot write. A signed-in user sees
only their own questions and answers through "My questions" (Section 7.3). That view shows the user's
own answers, not the verifier internals.

Rejected alternatives: (b) a reviewer page in the public app behind a password or the `reviewer` tier,
which puts every user's questions one auth bug away from the public internet; (c) CLI only, which is safe
but makes the exit-gate review slow to read.

## 6. Privacy and retention (d)

### 6.1 What is kept

| Data | Local | Hosted | Retention (hosted) |
|---|---|---|---|
| Question text, rendered answer, retrieval and verification rows | yes | yes | 90 days, then deleted with all child rows (D-159) |
| Full request and response bodies (JSONL) | yes, in `logs/generation/` (git-ignored) | **no** | not kept |
| Session key (random uuid per browser session) | yes | yes | with the query rows |
| Signed-in user key (HMAC of issuer and subject) | yes | yes | while the user has rows; deleted 90 days after last sign-in, or at once by "Delete my history" |
| Email address, name, picture from the identity provider | no | **no** (read from `st.user` for the session only) | not kept |
| IP address, user agent, cookies | no | **no** (the app never reads them) | not kept by the app; Render's own request logs are the platform's |
| Feedback (rating and fixed reason) | yes | yes | with the query rows |
| Refused-admission counts | yes | yes | 400 days (no personal data) |
| App starts | yes | yes | kept (no personal data) |

Locally there is no automatic purge: the local data is Kene's own testing. `purge_expired(days)` (owner
only, at least 90 days) can be
run by hand.

**Erasure:** a signed-in user can delete their history at once (Section 7.3). An anonymous user can ask
for a specific question to be deleted by quoting its reference through the contact route Kene names in the
README. Kene runs `erase_query(query_id)` (owner function, Phase 11 runbook). Anonymous rows cannot be
tied to a person, so there is nothing else to find.

### 6.2 Who else sees the question

The question is sent to **Anthropic** (generation, verification and the note tagger) and to **OpenAI**
(the query embedding). Their API data policies apply. The hosting providers (**Render** for the app,
**Supabase** for the database) process it, and **Google** (or the chosen provider) processes the sign-in.
The UI and README say this plainly.

### 6.3 Streamlit and analytics (D-161)

`.streamlit/config.toml` sets `browser.gatherUsageStats = false`, so Streamlit's own usage statistics are
off. No analytics script is added. `.streamlit/secrets.toml` (sign-in secrets, Phase 11) is added to
`.gitignore` in Phase 9 step 4.

### 6.4 Wording (D-167)

**UI, above the question box:**

> Do not enter personal or taxpayer details: no names, Tax Identification Numbers, NIN or BVN, account
> numbers, phone numbers or email addresses. Use made-up figures. Your question and the answer are stored
> for 90 days so that answers can be checked, then deleted. Questions are sent to Anthropic and OpenAI to
> find and check the answer. [Privacy details]

**README "Privacy" section (Phase 10, drafted here):**

> The public demo stores each question, the answer and how it was checked (the statute passages retrieved,
> each statement's verification result) for 90 days, so that any answer can be reviewed, and then deletes
> them. It does not store IP addresses, names or email addresses. If you sign in, it stores a one-way key
> derived from your sign-in account so it can show you your own past questions; you can delete them at
> any time from "My questions". Questions are sent to Anthropic (Claude) to write and check the answer and
> to OpenAI to search the statutes; their API data policies apply. Do not enter real personal or taxpayer
> data. To ask for an anonymous question to be deleted, quote its reference to [contact named by Kene].

## 7. Access tiers, sign-in and personal features (k)

### 7.1 Tiers (D-162)

The app opens directly, without sign-in, with very limited use. Sign-in is optional and lifts the limit.

| Tier | Who | Limit (hook in Phase 9, numbers set in Phase 11) | Suggested starting values for Phase 11 |
|---|---|---|---|
| `local` | `TAXY_DEPLOYMENT=local` | none (the global cost cap still shows) | none |
| `anonymous` | not signed in | per browser session, plus a shared daily pool for all anonymous use | 3 per session; 50 a day pooled |
| `signed_in` | signed in through the identity provider | per user per day | 20 a day |
| `reviewer` | `users.tier = 'reviewer'`, set by Kene in the database | higher per day | 100 a day |
| all hosted tiers | | **global daily cost cap**, checked first | $0.50 a day (about $15 a month, within the $30 monthly API limit, with room for development) |

The day runs from midnight West Africa Time (Africa/Lagos, UTC+1). A per-session limit is easy to reset
by reloading the page. That is accepted, because the anonymous daily pool and the global cost cap are
what bound the spend. The app stores no IP address to make the anonymous limit stricter (Section 6).

### 7.2 Sign-in through an outside identity provider (D-163)

- **How:** Streamlit's built-in OIDC sign-in (`st.login("google")`, `st.user`, `st.logout()`), one
  provider (Google) in v1. There is no account creation in the app and no password, so none is stored.
  Authlib is pinned in `requirements.txt` at an exact version at least two weeks old when Phase 11 adds it.
- **Secrets:** `client_id`, `client_secret`, `cookie_secret`, `redirect_uri` and `server_metadata_url` go in
  `.streamlit/secrets.toml` (Render secret file), and `TAXY_USER_KEY_SECRET` (the HMAC key) goes in an
  environment variable. **Kene sets them in the deployment settings.** The app reads them only through
  Streamlit and `os.environ` at run time. Code and docs never print, log or store them, and the start-up
  check confirms only that they are present.
- **What is stored:** `user_key = HMAC-SHA256(TAXY_USER_KEY_SECRET, issuer + "|" + subject)`, the
  provider name, `first_seen`, `last_seen` and the tier. Not the email, the name or the provider's subject
  in clear. The sidebar shows the email from `st.user` during the session only. If the HMAC secret is
  changed, existing histories are no longer linked to their users. The Phase 11 runbook says this.
- **Retention:** as Section 6.1. A user row is deleted 90 days after the last sign-in once no queries
  remain, or at once by "Delete my history".
- **Phase:** Phase 9 builds the identity abstraction (`identity() -> Identity(tier, user_key, display)`),
  the `users` table, the tier plumbing and "My questions", all tested with a fake identity. Phase 11 wires
  `st.login`, registers the OAuth client for the live URL and adds Authlib. Phase 9 stays at $0 and needs no
  secrets.

### 7.3 Signed-in extras (D-164)

- **Higher limit:** the `signed_in` tier (Section 7.1).
- **"My questions":** a sidebar page listing the user's own final rows (time, reference, question, status)
  from `user_history(user_key)`. Opening one shows the stored `rendered` answer with its citation chips
  (passages with `supporting` true) and records a delivery `shown_in_history`. Isolation: the `user_key`
  is derived server-side from `st.user` in the running session and is never taken from a URL, query
  parameter or form field. The app role can read history only through `user_history`, and only for the key
  it passes. A test signs in two fake users and checks that each sees only their own rows.
- **"Delete my history":** asks for confirmation, then calls `erase_user(user_key)`. The rows go at once,
  and the user is signed out.

### 7.4 What stays out of v1 (D-166)

Listed as **v1.1** candidates, each because it changes what is sent to the model, so the system G3
measured would no longer be the system users get:

- a **business profile** (company size, turnover band, sector) added to the prompt or used to filter
  retrieval;
- **conversation memory**: earlier questions or answers fed into a new question;
- saved preferences that change retrieval, the prompt or the tools;
- **personal income tax** coverage (D-64).

Any of these needs a new held-out measurement before it ships. v1 features that do not change the model
input (export, history, feedback, the section reader, examples) are in.

### 7.5 v1 UI extras (D-165)

| Extra | Design | Phase |
|---|---|---|
| Read the full cited section | Section 3.3: chunks from `corpus_chunks`, in file order, definition-aware; no model call | 9 |
| Export an answer | `st.download_button`, Markdown file `taxy-answer-Q-xxxxxxxx.md`: the question, date and time, reference, then `rendered` verbatim (claims with citations, gaps, sources, disclosures, the disclaimer with the snapshot date), then the text of each cited chunk with its citation label and gazette pages, and the config sha. No PDF (it would need a new dependency) | 9 |
| Per-answer feedback | `st.feedback("thumbs")`, then an optional fixed reason (Section 4.3 list), stored with `query_id` and `record_id`. No free text, so feedback cannot carry personal details | 9 |
| Example questions by topic | about 10 new questions, one or two per topic (company income tax and the levy, deductions and gains, VAT, exemptions, definitions, Schedules, registration and returns, assessment and objection, payment and penalties, calculation), in `app/examples.yml`. Written new in the build, never copied from a sealed or spent set (`e2e_v1`, the retrieval test split). Each passes the free pre-check, and each is answered once in the capped live check (Section 11). Clicking one fills the question box; it is not sent until the user presses enter | 9 |

## 8. Rate-limit and cost-cap hook (e)

Phase 9 builds the hook and the data. Phase 11 sets the numbers and switches them on.

```python
@dataclass(frozen=True)
class RequestContext:
    tier: str               # local | anonymous | signed_in | reviewer
    user_key: str | None
    session_key: str
    now: datetime           # UTC; the limit day is computed in Africa/Lagos

@dataclass(frozen=True)
class Limits:               # from app/limits.yml or the environment; None means no limit
    per_session: int | None
    per_user_day: int | None
    anonymous_pool_day: int | None
    cost_cap_day_usd: Decimal | None
    reserve_usd: Decimal    # counted for a question until its real cost is recorded

class Gate(Protocol):
    def limits_for(self, ctx: RequestContext) -> Limits: ...
```

- The Python gate only chooses the `Limits` for the context. **The check itself runs in the database,
  in `open_query()`**, under an advisory lock and in the same transaction as the receipt insert, so two
  sessions cannot both take the last question or the last cents of the cap.
- The spend counts `total_cost_usd` for finished rows and `cost_reserved_usd` for `received` rows, so
  questions in flight and failed closes are counted. The suggested reserve is $0.05: above the dearest
  Phase 7 question ($0.036) and well above the Phase 8 answered mean ($0.0134).
- The order of checks is cost cap, then anonymous pool, then per-user or per-session. The user sees the
  first that fails: "The service has reached its usage limit for today. Please try again tomorrow." /
  "You have used the 3 questions available without signing in. Sign in to ask more." / "You have reached
  today's limit of 20 questions. It resets at midnight West Africa Time."
- Phase 9 ships `LocalGate` (no limits; the sidebar shows today's spend) and `TierGate` (reads
  `app/limits.yml`; unit tested, not enabled). Phase 11 sets the values, enables `TierGate` for
  `TAXY_DEPLOYMENT=hosted`, and confirms the cap on the live URL (Phase 11 exit gate).
- The questions left are shown in the sidebar from `count_since` and `spend_since`.

## 9. Errors, timeouts and latency (f)

**Where the work runs (D-157).** `generate()` and the close run in a module-level worker pool
(`st.cache_resource`, 4 workers). The script waits on the future with `st.status` showing
"Finding the provisions and checking the answer. This usually takes 5 to 15 seconds." After 20 s the text
changes to "Still checking. Some answers take up to a minute." After 60 s it changes to "This is taking
longer than usual." Because the work is not on the script thread, a rerun (a new question, a click) or a
closed tab does not stop it: it finishes, and it is logged. One question at a time per session: the
question box is disabled while one is running.

**The UI waits up to 120 s** (more than twice the Phase 8 maximum of 46.8 s). It then shows "This is taking
much longer than usual. The answer will not appear here. Reference Q-xxxxxxxx." and records the delivery
`ui_timeout`. A signed-in user finds the finished answer in "My questions", and the reviewer sees it in the
viewer. The SDK timeouts and retries in `llm.py` are not changed: they belong to the measured system.

| Failure | Where caught | User sees | Logged as |
|---|---|---|---|
| Start-up check fails | Section 3.8 | "This service is not available right now. Please try again later." No question box | `app_starts.outcome = failed` |
| Database down at receipt | `open_query` | "The service could not record your question, so it was not sent. Please try again in a few minutes." | nothing (count only if possible) |
| A limit reached | `open_query` | the limit text (Section 8) | `admission_counts` |
| Personal-data pattern | screen | the screen text with only "Edit question" (Section 3.6); hard block | nothing: the question is not sent and not stored |
| Embedding or database failure inside `generate()` (raises, no record) | worker | `ERROR_TEXT` "The system could not produce a grounded answer to this question." + "Please try again later." + `DISCLAIMER` | `status = app_error`, `app_error` = exception type and message passed through `llm.redact()`, `record_id` NULL |
| Model API error inside `generate()` | pipeline | the pipeline's `error` text, rendered | the `gen-log/3` record, `status = error` |
| Verifier outage | pipeline | "The answer could not be checked, so it is not shown. Please try again later." | `error` / `verification_unavailable` |
| Close fails twice | worker | "An answer was produced but could not be recorded, so it is not shown. Please try again later. Reference …" | row stays `received`; delivery `withheld_log_failure` if the database allows it |
| Parity check fails | card | the plain `rendered` text | flag `ui_fallback`, delivery `shown_plain` |
| UI wait over 120 s | script | the timeout text | delivery `ui_timeout`; the row is finished by the worker |

No error message shows a stack trace, a connection string, a key or a file path. Server logs get the
reference and the exception type only.

## 10. Tests (g)

All in the default suite are free. They fail, not skip, when the database is down (the project rule).
They make no paid call.

| File | What it checks |
|---|---|
| `tests/test_audit_schema.py` | migrations apply to an empty throwaway schema and re-apply as a no-op; `schema_migrations` holds each file's sha256; an edited applied file stops the runner; the columns, types, checks and indexes match a spec table in the test; row-level security is on everywhere, and the only policy on each table is the reviewer's `SELECT` policy; grants, under `SET ROLE` to throwaway copies of the roles: the app role cannot `SELECT`, `INSERT`, `UPDATE` or `DELETE` any table directly; **the reviewer role reads a row written through the functions** (an `open_query` and `close_query` pair, with its passages and claims), and it cannot write or execute any audit function; **a throwaway role with no grants cannot execute any audit function** (each one is tried) and sees no rows; the app role can execute exactly its ten functions and not `purge_expired` or `erase_query`; `has_function_privilege('public', ...)` is false for every audit function, including one added by a later test migration (the default privileges) |
| `tests/test_audit_write.py` | `project()` on `gen-log/3` records made by running `generate()` with the existing fake clients (`tests/test_tools.py` and `tests/test_verification.py` patterns), one per status and reason: answered with a shown computed item and a withheld one, removal lines, not_covered with gaps, each pre-check refusal, low_relevance, unsupported_answer, verification_unavailable, a tool error; then a write and read round trip; the same `record_id` in the JSONL record and the row; `close_query` twice raises; a final row cannot be changed; `app_error` with NULL `record_id` |
| `tests/test_audit_limits.py` | `open_query` counts and the cap with reserves; two concurrent `open_query` calls for the last slot admit exactly one; the WAT day boundary; `admission_counts` increments; `purge_expired`, run as the owner, deletes only old rows (with their children) and refuses `days` below 90; `erase_query` removes one query and its children; `erase_user` removes only that user's rows; `user_history` returns only the key passed |
| `tests/test_app_service.py` | the service with fakes: receipt failure means the fake model client is never called; a close failure means the answer is withheld and the delivery recorded; the temp-dir record is read and its `record_id` checked; the local JSONL append under the lock; the screen: each pattern (an email address, each Nigerian phone form, a 10-plus-digit run) blocks, and a blocked question makes no database call and no model call and is stored nowhere; amounts with commas or the ₦ sign (for example `₦1,000,000,000`, `1,000,000,000`, `₦1000000000`) and short plain amounts (`2600000`) pass; the worker finishes after a simulated rerun |
| `tests/test_startup.py` | the check passes on the repository; it fails on a tampered copy of `config_p7.json`, a changed rate file, a wrong corpus hash, a missing embedding row and a wrong schema version; on failure the app shows no question box (AppTest) |
| `tests/test_app.py` (Streamlit `AppTest`) | the first render shows the disclaimer (equal to `DISCLAIMER`), the snapshot date, the scope text and the privacy line; for each status the card shows the expected blocks; **parity**: joined block texts equal `rendered` for every fixture answer; a citation popover shows the chunk text; "Read the whole section" for a definition chunk and for a subsection group; export content holds the question, `rendered`, the disclaimer and the snapshot date; feedback writes a row; the limit messages; the screen message with only an "Edit question" button and no way to send the blocked text; the timeout path (a fake service that does not finish, with the wait shortened); two fake signed-in users see only their own history; "Delete my history" |
| `tests/test_review.py` | the viewer and `show_query.py` on written fixtures: evidence highlighting, hidden disclosures marked, the drift flag, prefix resolution, `compare_log.py` reports zero differences on a matching pair and names the field on a changed one |
| `tests/test_app_live.py` (marked `api`) | one answered question through the real service, run only with `TAXY_RUN_API_TESTS=1` (D-108); capped at $0.05 |

The app gets its services through one injectable object (`st.session_state["services"]` in tests), so no
test builds a real Anthropic or OpenAI client. An autouse fixture in the new app tests makes constructing
`AnthropicLLMClient` or an embedding client raise, so an accidental paid call fails the test instead of
spending money.

## 11. Exit gate (h)

**Gate (plan):** a reviewer can open the log for any past query and see exactly what was retrieved and how
the final answer was verified.

**"See exactly" means** that for the query, the viewer shows all of the following, and each agrees with the
`gen-log/3` JSONL record with the same `record_id`:

1. the question, time, reference, `record_id`, config sha (equal to the pinned config_p7 sha), model ids
   and prompt labels;
2. every passage shown to the model (P1 to P14) with rank, score, reasons and origin, and its chunk text;
3. the pre-check and relevance-floor results, and the refusal stage and reason if any;
4. for every claim: the rule check, the model verdict, the final verdict and reason, the evidence quotes
   found in the cited chunk text, the kept citations, the action, and whether it was shown;
5. every gap with its class and whether it was kept;
6. every computation with K1 to K6, shown or withheld and why;
7. the notes and disclosures, hidden ones included;
8. the rendered answer, whose sha256 equals the `shown_sha256` of its delivery (what the card displayed).

**Questions (D-168).** 13 live questions through the local app (`TAXY_DEPLOYMENT=local`): the 10 example
questions (Section 7.5), which include at least one calculation, plus three refusals that make no or
almost no paid call: a section number that does not exist (`no_such_section`), another country's tax law
(`foreign_law`) and an unrelated question (`low_relevance`, embedding only). Whether any example comes back
`not_covered` is not forced. That path, and every other status, is covered by the AppTest fixtures with
fake clients. Fault injection, free, with fake clients: a verifier outage (`verification_unavailable`),
an embedding failure (`app_error`), a close failure (`withheld_log_failure`) and a UI timeout. No sealed or
spent question is used.

**Who checks:**

- `compare_log.py` runs on all 13 live queries (free, automated): zero differences required.
- The overseer session draws 3 of the 13 with a seeded random choice (seed 20261113) and reads items 1 to 8
  in the viewer against the JSONL record. It also reads every fault-injection case.
- Kene opens 2 references of his choice in the viewer and confirms that he can see what was retrieved and
  how each shown statement was checked (one screen per query; a short guide like the Phase 8 spot check).

**Pass rule:** all 13 live queries have a `final` row and a `shown` delivery; `compare_log.py` reports
zero differences on all 13; the overseer finds items 1 to 8 present and agreeing on all 3 sampled queries
and on every fault case; each fault case behaves as Section 9 says; Kene confirms his 2; the default test
suite passes. The answers' quality is not judged here (G3 did that). A wrong answer does not fail this gate,
but a missing or wrong log entry does.

## 12. Build plan and cost (i)

The build runs in two parts, each ending with its own Summary for Overseer and review: **Build A**
(steps 1 to 3: schema, functions, roles, migrations, the start-up check and the service) and **Build B**
(steps 4 to 6: the Streamlit app, the viewer and the docs; then the free full suite, step 7). Build B
starts only after the overseer approves Build A. Before step 8 there is a stop: **the overseer reviews
the wording of `app/examples.yml` before any paid call**, and Kene gives the go for the live check.

| Step | Files | Paid |
|---|---|---|
| **Build A** | | |
| 1. Audit schema, functions, roles, migration runner and projection | `python/audit/__init__.py`, `python/audit/migrations/0001_audit.sql`, `0002_functions.sql`, `0003_roles.sql`, `python/audit/migrate.py`, `python/audit/project.py`, `python/audit/store.py`; `tests/test_audit_schema.py`, `tests/test_audit_write.py`, `tests/test_audit_limits.py` | $0 |
| 2. Start-up check | `python/audit/startup.py` (or `python/interface/startup.py`); `tests/test_startup.py` | $0 |
| 3. App service: screen, gate hook, worker, temp-dir record, close, deliveries, identity abstraction | `python/interface/service.py`, `python/interface/limits.py`, `python/interface/identity.py`, `python/interface/screen.py`, `app/limits.yml`; `tests/test_app_service.py` | $0 |
| *Stop: Build A summary and overseer review* | | |
| **Build B** | | |
| 4. Streamlit app: page, answer card and parity, section reader, export, feedback, examples, history page | `app/main.py`, `app/cards.py`, `app/copy.py` (every UI text in one place), `app/examples.yml`, `app/pages/my_questions.py`, `.streamlit/config.toml`; `.gitignore` (add `.streamlit/secrets.toml`); `tests/test_app.py` | $0 |
| 5. Viewer and CLI | `app/review.py`, `python/audit/show_query.py`, `python/audit/compare_log.py`; `tests/test_review.py` | $0 |
| 6. Docs | this design's build record; `docs/README.md`; a README "Run the app" and "Privacy" section (Section 6.4 text); `CLAUDE.md` run line (agent file, not committed) | $0 |
| 7. Free full suite | `.venv\Scripts\python -m pytest -q` | $0 |
| *Stop: Build B summary; the overseer reviews `app/examples.yml` wording; Kene's go for step 8* | | |
| 8. Capped live check (after Kene's go) | the 13 questions of Section 11 through the local app; `tests/test_app_live.py` once with `TAXY_RUN_API_TESTS=1` | about $0.15, **cap $0.40** |
| 9. Exit-gate review | overseer sample, Kene's 2 references, build record | $0 |

**Cost.** Steps 1 to 7 and 9 are free: no model or embedding call. Step 8: 10 answered questions at the
Phase 8 mean of $0.0134 plus 3 refusals at about $0.0002 is about $0.14, plus the one `api` test (about $0.02). **Cap
$0.40 for the whole phase. Stop rule:** the runner stops at the first question that would take the
spend past the cap. The live check runs once. If a question gives a wrong log entry, that is a bug fix
plus one rerun of that question only, proposed with a new cap before any further spend (D-97 precedent).
Within the $30 monthly limit.

No new dependency in Phase 9. Authlib comes in Phase 11 with sign-in.

## 13. Risks and open items

| Risk | Mitigation |
|---|---|
| The structured card shows something other than what the pipeline rendered | the parity check on every card, with the plain-text fallback; the AppTest parity test on every status |
| A UI hint or wording reads as advice | every UI text sits in `app/copy.py` for one review; hints say only what to rephrase, never what the law says |
| Users type personal data anyway | the hard-block screen for emails, phone numbers and long digit runs; the privacy line; 90-day retention; no IP or email stored. Names and addresses are not detected, and the privacy line covers them |
| The screen blocks a real amount written as 10 or more plain digits | the message tells the user to write it with commas or the ₦ sign, and both forms pass |
| Startup check imports eval modules that later grow an eval-only import | a test imports the app's start-up module in a clean process with only `requirements.txt` packages |
| The cost cap undercounts in-flight questions | the reserve per received row and the advisory lock (Section 8) |
| Long tail of latency beyond 120 s | the worker finishes and logs; the user gets a reference, and signed-in users see it in their history |
| The hosted service loses the full request bodies | by design (D-152); everything the gate needs is in the database; the bodies are reproducible from the pinned hashes |
| A corpus reload changes chunk text under old audit rows | `chunk_text_sha256` per passage and the viewer's drift flag; no foreign key to the corpus |
| The HMAC secret is rotated or lost | histories detach (documented); no other data depends on it |
| A chat layout implies memory | the "each question is answered on its own" line; memory is listed for v1.1 (D-166) |

**Open items for Phase 10:**

1. Check the privacy note (Section 6.4, UI and README text) against the Nigeria Data Protection Act 2023.
2. Kene names the contact route for deletion requests of anonymous questions (Section 6.1, D-167).
3. Update the README "Status" line, which still describes Phase 1.

## 14. Decisions (j)

Each row lists options, the recommendation and the phase that builds it. **Approved by Kene 2026-11-12:
D-150 to D-168 as recommended, except D-160, which was changed to option (c), a hard block.** The
approved options are recorded in the [decision log](../00_planning/decision_log.md).

| Id | Decision | Options | Recommendation | Phase |
|---|---|---|---|---|
| D-150 | How the audit rows are produced | (a) run `generate()` with a fresh temp `log_dir`, read the one `gen-log/3` record back and project it; (b) add a log-sink parameter to `generate()`; (c) build rows from the `Answer` alone | **(a)**: no change under `python/generation/`, one source for JSONL and the database, and it removes the concurrent-append race. (c) lacks scores, distances, pre-check and floor logs | 9 |
| D-151 | Where the audit lives and how it is versioned | (a) schema `audit`, numbered SQL migrations with sha256 records, `audit-schema/1`, RLS on with a `SELECT` policy for the reviewer role only, `EXECUTE` revoked from `PUBLIC` (also by default privileges) and granted to the app role on its ten functions only (Section 4.6); (b) tables in `public` added to `schema.sql`; (c) a migration tool such as Alembic | **(a)**: kept apart from the corpus, not exposed by the Supabase Data API, no new dependency | 9 |
| D-152 | What the database stores | (a) everything the gate needs plus the final generator JSON and per-call ids; no prompt bodies or thinking; full JSONL locally only, none hosted; (b) store the full `gen-log/3` record as `jsonb` too; (c) keep JSONL on the hosted disk | **(a)**: about 15 KB a row against 55 KB; the bodies are reproducible from hashes; the Render disk is wiped on deploy | 9 |
| D-153 | When the database write fails | (a) receipt row before any paid call; if the close fails after one retry, the answer is withheld with a reference; (b) show the answer and log to a spill file; (c) receipt only, no withholding | **(a)**: keeps "every shown answer is in the log" and keeps the cost cap honest; costs about $0.01 in a rare case | 9 |
| D-154 | Start-up proof of the shipped configuration | (a) pinned config_p7 sha256 plus `current_config()` equality, the app's own config, corpus hashes and counts, schema version; fail closed and record in `app_starts`; (b) sha256 of config_p7 only; (c) copy `current_config()` into `python/` | **(a)**: the sha alone does not prove the code still matches the file; reusing the eval builder keeps one definition | 9 |
| D-155 | How the answer is drawn | (a) structured card from the record, with a runtime parity check against `rendered` and a plain-text fallback; (b) `rendered` Markdown only, with a sources panel; (c) structured with no parity check | **(a)**: clickable citations without ever showing text the pipeline did not render | 9 |
| D-156 | Hidden disclosures (kind `hidden`) | (a) shown in the viewer only, marked "not shown to the user"; (b) also in the public UI behind an expander | **(a)**: the public answer stays exactly the measured one; D-09 is not narrowed because the reviewer sees them | 9 |
| D-157 | Long questions and timeouts | (a) worker pool, staged status text, 120 s UI wait, then a reference; work always finishes and is logged; SDK settings unchanged; (b) block the script thread; (c) lower the SDK timeouts or retries | **(a)**: a rerun cannot lose a log row; (c) would change the measured system | 9 |
| D-158 | Log viewer and who may see it | (a) local-only reviewer app plus CLI on a read-only role; no log page on the public app; (b) a password-protected reviewer page in the public app; (c) CLI only | **(a)**: no public path to other users' questions | 9 |
| D-159 | Retention on the hosted service | (a) 90 days, then rows and children deleted by `purge_expired` (owner only, refuses fewer than 90 days; `erase_query` also owner only); (b) 30 days; (c) 365 days | **(a)**: long enough for Phase 12 QA and user reports, short for a public demo; local data has no automatic purge | 9 (function), 11 (schedule) |
| D-160 | Personal-data screen | (a) warn and confirm on email, phone and 10-plus-digit patterns, storing pattern names only; (b) none; (c) hard block: on an email address, a Nigerian phone number or a 10-plus-digit run the question is not sent and not stored, the only button is "Edit question", and the message shows how to write amounts (with commas or ₦) | **(c), approved by Kene** (amended from the recommended (a)): nothing matching these patterns reaches the models or the log; no screen field is stored | 9 |
| D-161 | Telemetry | (a) Streamlit usage statistics off, no analytics, no IP stored; (b) default Streamlit settings | **(a)** | 9 |
| D-162 | Access tiers and the limit hook | (a) anonymous: per-session limit plus a shared daily pool; signed in: per-user daily limit; global daily cost cap with a reserve per question, enforced in `open_query` under a lock; numbers in Phase 11 (suggested 3 per session, 50 a day pooled, 20 a day signed in, $0.50 a day cap, $0.05 reserve); (b) cost cap only; (c) sign-in required for any use | **(a)**: matches Kene's direction (open with very limited use) and bounds spend even if the session limit is bypassed | 9 (hook), 11 (numbers and switch-on) |
| D-163 | Sign-in | (a) Streamlit OIDC with one provider (Google); store only an HMAC user key, the provider and dates; no email or name; Authlib pinned in Phase 11; (b) as (a) but store the email; (c) no sign-in in v1 | **(a)**: no passwords, minimal personal data, enough for limits and history | 9 (abstraction, tables), 11 (wiring, secrets by Kene) |
| D-164 | Signed-in history | (a) "My questions" from the audit log through `user_history`, keyed server-side, with "Delete my history"; same 90-day retention; (b) history kept longer for signed-in users; (c) no history | **(a)** | 9 (built and tested with a fake identity), 11 (live) |
| D-165 | v1 UI extras | (a) full-section reader (definition-aware, no model call), Markdown export with citations, disclaimer, snapshot and cited text; thumbs feedback with fixed reasons and no free text; about 10 new example questions by topic, not from sealed or spent sets; (b) as (a) with free-text feedback; (c) as (a) with PDF export | **(a)**: free text invites personal data, and PDF needs a new dependency | 9 |
| D-166 | Personalisation | (a) out of v1: a business profile, conversation memory and preferences that change retrieval or the prompt go to v1.1, each needing a new held-out measurement; (b) a business profile in v1 | **(a)**: anything that changes what is sent to the model changes the system G3 measured | v1.1 |
| D-167 | UI and README wording | (a) the texts in Sections 3.1, 3.2, 3.7 and 6.4 (title "Nigerian SME Tax Reference", the scope box with personal income tax first, the privacy line, the README privacy paragraph); Kene names the contact route for deletion requests; (b) Kene's own wording | **(a)**, closing overseer open item 7 for the UI; the README copy lands in Phase 10 | 9 (UI), 10 (README) |
| D-168 | Exit-gate procedure and spend | (a) Section 11: 13 live questions plus 4 free fault cases, `compare_log.py` zero differences, an overseer sample of 3, Kene's 2 references; cap $0.40 for the phase, stop rule as Section 12; (b) the overseer reads all 13, with no comparator | **(a)**: the comparator checks every field mechanically, and the people check that the viewer is readable | 9 |
