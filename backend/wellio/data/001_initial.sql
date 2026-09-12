-- PostgreSQL migration 1. Snapshot schemaVersion remains 4 independently.
CREATE TABLE server_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    reset_epoch BIGINT NOT NULL CHECK (reset_epoch > 0),
    revision BIGINT NOT NULL CHECK (revision > 0),
    schema_version INTEGER NOT NULL,
    seed_source TEXT NOT NULL,
    snapshot_json JSONB NOT NULL CHECK (jsonb_typeof(snapshot_json) = 'object'),
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL
);

CREATE TABLE action_requests (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    request_epoch BIGINT NOT NULL,
    result_epoch BIGINT NOT NULL,
    kind TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    result_json JSONB NOT NULL CHECK (jsonb_typeof(result_json) = 'object'),
    created_at BIGINT NOT NULL,
    continuation_json JSONB CHECK (continuation_json IS NULL OR jsonb_typeof(continuation_json) = 'object'),
    PRIMARY KEY (session_id, request_id)
);

CREATE TABLE context_reads (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    reset_epoch BIGINT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object'),
    UNIQUE (session_id, run_id, request_id, reset_epoch)
);

CREATE TABLE user_inputs (
    id TEXT PRIMARY KEY,
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    reset_epoch BIGINT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object'),
    UNIQUE (session_id, request_id, reset_epoch)
);
CREATE INDEX user_inputs_latest ON user_inputs (session_id, reset_epoch, sequence DESC);

CREATE TABLE write_authorizations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    reset_epoch BIGINT NOT NULL,
    source_message_id TEXT NOT NULL REFERENCES user_inputs(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object'),
    consumed_by_request_id TEXT,
    UNIQUE (session_id, reset_epoch, source_message_id)
);

CREATE TABLE meal_operations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    reset_epoch BIGINT NOT NULL,
    meal_id TEXT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object')
);
CREATE INDEX meal_operations_session ON meal_operations (session_id, reset_epoch);

CREATE TABLE meal_entities (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    reset_epoch BIGINT NOT NULL,
    meal_id TEXT NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    head_operation_id TEXT,
    PRIMARY KEY (session_id, reset_epoch, meal_id)
);

CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    reset_epoch BIGINT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object'),
    UNIQUE (session_id, request_id)
);

CREATE TABLE readiness_checks (
    check_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    reset_epoch BIGINT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object')
);
CREATE INDEX readiness_checks_session ON readiness_checks (session_id, reset_epoch);
