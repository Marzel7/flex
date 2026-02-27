-- ============================================================================
-- MIGRATION: Phase H.1 Build Tick Enhancement
-- ============================================================================
-- Date: February 27, 2026
-- Schema Version: 11 (from 10)
-- Purpose: Add build_tick global time axis for score history snapshots
--
-- Problem Solved:
-- Current H.1 uses build_version (structural) as history key, causing sparse
-- history (only changed networks recorded). This breaks stability/trend
-- calculations which need continuous history for all networks.
--
-- Solution:
-- Introduce build_tick as global per-run time axis. Snapshot ALL networks
-- every build to network_score_history(network_name, build_tick).
-- ============================================================================

-- ============================================================================
-- STEP 1: Add build_tick column (nullable for backward compatibility)
-- ============================================================================
-- build_tick is the global per-run time axis
-- Old rows will have NULL build_tick; new rows will have build_tick values
ALTER TABLE network_score_history ADD COLUMN build_tick INTEGER;

-- ============================================================================
-- STEP 2: Add network_version to preserve Phase C structural version
-- ============================================================================
ALTER TABLE network_score_history ADD COLUMN network_version INTEGER;

-- ============================================================================
-- STEP 3: Create unique index on (network_name, build_tick)
-- ============================================================================
-- Enforces (network_name, build_tick) uniqueness for new snapshots
-- Old data with NULL build_tick will not conflict with new ticks
CREATE UNIQUE INDEX IF NOT EXISTS uq_score_history_network_tick
ON network_score_history(network_name, build_tick);

-- ============================================================================
-- STEP 4: Optional performance indexes (recommended)
-- ============================================================================
-- Index for queries on build_tick alone
CREATE INDEX IF NOT EXISTS idx_score_history_tick
ON network_score_history(build_tick);

-- Index for time-series queries on network + descending tick
CREATE INDEX IF NOT EXISTS idx_score_history_network_tick_desc
ON network_score_history(network_name, build_tick DESC);

-- ============================================================================
-- VERIFICATION QUERIES (run after migration)
-- ============================================================================
-- Check columns added
-- PRAGMA table_info(network_score_history);
-- Expected: build_tick (INTEGER) and network_version (INTEGER) columns exist
--
-- Check indexes created
-- SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='network_score_history';
-- Expected: uq_score_history_network_tick, idx_score_history_tick,
--           idx_score_history_network_tick_desc all present
--
-- ============================================================================
-- NOTES
-- ============================================================================
-- 1. ALTER TABLE ADD COLUMN is idempotent in SQLite (fails silently if exists)
--    but we wrap in Python to catch and report cleanly.
--
-- 2. Columns are nullable initially. Phase H.1 fills build_tick on each run.
--
-- 3. This does NOT break existing schema or code:
--    - Phase C unchanged (build_version still in networks_release)
--    - Phase H.1 will populate both build_version and build_tick in history
--
-- 4. Existing data in network_score_history remains untouched.
--    New builds will record both old and new columns.
--
-- 5. For v2.0 or later, consider:
--    - Backfill build_tick for existing history rows (if needed)
--    - Rename build_version to network_version everywhere for clarity
--    - Make build_tick NOT NULL with default
