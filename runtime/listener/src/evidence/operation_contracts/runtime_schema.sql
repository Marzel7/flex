PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS operation_contract_versions (
    contract_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    contract_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    registered_at INTEGER NOT NULL,
    PRIMARY KEY(contract_id, contract_version),
    UNIQUE(contract_digest)
);

CREATE TABLE IF NOT EXISTS operation_contract_activation_events (
    event_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS behaviour_observations (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS topology_revisions (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS detector_inputs (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS detector_results (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_recommendations (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_runtime_references (
    output_type TEXT NOT NULL,
    output_id TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    PRIMARY KEY(output_type, output_id, reference_type, reference_id)
);

CREATE TRIGGER IF NOT EXISTS operation_contract_versions_no_update BEFORE UPDATE ON operation_contract_versions
BEGIN SELECT RAISE(ABORT, 'immutable Operation Contract cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS operation_contract_versions_no_delete BEFORE DELETE ON operation_contract_versions
BEGIN SELECT RAISE(ABORT, 'immutable Operation Contract cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS operation_contract_activation_events_no_update BEFORE UPDATE ON operation_contract_activation_events
BEGIN SELECT RAISE(ABORT, 'immutable activation event cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS operation_contract_activation_events_no_delete BEFORE DELETE ON operation_contract_activation_events
BEGIN SELECT RAISE(ABORT, 'immutable activation event cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS behaviour_observations_no_update BEFORE UPDATE ON behaviour_observations
BEGIN SELECT RAISE(ABORT, 'immutable Behaviour Observation cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS behaviour_observations_no_delete BEFORE DELETE ON behaviour_observations
BEGIN SELECT RAISE(ABORT, 'immutable Behaviour Observation cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS topology_revisions_no_update BEFORE UPDATE ON topology_revisions
BEGIN SELECT RAISE(ABORT, 'immutable Topology Revision cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS topology_revisions_no_delete BEFORE DELETE ON topology_revisions
BEGIN SELECT RAISE(ABORT, 'immutable Topology Revision cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS detector_inputs_no_update BEFORE UPDATE ON detector_inputs
BEGIN SELECT RAISE(ABORT, 'immutable Detector Input cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS detector_inputs_no_delete BEFORE DELETE ON detector_inputs
BEGIN SELECT RAISE(ABORT, 'immutable Detector Input cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS detector_results_no_update BEFORE UPDATE ON detector_results
BEGIN SELECT RAISE(ABORT, 'immutable Detector Result cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS detector_results_no_delete BEFORE DELETE ON detector_results
BEGIN SELECT RAISE(ABORT, 'immutable Detector Result cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS lifecycle_recommendations_no_update BEFORE UPDATE ON lifecycle_recommendations
BEGIN SELECT RAISE(ABORT, 'immutable Lifecycle Recommendation cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS lifecycle_recommendations_no_delete BEFORE DELETE ON lifecycle_recommendations
BEGIN SELECT RAISE(ABORT, 'immutable Lifecycle Recommendation cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS operation_runtime_references_no_update BEFORE UPDATE ON operation_runtime_references
BEGIN SELECT RAISE(ABORT, 'immutable runtime reference cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS operation_runtime_references_no_delete BEFORE DELETE ON operation_runtime_references
BEGIN SELECT RAISE(ABORT, 'immutable runtime reference cannot be deleted'); END;
