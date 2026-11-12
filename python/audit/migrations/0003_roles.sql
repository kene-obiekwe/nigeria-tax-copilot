-- audit-schema/1, migration 3: group roles, the PUBLIC revoke and default privileges, the grants
-- and the reviewer policies (interface design, Section 4.6; D-151).
--
-- The two group roles are NOLOGIN. Login users that join them, and their passwords, are created by
-- Kene, never here. An existing role of either name is accepted only if it is a plain group role
-- (no login, not a superuser, no BYPASSRLS), so a migration can never grant to a role that bypasses
-- these rules.

DO $roles$
DECLARE
    v_name text;
BEGIN
    IF '{{app_role}}' = '{{review_role}}' THEN
        RAISE EXCEPTION 'the app role and the reviewer role must differ';
    END IF;
    FOREACH v_name IN ARRAY ARRAY['{{app_role}}', '{{review_role}}'] LOOP
        IF v_name = current_user THEN
            RAISE EXCEPTION 'a group role cannot be the migration owner';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', v_name);
        ELSIF EXISTS (SELECT 1 FROM pg_roles
                      WHERE rolname = v_name AND (rolcanlogin OR rolsuper OR rolbypassrls)) THEN
            RAISE EXCEPTION 'role % exists but is not a plain NOLOGIN group role', v_name;
        END IF;
    END LOOP;
END
$roles$;

-- 1. No function in the schema is executable by PUBLIC.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA {{schema}} FROM PUBLIC;

-- 2. Default privileges for functions the owner adds to this schema later. Note (decision log
-- C-entry against D-151): PostgreSQL adds per-schema default privileges to the global ones and
-- cannot revoke a global grant per schema, and PUBLIC EXECUTE on functions is a global built-in
-- default. This statement therefore does not stop PUBLIC from executing a later function on its
-- own. The migration runner closes that gap: after every migration it revokes EXECUTE on every
-- function in the schema from PUBLIC, in the same transaction, and the start-up check confirms it.
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA {{schema}} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Tables and sequences: nothing for PUBLIC (none is granted by default; stated for the record).
REVOKE ALL ON SCHEMA {{schema}} FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA {{schema}} FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA {{schema}} FROM PUBLIC;

-- 3. Schema usage for the two group roles.
GRANT USAGE ON SCHEMA {{schema}} TO {{app_role}}, {{review_role}};

-- 4. The app role: EXECUTE on exactly its ten functions, and no table privilege.
GRANT EXECUTE ON FUNCTION {{schema}}.record_start(text, text, text, text, text, text, text, text, bigint, date, text, text, text[]) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.open_query(uuid, bigint, text, text, text, text, numeric, jsonb) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.close_query(uuid, jsonb) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.add_delivery(uuid, text, text) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.add_feedback(uuid, text, integer, text) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.touch_user(text, text) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.user_history(text, integer, integer) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.erase_user(text) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.spend_since(timestamptz) TO {{app_role}};
GRANT EXECUTE ON FUNCTION {{schema}}.count_since(text, text, timestamptz) TO {{app_role}};

-- 5. The reviewer role: SELECT on every audit table, and the reviewer's SELECT policy on each
-- (row-level security is on everywhere, so without a policy the grant would show no rows). No
-- other role has a policy. The reviewer's SELECT on the corpus tables in public is not granted
-- here (Build A decision 2(a)): it is a separate owner-run grant, added with the viewer.
GRANT SELECT ON ALL TABLES IN SCHEMA {{schema}} TO {{review_role}};
CREATE POLICY review_select ON schema_migrations FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON app_starts FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON users FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON queries FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON passages FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON claims FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON gaps FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON computations FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON deliveries FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON feedback FOR SELECT TO {{review_role}} USING (true);
CREATE POLICY review_select ON admission_counts FOR SELECT TO {{review_role}} USING (true);
