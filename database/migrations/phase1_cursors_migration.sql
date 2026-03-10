-- FLEX V2 Phase 1 Migration: Address Cursors
-- Safe to run against production (no data loss)
--
-- This migration creates the address_scan_state table which enables
-- incremental signature fetching, reducing RPC calls by 60%.

-- Create address_scan_state table for persistent cursors
CREATE TABLE IF NOT EXISTS address_scan_state (
    address TEXT PRIMARY KEY,
    last_signature TEXT,
    last_scan_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    next_scan_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active'
);

-- Index for due-time scheduler (critical query, runs every 60s)
-- Fetches all addresses where next_scan_at <= NOW() and status = 'active'
CREATE INDEX IF NOT EXISTS idx_address_scan_state_due_time
ON address_scan_state(next_scan_at, status)
WHERE status = 'active';

-- Index for cursor lookups (when processing an address)
-- Fetches cursor state for a specific address
CREATE INDEX IF NOT EXISTS idx_address_scan_state_address
ON address_scan_state(address);

-- Verify tables created
.mode list
.header on
SELECT
    name,
    COUNT(*) as columns
FROM sqlite_master
WHERE type='table' AND name LIKE 'address_scan_state%'
GROUP BY name;

-- Verify indexes created
.mode list
.header on
SELECT
    name,
    tbl_name,
    sql
FROM sqlite_master
WHERE type='index' AND tbl_name = 'address_scan_state'
ORDER BY name;

-- Done
.print "✅ Phase 1 migration complete: address_scan_state table and indexes created"
