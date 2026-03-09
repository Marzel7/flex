-- RPC Metrics Schema Migration: Add Real Credits Savings Tracking
-- Date: 2026-03-05
-- Purpose: Track actual credits saved by cache hits (not estimates)

-- ============================================================================
-- Add columns for cache savings tracking
-- ============================================================================

-- Add cache_action column (skip, refresh, full_scan, none)
ALTER TABLE rpc_metrics ADD COLUMN cache_action TEXT DEFAULT 'none';

-- Add credits_saved column (actual credits avoided)
ALTER TABLE rpc_metrics ADD COLUMN credits_saved INTEGER DEFAULT 0;

-- ============================================================================
-- Add index for efficient cache analysis
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_rpc_metrics_cache
ON rpc_metrics(cache_action, recorded_at DESC);

-- ============================================================================
-- Optional: Create view for quick cache hit analysis
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_cache_savings_24h AS
SELECT
    DATE(recorded_at) as date,
    cache_action,
    COUNT(*) as hit_count,
    SUM(credits_saved) as total_credits_saved,
    ROUND(AVG(credits_saved), 1) as avg_credits_saved,
    ROUND(SUM(CAST(credits_saved > 0 AS INTEGER)) * 100.0 / COUNT(*), 1) as pct_with_savings
FROM rpc_metrics
WHERE recorded_at >= datetime('now', '-24 hours')
  AND cache_action IN ('skip', 'refresh', 'full_scan')
GROUP BY DATE(recorded_at), cache_action
ORDER BY date DESC, hit_count DESC;

CREATE VIEW IF NOT EXISTS v_cumulative_cache_savings AS
SELECT
    'all_time' as period,
    COUNT(*) as total_cache_hits,
    SUM(credits_saved) as total_credits_saved,
    COUNT(CASE WHEN cache_action = 'skip' THEN 1 END) as skip_count,
    COUNT(CASE WHEN cache_action = 'refresh' THEN 1 END) as refresh_count,
    SUM(CASE WHEN cache_action = 'skip' THEN credits_saved ELSE 0 END) as skip_savings,
    SUM(CASE WHEN cache_action = 'refresh' THEN credits_saved ELSE 0 END) as refresh_savings
FROM rpc_metrics
WHERE cache_action IN ('skip', 'refresh')
UNION ALL
SELECT
    'last_24h',
    COUNT(*),
    SUM(credits_saved),
    COUNT(CASE WHEN cache_action = 'skip' THEN 1 END),
    COUNT(CASE WHEN cache_action = 'refresh' THEN 1 END),
    SUM(CASE WHEN cache_action = 'skip' THEN credits_saved ELSE 0 END),
    SUM(CASE WHEN cache_action = 'refresh' THEN credits_saved ELSE 0 END)
FROM rpc_metrics
WHERE cache_action IN ('skip', 'refresh')
  AND recorded_at >= datetime('now', '-24 hours')
UNION ALL
SELECT
    'last_7d',
    COUNT(*),
    SUM(credits_saved),
    COUNT(CASE WHEN cache_action = 'skip' THEN 1 END),
    COUNT(CASE WHEN cache_action = 'refresh' THEN 1 END),
    SUM(CASE WHEN cache_action = 'skip' THEN credits_saved ELSE 0 END),
    SUM(CASE WHEN cache_action = 'refresh' THEN credits_saved ELSE 0 END)
FROM rpc_metrics
WHERE cache_action IN ('skip', 'refresh')
  AND recorded_at >= datetime('now', '-7 days');

CREATE VIEW IF NOT EXISTS v_cache_savings_by_section AS
SELECT
    section,
    cache_action,
    COUNT(*) as hit_count,
    SUM(credits_saved) as total_saved,
    ROUND(AVG(credits_saved), 1) as avg_saved,
    ROUND(MIN(credits_saved), 1) as min_saved,
    ROUND(MAX(credits_saved), 1) as max_saved
FROM rpc_metrics
WHERE cache_action IN ('skip', 'refresh')
  AND recorded_at >= datetime('now', '-30 days')
GROUP BY section, cache_action
ORDER BY section, total_saved DESC;

-- ============================================================================
-- Verification queries (run after migration)
-- ============================================================================

-- Verify columns were added:
-- SELECT sql FROM sqlite_master WHERE type='table' AND name='rpc_metrics';

-- Check current state:
-- SELECT COUNT(*) as total_records,
--        COUNT(CASE WHEN cache_action != 'none' THEN 1 END) as with_cache_data
-- FROM rpc_metrics;

-- View cache savings:
-- SELECT * FROM v_cumulative_cache_savings;
