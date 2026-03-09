-- RPC Savings Dashboard - SQL Views & Queries
-- Date: 2026-03-05
-- Purpose: Aggregate real RPC credits spent and saved for dashboard visualization
-- Database: SQLite with WAL mode

-- ============================================================================
-- VIEW 1: Daily RPC Spend and Savings (Primary Dashboard View)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_daily_savings AS
SELECT
  DATE(recorded_at) AS day,

  -- Actual RPC credits consumed
  SUM(COALESCE(credits, 0)) AS credits_spent,

  -- Credits avoided through caching
  SUM(COALESCE(credits_saved, 0)) AS credits_saved,

  -- Baseline cost without optimizations (what we would have spent)
  (SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))) AS credits_baseline,

  -- Savings percentage: savings / (spent + saved) * 100
  ROUND(
    100.0 * SUM(COALESCE(credits_saved, 0)) /
    NULLIF((SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))), 0),
    1
  ) AS savings_pct,

  -- Total events by cache action
  SUM(CASE WHEN cache_action = 'skip' THEN 1 ELSE 0 END) AS cache_skip_events,
  SUM(CASE WHEN cache_action = 'refresh' THEN 1 ELSE 0 END) AS cache_refresh_events,
  SUM(CASE WHEN cache_action = 'full_scan' THEN 1 ELSE 0 END) AS full_scan_events,
  SUM(CASE WHEN cache_action = 'none' THEN 1 ELSE 0 END) AS no_cache_events,

  -- Credits saved by cache type
  SUM(CASE WHEN cache_action = 'skip' THEN credits_saved ELSE 0 END) AS credits_saved_skip,
  SUM(CASE WHEN cache_action = 'refresh' THEN credits_saved ELSE 0 END) AS credits_saved_refresh,

  -- Request counts
  COUNT(*) AS total_requests,
  SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) AS cache_hit_events,

  -- Cache hit rate
  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS cache_hit_rate_pct

FROM rpc_metrics
WHERE recorded_at IS NOT NULL
GROUP BY DATE(recorded_at)
ORDER BY day DESC;

-- ============================================================================
-- VIEW 2: Section Breakdown (Credits Saved by Component)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_savings_by_section AS
SELECT
  section,
  DATE(recorded_at) AS day,

  -- Credits
  SUM(COALESCE(credits, 0)) AS credits_spent,
  SUM(COALESCE(credits_saved, 0)) AS credits_saved,
  (SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))) AS credits_baseline,

  -- Percentages
  ROUND(
    100.0 * SUM(COALESCE(credits_saved, 0)) /
    NULLIF((SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))), 0),
    1
  ) AS savings_pct,

  -- Cache types
  SUM(CASE WHEN cache_action = 'skip' THEN 1 ELSE 0 END) AS cache_skip_events,
  SUM(CASE WHEN cache_action = 'refresh' THEN 1 ELSE 0 END) AS cache_refresh_events,
  COUNT(*) AS total_requests

FROM rpc_metrics
WHERE recorded_at IS NOT NULL
GROUP BY section, DATE(recorded_at)
ORDER BY day DESC, credits_saved DESC;

-- ============================================================================
-- VIEW 3: Cache Action Performance
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_cache_performance AS
SELECT
  cache_action,

  COUNT(*) AS event_count,
  SUM(COALESCE(credits_saved, 0)) AS total_credits_saved,
  AVG(COALESCE(credits_saved, 0)) AS avg_credits_saved,
  MIN(COALESCE(credits_saved, 0)) AS min_credits_saved,
  MAX(COALESCE(credits_saved, 0)) AS max_credits_saved,

  -- Efficiency: total savings / total events
  ROUND(
    SUM(COALESCE(credits_saved, 0)) / NULLIF(COUNT(*), 0),
    1
  ) AS efficiency_per_event,

  -- Time period
  MIN(DATE(recorded_at)) AS first_seen,
  MAX(DATE(recorded_at)) AS last_seen

FROM rpc_metrics
WHERE cache_action != 'none' AND recorded_at IS NOT NULL
GROUP BY cache_action
ORDER BY total_credits_saved DESC;

-- ============================================================================
-- VIEW 4: Cumulative Savings Summary (All Time)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_cumulative_savings AS
SELECT
  DATE(MIN(recorded_at)) AS period_start,
  DATE(MAX(recorded_at)) AS period_end,

  SUM(COALESCE(credits, 0)) AS total_credits_spent,
  SUM(COALESCE(credits_saved, 0)) AS total_credits_saved,
  (SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))) AS total_credits_baseline,

  ROUND(
    100.0 * SUM(COALESCE(credits_saved, 0)) /
    NULLIF((SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))), 0),
    1
  ) AS overall_savings_pct,

  COUNT(*) AS total_requests,
  SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) AS total_cache_hits,

  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS overall_hit_rate_pct,

  -- Breakdown by cache type
  SUM(CASE WHEN cache_action = 'skip' THEN credits_saved ELSE 0 END) AS skip_savings,
  SUM(CASE WHEN cache_action = 'refresh' THEN credits_saved ELSE 0 END) AS refresh_savings,

  SUM(CASE WHEN cache_action = 'skip' THEN 1 ELSE 0 END) AS skip_count,
  SUM(CASE WHEN cache_action = 'refresh' THEN 1 ELSE 0 END) AS refresh_count

FROM rpc_metrics
WHERE recorded_at IS NOT NULL;

-- ============================================================================
-- VIEW 5: Rolling 7-Day Average (for trend smoothing)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_rolling_7day_average AS
SELECT
  DATE(recorded_at) AS day,

  ROUND(
    AVG(SUM(COALESCE(credits, 0)))
    OVER (ORDER BY DATE(recorded_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    1
  ) AS avg_daily_credits_spent_7d,

  ROUND(
    AVG(SUM(COALESCE(credits_saved, 0)))
    OVER (ORDER BY DATE(recorded_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    1
  ) AS avg_daily_credits_saved_7d,

  ROUND(
    AVG(ROUND(
      100.0 * SUM(COALESCE(credits_saved, 0)) /
      NULLIF((SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))), 0),
      1
    ))
    OVER (ORDER BY DATE(recorded_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    1
  ) AS avg_savings_pct_7d

FROM rpc_metrics
WHERE recorded_at IS NOT NULL
GROUP BY DATE(recorded_at)
ORDER BY day DESC;

-- ============================================================================
-- INDEXES: Optimize for dashboard queries
-- ============================================================================

-- Primary index for daily aggregations (already exists for cache tracking)
CREATE INDEX IF NOT EXISTS idx_rpc_metrics_date_cache
ON rpc_metrics(DATE(recorded_at), cache_action);

-- Index for section-based filtering
CREATE INDEX IF NOT EXISTS idx_rpc_metrics_section_date
ON rpc_metrics(section, DATE(recorded_at));

-- Index for cache action filtering
CREATE INDEX IF NOT EXISTS idx_rpc_metrics_cache_action
ON rpc_metrics(cache_action, recorded_at DESC);

-- Index for recorded_at (if not already present)
CREATE INDEX IF NOT EXISTS idx_rpc_metrics_recorded_at
ON rpc_metrics(recorded_at DESC);

-- ============================================================================
-- QUERY 1: Last 30 Days Daily Savings (Primary Dashboard)
-- ============================================================================

-- Usage: SELECT * FROM v_rpc_daily_savings WHERE day >= DATE('now','-30 day');

-- ============================================================================
-- QUERY 2: This Week vs Last Week Comparison
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_week_comparison AS
WITH this_week AS (
  SELECT
    'this_week' AS period,
    SUM(credits) AS credits_spent,
    SUM(credits_saved) AS credits_saved,
    COUNT(*) AS request_count
  FROM rpc_metrics
  WHERE recorded_at >= datetime('now', '-7 days')
),
last_week AS (
  SELECT
    'last_week' AS period,
    SUM(credits) AS credits_spent,
    SUM(credits_saved) AS credits_saved,
    COUNT(*) AS request_count
  FROM rpc_metrics
  WHERE recorded_at >= datetime('now', '-14 days')
    AND recorded_at < datetime('now', '-7 days')
)
SELECT * FROM this_week
UNION ALL
SELECT * FROM last_week;

-- ============================================================================
-- QUERY 3: Top Savings Sections (Last 30 Days)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_top_savings_sections AS
SELECT
  section,
  SUM(credits) AS credits_spent,
  SUM(credits_saved) AS credits_saved,
  (SUM(credits) + SUM(credits_saved)) AS credits_baseline,
  ROUND(
    100.0 * SUM(credits_saved) /
    NULLIF((SUM(credits) + SUM(credits_saved)), 0),
    1
  ) AS savings_pct,
  COUNT(*) AS request_count
FROM rpc_metrics
WHERE recorded_at >= datetime('now', '-30 days')
GROUP BY section
ORDER BY credits_saved DESC;

-- ============================================================================
-- QUERY 4: Cache Hit Rate Trend (Last 30 Days)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_hit_rate_trend AS
SELECT
  DATE(recorded_at) AS day,
  section,
  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS hit_rate_pct,
  COUNT(*) AS total_requests
FROM rpc_metrics
WHERE recorded_at >= datetime('now', '-30 days')
GROUP BY DATE(recorded_at), section
ORDER BY day DESC, hit_rate_pct DESC;

-- ============================================================================
-- VIEW 6: Real-time Dashboard Summary (Last 24 Hours)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_dashboard_24h AS
SELECT
  -- Time window
  datetime('now') AS query_time,
  datetime('now', '-24 hours') AS period_start,
  datetime('now') AS period_end,

  -- Costs
  SUM(COALESCE(credits, 0)) AS credits_spent_24h,
  SUM(COALESCE(credits_saved, 0)) AS credits_saved_24h,
  (SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))) AS credits_baseline_24h,

  -- Savings percentage
  ROUND(
    100.0 * SUM(COALESCE(credits_saved, 0)) /
    NULLIF((SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))), 0),
    1
  ) AS savings_pct_24h,

  -- Cache performance
  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS cache_hit_rate_24h,

  -- Breakdown
  SUM(CASE WHEN cache_action = 'skip' THEN credits_saved ELSE 0 END) AS skip_savings_24h,
  SUM(CASE WHEN cache_action = 'refresh' THEN credits_saved ELSE 0 END) AS refresh_savings_24h,
  SUM(CASE WHEN cache_action = 'skip' THEN 1 ELSE 0 END) AS skip_count_24h,
  SUM(CASE WHEN cache_action = 'refresh' THEN 1 ELSE 0 END) AS refresh_count_24h,

  COUNT(*) AS total_requests_24h

FROM rpc_metrics
WHERE recorded_at >= datetime('now', '-24 hours');

-- ============================================================================
-- QUERY PERFORMANCE NOTES (SQLite WAL Mode)
-- ============================================================================

-- These queries are optimized for SQLite with WAL mode:
-- 1. Date aggregations use DATE() which is indexed
-- 2. Window functions use ROWS BETWEEN for efficiency
-- 3. Null coalescing prevents calculation errors
-- 4. Multiple indexes support different filter patterns
-- 5. Views avoid repeated subquery calculations

-- For best performance:
-- - Ensure indexes are created (see INDEX section above)
-- - Run ANALYZE periodically to update statistics
-- - Consider VACUUM after large data deletes
-- - Monitor query plans with EXPLAIN QUERY PLAN

