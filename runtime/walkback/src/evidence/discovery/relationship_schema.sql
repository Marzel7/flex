PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS motif_relationship_snapshots (
    relationship_snapshot_id TEXT PRIMARY KEY,
    relationship_version TEXT NOT NULL,
    replay_version TEXT NOT NULL,
    landscape_snapshot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS motif_relationship_observations (
    observation_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    relationship_snapshot_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS motif_relationship_evolution_snapshots (
    evolution_snapshot_id TEXT PRIMARY KEY,
    evolution_version TEXT NOT NULL,
    previous_relationship_snapshot_id TEXT NOT NULL,
    current_relationship_snapshot_id TEXT NOT NULL,
    operational_evolution_snapshot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS relationship_snapshots_no_update BEFORE UPDATE ON motif_relationship_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable relationship snapshot cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS relationship_snapshots_no_delete BEFORE DELETE ON motif_relationship_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable relationship snapshot cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS relationship_observations_no_update BEFORE UPDATE ON motif_relationship_observations
BEGIN SELECT RAISE(ABORT, 'immutable relationship observation cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS relationship_observations_no_delete BEFORE DELETE ON motif_relationship_observations
BEGIN SELECT RAISE(ABORT, 'immutable relationship observation cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS relationship_evolution_no_update BEFORE UPDATE ON motif_relationship_evolution_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable relationship evolution cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS relationship_evolution_no_delete BEFORE DELETE ON motif_relationship_evolution_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable relationship evolution cannot be deleted'); END;
