PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS evidence_schema_metadata (
    schema_version INTEGER PRIMARY KEY,
    installed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_envelopes (
    envelope_id TEXT PRIMARY KEY,
    observed_at INTEGER NOT NULL,
    acquired_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    evidence_digest TEXT NOT NULL UNIQUE,
    replay_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    payload_type TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    appended_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_provenance (
    envelope_id TEXT PRIMARY KEY,
    provider_request_id TEXT,
    rpc_verification_state TEXT NOT NULL,
    acquisition_method TEXT NOT NULL,
    source_metadata_json TEXT NOT NULL,
    FOREIGN KEY(envelope_id) REFERENCES evidence_envelopes(envelope_id)
);

CREATE TABLE IF NOT EXISTS artifact_references (
    envelope_id TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    compression TEXT NOT NULL,
    PRIMARY KEY(envelope_id, artifact_digest),
    FOREIGN KEY(envelope_id) REFERENCES evidence_envelopes(envelope_id)
);

CREATE TABLE IF NOT EXISTS writer_receipts (
    message_id TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL,
    committed_at INTEGER NOT NULL,
    FOREIGN KEY(envelope_id) REFERENCES evidence_envelopes(envelope_id)
);

CREATE TRIGGER IF NOT EXISTS evidence_envelopes_no_update BEFORE UPDATE ON evidence_envelopes
BEGIN SELECT RAISE(ABORT, 'immutable evidence cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS evidence_envelopes_no_delete BEFORE DELETE ON evidence_envelopes
BEGIN SELECT RAISE(ABORT, 'immutable evidence cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS evidence_provenance_no_update BEFORE UPDATE ON evidence_provenance
BEGIN SELECT RAISE(ABORT, 'immutable provenance cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS evidence_provenance_no_delete BEFORE DELETE ON evidence_provenance
BEGIN SELECT RAISE(ABORT, 'immutable provenance cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS artifact_references_no_update BEFORE UPDATE ON artifact_references
BEGIN SELECT RAISE(ABORT, 'immutable artifact reference cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS artifact_references_no_delete BEFORE DELETE ON artifact_references
BEGIN SELECT RAISE(ABORT, 'immutable artifact reference cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS writer_receipts_no_update BEFORE UPDATE ON writer_receipts
BEGIN SELECT RAISE(ABORT, 'immutable receipt cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS writer_receipts_no_delete BEFORE DELETE ON writer_receipts
BEGIN SELECT RAISE(ABORT, 'immutable receipt cannot be deleted'); END;
