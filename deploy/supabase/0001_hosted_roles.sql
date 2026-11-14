-- Hosted database, step 1: the two login roles, the corpus read grant and the search path
-- (deployment design Section 2.5, D-195). Owner-run (`postgres` on Supabase), idempotent: it can be run again.
--
-- Run it AFTER `python python/audit/migrate.py`, which creates the NOLOGIN group roles `taxy_app` and
-- `taxy_review`, and BEFORE `deploy/supabase/0002_revoke_api_roles.sql`. Run it through the owner line of
-- `.env.hosted`:
--
--   .venv\Scripts\python eval\deploy\with_hosted.py owner deploy\apply_sql.py deploy\supabase\0001_hosted_roles.sql
--
-- It sets no password: Kene sets each one with
-- `deploy/set_role_password.py`, in his own terminal, so no password reaches a statement log or this file.

DO $login$
DECLARE
    v_pair text[];
BEGIN
    -- {login role, group role, connection limit}: app 20, review 3.
    FOREACH v_pair SLICE 1 IN ARRAY ARRAY[['taxy_app_login', 'taxy_app', '20'],
                                          ['taxy_review_login', 'taxy_review', '3']] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_pair[2] AND NOT rolcanlogin) THEN
            RAISE EXCEPTION 'group role % is missing or can log in: run python/audit/migrate.py first', v_pair[2];
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_pair[1]) THEN
            EXECUTE format('CREATE ROLE %I LOGIN', v_pair[1]);
        END IF;
        -- Every attribute is set on every run, so a role that already exists is brought to the same state.
        EXECUTE format('ALTER ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS '
                       'CONNECTION LIMIT %s', v_pair[1], v_pair[3]);
        EXECUTE format('GRANT %I TO %I', v_pair[2], v_pair[1]);
        -- `vector` and `<=>` live in schema `extensions` on Supabase (Section 2.3): set the path explicitly.
        EXECUTE format('ALTER ROLE %I SET search_path = public, extensions', v_pair[1]);
    END LOOP;
END
$login$;

-- Retrieval and the start-up probe read the corpus tables, so the app group role needs SELECT on them.
-- (Locally the app runs as the owner; this grant is new for hosted.) The reviewer's SELECT on the same
-- tables is python/audit/grant_review.py --apply (D-169).
GRANT USAGE ON SCHEMA public TO taxy_app, taxy_review;
GRANT SELECT ON public.corpus_chunks, public.chunk_embeddings, public.discrepancy_register,
                public.corpus_loads TO taxy_app;

-- The pgvector type and operators are in `extensions`: both group roles need to see that schema.
GRANT USAGE ON SCHEMA extensions TO taxy_app, taxy_review;
