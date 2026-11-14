# Runbook: the hosted demo

> This project is a research and reference tool, not tax advice. This page is the operator's runbook for the
> hosted demo at <https://taxy-1db6.onrender.com>: how to deploy, check, roll back, rotate secrets, handle
> requests and incidents, and switch the service off.

The design behind every entry is the [deployment design](deployment_design.md) (D-192 to D-213). Each entry
says when it applies, who does it, the steps, how to confirm and what to record. Commands are PowerShell, run
from the repository root, one per block.

**Rules for every entry.**

- Kene types every password, key and secret, in the provider's page or in `.env.hosted`. Nobody reads a value
  out, pastes it into a chat or a commit, or prints it. The scripts print error types only, never a value.
- `.env.hosted` (git-ignored, never in the image) holds three lines: `DATABASE_URL_OWNER`, `DATABASE_URL_APP` and
  `REVIEW_DATABASE_URL`. `eval\deploy\with_hosted.py` passes exactly one of them to one script. Keep the owner
  line empty except while an owner step runs.
- **A Render environment change takes effect only through a deploy.** Choose **"Save only"**, then
  "Manual Deploy", then **"Deploy a specific commit"** with the approved commit, so that a newer commit on
  `main` is never deployed by accident. Saving a secret file also starts a deploy.
- Times in the database are UTC. The limit day runs from midnight West Africa Time (23:00 UTC).
- Record each run in a private operations log (date, entry, commit, result). The log is never committed.

## R1. Deploy

**When:** a commit has been reviewed by the overseer and pushed. **Who:** Kene clicks; the checks can be run
by a build session.

1. Check the image of the approved commit (no secret, no forbidden file):

   ```powershell
   .venv\Scripts\python eval\deploy\check_image.py --rev <commit>
   ```

2. In the Render dashboard, open the `taxy` service, then "Manual Deploy", then "Deploy a specific commit", and
   enter the full commit hash. Wait until the deploy shows "Live".
3. Open <https://taxy-1db6.onrender.com> once, so that the first session writes its start-up row.
4. Check the start-up row (outcome `ok`, pinned configuration, the approved commit):

   ```powershell
   .venv\Scripts\python eval\deploy\with_hosted.py review eval\deploy\check_live_start.py --commit <commit>
   ```

**Confirm:** the script prints `clean: ok, no problems, pinned config, approved commit` and names a start id
newer than the last one recorded. **Record:** commit, date, start id.

## R2. Roll back

**When:** a deploy is live and wrong. **Who:** Kene.

1. In Render, open "Rollbacks" and roll back to the last good deploy. Render reuses that build and turns
   auto-deploy off.
2. Open the URL once, then run R1 step 4 with the commit of the deploy you rolled back to.

A database change is **never** rolled back this way: audit migrations are forward-only (D-151). If a
migration is the problem, stop questions with R14 K2 and fix forward with a reviewed commit.

**Record:** both commits, the date and why.

## R3. Rotate the database passwords

**When:** a password may have leaked, or once a year. **Who:** Kene (passwords), a build session may run the
checks.

1. Fill the owner line of `.env.hosted`, then set a new password for the app login (the script asks for it;
   at least 16 characters):

   ```powershell
   .venv\Scripts\python eval\deploy\with_hosted.py owner deploy\set_role_password.py app
   ```

2. Type the new `DATABASE_URL` into Render (same form as Section 4 of the design, with the certificate path
   `/srv/taxy/deploy/supabase/prod-ca-2021.crt`), "Save only", then deploy the current commit (R1 steps 2 to 4).
3. For the reviewer login, run the same command with `review` and retype `REVIEW_DATABASE_URL` in `.env.hosted`.
4. Empty the owner line again.

**Confirm:** R1 step 4 is clean, and:

```powershell
.venv\Scripts\python eval\deploy\with_hosted.py owner eval\deploy\check_hosted_db.py
```

prints `clean: no finding`. **Record:** the date and which login.

## R4. Rotate the API keys

**When:** a key may have leaked, or the provider asks. **Who:** Kene.

1. Create a new key at the provider (the hosted service has its own keys, separate from local work).
2. Type it into Render (`LLM_API_KEY` or `EMBEDDING_API_KEY`), "Save only", deploy the current commit (R1).
3. Revoke the old key at the provider.
4. Ask one cheap question on the live page (for example a question about a section number the Act does not
   have) and check that it gets an answer card with a reference.

**Record:** the date and which key (never the key).

## R5. Rotate the OAuth client secret or the cookie secret

**When:** either may have leaked. **Who:** Kene.

1. For the client secret: create a new one in the Google Cloud console for the "Taxy" OAuth client. For the
   cookie secret: make a new random string in your own terminal
   (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
2. Edit Render's secret file `secrets.toml` (`client_secret` or `cookie_secret`) and save. Saving a secret file
   starts a deploy: check in "Events" that the commit is the approved one, then run R1 steps 3 and 4.
3. Delete the old client secret in the Google console.

A new `cookie_secret` signs everyone out. **Confirm:** "Sign in with Google" works and the sidebar shows
"Questions left today: 10". **Record:** the date and which secret.

## R6. `TAXY_USER_KEY_SECRET` (rotate only if leaked)

**When:** only if this secret leaked. **Who:** Kene.

A new secret gives every signed-in user a new one-way key, so existing histories are detached: they no longer
show in "My questions", and they are purged at 90 days as usual.

1. Make a new random string as in R5 and type it into Render, "Save only", deploy the current commit (R1).
2. Put a one-line notice in the README Status for 90 days: signed-in histories from before the date are no
   longer shown.

**Record:** the date (it starts the 90 days).

## R7. Raise or lower the limits; watch the credits

**Who:** Kene.

- **Lower the daily cap at once:** set `TAXY_COST_CAP_DAY_USD` in Render to a value below $0.50 (`0` stops
  every question), "Save only", deploy the current commit. The variable can only lower the cap; the lower of
  the file and the variable applies.
- **Any other change** (raise the cap, change a count limit) is a reviewed commit to `app/limits.yml`, then R1.
- **Today's spend:** the live page's sidebar shows "Spend today" for the whole service.
- **Credits:** check the model provider's credit balance every week. Under $2, apply R14 K2 until it is topped
  up. When the balance reaches zero, every question ends with the pipeline's error text.

**Record:** each change, with the date and the reason. **Undo:** delete the variable, "Save only", deploy the
current commit; confirm with R1 step 4 and one cheap question.

## R8. Retention

**When:** the first week of each month. **Who:** Kene.

The 90-day purge runs as pg_cron job `taxy-purge-90d` at 01:00 UTC every day, and `taxy-cron-history`
deletes the job history older than 30 days. In the Supabase dashboard, SQL editor, run:

```sql
select jobid, status, start_time from cron.job_run_details order by start_time desc limit 10;
```

**Confirm:** a `succeeded` row for each of the last days. **Fallback** if the jobs are not running: run
`select audit.purge_expired(90);` by hand as the owner on the first of each month (rows then live at most
about 120 days). **Record:** the date and the result.

## R9. An erasure or access request

**When:** a request reaches the project mailbox. **Who:** Kene. Respond within 30 days.

1. For an anonymous question, the request must quote its reference. Look it up as the reviewer:

   ```powershell
   .venv\Scripts\python eval\deploy\with_hosted.py review python\audit\show_query.py Q-xxxxxxxx
   ```

2. Check that the reference matches exactly one row. For **access**, send the requester that Markdown report.
   For **erasure**, run `select audit.erase_query('<query_id>');` as the owner in the Supabase SQL editor, then
   run step 1 again and confirm the row is gone.
3. A signed-in user who writes in is asked to use "Delete my history" on the page: the app cannot link an
   email address to its one-way key, and does not try to.
4. Reply from the project mailbox.

**Record:** the date, the reference and the action in the private request log (no question text).

## R10. A breach

**When:** a breach is suspected. **Who:** Kene. Not legal advice; see Section 8.2 of the design.

1. **Contain:** R14 K1, then rotate every secret (R3 to R6).
2. **Assess:** what was exposed (questions, one-way keys, which period), and whether it is likely to put
   people's rights at risk.
3. **Within 72 hours** of becoming aware, notify the Nigeria Data Protection Commission through the channel
   its website gives at that time, with items (a) to (h) of Article 33(5) of the GAID: the circumstances; the
   date or period; the personal information involved; the risk of harm; the number of individuals at real
   risk; the steps taken to reduce harm; the steps taken to notify individuals; a contact.
4. The app stores no contact detail for any user, so put a notice on the live page and in the README.
5. Keep a timeline in the private log.

## R11. Supabase pause warning

**When:** Supabase emails that the free project will be paused, or the live page shows the "not available
right now" text. **Who:** Kene.

1. Open the project dashboard; that counts as activity.
2. If the project is paused, choose "Resume project" and wait until it is up.
3. Run R1 steps 3 and 4 with the current commit.

## R12. Monthly data-protection count (for information only)

**When:** the first week of each month. **Who:** Kene.

In the Supabase SQL editor, run the upper-bound count of people over the last 182 days (signed-in keys plus
anonymous session keys; one person can have many sessions):

```sql
select (select count(distinct user_key) from audit.queries where user_key is not null
          and received_at > now() - interval '182 days')
     + (select count(distinct session_key) from audit.queries where user_key is null
          and received_at > now() - interval '182 days') as upper_bound;
```

Note the figure against the threshold of 200 in the private log. **This count is for information only:** Kene
decided on 2026-11-14 not to register with the Commission and accepts the risk (D-203 outcome, design
Section 8.1). It does not trigger registration.

## R13. A cost spike

**When:** the sidebar's "Spend today" is unusually high, or the provider warns. **Who:** Kene.

The $0.50 daily cap stops spending within the day by itself. To see where it went, run in the Supabase SQL
editor:

```sql
select tier, status, refusal_reason, count(*), sum(total_cost_usd) from audit.queries
where received_at > now() - interval '1 day' group by 1, 2, 3 order by 5 desc nulls last;
```

and `select * from audit.admission_counts order by day desc limit 20;`. If it is abuse, apply R14 K2, then
lower the pool or the per-session limit by a reviewed commit to `app/limits.yml` (R1). **Record:** the date,
the figures and the action.

## R14. Kill switch

| Level | How | Takes effect | Undo |
|---|---|---|---|
| **K1** stop everything | Render, the service's settings: "Suspend service" | at once; visitors see Render's page | "Resume service", then R1 steps 3 and 4 |
| **K2** stop questions, keep reading | Render: `TAXY_COST_CAP_DAY_USD` = `0`, "Save only", "Deploy a specific commit" with the current commit | after the deploy (about a minute); every question gets the cap text, no paid call | delete the variable, "Save only", deploy the current commit; confirm with one cheap question that gets an answer card |
| **K3** stop paid calls in flight | revoke the hosted key at the model provider (and the embedding provider) | at once; questions end with the pipeline's error text | create new keys and follow R4 |

K2 was tested on the live service in the exit gate (design Section 17): the question got the cap text, no
query row was written and a `cost_cap` count was recorded.
