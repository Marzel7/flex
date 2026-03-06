-- RPC Efficiency Score - SQL Views & Implementation
-- Date: 2026-03-05
-- Purpose: Track how effectively the system saves RPC credits through caching
-- Formula: efficiency_score = credits_saved / credits_spent

-- ============================================================================
-- VIEW 1: Daily RPC Efficiency Score (Primary Metric)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_efficiency_score AS
SELECT
  DATE(recorded_at) AS day,

  -- Credits consumed
  SUM(COALESCE(credits, 0)) AS credits_spent,

  -- Credits avoided through caching
  SUM(COALESCE(credits_saved, 0)) AS credits_saved,

  -- Efficiency score: how many credits saved per credit spent
  -- Protected against division by zero with NULLIF
  ROUND(
    SUM(COALESCE(credits_saved, 0)) /
    NULLIF(SUM(COALESCE(credits, 0)), 0),
    2
  ) AS efficiency_score,

  -- Baseline (what we would have spent without optimization)
  (SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))) AS credits_baseline,

  -- Request counts for context
  COUNT(*) AS total_requests,
  SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) AS cache_hits,

  -- Cache hit rate percentage
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
-- VIEW 2: 24-Hour Real-Time Efficiency Score
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_efficiency_24h AS
SELECT
  datetime('now') AS query_time,
  datetime('now', '-24 hours') AS period_start,
  datetime('now') AS period_end,

  -- Last 24 hours metrics
  SUM(COALESCE(credits, 0)) AS credits_spent_24h,
  SUM(COALESCE(credits_saved, 0)) AS credits_saved_24h,

  -- Efficiency score (24h)
  ROUND(
    SUM(COALESCE(credits_saved, 0)) /
    NULLIF(SUM(COALESCE(credits, 0)), 0),
    2
  ) AS efficiency_score_24h,

  -- Additional context
  (SUM(COALESCE(credits, 0)) + SUM(COALESCE(credits_saved, 0))) AS credits_baseline_24h,
  COUNT(*) AS total_requests_24h,
  SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) AS cache_hits_24h,

  -- Cache hit rate
  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS cache_hit_rate_pct_24h

FROM rpc_metrics
WHERE recorded_at >= datetime('now', '-24 hours')
  AND recorded_at IS NOT NULL;

-- ============================================================================
-- VIEW 3: All-Time Efficiency Score
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_efficiency_all_time AS
SELECT
  MIN(DATE(recorded_at)) AS period_start,
  MAX(DATE(recorded_at)) AS period_end,
  COUNT(DISTINCT DATE(recorded_at)) AS days_tracked,

  -- All-time metrics
  SUM(COALESCE(credits, 0)) AS total_credits_spent,
  SUM(COALESCE(credits_saved, 0)) AS total_credits_saved,

  -- All-time efficiency score
  ROUND(
    SUM(COALESCE(credits_saved, 0)) /
    NULLIF(SUM(COALESCE(credits, 0)), 0),
    2
  ) AS efficiency_score_all_time,

  -- Average daily metrics
  ROUND(
    SUM(COALESCE(credits, 0)) / NULLIF(COUNT(DISTINCT DATE(recorded_at)), 0),
    1
  ) AS avg_daily_credits_spent,

  ROUND(
    SUM(COALESCE(credits_saved, 0)) / NULLIF(COUNT(DISTINCT DATE(recorded_at)), 0),
    1
  ) AS avg_daily_credits_saved,

  -- Overall statistics
  COUNT(*) AS total_requests,
  SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) AS total_cache_hits,

  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS overall_cache_hit_rate_pct

FROM rpc_metrics
WHERE recorded_at IS NOT NULL;

-- ============================================================================
-- VIEW 4: Efficiency Score by Section
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_efficiency_by_section AS
SELECT
  section,
  DATE(recorded_at) AS day,

  SUM(COALESCE(credits, 0)) AS credits_spent,
  SUM(COALESCE(credits_saved, 0)) AS credits_saved,

  ROUND(
    SUM(COALESCE(credits_saved, 0)) /
    NULLIF(SUM(COALESCE(credits, 0)), 0),
    2
  ) AS efficiency_score,

  COUNT(*) AS request_count,

  ROUND(
    100.0 * SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN 1 ELSE 0 END) /
    NULLIF(COUNT(*), 0),
    1
  ) AS cache_hit_rate_pct

FROM rpc_metrics
WHERE recorded_at IS NOT NULL
GROUP BY section, DATE(recorded_at)
ORDER BY day DESC, efficiency_score DESC;

-- ============================================================================
-- VIEW 5: Efficiency Trend (7-Day Average)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_efficiency_trend_7day AS
SELECT
  DATE(recorded_at) AS day,

  ROUND(
    AVG(SUM(COALESCE(credits, 0)))
    OVER (ORDER BY DATE(recorded_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    1
  ) AS avg_daily_spent_7d,

  ROUND(
    AVG(SUM(COALESCE(credits_saved, 0)))
    OVER (ORDER BY DATE(recorded_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    1
  ) AS avg_daily_saved_7d,

  ROUND(
    AVG(
      SUM(COALESCE(credits_saved, 0)) /
      NULLIF(SUM(COALESCE(credits, 0)), 0)
    )
    OVER (ORDER BY DATE(recorded_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
    2
  ) AS efficiency_trend_7d

FROM rpc_metrics
WHERE recorded_at IS NOT NULL
GROUP BY DATE(recorded_at)
ORDER BY day DESC;

-- ============================================================================
-- VIEW 6: Efficiency Score Interpretation & Health Status
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_rpc_efficiency_health AS
SELECT
  DATE(recorded_at) AS day,

  ROUND(
    SUM(COALESCE(credits_saved, 0)) /
    NULLIF(SUM(COALESCE(credits, 0)), 0),
    2
  ) AS efficiency_score,

  CASE
    WHEN (SUM(COALESCE(credits_saved, 0)) / NULLIF(SUM(COALESCE(credits, 0)), 0)) < 1
      THEN 'POOR'
    WHEN (SUM(COALESCE(credits_saved, 0)) / NULLIF(SUM(COALESCE(credits, 0)), 0)) < 3
      THEN 'WEAK'
    WHEN (SUM(COALESCE(credits_saved, 0)) / NULLIF(SUM(COALESCE(credits, 0)), 0)) < 5
      THEN 'GOOD'
    WHEN (SUM(COALESCE(credits_saved, 0)) / NULLIF(SUM(COALESCE(credits, 0)), 0)) < 10
      THEN 'EXCELLENT'
    ELSE 'OUTSTANDING'
  END AS health_status,

  CASE
    WHEN (SUM(COALESCE(credits_saved, 0)) / NULLIF(SUM(COALESCE(credits, 0)), 0)) < 3
      THEN 'ALERT'
    WHEN (SUM(COALESCE(credits_saved, 0)) / NULLIF(SUM(COALESCE(credits, 0)), 0)) < 5
      THEN 'WARNING'
    ELSE 'NORMAL'
  END AS alert_level,

  SUM(COALESCE(credits, 0)) AS credits_spent,
  SUM(COALESCE(credits_saved, 0)) AS credits_saved,
  COUNT(*) AS request_count

FROM rpc_metrics
WHERE recorded_at IS NOT NULL
GROUP BY DATE(recorded_at)
ORDER BY day DESC;

-- ============================================================================
-- INDEX: Optimize efficiency score queries
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_rpc_metrics_efficiency
ON rpc_metrics(DATE(recorded_at), cache_action, credits, credits_saved);

-- ============================================================================
-- QUERY EXAMPLES
-- ============================================================================

-- Example 1: Last 30 days efficiency trend
-- SELECT * FROM v_rpc_efficiency_score
-- WHERE day >= DATE('now', '-30 days')
-- ORDER BY day ASC;

-- Example 2: Current 24h efficiency
-- SELECT * FROM v_rpc_efficiency_24h;

-- Example 3: All-time efficiency
-- SELECT * FROM v_rpc_efficiency_all_time;

-- Example 4: Efficiency by section (last 30 days)
-- SELECT *
-- FROM v_rpc_efficiency_by_section
-- WHERE day >= DATE('now', '-30 days')
-- ORDER BY day DESC, section;

-- Example 5: 7-day trending
-- SELECT * FROM v_rpc_efficiency_trend_7day
-- WHERE day >= DATE('now', '-30 days')
-- ORDER BY day ASC;

-- Example 6: Health status with alerts
-- SELECT *
-- FROM v_rpc_efficiency_health
-- WHERE alert_level IN ('ALERT', 'WARNING')
-- ORDER BY day DESC;

