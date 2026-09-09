"""Every table HeadOfContext owns, in one place (ADR 0022).

The migration ledger (`db.migrations`) applies these; each store keeps a `schema` attribute
pointing here for library use and tests. Editing an applied statement is never the way to
change a table: add a migration.
"""

from __future__ import annotations

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq               BIGSERIAL PRIMARY KEY,
    event_id          TEXT NOT NULL UNIQUE,
    kind              TEXT NOT NULL,
    ts                TIMESTAMPTZ NOT NULL,
    subject           TEXT NOT NULL,
    actor             TEXT NOT NULL,
    delegation_depth  INTEGER NOT NULL,
    action            TEXT NOT NULL,
    resource          TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    reason            TEXT NOT NULL,
    engine_latency_ms DOUBLE PRECISION NOT NULL,
    token_fingerprint TEXT,
    args_hash         TEXT,
    decision_id       TEXT,
    payload           TEXT NOT NULL,
    prev_hash         TEXT NOT NULL,
    hash              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_subject_ts ON audit_events (subject, ts);
CREATE INDEX IF NOT EXISTS audit_events_decision ON audit_events (decision_id);

CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
"""

REVOCATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_revocations (
    revocation_id TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,
    revoked_at    TIMESTAMPTZ NOT NULL
);
"""

APPROVALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id        TEXT PRIMARY KEY,
    subject           TEXT NOT NULL,
    actor             TEXT NOT NULL,
    delegation_depth  INTEGER NOT NULL,
    tool              TEXT NOT NULL,
    args_hash         TEXT NOT NULL,
    decision_id       TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    expires_at        TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL,
    resolved_by       TEXT,
    resolved_at       TIMESTAMPTZ,
    resolution_reason TEXT,
    consumed_at       TIMESTAMPTZ,
    approval_reason   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS approval_requests_pending ON approval_requests (status, subject);
"""

MANDATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
    mandate_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    agent TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    max_token_ttl_seconds INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT
);
CREATE INDEX IF NOT EXISTS mandates_subject_idx ON mandates (subject, created_at);
"""

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_provenance (
    memory_id    TEXT PRIMARY KEY,
    written_for  TEXT NOT NULL,
    written_by   TEXT NOT NULL,
    derived_from TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    backend      TEXT NOT NULL,
    backend_ref  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (backend, backend_ref)
);
CREATE INDEX IF NOT EXISTS memory_provenance_written_for ON memory_provenance (written_for);
"""

CONNECTOR_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_state_v2 (
    namespace  TEXT NOT NULL,
    connector  TEXT NOT NULL,
    synced_at  TIMESTAMPTZ,
    tuples     TEXT NOT NULL,
    PRIMARY KEY (namespace, connector)
);
"""

MEMORY_CONTENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_content (
    namespace   TEXT NOT NULL,
    backend_ref TEXT NOT NULL,
    memory_id   TEXT NOT NULL,
    content     TEXT NOT NULL,
    tsv         TSVECTOR NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, backend_ref)
);
CREATE INDEX IF NOT EXISTS memory_content_tsv ON memory_content USING GIN (tsv);
-- Embeddings only where pgvector exists: the migration must not fail on a plain PostgreSQL.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS memory_embedding (
            namespace   TEXT NOT NULL,
            backend_ref TEXT NOT NULL,
            embedding   vector NOT NULL,
            PRIMARY KEY (namespace, backend_ref),
            FOREIGN KEY (namespace, backend_ref)
                REFERENCES memory_content (namespace, backend_ref) ON DELETE CASCADE
        );
    END IF;
END $$;
"""


AUDIT_TRUNCATE_GUARD_SCHEMA = """
DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
"""

__all__ = [
    "APPROVALS_SCHEMA",
    "AUDIT_SCHEMA",
    "AUDIT_TRUNCATE_GUARD_SCHEMA",
    "CONNECTOR_STATE_SCHEMA",
    "LEDGER_SCHEMA",
    "MANDATES_SCHEMA",
    "MEMORY_CONTENT_SCHEMA",
    "REVOCATIONS_SCHEMA",
]
