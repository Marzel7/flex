-- RPC Metrics: Credits Per Funder Tracking
-- Adds funder discovery tracking to wallet_scan_metrics table

-- ============================================================================
-- 1. SCHEMA MIGRATION: Add funder_discovered column
-- ============================================================================

ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS funder_discovered INTEGER DEFAULT 0;

-- Add index for efficient funder discovery aggregation
CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_funder_discovered
    ON wallet_scan_metrics(funder_discovered, created_at);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_creator_funder
    ON wallet_scan_metrics(creator_address, funder_discovered);

-- ============================================================================
-- 2. VIEW: Credits Per Funder (Daily)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_credits_per_funder_daily AS
SELECT
    DATE(created_at) as date,
    creator_address,
    SUM(credits_estimated) as total_credits,
    SUM(funder_discovered) as funders_found,
    CASE
        WHEN SUM(funder_discovered) = 0 THEN NULL
        ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
    END as credits_per_funder,
    COUNT(*) as total_calls
FROM wallet_scan_metrics
WHERE funder_discovered > 0
   OR (section = 'creator_funding' AND created_at >= datetime('now', '-24 hours'))
GROUP BY DATE(created_at), creator_address
ORDER BY date DESC, total_credits DESC;

-- ============================================================================
-- 3. VIEW: Credits Per Funder (Aggregated)
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_credits_per_funder_summary AS
SELECT
    'Overall' as period,
    SUM(credits_estimated) as total_credits,
    SUM(funder_discovered) as funders_found,
    CASE
        WHEN SUM(funder_discovered) = 0 THEN NULL
        ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
    END as credits_per_funder,
    COUNT(DISTINCT creator_address) as unique_creators,
    COUNT(DISTINCT DATE(created_at)) as days_tracked
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours');

-- ============================================================================
-- 4. QUERY: Credits Per Funder (Last 24 Hours)
-- ============================================================================

-- SELECT
--     SUM(credits_estimated) AS total_credits,
--     SUM(funder_discovered) AS funders_found,
--     CASE
--         WHEN SUM(funder_discovered) = 0 THEN NULL
--         ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
--     END AS credits_per_funder,
--     COUNT(DISTINCT creator_address) as unique_creators
-- FROM wallet_scan_metrics
-- WHERE created_at >= datetime('now', '-24 hours');

-- ============================================================================
-- 5. QUERY: Credits Per Funder By Creator
-- ============================================================================

-- SELECT
--     creator_address,
--     SUM(credits_estimated) as total_credits,
--     SUM(funder_discovered) as funders_found,
--     COUNT(*) as discovery_records,
--     CASE
--         WHEN SUM(funder_discovered) = 0 THEN NULL
--         ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
--     END as credits_per_funder
-- FROM wallet_scan_metrics
-- WHERE created_at >= datetime('now', '-24 hours')
--   AND creator_address IS NOT NULL
-- GROUP BY creator_address
-- ORDER BY total_credits DESC
-- LIMIT 20;

-- ============================================================================
-- 6. QUERY: Credits Per Funder Daily Trend
-- ============================================================================

-- SELECT
--     DATE(created_at) as date,
--     SUM(credits_estimated) as daily_credits,
--     SUM(funder_discovered) as funders_found,
--     COUNT(DISTINCT creator_address) as unique_creators,
--     CASE
--         WHEN SUM(funder_discovered) = 0 THEN NULL
--         ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
--     END as credits_per_funder
-- FROM wallet_scan_metrics
-- WHERE created_at >= datetime('now', '-30 days')
--   AND (funder_discovered > 0 OR section = 'creator_funding')
-- GROUP BY DATE(created_at)
-- ORDER BY date ASC;

-- ============================================================================
-- 7. QUERY: Efficiency Benchmarks
-- ============================================================================

-- SELECT
--     CASE
--         WHEN SUM(funder_discovered) = 0 THEN NULL
--         ELSE ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2)
--     END as credits_per_funder,
--     CASE
--         WHEN ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2) < 3 THEN '✅ Excellent'
--         WHEN ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2) < 6 THEN '✅ Good'
--         WHEN ROUND(SUM(credits_estimated) * 1.0 / SUM(funder_discovered), 2) < 8 THEN '⚠️ Acceptable'
--         ELSE '❌ Poor'
--     END as efficiency_rating,
--     SUM(funder_discovered) as total_funders,
--     SUM(credits_estimated) as total_credits
-- FROM wallet_scan_metrics
-- WHERE created_at >= datetime('now', '-24 hours')
--   AND funder_discovered > 0;

-- ============================================================================
-- 8. QUERY: Compare Period Efficiency (Before/After Optimization)
-- ============================================================================

-- WITH before_period AS (
--     SELECT
--         SUM(credits_estimated) as credits,
--         SUM(funder_discovered) as funders,
--         ROUND(SUM(credits_estimated) * 1.0 / NULLIF(SUM(funder_discovered), 0), 2) as cpf
--     FROM wallet_scan_metrics
--     WHERE created_at >= '2026-03-01' AND created_at < '2026-03-05'
-- ),
-- after_period AS (
--     SELECT
--         SUM(credits_estimated) as credits,
--         SUM(funder_discovered) as funders,
--         ROUND(SUM(credits_estimated) * 1.0 / NULLIF(SUM(funder_discovered), 0), 2) as cpf
--     FROM wallet_scan_metrics
--     WHERE created_at >= '2026-03-05'
-- )
-- SELECT
--     'Before' as period,
--     before_period.credits as total_credits,
--     before_period.funders as funders_found,
--     before_period.cpf as credits_per_funder
-- FROM before_period
-- UNION ALL
-- SELECT
--     'After' as period,
--     after_period.credits,
--     after_period.funders,
--     after_period.cpf
-- FROM after_period;
