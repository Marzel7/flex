PRAGMA foreign_keys=ON;

CREATE TABLE retention_manifest (
    retention_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    f9_contract_digest TEXT NOT NULL,
    source_engineering_revision TEXT NOT NULL,
    vocabulary_json TEXT NOT NULL,
    vocabulary_digest TEXT NOT NULL,
    logical_capture_sequence INTEGER NOT NULL CHECK(logical_capture_sequence >= 0),
    manifest_digest TEXT NOT NULL UNIQUE
);
CREATE TABLE operation_cohort (
    retention_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    operation_id TEXT NOT NULL,
    PRIMARY KEY(retention_id, position),
    UNIQUE(retention_id, operation_id),
    FOREIGN KEY(retention_id) REFERENCES retention_manifest(retention_id)
);
CREATE TABLE evaluation_summaries (
    retention_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    PRIMARY KEY(retention_id, operation_id),
    FOREIGN KEY(retention_id, operation_id) REFERENCES operation_cohort(retention_id, operation_id)
);
CREATE TABLE normalized_runtime_projections (
    retention_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    PRIMARY KEY(retention_id, operation_id, input_digest),
    FOREIGN KEY(retention_id, operation_id) REFERENCES operation_cohort(retention_id, operation_id)
);
CREATE TABLE candidate_payloads (
    retention_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    PRIMARY KEY(retention_id, candidate_id),
    FOREIGN KEY(retention_id) REFERENCES retention_manifest(retention_id)
);
CREATE TABLE nomination_dispositions (
    retention_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    nomination_state TEXT NOT NULL CHECK(nomination_state IN ('PROPOSED','SUPPORTED')),
    supporting_identity_digest TEXT NOT NULL,
    reviewer_class TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    reviewed_sequence INTEGER NOT NULL CHECK(reviewed_sequence >= 0),
    authority_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    PRIMARY KEY(retention_id, review_id),
    UNIQUE(retention_id, candidate_id),
    UNIQUE(retention_id, group_id),
    UNIQUE(retention_id, reviewed_sequence),
    FOREIGN KEY(retention_id, candidate_id) REFERENCES candidate_payloads(retention_id, candidate_id)
);
CREATE TABLE nomination_disposition_members (
    retention_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    operation_id TEXT NOT NULL,
    PRIMARY KEY(retention_id, review_id, position),
    UNIQUE(retention_id, review_id, operation_id),
    FOREIGN KEY(retention_id, review_id) REFERENCES nomination_dispositions(retention_id, review_id),
    FOREIGN KEY(retention_id, operation_id) REFERENCES operation_cohort(retention_id, operation_id)
);

CREATE TRIGGER retention_manifest_no_update BEFORE UPDATE ON retention_manifest BEGIN SELECT RAISE(ABORT, 'immutable retention_manifest'); END;
CREATE TRIGGER retention_manifest_no_delete BEFORE DELETE ON retention_manifest BEGIN SELECT RAISE(ABORT, 'immutable retention_manifest'); END;
CREATE TRIGGER operation_cohort_no_update BEFORE UPDATE ON operation_cohort BEGIN SELECT RAISE(ABORT, 'immutable operation_cohort'); END;
CREATE TRIGGER operation_cohort_no_delete BEFORE DELETE ON operation_cohort BEGIN SELECT RAISE(ABORT, 'immutable operation_cohort'); END;
CREATE TRIGGER evaluation_summaries_no_update BEFORE UPDATE ON evaluation_summaries BEGIN SELECT RAISE(ABORT, 'immutable evaluation_summaries'); END;
CREATE TRIGGER evaluation_summaries_no_delete BEFORE DELETE ON evaluation_summaries BEGIN SELECT RAISE(ABORT, 'immutable evaluation_summaries'); END;
CREATE TRIGGER normalized_runtime_projections_no_update BEFORE UPDATE ON normalized_runtime_projections BEGIN SELECT RAISE(ABORT, 'immutable normalized_runtime_projections'); END;
CREATE TRIGGER normalized_runtime_projections_no_delete BEFORE DELETE ON normalized_runtime_projections BEGIN SELECT RAISE(ABORT, 'immutable normalized_runtime_projections'); END;
CREATE TRIGGER candidate_payloads_no_update BEFORE UPDATE ON candidate_payloads BEGIN SELECT RAISE(ABORT, 'immutable candidate_payloads'); END;
CREATE TRIGGER candidate_payloads_no_delete BEFORE DELETE ON candidate_payloads BEGIN SELECT RAISE(ABORT, 'immutable candidate_payloads'); END;
CREATE TRIGGER nomination_dispositions_no_update BEFORE UPDATE ON nomination_dispositions BEGIN SELECT RAISE(ABORT, 'immutable nomination_dispositions'); END;
CREATE TRIGGER nomination_dispositions_no_delete BEFORE DELETE ON nomination_dispositions BEGIN SELECT RAISE(ABORT, 'immutable nomination_dispositions'); END;
CREATE TRIGGER nomination_disposition_members_no_update BEFORE UPDATE ON nomination_disposition_members BEGIN SELECT RAISE(ABORT, 'immutable nomination_disposition_members'); END;
CREATE TRIGGER nomination_disposition_members_no_delete BEFORE DELETE ON nomination_disposition_members BEGIN SELECT RAISE(ABORT, 'immutable nomination_disposition_members'); END;
