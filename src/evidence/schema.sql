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

CREATE TABLE IF NOT EXISTS normalized_evidence_records (
    evidence_id TEXT PRIMARY KEY,
    logical_fact_id TEXT NOT NULL,
    fact_family TEXT NOT NULL,
    fact_schema_version TEXT NOT NULL,
    chain TEXT NOT NULL,
    network TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    raw_artifact_digest TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    acquired_at INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_request_id TEXT,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    replay_version TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    provenance_quality TEXT NOT NULL,
    corrects_evidence_id TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(evidence_id),
    FOREIGN KEY(raw_artifact_digest) REFERENCES evidence_envelopes(evidence_digest)
);

CREATE INDEX IF NOT EXISTS normalized_evidence_logical_fact
ON normalized_evidence_records(logical_fact_id, fact_family);

CREATE TABLE IF NOT EXISTS normalized_evidence_provenance (
    evidence_id TEXT NOT NULL,
    provider_request_id TEXT NOT NULL,
    endpoint_method TEXT NOT NULL,
    request_parameters_digest TEXT NOT NULL,
    upstream_dependency TEXT,
    acquisition_path TEXT NOT NULL,
    cache_source TEXT NOT NULL,
    dependency_group TEXT NOT NULL,
    parent_evidence_ids_json TEXT NOT NULL,
    PRIMARY KEY(evidence_id, provider_request_id),
    FOREIGN KEY(evidence_id) REFERENCES normalized_evidence_records(evidence_id)
);

CREATE TABLE IF NOT EXISTS normalization_status (
    envelope_id TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    fact_schema_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'PENDING','RUNNING','COMPLETE','FAILED','UNSUPPORTED','RETRY'
    )),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    artifact_representation TEXT NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    fact_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(envelope_id, parser_id, parser_version, fact_schema_version),
    FOREIGN KEY(envelope_id) REFERENCES evidence_envelopes(envelope_id)
);

CREATE TABLE IF NOT EXISTS primitive_observations (
    primitive_id TEXT PRIMARY KEY,
    primitive_type TEXT NOT NULL,
    primitive_version TEXT NOT NULL,
    subjects_json TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    window_start INTEGER,
    window_end INTEGER,
    output_payload_json TEXT NOT NULL,
    output_digest TEXT NOT NULL,
    quality_state TEXT NOT NULL CHECK(quality_state IN (
        'PROVEN','DISPROVEN','INCOMPLETE','CONFLICTING','UNVERIFIABLE'
    )),
    missing_inputs_json TEXT NOT NULL,
    failure_state TEXT,
    generated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS primitive_evidence_inputs (
    primitive_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(primitive_id, evidence_id),
    FOREIGN KEY(primitive_id) REFERENCES primitive_observations(primitive_id),
    FOREIGN KEY(evidence_id) REFERENCES normalized_evidence_records(evidence_id)
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
CREATE TRIGGER IF NOT EXISTS normalized_evidence_records_no_update BEFORE UPDATE ON normalized_evidence_records
BEGIN SELECT RAISE(ABORT, 'immutable normalized evidence cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS normalized_evidence_records_no_delete BEFORE DELETE ON normalized_evidence_records
BEGIN SELECT RAISE(ABORT, 'immutable normalized evidence cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS normalized_evidence_provenance_no_update BEFORE UPDATE ON normalized_evidence_provenance
BEGIN SELECT RAISE(ABORT, 'immutable normalized provenance cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS normalized_evidence_provenance_no_delete BEFORE DELETE ON normalized_evidence_provenance
BEGIN SELECT RAISE(ABORT, 'immutable normalized provenance cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS primitive_observations_no_update BEFORE UPDATE ON primitive_observations
BEGIN SELECT RAISE(ABORT, 'immutable primitive cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS primitive_observations_no_delete BEFORE DELETE ON primitive_observations
BEGIN SELECT RAISE(ABORT, 'immutable primitive cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS primitive_evidence_inputs_no_update BEFORE UPDATE ON primitive_evidence_inputs
BEGIN SELECT RAISE(ABORT, 'immutable primitive input cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS primitive_evidence_inputs_no_delete BEFORE DELETE ON primitive_evidence_inputs
BEGIN SELECT RAISE(ABORT, 'immutable primitive input cannot be deleted'); END;
