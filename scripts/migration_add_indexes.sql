-- Phase 9A: Index Migration Script (Idempotent)
--
-- This migration adds performance indexes to the monitoring schema.
-- All indexes use IF NOT EXISTS for idempotent operation.
--
-- Target Query Performance:
-- - Alert queries (A1-A3): 10-50x improvement
-- - Network queries (B1-B4): 5-20x improvement
-- - Biggest movers (C): 3-10x improvement
--
-- Safe to run multiple times. No data loss or schema changes.

-- ==========================================================================
-- ALERT INDEXES (High Priority)
-- ==========================================================================

-- Index for created_at ordering (all alert queries)
CREATE INDEX IF NOT EXISTS idx_network_alerts_created_at_desc
  ON network_alerts(created_at DESC);

-- Compound index for ACTIVE view filtering: acknowledged=0 + ordering by created_at
CREATE INDEX IF NOT EXISTS idx_network_alerts_acknowledged_created
  ON network_alerts(acknowledged, created_at DESC);

-- Index for escalated alerts filtering
CREATE INDEX IF NOT EXISTS idx_network_alerts_escalated_created
  ON network_alerts(is_escalated, created_at DESC);

-- Compound index for severity + type filtering (used in filtered queries)
CREATE INDEX IF NOT EXISTS idx_network_alerts_severity_type_created
  ON network_alerts(severity, alert_type, created_at DESC);

-- Index for suppression expiry checks
CREATE INDEX IF NOT EXISTS idx_network_alerts_suppressed_until
  ON network_alerts(suppressed_until);

-- ==========================================================================
-- NETWORK SCORE INDEXES (Medium Priority)
-- ==========================================================================

-- Index for risk_band filtering (band filter queries)
CREATE INDEX IF NOT EXISTS idx_network_scores_risk_band
  ON network_scores(risk_band, smoothed_score DESC);

-- Index for trend-based sorting
CREATE INDEX IF NOT EXISTS idx_network_scores_trend
  ON network_scores(stability_trend DESC);

-- ==========================================================================
-- SCORE HISTORY INDEXES (Medium Priority)
-- ==========================================================================

-- Index for build_version-based queries (biggest movers, subqueries)
CREATE INDEX IF NOT EXISTS idx_network_score_history_build_version
  ON network_score_history(build_version DESC);

-- Compound index for network + build lookups (delta calculations)
CREATE INDEX IF NOT EXISTS idx_network_score_history_network_build
  ON network_score_history(network_name, build_version DESC);

-- ==========================================================================
-- ANALYSIS COMMENTS
-- ==========================================================================

-- These indexes were added based on EXPLAIN QUERY PLAN analysis (QUERY_PLAN_REPORT.md).
--
-- Alert Queries (A1-A3):
--   - Multiple WHERE conditions filtering on severity, alert_type, acknowledged, is_escalated
--   - All queries ORDER BY created_at DESC
--   - Impact: Compound indexes eliminate full table scans
--
-- Network Queries (B1-B4):
--   - JOIN between network_scores and networks_release on network_name
--   - Filtering on risk_band (band filter query)
--   - Ordering by smoothed_score, stability_trend, or CASE statements
--   - Impact: Individual indexes on network_scores speed up filtering and sorting
--
-- Score History Queries (C):
--   - Self-join on network_score_history for delta calculation
--   - Subquery to find MAX(build_version)
--   - Impact: build_version index speeds up subquery, network+build index speeds up delta join
--
-- Production Safe:
--   - All indexes are additive (no modifications to existing data)
--   - Idempotent (IF NOT EXISTS prevents re-creation)
--   - No locking required during creation in write-idle periods
--   - Can be deployed without downtime

-- ==========================================================================
-- Verification Queries
-- ==========================================================================

-- After running this migration, verify indexes were created:
--
--   SELECT name, tbl_name, sql FROM sqlite_master
--   WHERE type='index'
--   AND tbl_name IN ('network_alerts', 'network_scores', 'network_score_history')
--   ORDER BY tbl_name, name;
--
-- Expected: 10 custom indexes created (1 PRIMARY on each key table, remainder as listed above)
