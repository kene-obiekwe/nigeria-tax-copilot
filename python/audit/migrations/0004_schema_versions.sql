-- audit-schema/2, migration 4: the app role reads the recorded migrations through a function (D-210).
--
-- The start-up check confirms that the schema is at its label and that every recorded sha256 matches its
-- file (interface design Section 3.8, item 5). On the hosted database the app runs as its own login, which
-- holds no privilege on any audit table, so it cannot read schema_migrations directly. This function returns
-- the version, name and sha256 of each recorded migration, and nothing else. Like the other functions it is
-- SECURITY DEFINER with search_path pinned to {{schema}}, pg_catalog, pg_temp. EXECUTE goes to the app role
-- only; the runner revokes it from PUBLIC after this migration, and the reviewer reads the table itself.

CREATE FUNCTION schema_versions() RETURNS TABLE (version integer, name text, sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
    SELECT m.version, m.name, m.sha256 FROM schema_migrations m ORDER BY m.version;
$$;

GRANT EXECUTE ON FUNCTION {{schema}}.schema_versions() TO {{app_role}};
