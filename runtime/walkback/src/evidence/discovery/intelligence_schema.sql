PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS motif_intelligence (
    intelligence_id TEXT PRIMARY KEY,
    motif_id TEXT NOT NULL,
    intelligence_version TEXT NOT NULL,
    replay_version TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS motif_intelligence_references (
    intelligence_id TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    PRIMARY KEY(intelligence_id, reference_type, reference_id)
);

CREATE TABLE IF NOT EXISTS motif_rankings (
    ranking_id TEXT PRIMARY KEY,
    intelligence_version TEXT NOT NULL,
    ordered_intelligence_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS motif_intelligence_no_update BEFORE UPDATE ON motif_intelligence
BEGIN SELECT RAISE(ABORT, 'immutable motif intelligence cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS motif_intelligence_no_delete BEFORE DELETE ON motif_intelligence
BEGIN SELECT RAISE(ABORT, 'immutable motif intelligence cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS motif_intelligence_references_no_update BEFORE UPDATE ON motif_intelligence_references
BEGIN SELECT RAISE(ABORT, 'immutable motif intelligence reference cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS motif_intelligence_references_no_delete BEFORE DELETE ON motif_intelligence_references
BEGIN SELECT RAISE(ABORT, 'immutable motif intelligence reference cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS motif_rankings_no_update BEFORE UPDATE ON motif_rankings
BEGIN SELECT RAISE(ABORT, 'immutable motif ranking cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS motif_rankings_no_delete BEFORE DELETE ON motif_rankings
BEGIN SELECT RAISE(ABORT, 'immutable motif ranking cannot be deleted'); END;
