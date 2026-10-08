-- PREPARED ONLY. Do not execute outside a separately approved monitor-store
-- maintenance window with the monitor writer quiesced and a verified backup.
-- SQLite executes these additive schema changes atomically when the enclosing
-- transaction commits; rollback leaves the prior schema unchanged.
BEGIN IMMEDIATE;
ALTER TABLE operation_monitor_facts ADD COLUMN terminal_coverage_state TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE operation_monitor_facts ADD COLUMN terminal_coverage_provenance_digest TEXT;
ALTER TABLE operation_monitor_facts ADD COLUMN terminal_coverage_gap_digest TEXT;
ALTER TABLE operation_monitor_facts ADD COLUMN terminal_coverage_updated_at INTEGER;
COMMIT;
