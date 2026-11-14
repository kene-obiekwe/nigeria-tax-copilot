# Deployment Design (Phase 11)

**Status:** approved by Kene 2026-11-14 (written 2026-11-13, revised 2026-11-14). Decisions D-192 to D-207,
all option (a), are in the [decision log](../00_planning/decision_log.md); D-194 closes D-183. D-189 to D-191
belong to the [UI revision](../phase09_interface/ui_revision.md), so this design's decisions start at D-192.
**Plan:** [implementation plan](../00_planning/implementation_plan.md), Phase 11 (and the Phase 12 gate
wording). **Builds on:** [interface design](../phase09_interface/interface_design.md) (D-150 to D-170),
[packaging design](../phase10_packaging/packaging_design.md) (D-171 to D-188) and
[reproduce](../reference/reproduce.md).

> **Disclaimer.** This is a research and reference tool. It is not tax advice and is not a substitute for a
> licensed tax professional. It must not be used for an actual filing or compliance decision. Nothing in
> this design is legal advice; the data-protection points state what official texts say and leave every
> conclusion to Kene.

This design turns Phase 11 into steps a build session can follow without making design choices. One rule
runs through it: **the deployment runs exactly the shipped system.** That means the frozen Phase 7
configuration `eval/tools/config_p7.json` (sha256 `e2f61fe6...ec25`), proved at every start by the D-154
start-up check, the D-21 retrieval record `config_final.json`, the shipped retrieval configuration
`config_v3.json`, `config_g2_v4.json` and the D-149 pins. Nothing under `python/generation/`,
`python/retrieval/`, `python/tools/`, `python/embed/`, the audit migrations, `data/` or any frozen or
pinned file changes. The small interface changes Phase 9 left to this phase are listed in Section 0.2
(D-192).

Contents: 0 Inputs and scope. 1 Container image. 2 Hosted database. 3 Hosting (3.4 the cold-start notice). 4 Secrets. 5 Sign-in.
6 Limits, cost and kill switch. 7 Retention and erasure. 8 Data-protection follow-ups. 9 Exit gate.
10 Runbook outline. 11 README and docs after go-live. 12 Build plan. 13 What can go wrong. 14 Decisions.
15 Sources. 16 Local measurements. 17 Build record.

## 0. Inputs and scope

### 0.1 Facts this design rests on (checked 2026-11-13)

| Fact | Value | Where |
|---|---|---|
| Phase 9 hooks left for Phase 11 | `gate_for("hosted")` raises, so a hosted app cannot start without a cap; `TierGate` reads `app/limits.yml` (suggested values, not final); sign-in is an identity abstraction tested with a fake identity; the profile block and the My questions page call `services.extra["sign_in"]` and `services.extra["sign_out"]`, which nothing sets yet, so the "Sign in with Google" and "Log out" buttons are disabled and the sidebar caption says sign-in "is switched on in a later phase" | `python/interface/limits.py`, `identity.py`, `app/services.py`, `app/chrome.py`, `app/pages/my_questions.py`, `app/copy.py`; interface design Sections 7 and 8, Build record deviation 5; UI revision |
| Start-up check, hosted part | needs `DATABASE_URL`, `LLM_API_KEY`, `EMBEDDING_API_KEY` and `TAXY_USER_KEY_SECRET` present (name and non-empty only); refuses a `TAXY_JSONL_DIR`; records `RENDER_GIT_COMMIT` as `app_starts.git_commit` | `python/interface/startup.py` (`HOSTED_REQUIRED_ENV`, `git_commit()`) |
| Files the start-up check reads | besides `python/` and `app/`: `eval/tools/run_exit_check.py`, `config_p7.json`, `eval/guardrails/run_adversarial.py`, `config_g2_v4.json`, `eval/retrieval/run_eval.py`, **`config_v3.json`** and `config_final.json`, `eval/generation/run_exit_check.py`, `data/curated/*`, `data/derived/chunks/*`, `data/derived/section_map.csv` | measured in a local image build, Section 16; interface design Section 3.8 names only the two eval modules and "the frozen config files" |
| Retrieval search | exact cosine search with `ORDER BY embedding <=> q, chunk_id`; **no ANN index** ("Exact search only (no ANN index) at this corpus size") | `python/embed/schema.sql`, `python/retrieval/retrieve.py` |
| Database connections | `check_pgvector.connect()` uses `DATABASE_URL` when set, `connect_timeout=5`; `generate()` holds one connection for a whole question; each audit call opens its own short connection; `open_query()` takes `pg_advisory_xact_lock` | `python/check_pgvector.py`, `generation/generate.py` (line 718), `audit/store.py`, `audit/migrations/0002_functions.sql` |
| Local database | pgvector 0.8.7 on PostgreSQL 17 (container on 5434) | `SELECT extversion` 2026-11-13 |
| Cost per question | Phase 8: mean $0.0089; answered $0.0134; declined $0.0071; refused $0.0002; subset T mean $0.0128. Phase 9 live check: 10 answered, mean $0.0181, highest $0.0241; refusals $0.0001 to $0.0002. Dearest Phase 7 question $0.036. The query embedding (a few hundred-thousandths of a cent) is not in `total_cost_usd` | interface design Sections 1 and 8 and live check; [evaluation report](../phase08_evaluation/evaluation_report.md) Section 4 |
| Tested dependency set | the clean-clone venv of 2026-11-13: 70 packages, suite 1864 passed, 31 skipped | [clean-clone check](../phase10_packaging/clean_clone_check.md), "`pip freeze` of the clean venv" |
| Phase 8 cases for the live gate | subset T (the 30 Phase 4 test-split questions, ids `Q...`): all 30 answered in both runs, with identical passage lists in run 1 and run 2 | `eval/e2e/results/e2e_run1.json`, `e2e_run2.json` |
| Streamlit sign-in | Streamlit 1.66 `st.login` needs Authlib 1.3.2 or later and `httpx` (its `auth` extra: `Authlib>=1.3.2`, `httpx>=0.24.1`); neither is installed (`anthropic` uses `httpx2`); callback path `/oauth2callback`; identity cookie expires after 30 days | installed `streamlit` metadata and `streamlit/web/server/starlette/starlette_auth_routes.py`; Streamlit docs (Section 15) |

### 0.2 What the Phase 11 build may change (D-192)

The prompt for this design keeps `python/` and the frozen files out of scope. Phase 9 left four switches to
Phase 11 on purpose (interface design Sections 7.2 and 8, Build record deviation 5). The design therefore
proposes a closed list. Everything else stays as it is.

| May change | Why |
|---|---|
| `python/interface/limits.py` | `gate_for("hosted")` returns `TierGate.from_file()`, failing closed if the cap is null or the reserve is not positive; the cap may be **lowered only** by `TAXY_COST_CAP_DAY_USD` (Section 6.3) |
| `app/services.py` | on a hosted deployment only: identity from `st.user` through `resolve_identity()`; `services.extra["sign_in"]` set to a call of `st.login("google")` and `services.extra["sign_out"]` set to `st.logout` (Section 5.5). `app/chrome.py` and `app/pages/my_questions.py` already call both hooks and do not change |
| `app/main.py` | the early start-up notice, drawn before the heavy imports and cleared once the start-up check has run (Section 3.4, D-207) |
| `app/copy.py`, `app/limits.yml` | the start-up notice text (Section 3.4); the final limits (Section 6.1) |
| `tests/test_app.py`, `tests/test_app_service.py`, `tests/test_docs.py` | the matching free tests |
| New: `Dockerfile`, `.dockerignore`, `requirements.lock.txt`, `deploy/`, `eval/deploy/`, `docs/phase11_deployment/` | the image, the hosted set-up and the gate checks |
| `requirements.txt` | two exact pins, `Authlib==1.9.1` and `httpx==0.28.1` (Section 5.2) |
| `README.md`, `docs/README.md`, `NOTICE.md` if named in Section 11 | go-live text |

Not changed: `python/generation/`, `python/retrieval/`, `python/tools/`, `python/embed/`, `python/audit/`
(migrations, store, startup) and `python/interface/startup.py`, `screen.py` and `service.py`; every file
under `eval/` and `data/` that exists today; `.streamlit/config.toml` (hosted flags go on the command
line, Section 1.5). The start-up check does not hash any file in the "may change" list, so it still proves the
shipped pipeline.

## 1. Container image (D-193, D-194)

### 1.1 Base image and Python

`python:3.12-slim-trixie`, pinned by its index digest
`sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` (read with
`docker buildx imagetools inspect` on 2026-11-13). The image's Python is **3.12.16**; the measured system
and the clean clone ran 3.12.2 on Windows. A patch release on another operating system is expected for any
hosted build. The start-up check, the retrieval parity check (Section 9.1) and the live gate (Section 9.2)
are what prove the behaviour, and `app_starts.python_version` records the version. The digest changes only
through a new decision.

### 1.2 Dependencies and the lock (closes D-183)

`requirements.txt` stays the only requirements file in the image: never `requirements-eval.txt` or
`requirements-ocr.txt`. The scratch build of 2026-11-13 showed why D-183 left the lock to this phase. A plain
`pip install -r requirements.txt` resolved **newer** transitive versions than the tested working venv, for
example `python-dateutil` 4.0.0 (working venv 2.9.0.post0), `starlette` 1.10.0 (1.8.0) and `regex`
2026.11.9 (released that week). The clean-clone venv of the same day resolved the same set as the image,
apart from `colorama` (Windows only) and `hf-xet` (Linux wheel). The suite passed on that set (1864/31).

**Approved (D-194 (a)):** `requirements.lock.txt` is the clean-clone `pip freeze` with `colorama` removed,
`hf-xet==1.8.0` added (as resolved in the image), plus the sign-in packages and whatever they resolve to
(`Authlib==1.9.1`, `httpx==0.28.1`, and `joserfc`, `httpcore` at the versions pip resolves, each published at
least 14 days before the build day). The Dockerfile runs `pip install --no-cache-dir --no-deps -r
requirements.lock.txt`, then `pip check`. A free test checks that every exact pin in `requirements.txt` has
the same version in the lock, so the two cannot drift. No hashes in v1: PyPI files are immutable per
version, and hash generation needs a new tool (option (b) in Section 14).

### 1.3 What goes into the image: an allow-list `.dockerignore`

The `.dockerignore` starts with `*` and allows only named paths. Under an allow-list, a file nobody listed
never enters the image. That covers Render's secret files too: Render copies them into the root of the build
context, so `COPY . .` would otherwise put them in the image (Render "Using secrets", Section 15).

```
*
!requirements.txt
!requirements.lock.txt
!.streamlit/config.toml
!app/
!python/
!data/curated/
!data/derived/chunks/
!data/derived/section_map.csv
!eval/tools/run_exit_check.py
!eval/tools/config_p7.json
!eval/guardrails/run_adversarial.py
!eval/guardrails/config_g2_v4.json
!eval/retrieval/run_eval.py
!eval/retrieval/config_final.json
!eval/retrieval/config_v3.json
!eval/generation/run_exit_check.py
!deploy/supabase/prod-ca-2021.crt
**/__pycache__
app/review.py
python/ingest/
data/curated/ntaa_nrs_proofread/
```

**In the image (1.8 MB of project files, measured):** the app, the Python packages the app imports, the
curated policies, rate table and register, the 400 chunks with their build manifest, the section map, and
the eval modules and frozen configurations the start-up check reads. **Never in the image:** `.env` and any
`.env.*`, `.streamlit/secrets.toml`, agent files (`CLAUDE.md`, `.agents/`, `.claude/` and the rest of
CLAUDE.md Section 1), `.git`, `logs/`, `data/raw/`, `data/cache/` (no embedding cache is needed at run time:
the vectors are in the database, and a question is embedded by the API), `data/derived/ocr/`, `docs/`,
`tests/`, every evaluation question set (sealed or spent), the review packs, `app/review.py` (D-158) and
`python/ingest/`. In the scratch build the start-up check passed with exactly this list. The first attempt
without `config_v3.json` failed `live_config`, which shows the check catches a missing file.

**Added in B3a (D-211):** `deploy/supabase/prod-ca-2021.crt`, Supabase's public CA certificate (one certificate,
no key), so that the hosted app can connect with `sslmode=verify-full` (Section 2.2). It is the only file of
`deploy/` in the image; the SQL files and the password script stay out.

### 1.4 User, layout and command

- Working directory `/srv/taxy`; `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
  HOME=/tmp TAXY_DEPLOYMENT=hosted`.
- Non-root user `taxy`, uid and gid **1000**. Render's guide says the app user must be in group 1000 to
  read secret files. The project files belong to root and are read-only to `taxy`.
- `RUN ln -s /etc/secrets/secrets.toml /srv/taxy/.streamlit/secrets.toml`: Streamlit finds the sign-in
  secrets where it looks by default. The link is not a secret, and the file exists only at run time.
- `EXPOSE 10000`; `CMD ["sh", "-c", "exec streamlit run app/main.py --server.port=${PORT:-10000}
  --server.address=0.0.0.0 --server.headless=true --server.fileWatcherType=none
  --client.showErrorDetails=none"]`. Render's default `PORT` is 10000, and the service must bind
  `0.0.0.0` (Render "Web services").

### 1.5 Streamlit settings

`.streamlit/config.toml` is unchanged and goes into the image as it is: `browser.gatherUsageStats = false`
(D-161); `client.toolbarMode = "viewer"`, which keeps Streamlit's menu at the top right with the System, Light
and Dark theme choice and hides the developer options (rerun, clear cache, deploy), as the UI revision set it;
`client.showSidebarNavigation = false`; and the theme colours, all local, with no font or image from another
site. The hosted command line adds `client.showErrorDetails=none`, so no exception type, message or
traceback reaches the browser. That backs up the rule in interface design Section 9 that no error shows a stack trace.
It also adds `server.fileWatcherType=none` (on Docker Desktop Streamlit fell back to polling, which spends CPU on a 0.1 CPU plan) and `server.headless=true`.
XSRF protection stays on (the default).

### 1.6 Size

Measured: **725 MB** (725,427,178 bytes). Of that, 621 MB is site-packages (pyarrow 173 MB, pandas 82 MB,
numpy 70 MB with its libraries, Streamlit 36 MB, the two model SDKs 56 MB) and 1.8 MB is project files.
Most of the size comes from Streamlit's own dependencies (pyarrow, pandas), which this design does not
remove. `pytest` from `requirements.txt` is in the image; it is never run there.

### 1.7 The no-secret image test

A new free script, `eval/deploy/check_image.py`, builds the image from a clean checkout of the commit to be
deployed, then fails on any of the following:

1. a file under `/srv/taxy` that is not in `git ls-files` at that commit, or not matched by the allow-list;
2. any forbidden path present (`.env*`, `secrets.toml` other than the link, the agent files, `.git`,
   `logs`, `data/raw`, `data/cache`, `tests`, `docs`, `eval/e2e`, `app/review.py`);
3. a secret-shaped string anywhere under `/srv/taxy` (API-key prefixes of both providers, PEM private-key
   headers, a `postgresql://user:password@` URL, `client_secret`);
4. an `ENV`, `ARG` or `LABEL` in `docker history --no-trunc` or `docker inspect` whose name contains
   `KEY`, `SECRET`, `PASSWORD` or `DATABASE_URL`;
5. the image's `Config.Env` holding anything beyond the base image's variables and the four in Section 1.4.

It prints the image size and the file count, and removes the image unless `--keep` is given. It needs Docker
and about 3 minutes, so it is an opt-in script, not part of the default suite. In the scratch run all five
checks were clean.

**Build record, B1.1 (2026-11-14; the two wording fixes Kene chose after the overseer's review of B1.1).** The
text above is kept as approved; these two readings are what the script does.

- **Check 4 (names added by this build).** The base image sets `GPG_KEY`, the public id of the Python release
  signing key, and its history shows it. A name the base image already carries is exempt from check 4, so the
  check looks at the names this build adds. Without that exemption it would fail on every build.
- **Check 5 (five variables).** Section 1.4 sets **five** variables, not four: `PYTHONDONTWRITEBYTECODE`,
  `PYTHONUNBUFFERED`, `PIP_NO_CACHE_DIR`, `HOME` and `TAXY_DEPLOYMENT`. `Config.Env` may hold the base image's
  variables (unchanged) and these five with their Section 1.4 values, and nothing else.

## 2. Hosted database: Supabase (D-195, D-196)

### 2.1 Plan, region and limits

- **Plan:** Free. Nano compute: memory "up to 0.5 GB", shared CPU, 60 direct connections, 200 pooler
  clients, default pool size 15 per role. Database 500 MB, egress 5 GB a month. Two free projects per
  owner. Sources in Section 15.
- **Size:** corpus tables under 10 MB. Audit rows about 15 KB a question (interface design Section 4.4); at
  the cap of Section 6 (about 30 questions a day), 90 days is about 40 MB.
- **Pausing:** a free project is paused when it has too little database activity over a week. A warning
  email comes about a week before, and dashboard visits, API calls and app requests count as activity. A
  paused project can be resumed from the dashboard for up to a year with its data. Handling: Section 13
  and runbook R11 (D-206).
- **Region (D-195):** **Frankfurt, `eu-central-1`**, the same metropolitan area as Render's Frankfurt
  region. Render has no African region; its five are Oregon, Ohio, Virginia, Frankfurt and Singapore.
  Render does not support changing a service's region after creation. *Unverified:* that a Supabase project's
  region cannot be changed either. The build treats it as fixed.

### 2.2 Connection mode

| Mode | Host and port | Network | Fits `open_query()` and the pipeline? | Verdict |
|---|---|---|---|---|
| Direct | `db.<ref>.supabase.co:5432` | IPv6, or IPv4 with the paid add-on | yes | **not reachable from Render without the paid IPv4 add-on**: Render has no outbound IPv6 |
| Shared pooler, session mode | `aws-<n>-eu-central-1.pooler.supabase.com:5432` | IPv4 on all plans | yes: one server connection per client connection for its lifetime, so prepared statements and the transaction-scoped advisory lock behave as locally | **chosen** |
| Shared pooler, transaction mode | same host, port 6543 | IPv4 | the advisory lock is transaction-scoped and would work, but the shared pooler does not support prepared statements in transaction mode, and psycopg 3 prepares a query after it has run 5 times on one connection (`prepare_threshold` default 5). `generate()` keeps one connection for a whole question and repeats the same distance queries | rejected |

Facts and dates are in Section 15. Render's own Supabase article (2026-10-23) recommends transaction mode
for typical web services. Its advice for apps that need prepared statements is session mode, which applies
here. Changing `prepare_threshold` would mean changing `check_pgvector.py`, which is out of scope.

**Concurrency:** at most 4 worker threads (D-157), each holding one connection for a question, plus short
audit and page connections. That is well under the pool size of 15 per role and the 200 pooler clients.

**TLS:** the connection string carries `sslmode=verify-full` with `sslrootcert` pointing to Supabase's CA
certificate. That file is public, downloaded by Kene from the dashboard and committed as
`deploy/supabase/prod-ca-2021.crt` (the dashboard's file name; this design first said `prod-ca.crt`, see the
Build 2 record). If `verify-full` cannot connect through the pooler, the build records the
error and uses `sslmode=require`. That rule is fixed here; the build does not choose. In the dashboard,
"Enforce SSL on incoming connections" is turned on.

**Pooler user names** take the form `<role>.<project-ref>`. *Unverified:* that the shared pooler accepts
custom login roles. Build step B2.3 proves it by connecting as `taxy_app_login`.

### 2.3 pgvector and retrieval parity

- **Enabling the extension:** in the dashboard (Database, then Extensions, then `vector`), into schema `extensions`, which is
  Supabase's documented default. `schema.sql` never schema-qualifies the type, and its `CREATE EXTENSION IF NOT
  EXISTS vector` is then a no-op.
- **Version:** *unverified.* Supabase's pgvector page states no version. One source lists 0.8.0 for
  PostgreSQL 17, and a 2026 changelog says explicit extension versions are deprecated and some extensions
  ship in several versions. The build records `SELECT extversion` and `SELECT version()`. **Parity is not
  claimed from version numbers. It is proved by the result** (Section 9.1).
- **Index parity:** neither side has an ANN index. The same `<=>` query runs over the same 400 stored
  vectors, so the only possible differences are floating-point rounding of the distance across builds or
  CPUs. Those could swap two chunks only at a near-tie. The free check of Section 9.1 compares every ranked
  id and reports the largest distance difference.
- **`search_path`:** the login roles get `ALTER ROLE ... SET search_path = public, extensions` and the
  group roles get `USAGE ON SCHEMA extensions`, so `vector` and `<=>` resolve for the app and the
  reviewer. This is set every time, never assumed.

### 2.4 Loading the corpus at $0 (no re-embedding)

Run from Kene's machine through the session pooler as the owner, with the published caches present (A4):

```powershell
.venv\Scripts\python eval\deploy\with_hosted.py owner eval\reproduce\reproduce.py --profile clean --only A4,A5,A6
```

A6 loads 400 chunks and 400 vectors with the **cache-only** client. A cache miss is a failure, never an
API call (D-172, D-173). It requires `corpus_loads.chunks_sha256` to equal the committed chunk file.
`with_hosted.py` (new, Section 2.7) loads `.env.hosted` into the environment of that one process and
prints no value.

### 2.5 Audit migrations, roles and RLS

1. `migrate.py` as the owner (`postgres` on Supabase is the owner role, interface design Section 4.6),
   then `migrate.py --check`. That gives `audit-schema/1`, the NOLOGIN group roles `taxy_app` and
   `taxy_review`, RLS on every audit table with the reviewer `SELECT` policy only, and no `PUBLIC` execute.
2. `deploy/supabase/0001_hosted_roles.sql` (new, owner-run, idempotent):
   - `CREATE ROLE taxy_app_login LOGIN IN ROLE taxy_app` and `CREATE ROLE taxy_review_login LOGIN IN ROLE
     taxy_review`, both without a password, `NOINHERIT` off, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`,
     each with `CONNECTION LIMIT` (app 20, review 3);
   - `GRANT SELECT ON public.corpus_chunks, public.chunk_embeddings, public.discrepancy_register,
     public.corpus_loads TO taxy_app`. Retrieval and the start-up probe read them; locally the app runs as
     the owner, so this grant is new for hosted;
   - `GRANT USAGE ON SCHEMA extensions TO taxy_app, taxy_review`; the `search_path` of Section 2.3.
3. `python\audit\grant_review.py --apply` as the owner (D-169: the reviewer's `SELECT` on the corpus tables).
4. **Passwords: Kene sets them.** `deploy/set_role_password.py` (new) runs in Kene's own terminal. It asks for the
   password with `getpass` (nothing echoed), computes the SCRAM-SHA-256 verifier locally and sends `ALTER ROLE
   ... PASSWORD 'SCRAM-SHA-256$...'`, so the clear password never reaches the server's statement log or the
   SQL editor history. It prints only "password set for <role>". No build session sees, sets or stores it.

### 2.6 Keeping the audit schema, and everything else, off the Data API

Supabase's defaults matter here. New tables in `public` get broad privileges (select, insert, update,
delete) for `anon`, `authenticated` and `service_role`. Tables created with SQL do not get RLS
automatically. Unless the Data API is turned off, those grants make the tables reachable through it.
Without these steps the corpus tables would be **writable** through the Data API, and the start-up check
compares the loaded chunk file hash (`corpus_loads.chunks_sha256`), not every row's text.

1. **Turn the Data API off** ("Enable Data API" off, in the dashboard's Data API settings). Then "none of the
   auto-generated REST endpoints respond, regardless of grants or RLS". Also turn off "automatically
   expose new tables".
2. `deploy/supabase/0002_revoke_api_roles.sql` (new, owner-run): `REVOKE ALL` on the four corpus tables
   and their sequences from `anon` and `authenticated`, and from `service_role` too (this project never
   uses the service-role key); `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON
   TABLES/SEQUENCES/FUNCTIONS FROM anon, authenticated, service_role`; RLS **on** for the four corpus tables
   with one policy `FOR SELECT TO taxy_app, taxy_review USING (true)`. The owner (the loader) is not filtered,
   because RLS is not forced, the same as the audit design.
3. `eval/deploy/check_hosted_db.py` (new, free, read-only, run as the owner). It must report **zero**
   privileges for `anon`, `authenticated`, `service_role` and `authenticator` on schema `audit`, on every
   audit table and function, and on the corpus tables. It must find RLS on for all 15 tables (11 audit, 4
   corpus). The app login must have exactly the ten audit functions plus `SELECT` on the four corpus tables
   and nothing else. The review login must have `SELECT` only. There must be no ANN index on
   `chunk_embeddings`. It also reports `extversion`, `version()` and `SHOW search_path` per login. Any
   finding fails the step.

### 2.7 Owner credentials (D-196)

The owner (`postgres`) connection string never goes to Render. It lives in **`.env.hosted`** at the
repository root. That file is git-ignored by the existing `.env.*` rule, never enters the image under the
allow-list, and holds `DATABASE_URL_OWNER`, `DATABASE_URL_APP` and `REVIEW_DATABASE_URL`, which Kene types.
`eval/deploy/with_hosted.py <owner|app|review> <script> [args]` sets `DATABASE_URL` (or
`REVIEW_DATABASE_URL`) for the child process from the named line and prints no value. After the gate,
Kene empties the owner line (runbook R2). The database password itself is set by Kene at project creation in
the Supabase page and stored only in his password manager.

## 3. Hosting: Render (D-197)

### 3.1 Service

| Item | Choice |
|---|---|
| Type | Web Service, runtime Docker, from the GitHub repository (Render's GitHub app with access to this repository only), branch `main`, Dockerfile path `./Dockerfile` |
| Region | Frankfurt (Section 2.1) |
| Plan | **Free** (512 MB, 0.1 CPU; spins down after 15 minutes without inbound traffic; about one minute to spin up; 750 free instance hours a month per workspace; no persistent disk; may be restarted at any time; Render says not for production). Starter (`0.5c-512mb`, 0.5 CPU, 512 MB, no spin-down) is the upgrade if the trigger in Section 3.3 fires. *Unverified:* the Starter price; search results put it at $7 a month, and Render's compute-plan page links to the pricing page for prices |
| Health check path | `/_stcore/health` (Streamlit's own endpoint; it answers `ok` once the server is up; measured). Render marks an instance unhealthy after 15 s of failed checks and restarts it after 60 s; a deploy whose instance is not healthy within 15 minutes is cancelled and the old one keeps serving |
| Deploy trigger | **Auto-Deploy Off.** Each deploy is "Manual Deploy, then Deploy a specific commit" with the commit the overseer approved. Render notes that deploying a specific commit disables auto-deploys anyway, and a dashboard rollback also disables them. Rejected: "On Commit" (every docs push would redeploy and restart the service with unreviewed code) and "After CI Checks Pass" (the repository has no CI) |
| Zero downtime | yes without a persistent disk: the old instance serves until the new one is healthy |

### 3.2 Recording and checking the deployed commit

`RENDER_GIT_COMMIT`, which Render sets at build and run time for all runtimes, Docker included, is written to
`app_starts.git_commit` by the existing start-up check. After each deploy the build or the runbook runs
`eval/deploy/check_live_start.py` (new, free, as the reviewer). The latest `app_starts` row with
`deployment = 'hosted'` must have `outcome = 'ok'`, empty `problems`, `config_sha256` equal to the pinned
value and `git_commit` equal to the approved commit. A start row is written only when the first browser
session runs the script, so the check opens the live URL once in a browser before it reads.

### 3.3 Memory, CPU and cold start (measured locally, $0; Section 16)

- **Memory:** 51 MiB before the first page and **65 MiB** after it (the start-up check done, every
  pipeline module imported), against a 512 MB limit. A question's peak was not measured, because that needs a
  paid call. It is measured in the gate from Render's metrics (Section 9.2). If it goes over 400 MB, the gate
  stops.
- **Cold start at 0.1 CPU** (`docker run --cpus 0.1 --memory 512m`): the health endpoint answered **47 s**
  after the container started, and the first page, with the start-up check, finished **26 s** after the first
  browser request: about **73 s** in all, before Render's own wake-up. At full CPU the imports took 3.2 s and
  the check 1.8 s. A rerun on 2026-11-14 gave 27 to 64 s to health and 31 s to the question box; what the
  visitor sees meanwhile is Section 3.4.
- **Upgrade trigger (D-197):** move to Starter if the measured wake-up on the live URL is over 120 s in the
  gate, or if Kene wants reviewers never to wait. That costs money and is Kene's decision.

### 3.4 What a visitor sees during the cold start (D-207)

**Render's part (checked 2026-11-14, render.com/docs/free):** a Free web service "spins back up whenever it
next receives an HTTP request or new WebSocket connection", which "takes about one minute", and "Render
displays a loading page to connecting browsers while a service is spinning up." *Unverified:* what that page
shows, whether it moves on to the app by itself, and whether it hands over when the port opens or when the
health check passes. Render's docs say none of this, and the community forum is not an official source. Build
step B3 screenshots the live URL from a quiet start (more than 15 minutes with no traffic) every 10 s until
the question box appears, and records each stage and its time.

**The app's part (measured 2026-11-14, $0, 0.1 CPU and 512 MB, Section 16).** After Render's page, the browser
first downloads Streamlit's own front end (30 files) from the 0.1 CPU instance. Only then does `main.py` run,
and its first lines import the pipeline (about 30 s at this CPU, Section 16) before anything is drawn. A
scratch copy of `main.py` that drew a notice right after `import streamlit as st` showed it:

| Run | Notice seen after the page request | Notice cleared, page drawn | Question box |
|---|---|---|---|
| Cold container, browser with no cached files | **7.7 s** | 25.3 s (script done) | 31.0 s |
| Warm browser cache, cold container | 2.1 s | 20.7 s | about 21 s |
| First page ever served by the container | 13.5 s | not measured (the probe ran as a second entry file, which broke the page links) | not measured |

Without the notice, the visitor sees an empty Streamlit page for the whole 20 to 31 s. With it, the empty
page lasts 2 to 8 s (up to about 14 s on the very first page). `main.py` cannot shorten that part: it is the
front end's download, before any script runs. Calling `st.set_page_config` after the notice still set the
title and the layout (Streamlit 1.66 allows it), so the page set-up order can stay as it is.

**Change (build step B1.2, D-207):** in `app/main.py`, directly after `import streamlit as st` and before
`from app import cards`, the script checks whether `app.services` is already in `sys.modules`. Only when it is
not (the first run in a new process), it draws `st.empty().info(ui.STARTUP_NOTICE)`. It clears that slot
right after `get_services()` returns. On a failed start-up check, the unavailable text then replaces it.
Because of the check, later reruns (every click) never flash the notice. The text lives in `app/copy.py` as
`STARTUP_NOTICE`, which must be importable before the heavy imports. `app/copy.py` imports nothing heavy
today, and an AppTest checks that the notice is drawn on a cold run and gone once the page is ready. The
wording, plain and with the measured wait:

> Taxy is starting up after a quiet spell. This usually takes under a minute. Please keep this page open:
> the question box appears when it is ready.

The rule for the figure is fixed here: if the live in-app part measured in B3 (notice seen to question box)
is over 50 s, "under a minute" becomes "about two minutes". Otherwise the text stays.

## 4. Secrets and environment (D-198)

| Name | Where | Set by | Secret | Checked at start |
|---|---|---|---|---|
| `TAXY_DEPLOYMENT=hosted` | Dockerfile `ENV` | build | no | deployment |
| `DATABASE_URL` (`taxy_app_login` through the session pooler, TLS) | Render environment | Kene | yes | presence (`hosted_env`) |
| `LLM_API_KEY` (a key only for the hosted service) | Render environment | Kene | yes | presence |
| `EMBEDDING_API_KEY` (a key only for the hosted service) | Render environment | Kene | yes | presence |
| `TAXY_USER_KEY_SECRET` (HMAC key, 48 random bytes, URL-safe) | Render environment | Kene | yes | presence |
| `TAXY_COST_CAP_DAY_USD` (optional; can only lower the cap) | Render environment | Kene | no | read by the gate |
| `secrets.toml` with `[auth]` `redirect_uri`, `cookie_secret` and `[auth.google]` `client_id`, `client_secret`, `server_metadata_url` | Render secret file `secrets.toml`, mounted at `/etc/secrets/secrets.toml` | Kene | yes (`cookie_secret`, `client_secret`) | not by the start-up check; sign-in fails visibly without it |
| `PORT`, `RENDER_GIT_COMMIT` | Render | Render | no | commit recorded |
| `DATABASE_URL_OWNER`, `DATABASE_URL_APP`, `REVIEW_DATABASE_URL` | `.env.hosted` on Kene's machine only | Kene | yes | never on Render |

Rules: Kene types every value into the Render or Supabase page, or into `.env.hosted`. A build session never
asks for, reads out, prints or logs a value. The random secrets are made by Kene in his own terminal with
`python -c "import secrets; print(secrets.token_urlsafe(48))"` and pasted straight into Render. Nothing goes
in as a Docker build argument. **Dedicated keys:** Kene creates separate API keys for the hosted service at
both providers, so revoking one (kill switch K3) leaves local work untouched and hosted usage can be told
apart. The start-up check stays presence-only (`hosted_checks`). No new check reads a value.

**The form of Render's `DATABASE_URL` (D-211).** The same form as `DATABASE_URL_APP` in `.env.hosted` (the user name
`taxy_app_login.<project-ref>`, the session pooler on port 5432), with the certificate's absolute path inside the
image, because the app's working directory is not the repository root there:

```
postgresql://taxy_app_login.<project-ref>:<app password>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=verify-full&sslrootcert=/srv/taxy/deploy/supabase/prod-ca-2021.crt
```

The laptop lines keep the relative path `deploy/supabase/prod-ca-2021.crt` (commands run from the repository
root). Kene types the value into Render; the build never sees it.

## 5. Sign-in (D-199)

### 5.1 Steps for Kene (Google Cloud console)

1. Go to console.cloud.google.com, signed in with the account that will own the client. Create a project
   named `taxy-demo`.
2. Open "Google Auth Platform", then "Branding". App name: `Nigerian SME Tax Reference`. User support email:
   the project mailbox (D-181). Developer contact: the same mailbox. Application home page: the live Render
   URL. Privacy policy link: the README "Privacy" section on GitHub. Save. No logo (a logo triggers brand
   verification).
3. "Audience": user type **External**.
4. "Data access": add only `openid`, `.../auth/userinfo.email` and `.../auth/userinfo.profile`, the
   non-sensitive scopes. Save.
5. "Clients", then "Create client". Type **Web application**, name `taxy-render`. Authorized redirect URIs:
   `https://<service>.onrender.com/oauth2callback` (the exact Render URL) and
   `http://localhost:8501/oauth2callback` (for the free local sign-in test). Create.
6. Copy the client ID and the client secret into your password manager. Do not paste them in chat.
7. "Audience", then **"Publish app"**, then confirm, so the status reads **In production**.
8. In Render, open the service's "Environment" page, then "Secret Files", then add `secrets.toml` and type:

   ```toml
   [auth]
   redirect_uri = "https://<service>.onrender.com/oauth2callback"
   cookie_secret = "<the random string you made>"

   [auth.google]
   client_id = "<client id>"
   client_secret = "<client secret>"
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```

   For the local test, put the same file (with the localhost `redirect_uri`) in `.streamlit/secrets.toml`,
   which is git-ignored and outside the allow-list.

### 5.2 Pins

- **`Authlib==1.9.1`**, published 2026-10-14 (30 days before this design). The 1.9.0 notes list JOSE
  key, scope and audience enforcement and nonce validation for the OIDC client. 1.9.2 to 1.9.5 (2026-11-01 to
  2026-11-05) are under 14 days old today and their notes are not itemised. Authlib 1.9.1 needs
  `cryptography>=45.0.1` (installed: 50.0.2) and `joserfc>=1.6.8`. Before pinning, the build checks GitHub's
  security advisories for Authlib. If one affects 1.9.1 and its fix is under 14 days old, the build stops
  and asks Kene. It does not pick a version.
- **`httpx==0.28.1`** (published 2024-12-06; the current release). Streamlit's starlette OIDC client
  imports it.

### 5.3 Publishing status and user limits

- **Testing:** limited to 100 test users listed on the consent screen. A test user's authorisation expires 7
  days after consent. Google's page says apps requesting only name, email and profile scopes skip the
  test-user list and the 7-day expiry.
- **In production:** "available to any user with a Google Account". The 100-user lifetime cap applies to
  unverified apps that request unapproved **sensitive or restricted** scopes. This app requests neither.
- *Unverified:* whether Google shows the app name or only the domain on an unverified consent screen, and
  whether an `onrender.com` redirect host needs an authorised domain. Build step K5 records what the consent
  screen shows for a Google account that is not the owner. If publishing is refused, the fallback is
  Testing status with a named test-user list, and the decision goes back to Kene.

### 5.4 What the app stores, and the cookie

As D-163: the HMAC user key, the provider, first and last seen and the tier. Never the email, the name or
the subject in clear. The app reads `iss` and `sub` from `st.user`. *Unverified:* that `st.user` exposes
both for Google; a free local sign-in test with the localhost redirect proves it before deploy. Streamlit
keeps an identity cookie in the browser for up to 30 days, or until sign-out. The privacy text does not
mention it yet (Section 8.4, D-204).

**Expiry (D-209, approved 2026-11-14, option (a)).** Streamlit does not check the identity token's `exp` by itself. The app does
not check it either: the 30-day identity cookie is the only expiry of a sign-in, and a user can log out at any time.

### 5.5 Wiring the two sign-in hooks

The page already calls `services.extra["sign_in"]` (the "Sign in with Google" button in the profile block,
`app/chrome.py`) and `services.extra["sign_out"]` (the "Log out" button there and the sign-out on the My
questions page). Each button is disabled while its hook is unset. Phase 11 sets both in `build_services()`
on a hosted deployment only: `sign_in` is `lambda: st.login("google")` and `sign_out` is `st.logout`. Local
and test deployments leave them unset, so the buttons stay disabled there, and the `SIDEBAR_SIGN_IN` caption
(shown only while `sign_in` is unset) keeps its text. Free tests (AppTest with a stubbed `st.login` and
`st.logout`) check that both are set only when `TAXY_DEPLOYMENT=hosted`, that each button calls its hook, and
that a signed-in session reaches `resolve_identity()` with the `st.user` claims. The local sign-in test in B3
runs the app as hosted against the local container, with placeholder values (not real keys) for the two API
key variables, and asks no question.

## 6. Limits, cost and the kill switch (D-200, D-201)

### 6.1 Final numbers (`app/limits.yml`)

| Limit | Value | Reasoning from measured cost |
|---|---|---|
| Global daily cost cap, all hosted tiers, checked first | **$0.50** per West Africa Time day | at most $15.50 in a 31-day month against the project's $30 monthly API spend limit, leaving about $14 for the gate, Phase 12 and development. At the Phase 9 answered mean of $0.0181 that is about 27 answered questions a day; at the Phase 8 overall mean of $0.0089, about 55 |
| Reserve per question in flight | **$0.05** | above the dearest Phase 7 question ($0.036) and every Phase 9 answer (highest $0.0241). The day's spend can pass the cap only by a question that costs more than its reserve, by that excess |
| Anonymous, per browser session | **3** | as suggested in Phase 9. A reload resets it, which is accepted (interface design Section 7.1) |
| Anonymous, shared daily pool | **20** (lowered from the suggested 50) | 20 answered at $0.0181 is $0.36, which leaves room under the cap for signed-in users. With 50 the pool could never be used up before the cap |
| Signed in, per user per day | **10** (lowered from 20) | 10 answered is about $0.18, so three heavy users alone cannot use the whole cap |
| Reviewer (set by Kene in `users.tier`), per day | **50** | for Phase 12 and reviewers; the cap still applies |

**Credits:** the cap bounds spend. It does not keep a prepaid credit balance from running out. If the
balance reaches zero, every model call fails, and users see the pipeline's error text, logged as `error`.
Runbook R7 covers watching the balance.

### 6.2 What users see

The existing texts in `app/copy.py` (interface design Section 8) are kept:

- cap: "The service has reached its usage limit for today. Please try again tomorrow."
- session: "You have used the 3 questions available without signing in. Sign in to ask more."
- pool: `LIMIT_ANONYMOUS_POOL`
- daily: "You have reached today's limit of 10 questions. It resets at midnight West Africa Time."

The sidebar shows the questions left. The "Sign in with Google" and "Log out" buttons and their texts already
exist (`SIGN_IN_BUTTON`, `LOG_OUT_BUTTON`); on hosted they become active (Section 5.5). The only new text is
`STARTUP_NOTICE` (Section 3.4), which the overseer reviews in `app/copy.py`.

### 6.3 Kill switch (three levels)

| Level | How | Takes effect | What users see |
|---|---|---|---|
| K1: stop everything | Render dashboard: "Suspend service" | at once | Render's own page; nothing of the app |
| K2: stop questions, keep reading | Render environment: `TAXY_COST_CAP_DAY_USD=0`, then save and deploy | after the restart (about the cold start) | the page and the section reader work; every question gets the cap text; no paid call |
| K3: stop paid calls in flight | revoke the hosted key at the model provider (and the embedding provider) | at once | questions end with the pipeline's error text; the start-up check still passes (the key is present but invalid) |

The override is read by `TierGate`, which uses the **lower** of the file value and the environment value
(Section 0.2). It can only reduce exposure; raising the cap needs a reviewed commit. *Unverified:* whether
saving an environment change restarts the service without a rebuild. Build step B3 records it.

## 7. Retention and erasure (D-202)

- **Where the 90-day purge runs: `pg_cron` in the Supabase database, as the owner.** It costs $0 and keeps
  the owner credential off Render. Upstream pg_cron runs a job as the user who scheduled it, and only the
  owner may execute `purge_expired` (interface design Section 4.6). Schedule, run once by the owner:
  `select cron.schedule('taxy-purge-90d', '0 1 * * *', $$select audit.purge_expired(90)$$);` (01:00 UTC is
  02:00 West Africa Time). Supabase's quickstart says `cron.job_run_details` is never cleaned up
  automatically, so a second job deletes its rows older than 30 days:
  `select cron.schedule('taxy-cron-history', '30 1 * * *', $$delete from cron.job_run_details where
  end_time < now() - interval '30 days'$$);`. *Unverified:* pg_cron availability on the Free plan (third-party
  sources say yes) and the job role on Supabase. The build checks `pg_available_extensions`, enables
  `pg_cron` in the dashboard, schedules, and reads the first `job_run_details` row the next day. **Fallback,
  fixed here:** if pg_cron is not available, Kene runs `purge_expired(90)` by hand on the first of every month
  (runbook R8); rows then live at most about 120 days, and the README says "90 days, purged monthly".
- Rejected: a Render cron job (paid, and it would put the owner credential on Render); purging by hand only
  (easy to forget).
- **`erase_query` (anonymous request quoting a reference):** runbook R9. Kene finds the row with
  `show_query.py Q-xxxxxxxx` (reviewer), checks that the reference matches exactly one row, runs `select
  audit.erase_query('<query_id>');` as the owner, confirms with `show_query.py` that it is gone, replies from
  the project mailbox, and logs the date and reference (no question text) in a private request log. The log is
  never committed.
- **`erase_user`:** self-service through "Delete my history" (D-164). The runbook adds no owner step. If a
  signed-in user writes in instead, Kene asks them to use the button, because Kene cannot link an email to an
  HMAC key, and does not try to.

## 8. Data-protection follow-ups (D-185; D-203, D-204)

Facts from the official texts, checked 2026-11-13. This is not legal advice.

### 8.1 Data-controller registration (P8)

Source: Nigeria Data Protection Commission, *Nigeria Data Protection Act 2023 General Application and
Implementation Directive 2025* (GAID, NDPC/NDP ACT-GAID/01/2025, dated 20 March 2025), PDF from
`ndpc.gov.ng` (`wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf`, sha256
`e4359e90...24cc5a` as downloaded 2026-11-13), Article 9 and Schedule 7.

- Schedule 7, paragraph 1(1) designates a controller of major importance if it "keeps or has access to a
  filing system ... for the processing of personal data" and, among other things, processes the personal data
  of more than 200 data subjects in six months, or works in listed sectors (aviation, communication,
  education, electric power, export and import, financial, health, hospitality, insurance, oil and gas,
  tourism, e-commerce, public service).
- Paragraph 3: Ordinary-High Level covers 200 to 1,000 data subjects within six months (fee ₦10,000),
  Extra-High 1,000 to 5,000 (₦100,000) and Ultra-High over 5,000 (₦250,000). Article 9(3): an Ordinary-High
  registrant renews every year and does not file the annual compliance audit return when it renews.
- Paragraph 4 lists who is not of major importance (small traders and artisans, communities of friends on
  social media). Paragraph 6 lists exempt bodies. Neither obviously describes this demo.
- **What this means for the demo, as facts only:** signed-in users are pseudonymous data subjects (P11).
  Whether an anonymous question with a random session key counts as one data subject is for Kene to decide.
  At the pool of 20 a day, anonymous sessions alone could pass 200 in about two weeks if each session were
  counted.

**Approved (D-203 (a)):** before the public link opens, Kene decides whether to register at Ordinary-High
Level (₦10,000 a year) or to take advice. Meanwhile the runbook (R12) runs a free monthly count from the
audit log: distinct signed-in user keys plus distinct anonymous session keys over the last 182 days. The
count is an upper bound, since one person can have many sessions. It goes to Kene with the threshold 200
named.

**Outcome, decided by Kene 2026-11-14:** Kene will not register with the Nigeria Data Protection Commission and accepts the risk. The runbook's monthly count (R12) stays, for information only; it does not trigger registration. This is Kene's decision on the facts above, not legal advice, and it does not
change the breach steps (Section 8.2) or the data-request steps (Section 8.3).

### 8.2 Breach notification (P7)

GAID Article 33(1) quotes NDPA section 40(2): within **72 hours** of becoming aware of a breach "likely to
result in a risk to the rights and freedoms of individuals", notify the Commission. Article 33(3): notify
affected data subjects "immediately" when the risk is high. Article 33(5) lists the content, items (a) to
(h): the circumstances; the date or period; the personal information involved; the risk of harm; the number
of individuals at real risk; the steps taken to reduce harm; the steps taken to notify individuals; and a
contact. Runbook R10 turns this into steps. The app stores no contact detail for any user, so individuals
cannot be written to directly. The notice goes on the live page and in the README.

### 8.3 Data requests (P5)

Through the project mailbox (D-181), by reference for anonymous questions. Access means the requester gets
the `show_query.py` Markdown for their reference, with other people's data left out (there is none in a row).
Erasure is runbook R9. Correction and objection are answered by erasure, since an answer cannot be
"corrected". Response target: **within 30 days**, as an operational promise. *Unverified:* the GAID text
searched (Articles 36 to 40) gave no response period. The build checks NDPA sections 34 to 38 in the
gazetted Act checked for D-186 and, if a period is stated, the runbook uses it.

### 8.4 Privacy text additions after go-live (D-204)

These two facts become true at go-live and the D-180 and D-186 text does not state them yet. The approved sentences (D-204), for the
README Privacy section and the `PRIVACY_DETAILS` popover alike (one AppTest and one `test_docs.py` check
each):

1. "The demo is hosted in Frankfurt, Germany (Render for the app, Supabase for the database)." This closes
   the P6 region item.
2. "The page uses a functional security cookie and, if you sign in, a sign-in cookie that lasts up to 30
   days or until you sign out."

## 9. Exit gate made testable (D-205)

**Plan gate:** the deployed URL answers a known Phase 4/8 test question correctly end-to-end (retrieval,
citation and verification working against the hosted database), and a rate limit is confirmed to cap LLM
API cost exposure.

### 9.1 (a) Hosted retrieval equals local, for every dev and test question ($0)

`eval/deploy/compare_retrieval.py` (new). It runs `run_eval.evaluate()` twice per configuration, once on the
local container and once on Supabase as `taxy_app_login`, with query vectors from the published query cache
through the cache-only client (a miss fails). Configurations: `config_final.json` (top 5, the G1 record) and
`config_v3.json` (top 8, the shipped retrieval). Questions: the dev and test splits of `questions_v1.jsonl`.
It is a verification, not a run: it writes no result file in the repository (D-173 precedent, D-20 stands).
**Pass:** for every question and both configurations, the ranked chunk ids are equal hosted and local,
and the test split on Supabase reproduces hit@5 30 of 30, hit@1 22 of 30 and MRR 0.8556. It also reports the
largest absolute distance difference; above 1e-6 is a warning in the record, not a failure, if the ids are
equal. A9 itself is run once against Supabase as a second, independent route.

### 9.2 (b) Known test questions end-to-end on the live URL (paid)

- **Questions:** 5 drawn with seed `20261114` from the 30 subset-T cases of Phase 8 (answered in both runs,
  identical passages in both), plus the two Phase 9 refusals, the `no_such_section` question (Q-e88f33ea)
  and the `foreign_law` question (Q-67c8ea25), word for word. Using spent questions here is a check of the
  deployment, not a measurement. It is the same use the plan sets for Phase 12.
- **How:** asked in the browser on the live URL while signed in as Kene (`signed_in` tier, 7 questions, under
  the limit of 10), one at a time, each reference noted.
- **`eval/deploy/compare_live.py`** (new, free, reviewer role) compares each live row with its Phase 8 case
  (or Phase 9 record) in three classes:

| Class | Fields | Rule |
|---|---|---|
| Must match exactly | `config_sha256`, `retrieval_config_sha256`, `guardrails_policy_sha256`; generator, verifier and tagger models; every prompt, schema and tool-definition label; `corpus` (load hashes); the pre-check result; for the two refusals, the status, reason, stage and `refusal_text` | zero differences |
| Must match, one stated exception | passages P1 to P8: chunk ids in label order | equal to the Phase 8 case; a difference is accepted only if Section 9.1 passed for that question (hosted and local agree with the cached vector), so the difference can only come from the fresh query embedding; every such difference is listed |
| Must hold, not compared | status `answered`; every shown claim `supported` with at least one kept citation; every cited chunk in the hosted corpus with `chunk_text_sha256` equal to the local text sha; a delivery `shown` whose `shown_sha256` equals `rendered_sha256`; the case's gold unit among the cited chunks; the latest `app_starts` row `ok` with the deployed commit | all true |

Answer wording is not compared: it comes from non-deterministic paid calls (D-171). The quality measurement
is G3's. Memory during the 7 questions is read from Render's metrics; over 400 MB fails the gate (Section
3.3).

### 9.3 (c) The limits and the cap stop questions on the live URL (a few hundredths of a cent)

Cheap questions are pre-check refusals (`no_such_section`, about $0.0002 each, the note tagger only).

1. **Session limit:** a fresh anonymous browser session asks 3 cheap questions, then a 4th. Expected: the
   session text, an `admission_counts` row for `session_limit`, and no `queries` row for the 4th.
2. **Signed-in daily limit:** after (b), Kene asks 3 more cheap questions (10 in all), then an 11th.
   Expected: the daily-limit text and no row.
3. **Kill switch K2:** set `TAXY_COST_CAP_DAY_USD=0`, then restart. One question gets the cap text: $0 spent, a
   `cost_cap` count, no row.
4. **The cap counts real spend:** read today's hosted spend S (reviewer, `spend_since`). Set the override to
   `S + 0.0501` and restart. One cheap question is admitted (S + 0.05 reserve fits). Once it finishes at
   about $0.0002, the next is refused with the cap text (S + 0.0002 + 0.05 is over S + 0.0501). Then remove
   the override and restart.
5. The anonymous pool is not exhausted on the live URL: that would need 20 questions over 7 sessions. It is
   covered by the Phase 9 database tests (`test_audit_limits.py`: counts, the cap with reserves, two
   concurrent last-slot calls).

### 9.4 Spend, cap and stop rule

| Part | Expected | Worst |
|---|---|---|
| (a) | $0 | $0 |
| (b) 5 answered (subset T mean $0.0128; Phase 9 answered mean $0.0181) | about $0.09 | $0.18 (at the dearest $0.036) |
| (b) 2 refusals | $0.0004 | $0.0004 |
| (c) about 8 cheap questions | $0.0016 | $0.002 |
| Query embeddings (not in `total_cost_usd`) | under $0.0001 | under $0.0001 |
| **Gate total** | **about $0.10** | **about $0.19** |

**Cap $0.40 for all of Phase 11** (the build steps are $0). **Stop rule:** before each answered question the
operator reads today's hosted spend. If spend plus $0.05 would pass $0.40, stop. The service's own $0.50
daily cap is a backstop. A failure in (b) or (c) is a bug fix plus one rerun of the failing item only, proposed
with its own cap before any further spend (D-97 precedent).

**Pass rule:** 9.1 passes; all 7 questions of (b) meet their three classes; each step of (c) behaves as
stated; the image check (Section 1.7) and the database check (Section 2.6) are clean for the deployed commit;
the default suite passes; the overseer reads the record.

## 10. Runbook outline (`docs/phase11_deployment/runbook.md`)

Built at step D1. Each entry: when, who, numbered steps, how to confirm, what to record. Commands are
PowerShell, one per block.

| # | Entry | Outline |
|---|---|---|
| R1 | Deploy | overseer-approved commit; `check_image.py` clean; Render "Deploy a specific commit"; open the URL once; `check_live_start.py` (row `ok`, commit equal); note the commit and date |
| R2 | Roll back | Render "Rollbacks" to the last good deploy (it reuses that build and disables auto-deploy); `check_live_start.py`; record why. A database change is never rolled back this way: migrations are forward-only (D-151) |
| R3 | Rotate the database app password | `set_role_password.py taxy_app_login` (Kene); update `DATABASE_URL` in Render; restart; check. The reviewer password: the same with `taxy_review_login` and `.env.hosted` |
| R4 | Rotate the API keys | create the new key at the provider; update Render; restart; revoke the old one; one cheap question to confirm |
| R5 | Rotate the OAuth client secret or `cookie_secret` | new secret in the Google console, or a new random string; update the secret file; restart. A new `cookie_secret` signs everyone out |
| R6 | `TAXY_USER_KEY_SECRET` (HMAC caveat) | **rotate only if leaked.** A new secret gives every user a new key, so existing histories are detached: they are no longer shown in "My questions" and are purged at 90 days. Record the date; note it on the page for 90 days |
| R7 | Raise or lower limits; watch credits | lower the cap at once with `TAXY_COST_CAP_DAY_USD`; any other change through a reviewed commit to `app/limits.yml` and R1; check the provider credit balance weekly; at under $2, apply K2 until topped up |
| R8 | Retention | confirm the purge job ran (`cron.job_run_details`); fallback: monthly `purge_expired(90)` by hand |
| R9 | Erasure request | Section 7 steps |
| R10 | Breach | (1) contain: K1, rotate every secret (R3 to R6); (2) assess what was exposed (questions, HMAC keys, which period) and whether it is likely to put rights at risk; (3) within 72 hours of becoming aware, notify the Commission through the channel its website gives at that time, with Article 33(5) items (a) to (h); (4) put a notice on the live page and in the README; (5) record a timeline in the private log |
| R11 | Supabase pause warning | on the warning email, open the project dashboard (that counts as activity); if paused, "Resume project", then R1's checks |
| R12 | Monthly data-protection count | the Section 8.1 count; report it to Kene with the 200 threshold, for information only (D-203 outcome: no registration) |
| R13 | Cost spike | the cap stops it within the day; read spend by tier and reason (reviewer); if abuse, K2, then lower the pool or the session limit by commit |
| R14 | Kill switch | K1 to K3 (Section 6.3) and how to undo each |

## 11. README and docs after go-live

- **README Status** (replacing the second sentence, as packaging design Section 7.1 planned): "**Phases 1 to
  11 are done.** The live demo is at <URL>. It runs on a free plan, so the first visit after about 15 quiet
  minutes can take up to about two minutes to wake, and the page says so while it starts. Phase 12 (final QA) is next. Gate G2 was **not met**;
  see Results at a glance." `tests/test_docs.py`
  `test_the_readme_follows_the_design_outline_and_status_rules` changes its pins to "Phases 1 to 11 are
  done", "Phase 12 (final QA) is next", the URL starting `https://` and ending `.onrender.com`, and keeps
  "G2 was **not met**".
- **README How to run:** one short "Hosted demo" paragraph (the limits of Section 6.1 and the sign-in note)
  and a link to the runbook. Local steps are unchanged.
- **Privacy:** the two D-204 sentences (Section 8.4) in the README and `PRIVACY_DETAILS`, with their tests.
- **Limitations** (`docs/reference/limitations.md`, item 5): "switched on only in Phase 11" becomes "on in
  the hosted demo (Section 6.1 numbers)"; add "free hosting: cold start; the database pauses after a quiet
  week".
- **`docs/README.md`:** rows for `phase11_deployment/deployment_design.md` and `runbook.md`. The folder row
  "11. Deployment" becomes "(done, D-xxx)" at the gate. `test_docs_index` gains the two names.

## 12. Build plan

Every password, key and secret is typed by Kene in the provider's page or in `.env.hosted`. A build session
never creates an account, signs in, or sees a value.

| Step | What | Who | Session and effort | Spend |
|---|---|---|---|---|
| 0 | Kene decides D-192 to D-206; decision rows written in B1 | Kene, overseer | overseer | $0 |
| B1.1 | `Dockerfile`, `.dockerignore` (Section 1.3), `requirements.lock.txt` and the two pins, the lock-equality test, `eval/deploy/check_image.py`; local build; image check clean; the start-up check inside the image against the local container (deployment `test`) | build | NEW "Phase 11 Build 1", high | $0 |
| B1.2 | the D-192 interface changes and their free tests: the hosted gate and cap override; identity from `st.user`; **`services.extra["sign_in"]` wired to `st.login("google")` and `services.extra["sign_out"]` to `st.logout()`** on hosted only (Section 5.5); the early start-up notice in `main.py` and `STARTUP_NOTICE` in `app/copy.py` (Section 3.4); the final `app/limits.yml`; `deploy/` SQL and `set_role_password.py`, `with_hosted.py`, `check_hosted_db.py`, `compare_retrieval.py`, `compare_live.py`, `check_live_start.py`, each with tests on fakes or the local container; full default suite | build | same session, second prompt | $0 |
| — | **Stop: overseer review of B1; commit on Kene's go; push on Kene's yes** (Render builds from GitHub) | overseer | | |
| K1 | Supabase: (1) sign in at supabase.com; (2) "New project", name `taxy`, region Frankfurt (`eu-central-1`), type a strong database password and keep it in your password manager; (3) on the create page, untick "Enable Data API" if offered; (4) Database, then Extensions: enable `vector` (schema `extensions`) and `pg_cron`; (5) Database settings: "Enforce SSL" on; download the CA certificate into `deploy/supabase/`; (6) Connect, then "Session pooler": copy the string and type the owner line into `.env.hosted` with your password | Kene, web page | — | $0 |
| B2 | Owner steps through `with_hosted.py`: A4 to A6 load (Section 2.4); `migrate.py` and `--check`; `0001_hosted_roles.sql`; `grant_review.py --apply`; `0002_revoke_api_roles.sql`; Data API settings confirmed off (Kene, dashboard); Kene runs `set_role_password.py` twice and types `DATABASE_URL_APP` and `REVIEW_DATABASE_URL`; `check_hosted_db.py` clean; the start-up check as `taxy_app_login` from the laptop; **gate (a)** (Section 9.1); the pg_cron jobs | build, Kene for passwords | NEW "Phase 11 Build 2", high | $0 |
| — | **Stop: overseer reviews the B2 record (privileges, parity)** | overseer | | |
| K2 | Kene creates the two hosted API keys at the providers and stores them in the password manager | Kene, web pages | — | $0 |
| K3 | Render: (1) sign in at render.com; (2) New, then Web Service, then connect GitHub and allow this repository only; (3) name, region Frankfurt, branch `main`, runtime Docker, plan Free; (4) Advanced: health check path `/_stcore/health`, Auto-Deploy **Off**; (5) Environment: add the four secret variables of Section 4, typed by you (`DATABASE_URL` ends with `?sslmode=verify-full&sslrootcert=/srv/taxy/deploy/supabase/prod-ca-2021.crt`, D-211); (6) create, then copy the URL into chat (not a secret) | Kene, web page | — | $0 |
| K4 | Google OAuth (Section 5.1, steps 1 to 8), with the URL from K3 | Kene, web page | — | $0 |
| B3a | The CA certificate in the image (D-211): the allow-list line, its tests, the image rebuilt and `check_image.py --worktree` clean; the form of Render's `DATABASE_URL` (Section 4) | build | Build 2, before K3 | $0 |
| B3 | Free sign-in test locally (localhost redirect; the "Sign in with Google" and "Log out" buttons call `st.login` and `st.logout`; checks `iss` and `sub`, the key, the sidebar); Render "Deploy a specific commit" (Kene clicks); `check_live_start.py`; consent and the page read in the browser; whether an environment save restarts the service; cold start measured on the live URL from a quiet start, with a screenshot every 10 s of Render's loading page, the start-up notice and the ready page (Section 3.4), and the notice wording rule applied; no question asked | build, Kene clicks | Build 2, second prompt | $0 |
| — | **Stop: overseer review; Kene's go for the paid gate** | overseer, Kene | | |
| G | Gate (b) and (c), spend under the $0.40 cap (Section 9) | build drives the browser; Kene signs in | NEW "Phase 11 gate", high | about $0.10 (worst $0.19) |
| D1 | `runbook.md`; this design's build record; README Status and Privacy; limitations; `docs/README.md`; `test_docs.py` pins; decision rows for the gate | build | gate session, second prompt | $0 |
| — | **Stop: overseer verifies the gate record; commit on Kene's go; push on Kene's yes; Kene empties the owner line in `.env.hosted`** | overseer, Kene | | |

**Phase spend:** about $0.10, **cap $0.40**, all in step G. Render Free, Supabase Free and Google cost $0.
Starter hosting, if Kene chooses it (D-197), would be a monthly charge outside the API budget.

## 13. What can go wrong

| Risk | Check |
|---|---|
| A file the start-up check needs is missing from the image | the start-up check inside the image in B1.1 (it caught `config_v3.json` in the scratch build); `check_live_start.py` after each deploy |
| A secret or git-ignored file in the image (including Render's secret-file copy into the build context) | the allow-list; `check_image.py` |
| Hosted retrieval differs (pgvector build, CPU rounding) | gate (a): every dev and test question, both configurations, ranked ids equal |
| A dependency resolves differently in the image | the lock with `--no-deps`; `pip check`; the lock-equality test; the suite and the start-up check |
| Render cannot reach the database (IPv6) | session pooler on IPv4 (Section 2.2); B2 connects as the app login |
| Prepared-statement errors through a pooler | session mode, never port 6543; `check_hosted_db.py` reports the port in the URL host (no value printed) |
| The Data API exposes or alters corpus or audit rows | Data API off; revokes; RLS on all 15 tables; `check_hosted_db.py` |
| `vector` or `<=>` not found for the app login | `search_path` and `USAGE ON SCHEMA extensions` set explicitly; the start-up check as the app login in B2 |
| The free database pauses in a quiet week | the warning email and R11; the start-up check fails closed with the unavailable text, so nothing wrong is shown |
| Cold start longer than the note says | measured in B3; the README states the measured figure; Starter if over 120 s (D-197) |
| A visitor thinks the app is broken while it wakes | Render's loading page, then the early start-up notice (Section 3.4); B3 screenshots each stage on the live URL |
| The notice flashes on every click | it is drawn only when `app.services` is not yet imported; an AppTest checks a warm rerun draws no notice |
| A question pushes memory past 512 MB | Render metrics in the gate; stop at 400 MB |
| The free instance restarts during a question | the row stays `received` and counts at its reserve; the user gets the timeout or error text; the reviewer sees it as unfinished |
| Spend runs away | the cap in `open_query` under a lock; K1 to K3; dedicated keys; the provider's monthly limit |
| Prepaid credits run out | R7 weekly check; K2 below $2 |
| Sign-in fails in production (consent status, redirect, Authlib) | the free local sign-in test; K4 status check; fallback Testing status, back to Kene |
| `st.user` lacks `iss` or `sub` | `resolve_identity` raises `IdentityError` and the session stays anonymous; caught in the local test before deploy |
| A pushed commit deploys unreviewed code | Auto-Deploy Off; deploys name a commit |
| The owner credential leaks | it never goes to Render; `.env.hosted` only; emptied after the gate; R3 rotation |
| The HMAC secret is rotated or lost | R6 caveat; no other data depends on it |
| Phase 11 code changes reach the measured pipeline | the D-192 list; the start-up check hashes the pipeline, so a change there fails closed |

## 14. Decisions (approved by Kene 2026-11-14, all option (a))

Each row below is in the [decision log](../00_planning/decision_log.md) with this date. For D-203, Kene has decided (2026-11-14) not to register with the Commission and accepts the risk; the R12 count stays, for information only.

| Id | Decision | Options | Recommendation |
|---|---|---|---|
| D-192 | What the Phase 11 build may change | (a) the closed list of Section 0.2: the interface wiring Phase 9 deferred (limits gate and cap override, sign-in identity, the `sign_in` and `sign_out` hooks wired to `st.login("google")` and `st.logout()`, the early start-up notice in `main.py` if D-207 (a) is chosen, copy, limits values, their tests), new deploy files and docs; nothing in the pipeline, the audit migrations or the frozen and pinned files; (b) no code change at all (no hosted gate and no sign-in, so the hosted app cannot start under D-162 and D-163); (c) also change `startup.py` for a pause flag | **(a)**: Phase 9 left exactly these switches to Phase 11, and the start-up check still proves the pipeline |
| D-193 | Container image | (a) `python:3.12-slim-trixie@sha256:5e2bdb1b...4fa5b`, the allow-list `.dockerignore` of Section 1.3, non-root uid and gid 1000, the secrets link, the command flags of Section 1.4; (b) a deny-list `.dockerignore`; (c) a multi-stage build to cut size | **(a)**: an allow-list cannot leak a file nobody listed; the size comes from Streamlit's own dependencies |
| D-194 | Dependency lock (closes D-183) | (a) `requirements.lock.txt` from the clean-clone freeze (Linux: no `colorama`, `hf-xet==1.8.0`), plus the sign-in packages, installed with `--no-deps`, a lock-equality test; (b) as (a) with `--require-hashes`; (c) no lock | **(a)**: it is the tested set (1864/31); a plain install already drifts (python-dateutil 4.0.0) |
| D-195 | Hosted database | (a) Supabase Free in Frankfurt, session pooler on 5432 with TLS, Data API off, revokes and RLS on the corpus tables, login roles in the group roles, the corpus loaded from the caches (A4 to A6); (b) the paid IPv4 add-on and a direct connection; (c) transaction pooler | **(a)**: works from Render at $0 and behaves like the local database |
| D-196 | Owner credential handling | (a) `.env.hosted` on Kene's machine only, `with_hosted.py`, passwords by `set_role_password.py` in Kene's terminal, owner line emptied after the gate; (b) owner steps typed by Kene in the Supabase SQL editor | **(a)**: the clear password never reaches a statement log, and the owner never reaches Render |
| D-197 | Hosting | (a) Render Free, Docker, Frankfurt, health `/_stcore/health`, Auto-Deploy Off with deploys of a named commit, Starter only if the wake-up is over 120 s or Kene wants no wait; (b) Starter from the start; (c) Auto-Deploy On Commit | **(a)**: matches the plan and $0; every deploy is a reviewed commit; the wait is explained to visitors by D-207 |
| D-198 | Secrets and keys | (a) the Section 4 table; dedicated hosted API keys; presence-only check; no build arguments; (b) reuse the development keys | **(a)** |
| D-199 | Sign-in | (a) Google Web client, External, In production, openid, email and profile only, no logo, `Authlib==1.9.1`, `httpx==0.28.1`, live and localhost redirect URIs; fallback Testing status; (b) Testing status with named test users; (c) no sign-in in v1 (D-163 (c)) | **(a)** |
| D-200 | Final limits | (a) cap $0.50 a day, reserve $0.05, anonymous 3 a session and 20 a day pooled, signed in 10 a day, reviewer 50 a day; (b) the Phase 9 suggestions (3, 50, 20, 100, $0.50); (c) cap $0.30 | **(a)**: under (b) the pool and the per-user limits could never bind before the cap |
| D-201 | Kill switch | (a) K1 Render suspend, K2 `TAXY_COST_CAP_DAY_USD` (lower only), K3 key revocation; (b) K1 and K3 only (no code); (c) a pause flag in the start-up check | **(a)**: K2 also gives the gate a $0 test of the cap |
| D-202 | Purge schedule | (a) pg_cron daily as the owner, plus the run-history cleanup; fallback monthly by hand; (b) Render cron job; (c) by hand only | **(a)**: $0, owner stays off Render |
| D-203 | Data-controller registration (P8) | (a) Kene decides before the link opens: register at Ordinary-High Level (₦10,000 a year) or take advice; monthly upper-bound count in the runbook; (b) open and count only; (c) open only to signed-in users and count them | **(a)**: the facts (more than 200 data subjects in six months) are close enough to the demo's scale that waiting is not a safe default; this is not legal advice. **Outcome (Kene, 2026-11-14):** no registration; Kene accepts the risk; the R12 count stays, for information only |
| D-204 | Privacy additions at go-live | (a) the two Section 8.4 sentences (hosting in Frankfurt, Germany; the cookies) in the README and the popover, with tests; (b) none | **(a)**: both become true at go-live |
| D-205 | Phase 11 exit gate | (a) Section 9: (a) hosted equals local retrieval on every dev and test question, both configurations ($0); (b) 5 subset-T questions plus 2 refusals on the live URL with the three comparison classes; (c) session limit, daily limit, K2 and the real-spend cap test; cap $0.40, stop rule; (b) the plan's single question only; (c) as (a) with 10 questions | **(a)**: covers retrieval exactly for free and keeps the paid part near $0.10 |
| D-206 | Supabase pausing | (a) accept; the warning email and a dashboard visit (R11); (b) a scheduled keep-alive job (a GitHub Action holding a database credential); (c) Supabase Pro | **(a)**: no new credential outside Render and Kene's machine; a pause fails closed |
| D-207 | What a visitor sees during the cold start | (a) Render's own loading page while the instance wakes, then an early in-app notice drawn before the heavy imports (Section 3.4), with the text in `app/copy.py`; no new host; (b) a static front-door page on an always-on host (GitHub Pages, from a separate public repository, since Pages on a private repository needs a paid GitHub plan) that polls `/_stcore/health` and then redirects: a new provider that receives every visitor's IP address, so the privacy text and the D-204 hosting sentence must name GitHub, and cross-origin polling needs Streamlit's CORS settings opened for that origin (*unverified* that a cross-origin page can read the health answer through Render's loading page); a Render Static Site would avoid the new provider, but Render's docs do not say whether static sites stay up (*unverified*); (c) Starter from the start, no spin-down (about $7 a month, *unverified* figure; the pricing page fetched 2026-11-14 showed no plan prices), which leaves the in-app notice for deploys and restarts only | **(a)**, together with D-197 (a): $0, no new provider and no privacy-text change. It replaces 20 to 31 s of empty page with a plain notice after 2 to 8 s. Option (c) stays the upgrade path under the D-197 trigger (live wake-up over 120 s, or Kene wants no wait), and the notice costs nothing to keep then |

## 15. Sources (checked 2026-11-13; the cold-start rows 2026-11-14)

| Fact | Source |
|---|---|
| Supabase connection modes, IPv6 direct, session pooler 5432 on IPv4, transaction 6543, "doesn't support prepared statements in transaction mode" | supabase.com/docs/guides/database/connecting-to-postgres |
| Render has no outbound IPv6; shared pooler advice; port notes (article dated 2026-10-23) | render.com/articles/how-to-connect-a-render-service-to-supabase-postgres |
| Supabase Free: 500 MB, 5 GB egress, two free projects | supabase.com/docs/guides/platform/billing-on-supabase |
| Nano compute: up to 0.5 GB, 60 direct connections, 200 pooler clients, pool 15 | supabase.com/docs/guides/platform/compute-and-disk |
| Pool size per role | supabase.com/docs/guides/database/connection-management |
| Pausing after a week of low activity, warning email, restore within a year | supabase.com/docs/guides/platform/free-project-pausing |
| pgvector in schema `extensions`, dashboard enable; no version stated | supabase.com/docs/guides/database/extensions/pgvector |
| pgvector 0.8.0 with PostgreSQL 17 (secondary); extension version pinning deprecated | supabase/postgres repository mirror; Supabase changelog "Extension version pinning is deprecated" (both via search) |
| Data API off, default grants in `public`, RLS not automatic for SQL-created tables, "automatically expose new tables" | supabase.com/docs/guides/api/securing-your-api |
| `cron.job_run_details` grows and is not cleaned automatically | supabase.com/docs/guides/cron/quickstart (via search); supabase.com/docs/guides/cron |
| Render Free: spin-down after 15 minutes, about one minute to wake, 750 hours, no persistent disk, not for production | render.com/docs/free |
| Render shows "a loading page to connecting browsers" while a Free service spins up; static sites are free (checked 2026-11-14) | render.com/docs/free; render.com/docs/static-sites; render.com/pricing (no plan prices shown) |
| Render compute plans (`free` 0.1 CPU 512 MB; `0.5c-512mb`; renamed August 2026, no price change) | render.com/docs/compute-plans; render.com/docs/compute-plans-update |
| Render `PORT` 10000, bind `0.0.0.0`, auto-deploy behaviour | render.com/docs/web-services |
| Auto-Deploy options, deploy a specific commit, zero downtime | render.com/docs/deploys |
| Rollbacks reuse the build artifact and disable auto-deploy | render.com/docs/rollbacks |
| Health checks: 5 s timeout, 15 s and 60 s thresholds, 15-minute deploy window | render.com/docs/health-checks |
| `RENDER_GIT_COMMIT` at build and run time, Docker included | render.com/docs/environment-variables |
| Secret files at `/etc/secrets/<file>`, group 1000, copied into the build context root | render.com/docs/docker-secrets |
| Render regions; region cannot be changed | render.com/docs/regions |
| Google OAuth Testing (100 users, 7 days), In production, non-sensitive-scope exception, unverified cap for sensitive scopes | support.google.com/cloud/answer/15549945; 13464321; 13464323 |
| Streamlit OIDC secrets, `/oauth2callback`, Google metadata URL, 30-day cookie | docs.streamlit.io/develop/concepts/connections/authentication |
| Authlib release dates and requirements; 1.9.0 notes | pypi.org/pypi/Authlib/json; pypi.org/pypi/Authlib/1.9.1/json; github.com/authlib/authlib/releases |
| httpx 0.28.1 | pypi.org/pypi/httpx/json |
| NDPC GAID 2025: Article 9, Article 33, Schedule 7 | ndpc.gov.ng PDF of 20 March 2025 (Section 8.1) |

## 16. Local measurements (2026-11-13 and 2026-11-14, $0)

A scratch Dockerfile and its ignore file were written outside the repository, with the Section 1.3 list, and
built from the working tree. No API key reached the container (both set empty), no question was asked, and
the database was the local container on 5434 through `host.docker.internal`, with the credentials passed in a
temporary environment file that was deleted afterwards.

| Measurement | Result |
|---|---|
| Build (cold cache) | 2 min 38 s; image 725 MB; 70 packages; Python 3.12.16 |
| Start-up check in the image, first attempt | failed `live_config`: `eval/retrieval/config_v3.json` missing |
| Start-up check in the image, with `config_v3.json` | `ok`, no problems; imports 3.2 s, check 1.8 s |
| Same at `--cpus 0.1` | `ok`; imports 29.7 s, check 4.1 s |
| Streamlit app, 512 MB limit | 51 MiB before the first page, 65 MiB after (also 65 MiB at 0.1 CPU) |
| Cold start at 0.1 CPU | health 47 s after start; first page 26 s after the first request |
| Image scan | none of the forbidden paths; no secret-shaped string; no secret-named history entry |
| `pip freeze` in the image against the clean-clone record | equal apart from `colorama` (absent) and `hf-xet==1.8.0` (present) |

Started and stopped: the scratch image `taxy-phase11-measure:scratch` (removed), the containers from it
(removed), and the browser pane tab on the local port (closed). The local audit schema gained five
`app_starts` rows with `deployment = 'test'` (start ids 9 to 13; id 9 is the failed first attempt). They
hold no personal data.

**Start-up notice, 2026-11-14.** The same scratch image was rebuilt from the working tree of that day (the
UI revision files included), then run at `--cpus 0.1 --memory 512m`, deployment `test`, both API keys empty,
the local container as the database. A scratch copy of `app/main.py`, mounted over the image's file, drew the
notice right after `import streamlit as st` and wrote timestamps to the container log. The repository file
was not changed. The browser was the app's browser pane. A fresh origin (`127.0.0.1` instead of `localhost`)
gave a browser with no cached files (0 of 30 front-end files from the cache).

| Measurement | Result |
|---|---|
| Health endpoint after the container started | 63.5 s (first start of the rebuilt image); 27.1 s (restart of the same container) |
| Page request to script start, no cached files | 6.3 s (first page ever served: 10.5 s) |
| Page request to notice seen | 7.7 s (cached files: 2.1 s; first page ever served: 13.5 s) |
| Page request to script done, notice cleared | 25.3 s (cached files: 17.6 s in the log, 20.7 s seen in the browser) |
| Page request to question box | 31.0 s |
| `st.set_page_config` after the notice | the page title and layout were set as usual |
| Memory after the page | 73 to 76 MiB of 512 MiB |

Started and stopped: the rebuilt scratch image and its container (both removed), the scratch environment
file (deleted), the browser pane tab (closed). The local audit schema gained three more `app_starts` rows
with `deployment = 'test'` (start ids 22 to 24, all `ok`).

## 17. Build record

### Build 1 (B1.1 and B1.2, 2026-11-14, session "Phase 11 Build 1")

The overseer approved B1 and Kene approved the deviations below as D-208 and the identity-token point as D-209 on 2026-11-14.

**B1.1: the container image.** `Dockerfile`, the allow-list `.dockerignore`, `requirements.lock.txt` (74 pins)
with the two pins `Authlib==1.9.1` and `httpx==0.28.1` in `requirements.txt`, the lock-equality test, and
`eval/deploy/check_image.py` with its tests.

| Measurement | Result |
|---|---|
| Image | 729,041,969 bytes (729 MB) at B1.1; 729,048,571 bytes after B1.2. The design measured 725 MB; the difference is the sign-in packages |
| Build | 169 s on a cold cache; Python 3.12.16; `pip check` clean; 75 packages with pip; 73 files under `/srv/taxy`; user uid 1000; the project files are not writable by it |
| `check_image.py --worktree` | checks 1 to 5 clean at B1.1 and again at B1.2 |
| Start-up check inside the image | deployment `test`, local container on 5434: `ok`, no problems, snapshot 2026-10-18 (start ids 26 and 27) |
| Default suite | B1.1: 1967 passed, 30 skipped (baseline 1880 plus 87 new tests) |

**B1.2: the D-192 changes.** The hosted gate (`python/interface/limits.py`: `gate_for("hosted")` returns
`TierGate`, fails closed on a missing file, a null cap or a reserve that is not positive, and
`TAXY_COST_CAP_DAY_USD` can only lower the cap, `0` stopping every question; a value that is not a number of 0 or
more fails closed); the final `app/limits.yml` (Section 6.1: $0.50, reserve $0.05, 3 a session, pool 20,
signed in 10, reviewer 50); identity from `st.user` through `resolve_identity()` and the `sign_in` and
`sign_out` hooks (`st.login("google")` and `st.logout()`) on a hosted deployment only (`app/services.py`; the
session stays anonymous if no key can be derived or the user store fails, and the log gets the error type
only); the early start-up notice (`app/main.py`, `STARTUP_NOTICE` in `app/copy.py`, the wording of Section 3.4).
`deploy/supabase/0001_hosted_roles.sql` and `0002_revoke_api_roles.sql` (idempotent), `deploy/set_role_password.py`,
`deploy/apply_sql.py`, and in `eval/deploy/`: `with_hosted.py`, `check_hosted_db.py`, `compare_retrieval.py`,
`compare_live.py`, `check_live_start.py`. Free tests: `tests/test_deploy_scripts.py` (new), additions to
`tests/test_app.py`, `tests/test_app_service.py` and `tests/test_check_image.py`.

| Measurement | Result |
|---|---|
| Default suite after B1.2 | 2109 passed, 30 skipped. The 142 more than the 1967 of B1.1 are the 101 tests of `tests/test_deploy_scripts.py` and a net 41 in `tests/test_app.py`, `tests/test_app_service.py` and `tests/test_check_image.py` (the hosted gate and override, hosted identity and hooks, the notice, the base-image pull) |
| Image rebuilt, `check_image.py --worktree` | clean; 73 files; the dependency layers came from the cache |
| In the rebuilt image (deployment `test`) | start-up check `ok` (start id 27); `gate_for("hosted")` gives 0.50, 0.05, 3, 20, 10, 50, and the override gives 0.20, 0.50 (a raise is ignored) and 0; Authlib 1.9.1, httpx 0.28.1, `st.login` and `st.logout` present; `app/main.py` in a fresh process: no exception, one question box, the notice drawn then cleared, no notice on a rerun |
| Authlib advisory check (Section 5.2) | GitHub's global advisory database for pip: 0 advisories for Authlib 1.9.1, joserfc 1.7.7 and httpx 0.28.1 (the same query lists 17 for Authlib 1.2.0, so it works); PyPI lists no vulnerability for 1.9.1. Nothing to stop on |

**Deviations from the design, and additions** (approved, D-208):

1. **joserfc 1.7.7.** pip resolves 1.8.0, published 2026-11-03, 11 days before the build day, so it breaks the
   14-day rule of Section 1.2. The lock pins 1.7.7 (published 2026-10-19; Authlib needs `joserfc>=1.6.8`).
   Kene chose to keep 1.7.7. 1.8.0 turns 14 days old on 2026-11-17.
2. **`app/copy.py` was not light.** Section 3.4 says `app/copy.py` imports nothing heavy. It imported
   `generation.generate`, `interface.screen` and `interface.startup` when loaded, which cost about 0.7 s of the
   3.2 s import at full CPU, so about 7 s at 0.1 CPU before the notice could be drawn. The fix stays inside the
   D-192 list: the four texts the pipeline owns (`DISCLAIMER`, `EDIT_BUTTON`, `SCREEN_TEXT`, `UNAVAILABLE_TEXT`)
   are now re-exported lazily (module `__getattr__`), so `ui.DISCLAIMER` and `from app.copy import DISCLAIMER`
   work as before. A test runs a fresh interpreter and checks that `import app.copy` loads none of the pipeline.
3. **`deploy/apply_sql.py` (new, not named in the design).** The owner steps of Section 2.5 are SQL files, and
   `with_hosted.py` runs a Python script, so a small runner applies one `.sql` file in one transaction through
   `check_pgvector.connect()`. It prints the file name and "applied", and on a failure the error type only.
4. **`set_role_password.py`:** the password must have at least 16 characters (not in the design), and the
   SCRAM-SHA-256 verifier is computed in Python (salted, 4096 rounds). A test logs in to the local server with the
   password it set, and a wrong password is refused.
5. **The draw of the five cases** (Section 9.2) was not specified beyond the seed. `compare_live.py draw` takes
   the subset T cases answered in both Phase 8 runs with identical passage lists (all 30), in id order, and
   `random.Random(20261114).sample(ids, 5)`. That gives Q005, Q012, Q028, Q031 and Q049.
6. **`compare_live.py` baselines.** The Phase 8 results hold no configuration hashes, prompt labels or `corpus`
   object, so the fixed fields of an answered live row are compared with a local answered Phase 9 row
   (`--baseline-ref`, default Q-9681c177), and a refusal with its own Phase 9 row. For an answered case the
   pre-check result is "no refusal" and the `flags` equal the Phase 8 case. The `corpus` comparison leaves out
   `load_id`, which differs between the two databases. A passage difference in P1 to P8 is accepted only with
   `--parity-passed` and is listed either way.
7. **`compare_retrieval.py`** compares the whole retrieved list (depth 20), not only the top 5, and the two
   `evaluate()` summaries, so Section 9.1 is checked more strictly than the text asks.
8. **`check_hosted_db.py`** also checks the two login roles' attributes (no superuser, BYPASSRLS, CREATEDB,
   CREATEROLE or REPLICATION; connection limits 20 and 3; membership of the group role) and fails on an unexpected
   table in the audit schema, beyond the items of Section 2.6.
9. **`check_image.py`:** a `--worktree` mode for building before a commit exists, and it pulls the base image if
   the local image store lacks it (a build can use its own cache and leave nothing in the store).
10. **PUBLIC `EXECUTE`.** `0002_revoke_api_roles.sql` removes the explicit default grants Supabase gives the three
    API roles on tables, sequences and functions the owner creates later. PostgreSQL's built-in `EXECUTE` for PUBLIC
    on new functions is not an API-role grant and stays; the audit functions are protected by the migration
    runner's revoke from PUBLIC (D-151) and by the privilege check of `check_hosted_db.py`.

**Open points that this build does not settle** (still the design's *unverified* items, for B2 and B3): that
`verify-full` TLS works through the pooler, that the shared pooler accepts the custom login roles, pg_cron on the
Free plan, and what `st.user` carries for Google. Streamlit does not check the identity token's `exp` by itself
(its documentation says so); Kene decided (D-209, option (a)) that the 30-day cookie of Section 5.4 is the only expiry.

### Build 2 (B2, 2026-11-14, session "Phase 11 Build 2")

Every owner step ran from the laptop through `with_hosted.py owner`, one step per command, over `sslmode=verify-full`.
No paid call was made ($0) and no question was asked through the app. Kene typed every password; no build session
saw one. The probes in this step used throwaway scripts outside the repository that print yes/no answers, catalog
facts and error types only.

**The design's unverified items, now settled:**

| Item | Result |
|---|---|
| `verify-full` TLS through the shared pooler (Section 2.2) | **works** with `deploy/supabase/prod-ca-2021.crt`. Two controls are refused: a public CA bundle in place of Supabase's CA, and the pooler's IP address as the host (the certificate names `*.pooler.supabase.com`). The `require` fallback is not used. All three lines of `.env.hosted` end in `?sslmode=verify-full&sslrootcert=deploy/supabase/prod-ca-2021.crt` (a relative path, so the commands run from the repository root) |
| The shared pooler accepts the custom login roles (Section 2.2) | **yes**: `taxy_app_login.<ref>` and `taxy_review_login.<ref>` connect through the session pooler on port 5432; `current_user` is the login role and `search_path` is `public, extensions` for both |
| pg_cron on the Free plan, and the job role (Section 7) | **available**: `pg_cron` 1.6.4 is installed (schema `pg_catalog`). Both jobs are scheduled and run as `postgres` (`cron.timezone` is GMT). The first `job_run_details` row (2026-11-15, 01:00 UTC) is still to be read, in B3. The by-hand fallback is not needed |
| pgvector and server versions (Section 2.3) | hosted `vector` 0.8.2 in schema `extensions`, PostgreSQL 17.11 (aarch64); local `vector` 0.8.7. Parity is proved by gate (a), not by the versions |

**What ran, and the results:**

| Step | Result |
|---|---|
| A4 to A6 (Section 2.4) | A4: both caches match `assets.json`. A5: pgvector 0.8.2. A6: 400 chunks, 400 vectors from the cache, 0 from the API, `chunks_sha256` equal to the committed file. 3 of 3 pass |
| `migrate.py`, then `--check` | versions 1 to 3 applied, `audit-schema/1`. After the D-210 fix (deviation 6), version 4 applied: `audit-schema/2`, versions 1 to 4 |
| `0001_hosted_roles.sql` | the first run failed and changed nothing (deviation 2); applied after the fix |
| `grant_review.py --apply` | the reviewer group role can SELECT the four corpus tables (deviation 3) |
| `0002_revoke_api_roles.sql` | applied. Before it, `anon`, `authenticated` and `service_role` held SELECT, INSERT, UPDATE and DELETE on all four corpus tables with RLS off (as Section 2.6 expected). After it, none of them holds any privilege on them, and RLS is on |
| Data API settings off (Section 2.6, Kene in the dashboard) | confirmed by Kene on 2026-11-14, after the B2 commit: "Enable Data API" off and saved; the "automatically expose new tables" setting is not shown while the Data API is off, so nothing can be exposed through it |
| Passwords | Kene ran `set_role_password.py` for `app` and `review` and typed `DATABASE_URL_APP` and `REVIEW_DATABASE_URL` (deviation 5) |
| `check_hosted_db.py` | clean (no finding) before and after migration 4 |
| App login probe | reads 400 chunks and 400 embeddings; a `DELETE` on `corpus_loads` is refused (`InsufficientPrivilege`) |
| Start-up check as `taxy_app_login` (deployment `test`) | the first run failed with `audit_schema` (start id 1; deviation 6). After the fix: `ok`, no problems, snapshot 2026-10-18, corpus load 1 (start id 2) |
| Gate (a), `compare_retrieval.py` (Section 9.1) | **pass** (table below) |
| A9 against Supabase, as `taxy_app_login` | pass: dev 20 questions, hit@5 20, hit@1 16, MRR 0.8792, 0 differences; test 30 questions, hit@5 30, hit@1 22, MRR 0.8556, 0 differences; 0 query vectors from the API |
| pg_cron jobs (Section 7) | job 1 `taxy-purge-90d`, `0 1 * * *`, `select audit.purge_expired(90)`; job 2 `taxy-cron-history`, `30 1 * * *`, the `job_run_details` clean-up of Section 7; both active, user `postgres` |

**Gate (a):** hosted and local retrieval for every dev and test question, with query vectors from the published cache.

| Configuration | Split | Questions | Differences (whole ranked list, depth 20) | Largest distance difference | Hosted hit@5, hit@1, MRR |
|---|---|---|---|---|---|
| `config_final.json` | dev | 20 | 0 | 5.38e-07 | 20, 16, 0.8792 |
| `config_final.json` | test | 30 | 0 | 5.37e-07 | 30, 22, 0.8556 |
| `config_v3.json` | dev | 20 | 0 | 5.38e-07 | 20, 16, 0.8792 |
| `config_v3.json` | test | 30 | 0 | 5.37e-07 | 30, 22, 0.8556 |

The test split reproduces the G1 record (30 of 30, 22 of 30, 0.8556). The largest distance difference is below the
1e-6 warning level. No result file was written.

**Deviations from the design and the prompt:**

1. **The certificate file is `deploy/supabase/prod-ca-2021.crt`**, the name the dashboard gives it, not
   `prod-ca.crt`. Section 2.2 now says so. No code named the file.
2. **`0001_hosted_roles.sql`: `NOSUPERUSER` removed** (approved by the overseer). Supabase's `postgres` role is not a
   superuser, and PostgreSQL 17 refuses any `ALTER ROLE` that names the attribute: "Only roles with the SUPERUSER
   attribute may alter roles with the SUPERUSER attribute." A role created by a role that is not a superuser is never
   one, and `check_hosted_db.py` still fails on `rolsuper`. The local tests run as a superuser, which is why they
   passed. Every other statement of the file passed first in a rolled-back trial.
3. **`grant_review.py --apply` ran before `0001_hosted_roles.sql`**: it was chained after the failed first run. It
   is harmless: it is the design's next step and grants the reviewer group role SELECT on the four corpus tables
   only. From then on every owner step ran as its own command.
4. **TLS for the first owner steps.** The owner line named no `sslmode` at first, so the steps up to and including
   `0002` ran with `PGSSLMODE=verify-full` and `PGSSLROOTCERT` (the certificate) set in the shell, which libpq
   applies when the line names no mode. Kene then added the settings to all three lines.
5. **The password commands ran through the owner line**: `with_hosted.py owner deploy\set_role_password.py app`
   (and `review`), as the script's own usage says. Run directly, as the prompt wrote them, the script would have
   connected to the local container.
6. **The start-up check could not run as the app login (D-210).** `migrate.check_schema()` read
   `audit.schema_migrations` directly, and the app role, by design, holds no audit table privilege ("permission
   denied for table schema_migrations"). Locally the app runs as the owner, so nothing had caught it. Kene approved
   D-210 (option 1, 2026-11-14), a scope extension into `python/audit` (`migrate.py` and the new migration only):
   migration `0004_schema_versions.sql` adds `audit.schema_versions()` (owner-owned, SECURITY DEFINER, search path
   pinned, returning version, name and sha256 only, EXECUTE for the app group role only). `check_schema()` reads the
   versions through it; `migrate.py`'s own owner path still reads the table; `APP_FUNCTIONS` has eleven entries; the
   label is `audit-schema/2` (versions 1 to 4). Every other check in `check_schema()` is unchanged, including "the app
   role has no audit table privilege". New tests run `check_schema()` as the app role on the local container.
   Migration 4 was applied locally and on Supabase. The failed `app_starts` row (start id 1, deployment `test`,
   outcome `failed`, problems `audit_schema`) holds no personal data and goes with the 90-day purge.
7. **The owner password was reset once.** The first connection was refused ("password authentication failed");
   Kene reset the database password in the dashboard and retyped the owner line.

**Left for B3 and later:** the first `job_run_details` row; the certificate inside the image (the allow-list of
Section 1.3 does not name `deploy/supabase/`) and the `sslrootcert` path in Render's `DATABASE_URL` (both settled
by D-211 in B3a: the certificate is allow-listed, and the path is `/srv/taxy/deploy/supabase/prod-ca-2021.crt`); whether the
dashboard's "Enforce SSL on incoming connections" is on (not tested here).

**Fixes at the overseer review** (approved with fixes, 2026-11-14): `eval/reproduce/reproduce.py` row A7 expected
the text `audit-schema/1`, so a clean-clone A7 failed after migration 4. It now compares against
`migrate.SCHEMA_LABEL` (D-210 scope), and `reproduce.py --profile clean --only A7` passes locally
(`audit-schema/2`, versions 1 to 4). The current-state descriptions of the label and the app functions were
updated in the interface design and in the `python/audit/__init__.py`, `store.py` and `startup.py` docstrings;
dated records were left as they are.

### B3a (2026-11-14, session "Phase 11 Build 2")

D-211: `deploy/supabase/prod-ca-2021.crt` added to the `.dockerignore` allow-list (Section 1.3), and the form of
Render's `DATABASE_URL` recorded (Section 4 and step K3). New static tests: the certificate is allowed, no other
file of `deploy/` (nor `.env.hosted`) is, and the file holds one certificate with no secret-shaped string.

| Measurement | Result |
|---|---|
| `check_image.py --worktree` | clean (checks 1 to 5 found nothing); 729,052,248 bytes; 75 files under `/srv/taxy` (the 73 of B1 plus the certificate and audit migration 4, D-210) |
| Inside the image | `/srv/taxy/deploy/supabase/prod-ca-2021.crt` is present (1,367 bytes, readable by the app user) and is the only file under `/srv/taxy/deploy` |
| Image | removed after the check |

### B3 (2026-11-14, session "Phase 11 Build 2")

No question was asked, locally or live, and no paid call was made ($0). Kene did every sign-in and every click in
Render and Google; the build session never typed a credential and read no secret, cookie or token. Times are UTC
(West Africa Time is one hour ahead).

**Step 1: the free local sign-in test** (Section 5.5). The app ran from the working tree as `TAXY_DEPLOYMENT=hosted`
against the local container, with placeholder values (not keys) for `LLM_API_KEY` and `EMBEDDING_API_KEY`, a
throwaway `TAXY_USER_KEY_SECRET` made for the test and never shown, and the local `.streamlit/secrets.toml` with the
localhost redirect. The start-up check passed (local start id 29, deployment `hosted`).

| Check | Result |
|---|---|
| "Sign in with Google" | enabled; calls `st.login("google")`: the browser went to `accounts.google.com` with the redirect `http://localhost:8501/oauth2callback`, scopes `openid email profile` and PKCE (S256) |
| Consent screen | "Sign in to Taxy"; shares name, profile picture and email address; Kene pressed Continue (deviation 1) |
| `iss` and `sub` from `st.user` (Section 5.4, *unverified* until now) | **settled**: both present for Google, because a user key was derived (`resolve_identity()` refuses claims without them) |
| What was stored | one `audit.users` row: a 64-character lower-case hex key (HMAC-SHA256), provider `google`, tier `signed_in`. The account's email appears in none of the 114 text and JSON columns of the audit schema |
| Sidebar profile block | "Signed in as" with the account for the session only, "Delete my history", "Log out"; "Questions left today" moved from 3 (anonymous) to 10 (signed in) |
| "Delete my history" | the dialog ("cannot be undone, and you will be signed out"), then "Yes, delete everything": the user row was gone (0 rows) and the session was signed out |
| "Log out" | after a second sign-in, it calls `st.logout()`: the sidebar went back to "Sign in with Google". That second sign-in left one HMAC-only user row in the local database; it cannot be linked to anyone, because the test's key secret no longer exists |

**Step 2: the deploy.** Kene used "Manual Deploy", then "Deploy a specific commit", with **`9944ddeba6d1da5e6b72dd6745a6f1c9a3a4c8a7`**
(origin/main, D-211). The overseer had amended the B3a commit before the push (a wording change in the Build 2
record), so the hash differs from the build's local `99b376c`; the content is the same apart from that line. The
deploy went Live.

**Step 3: `check_live_start.py`** (Section 3.2), as the reviewer, after the first page view: "hosted start id 3:
outcome ok, python 3.12.16, streamlit 1.66.0, commit 9944ddeba6d1; clean: ok, no problems, pinned config, approved
commit". After each cold start below the check passed again (start ids 4 and 5). The hosted database holds no
query and no user row.

**Step 4: the live page.** Title "Taxy · Nigerian SME Tax Reference"; the short disclaimer "Research and reference
tool. Not tax advice. Corpus snapshot 18 October 2026, Official Gazette text."; the consent text and checkbox; the
Privacy details (who runs it, what is stored); the menu with System, Light and Dark, Print and Record screen and no
developer options; in the sidebar "Questions left today: 3" and "Sign in with Google"; no console error.
**Environment saves (from Render's documentation, nothing changed):** saving environment variables in the dashboard
starts a deploy unless "Save only" is chosen ("Save, rebuild, and deploy", "Save and deploy" or "Save only"), and
saving a secret file starts a new deploy. So a variable change takes effect only through a deploy, and "Save only"
waits for the next one.

**Step 5: the cold start** (Section 3.4), each after more than 16 minutes with no traffic.

| Run | Render's loading page | Taxy page (title) | Start row written | Question box | Total |
|---|---|---|---|---|---|
| 1 (16:07:39) | HTTP 503, "Render - Application loading": "INCOMING HTTP REQUEST DETECTED", "SERVICE WAKING UP" (16:07:42); still shown at 16:07:59 | by 16:08:08 | start id 4, 16:08:07 | by 16:08:08 | **about 29 s** |
| 2 (16:26:49) | the same page, then "ALLOCATING COMPUTE RESOURCES" (16:26:56), "PREPARING INSTANCE FOR INITIALIZATION" (16:26:59), "STARTING THE INSTANCE" (16:27:03); still shown at 16:27:14 | 16:27:28 | start id 5, 16:27:27 | 16:27:30 | **about 41 s** |

Render's loading page moves on to the app by itself: the browser was left alone and the same tab became the Taxy
page. The start-up notice was not caught: the in-app part (from the last sample of Render's page to the question
box) was at most 16 s in run 2 and 9 s in run 1, shorter than the sampling gap. Both runs are well under the 120 s
upgrade trigger of Section 3.3. **The notice wording rule:** the in-app part is under 50 s, so "under a minute"
stays and `app/copy.py` is not changed. Run 1's screenshots were lost because the browser pane was hidden; its times
come from the page text read at each sample.

Screenshots (run 2, and the live page):
[Render's page at the request](img/b3_cold_01_render_162649.jpg),
[Render's page while the instance starts](img/b3_cold_02_render_162658.jpg),
[still Render's page at 16:27:14](img/b3_cold_03_render_162714.jpg),
[the ready page at 16:27:32](img/b3_cold_04_ready_162732.jpg),
[the ready page, wide](img/b3_live_ready_wide.jpg),
[the theme menu](img/b3_live_theme_menu.jpg).

**Deviations:**

1. **The Google app name is "Taxy"**, not "Nigerian SME Tax Reference" (Section 5.1, step 2). The consent screen
   reads "Sign in to Taxy". It matches the page's brand (D-189); Kene accepted it at the B3 review (2026-11-14).
2. **The deployed commit is `9944dde`**, the overseer's amended B3a commit (step 2).
3. **One local test row remains** (step 1, "Log out"): HMAC only, unlinkable, local database only.
4. **The notice was not observed live** (step 5); the rule is applied on the measured in-app part, which bounds it.

**For the paid gate:** the live service runs `9944dde` with a clean start (start id 5); the hosted database has no
query or user rows; the first `job_run_details` row (pg_cron, after 01:00 UTC on 2026-11-15) is still to be read;
the consent screen for a Google account that is not the owner (K5) is still to be recorded.
