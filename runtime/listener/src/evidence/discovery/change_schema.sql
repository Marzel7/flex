PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS operational_landscape_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    snapshot_version TEXT NOT NULL,
    observation_boundary INTEGER,
    input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_change_snapshots (
    change_snapshot_id TEXT PRIMARY KEY,
    change_version TEXT NOT NULL,
    previous_snapshot_id TEXT NOT NULL,
    current_snapshot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_change_records (
    record_id TEXT PRIMARY KEY,
    change_snapshot_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS landscape_snapshots_no_update BEFORE UPDATE ON operational_landscape_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable landscape snapshot cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS landscape_snapshots_no_delete BEFORE DELETE ON operational_landscape_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable landscape snapshot cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS change_snapshots_no_update BEFORE UPDATE ON operational_change_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable change snapshot cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS change_snapshots_no_delete BEFORE DELETE ON operational_change_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable change snapshot cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS change_records_no_update BEFORE UPDATE ON operational_change_records
BEGIN SELECT RAISE(ABORT, 'immutable change record cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS change_records_no_delete BEFORE DELETE ON operational_change_records
BEGIN SELECT RAISE(ABORT, 'immutable change record cannot be deleted'); END;
