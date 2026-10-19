PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS operational_evolution_snapshots (
    evolution_snapshot_id TEXT PRIMARY KEY,
    evolution_version TEXT NOT NULL,
    previous_landscape_snapshot_id TEXT NOT NULL,
    current_landscape_snapshot_id TEXT NOT NULL,
    change_snapshot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_evolution_records (
    record_id TEXT PRIMARY KEY,
    evolution_snapshot_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS evolution_snapshots_no_update BEFORE UPDATE ON operational_evolution_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable evolution snapshot cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS evolution_snapshots_no_delete BEFORE DELETE ON operational_evolution_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable evolution snapshot cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS evolution_records_no_update BEFORE UPDATE ON operational_evolution_records
BEGIN SELECT RAISE(ABORT, 'immutable evolution record cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS evolution_records_no_delete BEFORE DELETE ON operational_evolution_records
BEGIN SELECT RAISE(ABORT, 'immutable evolution record cannot be deleted'); END;
