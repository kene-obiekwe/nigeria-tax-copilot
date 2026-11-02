-- Vector store schema (Phase 3, D-19). Portable to Supabase: core PostgreSQL types plus
-- pgvector's vector; the type is never schema-qualified (Supabase installs it in "extensions").
-- Every statement is idempotent.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per chunk of data/derived/chunks/v1_chunks.jsonl, every field kept. Chapter, part and
-- schedule numbers are stored as printed and normalised to a whole number.
CREATE TABLE IF NOT EXISTS corpus_chunks (
    chunk_id               text PRIMARY KEY,
    act_id                 text NOT NULL,
    act_title              text NOT NULL,
    unit_type              text NOT NULL,
    citation               text NOT NULL,
    chapter_printed        text,
    chapter_no             smallint,
    chapter_title          text,
    part_printed           text,
    part_no                smallint,
    part_title             text,
    schedule_printed       text,
    schedule_no            smallint,
    schedule_title         text,
    schedule_part          text,
    section                text,
    subsections            text[] NOT NULL,
    na_section             text,
    heading                text NOT NULL,
    heading_source         text NOT NULL,
    context_header         text NOT NULL,
    text                   text NOT NULL,
    text_source            text NOT NULL,
    source_file            text NOT NULL,
    source_sha256          text NOT NULL,
    source_pages           integer[] NOT NULL,
    page_labels            text[] NOT NULL,
    cross_refs             jsonb NOT NULL,
    discrepancy_flag       boolean NOT NULL,
    register_ids           text[] NOT NULL,
    corpus_snapshot        date NOT NULL,
    edition_date           date NOT NULL,
    tokens_cl100k          integer NOT NULL,
    tokens_with_header     integer NOT NULL,
    tokens_bge_with_header integer NOT NULL,
    text_sha256            text NOT NULL,
    CHECK ((chapter_printed IS NULL) = (chapter_no IS NULL)),
    CHECK ((part_printed IS NULL) = (part_no IS NULL)),
    CHECK ((schedule_printed IS NULL) = (schedule_no IS NULL))
);

CREATE INDEX IF NOT EXISTS corpus_chunks_act_section_idx ON corpus_chunks (act_id, section);

-- One row per chunk per embedding model. The vector column has no fixed width, so models of
-- different dimensions share the table; the check keeps each row's width equal to dims.
-- Exact search only (no ANN index) at this corpus size.
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id     text NOT NULL REFERENCES corpus_chunks (chunk_id) ON DELETE CASCADE,
    model        text NOT NULL,
    dims         smallint NOT NULL,
    embedding    vector NOT NULL,
    input_sha256 text NOT NULL,
    embedded_at  timestamptz NOT NULL,
    PRIMARY KEY (chunk_id, model),
    CHECK (vector_dims(embedding) = dims)
);

-- data/curated/discrepancy_register.csv. Empty cells are NULL; cited_unit is NULL only for the
-- DR-000 rule row.
CREATE TABLE IF NOT EXISTS discrepancy_register (
    register_id        text PRIMARY KEY,
    act_id             text NOT NULL,
    cited_unit         text REFERENCES corpus_chunks (chunk_id),
    gazette_section    text,
    comparison_text    text NOT NULL,
    comparison_section text,
    type               text NOT NULL,
    cited_reading      text,
    comparison_reading text,
    similarity         numeric(4, 3),
    auto_class         text,
    cited_pages        text,
    comparison_pages   text,
    refs               text,
    reviewed_by        text NOT NULL,
    reviewed_on        date NOT NULL,
    notes              text
);

-- One row per loader run, so later audit rows can name the exact corpus that was loaded.
CREATE TABLE IF NOT EXISTS corpus_loads (
    load_id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loaded_at             timestamptz NOT NULL DEFAULT now(),
    corpus_snapshot       date NOT NULL,
    chunks_sha256         text NOT NULL,
    register_sha256       text NOT NULL,
    build_manifest_sha256 text NOT NULL,
    chunk_count           integer NOT NULL,
    register_count        integer NOT NULL,
    chunks_inserted       integer NOT NULL,
    chunks_updated        integer NOT NULL,
    chunks_deleted        integer NOT NULL,
    model                 text,
    embeddings_unchanged  integer NOT NULL DEFAULT 0,
    embeddings_from_cache integer NOT NULL DEFAULT 0,
    embeddings_from_api   integer NOT NULL DEFAULT 0,
    api_tokens            integer NOT NULL DEFAULT 0
);
