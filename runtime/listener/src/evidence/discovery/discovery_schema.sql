PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS discovery_candidates (
    candidate_id TEXT PRIMARY KEY,
    discovery_version TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK(lifecycle IN ('OBSERVED','RECURRING_PATTERN','INVESTIGATE','DISMISSED')),
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    generated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_candidate_references (
    candidate_id TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    PRIMARY KEY(candidate_id, reference_type, reference_id)
);

CREATE TABLE IF NOT EXISTS discovery_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS discovery_candidates_no_update BEFORE UPDATE ON discovery_candidates
BEGIN SELECT RAISE(ABORT, 'immutable discovery candidate cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS discovery_candidates_no_delete BEFORE DELETE ON discovery_candidates
BEGIN SELECT RAISE(ABORT, 'immutable discovery candidate cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS discovery_candidate_references_no_update BEFORE UPDATE ON discovery_candidate_references
BEGIN SELECT RAISE(ABORT, 'immutable discovery reference cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS discovery_candidate_references_no_delete BEFORE DELETE ON discovery_candidate_references
BEGIN SELECT RAISE(ABORT, 'immutable discovery reference cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS discovery_lifecycle_events_no_update BEFORE UPDATE ON discovery_lifecycle_events
BEGIN SELECT RAISE(ABORT, 'immutable discovery lifecycle cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS discovery_lifecycle_events_no_delete BEFORE DELETE ON discovery_lifecycle_events
BEGIN SELECT RAISE(ABORT, 'immutable discovery lifecycle cannot be deleted'); END;
