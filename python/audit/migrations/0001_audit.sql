-- audit-schema/1, migration 1: tables, indexes, the queries trigger and row-level security
-- (interface design, Sections 4.2 to 4.6; D-151).
--
-- A template: the runner (python/audit/migrate.py) replaces {{schema}}, {{app_role}} and
-- {{review_role}} with checked identifiers and sets search_path to {{schema}}, pg_catalog, pg_temp,
-- so the names below are unqualified. An applied migration is never edited; a change is a new file.

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
    app_version       text NOT NULL,
    audit_schema      text NOT NULL,
    python_version    text NOT NULL,
    streamlit_version text NOT NULL,
    config_file       text NOT NULL,
    config_sha256     text NOT NULL,
    corpus_load_id    bigint,
    corpus_snapshot   date,
    chunks_sha256     text,
    outcome           text NOT NULL CHECK (outcome IN ('ok', 'failed')),
    problems          text[] NOT NULL DEFAULT '{}'
);

-- Signed-in users (Section 7.2). No email, no name.
CREATE TABLE users (
    user_key    text PRIMARY KEY,
    provider    text NOT NULL,
    tier        text NOT NULL DEFAULT 'signed_in' CHECK (tier IN ('signed_in', 'reviewer')),
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now()
);

-- One row per admitted question.
CREATE TABLE queries (
    query_id                 uuid PRIMARY KEY,
    record_id                uuid UNIQUE,
    start_id                 bigint NOT NULL REFERENCES app_starts,
    state                    text NOT NULL CHECK (state IN ('received', 'final')),
    received_at              timestamptz NOT NULL,
    finished_at              timestamptz,
    tier                     text NOT NULL CHECK (tier IN ('local', 'anonymous', 'signed_in', 'reviewer')),
    user_key                 text REFERENCES users ON DELETE CASCADE,
    session_key              text NOT NULL,
    question                 text NOT NULL,
    question_chars           integer NOT NULL,
    cost_reserved_usd        numeric(8, 4) NOT NULL,
    status                   text CHECK (status IN ('answered', 'not_covered', 'refused', 'error', 'app_error')),
    refusal_flag             boolean,
    refusal_reason           text,
    refusal_stage            text,
    refusal_text             text,
    error                    text,
    app_error                text,
    flags                    text[] NOT NULL DEFAULT '{}',
    log_schema               text,
    config_file              text,
    config_sha256            text,
    retrieval_config_sha256  text,
    guardrails_policy_sha256 text,
    corpus                   jsonb,
    generator_model          text,
    prompt_version           text,
    schema_version           text,
    tool_defs_version        text,
    verifier_model           text,
    verifier_prompt_version  text,
    gap_prompt_version       text,
    tagger_model             text,
    versions                 jsonb,
    pre_check                jsonb,
    relevance_floor          jsonb,
    note_tagger              jsonb,
    notes                    jsonb,
    disclosures              jsonb,
    parse                    jsonb,
    generator_output         text,
    verification_summary     jsonb,
    tool_uses                jsonb,
    model_calls              jsonb,
    rendered                 text,
    rendered_sha256          text,
    usage                    jsonb,
    generation_cost_usd      numeric(10, 6),
    verification_cost_usd    numeric(10, 6),
    tagger_cost_usd          numeric(10, 6),
    total_cost_usd           numeric(10, 6),
    latency_ms               integer,
    gen_log_file             text,
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
    label             text NOT NULL,
    rank              smallint NOT NULL,
    chunk_id          text NOT NULL,
    citation          text NOT NULL,
    context_header    text NOT NULL,
    origin            text NOT NULL CHECK (origin IN ('retrieval', 'attachment', 'lookup', 'calculation_source')),
    score             double precision,
    distance          double precision,
    reasons           text[] NOT NULL,
    disputed          boolean NOT NULL,
    cited             boolean NOT NULL,
    supporting        boolean NOT NULL,
    chunk_text_sha256 text NOT NULL,
    PRIMARY KEY (query_id, label)
);

-- Every claim the verifier checked (shown or not).
CREATE TABLE claims (
    query_id         uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    claim_no         smallint NOT NULL,
    text             text NOT NULL,
    cited_labels     text[] NOT NULL,
    cited_chunk_ids  text[] NOT NULL,
    rule             jsonb NOT NULL,
    llm_verdict      text,
    second           jsonb,
    verdict          text NOT NULL CHECK (verdict IN ('supported', 'unsupported', 'contradicted', 'not_a_claim', 'unverified')),
    reason           text,
    sub_reason       text,
    unsupported_part text,
    reasoning        text,
    evidence         jsonb NOT NULL,
    citations        jsonb NOT NULL,
    kept_chunk_ids   text[] NOT NULL,
    action           text NOT NULL CHECK (action IN ('kept', 'removed', 'moved_not_rendered')),
    shown            boolean NOT NULL,
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
    class     text,
    kept      boolean NOT NULL,
    reason    text,
    shown     boolean NOT NULL,
    PRIMARY KEY (query_id, gap_no)
);

-- Every calculation the tool ran (computed, declined or error), with checks K1 to K6.
CREATE TABLE computations (
    query_id        uuid NOT NULL REFERENCES queries ON DELETE CASCADE,
    calc_id         text NOT NULL,
    scenario        text NOT NULL,
    status          text NOT NULL,
    inputs          jsonb NOT NULL,
    input_matches   jsonb NOT NULL,
    constants       jsonb NOT NULL,
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
    checks          jsonb NOT NULL,
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
    shown_sha256   text
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
    day     date NOT NULL,
    tier    text NOT NULL,
    reason  text NOT NULL,
    n       integer NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tier, reason)
);

-- The queries trigger (Section 4.6): the only UPDATE allowed is the one move from 'received' to
-- 'final', which may set the outcome columns but never the receipt columns; a DELETE is allowed
-- only inside erase_user, erase_query and purge_expired, which set the transaction-local flag
-- taxy.audit_delete for the length of their own delete (a cascade from users included). This
-- makes the log append-only for the app role. It is not tamper-proof against the owner or a
-- superuser, who can drop the trigger.
CREATE FUNCTION queries_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = {{schema}}, pg_catalog, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF coalesce(current_setting('taxy.audit_delete', true), '') <> 'on' THEN
            RAISE EXCEPTION 'audit rows are deleted only by erase_user, erase_query or purge_expired'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.state <> 'received' OR NEW.state <> 'final' THEN
        RAISE EXCEPTION 'an audit query row moves once, from received to final'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.query_id IS DISTINCT FROM OLD.query_id
       OR NEW.start_id IS DISTINCT FROM OLD.start_id
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.tier IS DISTINCT FROM OLD.tier
       OR NEW.user_key IS DISTINCT FROM OLD.user_key
       OR NEW.session_key IS DISTINCT FROM OLD.session_key
       OR NEW.question IS DISTINCT FROM OLD.question
       OR NEW.question_chars IS DISTINCT FROM OLD.question_chars
       OR NEW.cost_reserved_usd IS DISTINCT FROM OLD.cost_reserved_usd THEN
        RAISE EXCEPTION 'the receipt columns of an audit query row never change'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER queries_guard
    BEFORE UPDATE OR DELETE ON queries
    FOR EACH ROW EXECUTE FUNCTION queries_guard();

-- Row-level security on every table. The only policy is the reviewer's SELECT policy
-- (0003_roles.sql). RLS is not forced, so the owner and the SECURITY DEFINER functions are not
-- filtered by it.
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_starts ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE passages ENABLE ROW LEVEL SECURITY;
ALTER TABLE claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE gaps ENABLE ROW LEVEL SECURITY;
ALTER TABLE computations ENABLE ROW LEVEL SECURITY;
ALTER TABLE deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE admission_counts ENABLE ROW LEVEL SECURITY;
