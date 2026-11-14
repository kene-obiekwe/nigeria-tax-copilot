-- Hosted database, step 2: keep the corpus tables off the Supabase Data API (deployment design Section 2.6,
-- D-195). Owner-run, idempotent. Run it after 0001_hosted_roles.sql and `python python/audit/grant_review.py
-- --apply`, with the Data API turned off in the dashboard (that is the first protection; this is the second):
--
--   .venv\Scripts\python eval\deploy\with_hosted.py owner deploy\apply_sql.py deploy\supabase\0002_revoke_api_roles.sql
--
-- Supabase gives new tables in `public` broad privileges for `anon`, `authenticated` and `service_role`, and
-- tables made with SQL do not get row-level security. This project never uses those roles or the service-role
-- key, so every privilege they hold on the four corpus tables (and their sequences) is removed, the default
-- privileges for what the owner creates later are removed too, and row-level security is turned on with one
-- SELECT policy for the two group roles. The owner (the loader) is not filtered: RLS is not forced.

REVOKE ALL ON TABLE public.corpus_chunks, public.chunk_embeddings, public.discrepancy_register,
                    public.corpus_loads FROM anon, authenticated, service_role;

-- The sequences that belong to the four tables (corpus_loads has an identity column).
DO $seq$
DECLARE
    v_seq regclass;
BEGIN
    FOR v_seq IN
        SELECT d.objid::regclass
        FROM pg_depend d
        JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S'
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass
          AND d.deptype IN ('a', 'i') AND n.nspname = 'public'
          AND t.relname IN ('corpus_chunks', 'chunk_embeddings', 'discrepancy_register', 'corpus_loads')
    LOOP
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM anon, authenticated, service_role', v_seq);
    END LOOP;
END
$seq$;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated, service_role;

ALTER TABLE public.corpus_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunk_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discrepancy_register ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.corpus_loads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS corpus_select ON public.corpus_chunks;
DROP POLICY IF EXISTS corpus_select ON public.chunk_embeddings;
DROP POLICY IF EXISTS corpus_select ON public.discrepancy_register;
DROP POLICY IF EXISTS corpus_select ON public.corpus_loads;
CREATE POLICY corpus_select ON public.corpus_chunks FOR SELECT TO taxy_app, taxy_review USING (true);
CREATE POLICY corpus_select ON public.chunk_embeddings FOR SELECT TO taxy_app, taxy_review USING (true);
CREATE POLICY corpus_select ON public.discrepancy_register FOR SELECT TO taxy_app, taxy_review USING (true);
CREATE POLICY corpus_select ON public.corpus_loads FOR SELECT TO taxy_app, taxy_review USING (true);
