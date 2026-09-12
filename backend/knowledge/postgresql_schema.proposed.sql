-- SUPERSEDED design proposal. Actual demo DDL: wellio/data/knowledge_v1.sql.
-- Do not execute alongside the implemented schema; see INTEGRATION.md.
-- PROPOSED staging schema, not an application migration and not executed here.
-- PostgreSQL + pgvector. No SQLite fallback. Apply via the backend's migration
-- workflow only after checking extension availability in the target database.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS knowledge;

CREATE TABLE IF NOT EXISTS knowledge.documents (
    id text NOT NULL,
    document_version text NOT NULL CHECK (length(document_version) = 64),
    title text NOT NULL,
    source_url text NOT NULL,
    publisher text NOT NULL,
    language text NOT NULL,
    text text NOT NULL,
    metadata jsonb NOT NULL,
    fetched_at timestamptz NOT NULL,
    publication_status text NOT NULL DEFAULT 'staging'
        CHECK (publication_status IN ('staging', 'published', 'retired')),
    PRIMARY KEY (id, document_version)
);

CREATE TABLE IF NOT EXISTS knowledge.chunks (
    id text PRIMARY KEY,
    document_id text NOT NULL,
    document_version text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    heading_path jsonb NOT NULL,
    char_start integer NOT NULL CHECK (char_start >= 0),
    char_end integer NOT NULL CHECK (char_end > char_start),
    text text NOT NULL,
    metadata jsonb NOT NULL,
    language text NOT NULL,
    -- Pre-tokenized Chinese/English aliases can be supplied at import time.
    -- 'simple' is deliberately not advertised as Chinese word segmentation.
    search_text text NOT NULL DEFAULT '',
    search_vector tsvector GENERATED ALWAYS AS (
        CASE WHEN language = 'en'
             THEN to_tsvector('pg_catalog.english'::regconfig, search_text)
             ELSE to_tsvector('pg_catalog.simple'::regconfig, search_text)
        END
    ) STORED,
    FOREIGN KEY (document_id, document_version)
        REFERENCES knowledge.documents (id, document_version),
    UNIQUE (document_id, document_version, ordinal)
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_fts
    ON knowledge.chunks USING gin(search_vector);

CREATE TABLE IF NOT EXISTS knowledge.embedding_models (
    model_key text PRIMARY KEY,
    provider text NOT NULL,
    model_name text NOT NULL,
    model_revision text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    UNIQUE (model_key, dimensions)
);
CREATE TABLE IF NOT EXISTS knowledge.chunk_embeddings (
    chunk_id text NOT NULL REFERENCES knowledge.chunks(id),
    model_key text NOT NULL,
    dimensions integer NOT NULL,
    embedding vector NOT NULL,
    CHECK (vector_dims(embedding) = dimensions),
    FOREIGN KEY (model_key, dimensions)
        REFERENCES knowledge.embedding_models(model_key, dimensions),
    PRIMARY KEY (chunk_id, model_key)
);
-- Exact vector search is sufficient for the initial corpus. A future HNSW
-- expression index must use the selected model's actual dimensions and a
-- model_key predicate. Do not mix embeddings from different models.
-- documents.jsonl -> documents: remaining record fields go into metadata.
-- chunks.jsonl -> chunks: remaining fields go into metadata; embedding=null in
-- JSONL means NOT GENERATED, so do not insert a row in chunk_embeddings yet.
-- API/Agent reads must filter published documents and a released model/version;
-- merely importing staging rows must not make them available to live advice.
COMMIT;
