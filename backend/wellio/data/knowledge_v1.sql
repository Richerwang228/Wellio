-- Applied explicitly by `python -m wellio.knowledge_ingest`, not business startup.
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.releases (
    id text PRIMARY KEY,
    model text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    recipe text NOT NULL,
    status text NOT NULL DEFAULT 'staging' CHECK (status IN ('staging','active','retired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, dimensions)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_release ON {schema}.releases (status) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS {schema}.documents (
    release_id text NOT NULL REFERENCES {schema}.releases(id),
    id text NOT NULL,
    version text NOT NULL,
    title text NOT NULL,
    source_url text NOT NULL,
    text text NOT NULL,
    metadata jsonb NOT NULL,
    PRIMARY KEY (release_id, id)
);
CREATE TABLE IF NOT EXISTS {schema}.chunks (
    release_id text NOT NULL,
    id text NOT NULL,
    document_id text NOT NULL,
    dimensions integer NOT NULL,
    text text NOT NULL,
    char_start integer NOT NULL CHECK (char_start >= 0),
    char_end integer NOT NULL CHECK (char_end > char_start),
    metadata jsonb NOT NULL,
    embedding public.vector NOT NULL,
    PRIMARY KEY (release_id, id),
    FOREIGN KEY (release_id, document_id) REFERENCES {schema}.documents(release_id, id),
    FOREIGN KEY (release_id, dimensions) REFERENCES {schema}.releases(id, dimensions),
    CHECK (public.vector_dims(embedding) = dimensions),
    CHECK (public.vector_norm(embedding) > 0)
);
CREATE INDEX IF NOT EXISTS chunks_document_idx ON {schema}.chunks (release_id, document_id);
-- Exact cosine search for this small corpus; no ANN recall loss or index tuning.
