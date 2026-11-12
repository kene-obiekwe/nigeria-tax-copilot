-- audit-schema/1, migration 2: the write and read functions (interface design, Section 4.6).
--
-- Every function is SECURITY DEFINER with search_path pinned to {{schema}}, pg_catalog and, last,
-- pg_temp (so a caller's temporary table can never shadow an audit table). Who may execute each
-- one is set in 0003_roles.sql: the app role gets EXECUTE on exactly ten of them; purge_expired,
-- erase_query and the two helpers stay owner-only.

-- Helper (owner-only): the start of the limit day, midnight West Africa Time (Africa/Lagos).
CREATE FUNCTION wat_day_start(p_ts timestamptz) RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
    SELECT date_trunc('day', p_ts AT TIME ZONE 'Africa/Lagos') AT TIME ZONE 'Africa/Lagos';
$$;

-- One app_starts row per process start (Section 3.8).
CREATE FUNCTION record_start(p_deployment text, p_git_commit text, p_app_version text, p_audit_schema text,
                             p_python_version text, p_streamlit_version text, p_config_file text,
                             p_config_sha256 text, p_corpus_load_id bigint, p_corpus_snapshot date,
                             p_chunks_sha256 text, p_outcome text, p_problems text[])
RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
    INSERT INTO app_starts (deployment, git_commit, app_version, audit_schema, python_version, streamlit_version,
                            config_file, config_sha256, corpus_load_id, corpus_snapshot, chunks_sha256, outcome,
                            problems)
    VALUES (p_deployment, p_git_commit, p_app_version, p_audit_schema, p_python_version, p_streamlit_version,
            p_config_file, p_config_sha256, p_corpus_load_id, p_corpus_snapshot, p_chunks_sha256, p_outcome,
            coalesce(p_problems, '{}'))
    RETURNING start_id;
$$;

-- Admission and receipt in one transaction (Sections 4.5 and 8). Under a transaction advisory lock,
-- so two sessions cannot both take the last question or the last cents of the cap. Checks, in
-- order: the daily cost cap (finished rows at their real cost, received rows at their reserve),
-- the anonymous pool, the per-user limit, the per-session limit. A refusal increments
-- admission_counts and stores nothing else. A null limit (or a missing key) means no limit.
CREATE FUNCTION open_query(p_query_id uuid, p_start_id bigint, p_tier text, p_user_key text, p_session_key text,
                           p_question text, p_reserve_usd numeric, p_limits jsonb)
RETURNS TABLE (admitted boolean, reason text, remaining integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
#variable_conflict use_column
DECLARE
    v_day_start  timestamptz := wat_day_start(now());
    v_day        date := (now() AT TIME ZONE 'Africa/Lagos')::date;
    v_cap        numeric := (p_limits ->> 'cost_cap_day_usd')::numeric;
    v_pool       integer := (p_limits ->> 'anonymous_pool_day')::integer;
    v_per_user   integer := (p_limits ->> 'per_user_day')::integer;
    v_per_session integer := (p_limits ->> 'per_session')::integer;
    v_reason     text;
    v_left       integer;
    v_n          integer;
    v_spent      numeric;
BEGIN
    IF p_reserve_usd IS NULL OR p_reserve_usd < 0 THEN
        RAISE EXCEPTION 'open_query needs a reserve of 0 or more' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_per_user IS NOT NULL AND p_user_key IS NULL THEN
        RAISE EXCEPTION 'a per-user limit needs a user key' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('{{schema}}.open_query'));

    IF v_cap IS NOT NULL THEN
        SELECT coalesce(sum(coalesce(q.total_cost_usd, q.cost_reserved_usd)), 0) INTO v_spent
        FROM queries q WHERE q.received_at >= v_day_start;
        IF v_spent + p_reserve_usd > v_cap THEN
            v_reason := 'cost_cap';
        END IF;
    END IF;
    IF v_reason IS NULL AND v_pool IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM queries q WHERE q.tier = 'anonymous' AND q.received_at >= v_day_start;
        IF v_n >= v_pool THEN
            v_reason := 'anonymous_pool';
        ELSE
            v_left := least(coalesce(v_left, v_pool - v_n - 1), v_pool - v_n - 1);
        END IF;
    END IF;
    IF v_reason IS NULL AND v_per_user IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM queries q WHERE q.user_key = p_user_key AND q.received_at >= v_day_start;
        IF v_n >= v_per_user THEN
            v_reason := 'daily_limit';
        ELSE
            v_left := least(coalesce(v_left, v_per_user - v_n - 1), v_per_user - v_n - 1);
        END IF;
    END IF;
    IF v_reason IS NULL AND v_per_session IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM queries q
        WHERE q.session_key = p_session_key AND q.received_at >= v_day_start;
        IF v_n >= v_per_session THEN
            v_reason := 'session_limit';
        ELSE
            v_left := least(coalesce(v_left, v_per_session - v_n - 1), v_per_session - v_n - 1);
        END IF;
    END IF;

    IF v_reason IS NOT NULL THEN
        INSERT INTO admission_counts AS a (day, tier, reason, n) VALUES (v_day, p_tier, v_reason, 1)
        ON CONFLICT (day, tier, reason) DO UPDATE SET n = a.n + 1;
        RETURN QUERY SELECT false, v_reason, 0;
        RETURN;
    END IF;

    INSERT INTO queries (query_id, start_id, state, received_at, tier, user_key, session_key, question,
                         question_chars, cost_reserved_usd)
    VALUES (p_query_id, p_start_id, 'received', now(), p_tier, p_user_key, p_session_key, p_question,
            char_length(p_question), p_reserve_usd);
    RETURN QUERY SELECT true, NULL::text, v_left;
END;
$$;

-- The close (Section 4.5): moves a received row to final and inserts its passages, claims, gaps
-- and computations from the payload, all or nothing. Raises if the row is missing or not received,
-- or if the payload's question is not the received question. Only the outcome columns are taken
-- from payload.query; the receipt columns stay as received (the trigger also enforces this).
CREATE FUNCTION close_query(p_query_id uuid, p_payload jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
DECLARE
    r queries%ROWTYPE;
    n queries%ROWTYPE;
BEGIN
    SELECT * INTO r FROM queries q WHERE q.query_id = p_query_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no audit row for this query' USING ERRCODE = 'no_data_found';
    END IF;
    IF r.state <> 'received' THEN
        RAISE EXCEPTION 'this audit row is already final' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (p_payload ->> 'question') IS DISTINCT FROM r.question THEN
        RAISE EXCEPTION 'the payload question is not the received question' USING ERRCODE = 'data_exception';
    END IF;
    n := jsonb_populate_record(r, coalesce(p_payload -> 'query', '{}'::jsonb));

    UPDATE queries q SET
        state = 'final', record_id = n.record_id, finished_at = coalesce(n.finished_at, now()),
        status = n.status, refusal_flag = n.refusal_flag, refusal_reason = n.refusal_reason,
        refusal_stage = n.refusal_stage, refusal_text = n.refusal_text, error = n.error, app_error = n.app_error,
        flags = coalesce(n.flags, '{}'), log_schema = n.log_schema, config_file = n.config_file,
        config_sha256 = n.config_sha256, retrieval_config_sha256 = n.retrieval_config_sha256,
        guardrails_policy_sha256 = n.guardrails_policy_sha256, corpus = n.corpus,
        generator_model = n.generator_model, prompt_version = n.prompt_version,
        schema_version = n.schema_version, tool_defs_version = n.tool_defs_version,
        verifier_model = n.verifier_model, verifier_prompt_version = n.verifier_prompt_version,
        gap_prompt_version = n.gap_prompt_version, tagger_model = n.tagger_model, versions = n.versions,
        pre_check = n.pre_check, relevance_floor = n.relevance_floor, note_tagger = n.note_tagger,
        notes = n.notes, disclosures = n.disclosures, parse = n.parse, generator_output = n.generator_output,
        verification_summary = n.verification_summary, tool_uses = n.tool_uses, model_calls = n.model_calls,
        rendered = n.rendered, rendered_sha256 = n.rendered_sha256, usage = n.usage,
        generation_cost_usd = n.generation_cost_usd, verification_cost_usd = n.verification_cost_usd,
        tagger_cost_usd = n.tagger_cost_usd, total_cost_usd = n.total_cost_usd, latency_ms = n.latency_ms,
        gen_log_file = n.gen_log_file
    WHERE q.query_id = p_query_id;

    INSERT INTO passages
    SELECT p.* FROM jsonb_array_elements(coalesce(p_payload -> 'passages', '[]'::jsonb)) AS e,
         LATERAL jsonb_populate_record(NULL::passages, e || jsonb_build_object('query_id', p_query_id)) AS p;
    INSERT INTO claims
    SELECT c.* FROM jsonb_array_elements(coalesce(p_payload -> 'claims', '[]'::jsonb)) AS e,
         LATERAL jsonb_populate_record(NULL::claims, e || jsonb_build_object('query_id', p_query_id)) AS c;
    INSERT INTO gaps
    SELECT g.* FROM jsonb_array_elements(coalesce(p_payload -> 'gaps', '[]'::jsonb)) AS e,
         LATERAL jsonb_populate_record(NULL::gaps, e || jsonb_build_object('query_id', p_query_id)) AS g;
    INSERT INTO computations
    SELECT k.* FROM jsonb_array_elements(coalesce(p_payload -> 'computations', '[]'::jsonb)) AS e,
         LATERAL jsonb_populate_record(NULL::computations, e || jsonb_build_object('query_id', p_query_id)) AS k;
END;
$$;

-- What the UI did with the answer (Section 9). Append-only.
CREATE FUNCTION add_delivery(p_query_id uuid, p_outcome text, p_shown_sha256 text) RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
    INSERT INTO deliveries (query_id, outcome, shown_sha256) VALUES (p_query_id, p_outcome, p_shown_sha256)
    RETURNING delivery_id;
$$;

-- One feedback row per session and query, inserted or updated (Section 7.5). Final rows only.
CREATE FUNCTION add_feedback(p_query_id uuid, p_session_key text, p_rating integer, p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
BEGIN
    INSERT INTO feedback AS f (query_id, record_id, session_key, rating, reason)
    SELECT q.query_id, q.record_id, p_session_key, p_rating, p_reason
    FROM queries q WHERE q.query_id = p_query_id AND q.state = 'final'
    ON CONFLICT (query_id, session_key)
    DO UPDATE SET rating = EXCLUDED.rating, reason = EXCLUDED.reason, record_id = EXCLUDED.record_id, at = now();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'feedback needs a final audit row' USING ERRCODE = 'no_data_found';
    END IF;
END;
$$;

-- Inserts the user or sets last_seen; returns the user's tier (Section 7.2). The key must be the
-- 64-character hex HMAC, so an email address or a provider subject can never be stored here.
CREATE FUNCTION touch_user(p_user_key text, p_provider text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
DECLARE
    v_tier text;
BEGIN
    IF p_user_key IS NULL OR p_user_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'a user key is a 64-character lower-case hex HMAC' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO users AS u (user_key, provider) VALUES (p_user_key, p_provider)
    ON CONFLICT (user_key) DO UPDATE SET last_seen = now()
    RETURNING u.tier INTO v_tier;
    RETURN v_tier;
END;
$$;

-- That user's final rows, newest first (Section 7.3): only for the key passed.
CREATE FUNCTION user_history(p_user_key text, p_limit integer, p_offset integer)
RETURNS TABLE (query_id uuid, reference text, record_id uuid, received_at timestamptz, question text,
               status text, rendered text, cited_passages jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
    SELECT q.query_id, 'Q-' || left(q.query_id::text, 8), q.record_id, q.received_at, q.question, q.status,
           q.rendered,
           coalesce((SELECT jsonb_agg(jsonb_build_object('label', p.label, 'chunk_id', p.chunk_id,
                                                         'citation', p.citation,
                                                         'context_header', p.context_header,
                                                         'disputed', p.disputed) ORDER BY p.rank, p.label)
                     FROM passages p WHERE p.query_id = q.query_id AND p.supporting), '[]'::jsonb)
    FROM queries q
    WHERE q.user_key = p_user_key AND q.state = 'final'
    ORDER BY q.received_at DESC, q.query_id
    LIMIT least(greatest(coalesce(p_limit, 50), 0), 200) OFFSET greatest(coalesce(p_offset, 0), 0);
$$;

-- "Delete my history" (Section 7.3): the user and, by cascade, their queries and children.
CREATE FUNCTION erase_user(p_user_key text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
DECLARE
    v_n integer;
BEGIN
    SELECT count(*) INTO v_n FROM queries q WHERE q.user_key = p_user_key;
    PERFORM set_config('taxy.audit_delete', 'on', true);
    DELETE FROM users u WHERE u.user_key = p_user_key;
    PERFORM set_config('taxy.audit_delete', 'off', true);
    RETURN v_n;
END;
$$;

-- The figures the limit display needs (Section 8).
CREATE FUNCTION spend_since(p_since timestamptz) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
    SELECT coalesce(sum(coalesce(q.total_cost_usd, q.cost_reserved_usd)), 0)
    FROM queries q WHERE q.received_at >= p_since;
$$;

CREATE FUNCTION count_since(p_kind text, p_key text, p_since timestamptz) RETURNS integer
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
DECLARE
    v_n integer;
BEGIN
    IF p_kind = 'session' THEN
        SELECT count(*) INTO v_n FROM queries q WHERE q.session_key = p_key AND q.received_at >= p_since;
    ELSIF p_kind = 'user' THEN
        SELECT count(*) INTO v_n FROM queries q WHERE q.user_key = p_key AND q.received_at >= p_since;
    ELSIF p_kind = 'anonymous_pool' THEN
        SELECT count(*) INTO v_n FROM queries q WHERE q.tier = 'anonymous' AND q.received_at >= p_since;
    ELSIF p_kind = 'all' THEN
        SELECT count(*) INTO v_n FROM queries q WHERE q.received_at >= p_since;
    ELSE
        RAISE EXCEPTION 'count_since kind is session, user, anonymous_pool or all'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_n;
END;
$$;

-- Owner-only: retention (D-159). Refuses fewer than 90 days. Deletes queries older than p_days
-- (children by cascade), users not seen for p_days with no queries left, and admission counts
-- older than 400 days. App starts are kept.
CREATE FUNCTION purge_expired(p_days integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
DECLARE
    v_cut timestamptz;
    v_q integer;
    v_u integer;
    v_a integer;
BEGIN
    IF p_days IS NULL OR p_days < 90 THEN
        RAISE EXCEPTION 'purge_expired keeps at least 90 days (D-159)' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_cut := now() - make_interval(days => p_days);
    PERFORM set_config('taxy.audit_delete', 'on', true);
    DELETE FROM queries q WHERE q.received_at < v_cut;
    GET DIAGNOSTICS v_q = ROW_COUNT;
    DELETE FROM users u
    WHERE u.last_seen < v_cut AND NOT EXISTS (SELECT 1 FROM queries q WHERE q.user_key = u.user_key);
    GET DIAGNOSTICS v_u = ROW_COUNT;
    DELETE FROM admission_counts a WHERE a.day < (now() AT TIME ZONE 'Africa/Lagos')::date - 400;
    GET DIAGNOSTICS v_a = ROW_COUNT;
    PERFORM set_config('taxy.audit_delete', 'off', true);
    RETURN jsonb_build_object('queries', v_q, 'users', v_u, 'admission_counts', v_a);
END;
$$;

-- Owner-only: one query and, by cascade, its children (Section 6.1, a deletion request).
CREATE FUNCTION erase_query(p_query_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
DECLARE
    v_n integer;
BEGIN
    PERFORM set_config('taxy.audit_delete', 'on', true);
    DELETE FROM queries q WHERE q.query_id = p_query_id;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    PERFORM set_config('taxy.audit_delete', 'off', true);
    RETURN v_n = 1;
END;
$$;
