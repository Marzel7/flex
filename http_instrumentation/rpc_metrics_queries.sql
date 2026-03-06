-- RPC Metrics SQL Query Reference
-- All queries use wallet_scan_metrics table with extended monitoring fields

-- ============================================================================
-- 1. TOP 20 MOST EXPENSIVE ENDPOINTS
-- ============================================================================

SELECT
    provider,
    method,
    SUM(credits_estimated) as total_credits,
    COUNT(*) as call_count,
    ROUND(AVG(credits_estimated), 0) as avg_credits,
    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count,
    ROUND(AVG(latency_ms), 0) as avg_latency_ms
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
GROUP BY provider, method
ORDER BY total_credits DESC
LIMIT 20;


-- ============================================================================
-- 2. CREDITS BY PROVIDER (24 HOURS)
-- ============================================================================

SELECT
    provider,
    SUM(credits_estimated) as total_credits,
    COUNT(*) as call_count,
    ROUND(AVG(latency_ms), 0) as avg_latency_ms,
    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count,
    SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) as rate_limit_count,
    SUM(CASE WHEN rpc_fallback = 1 THEN 1 ELSE 0 END) as fallback_count,
    ROUND(100.0 * SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) as rate_limit_pct
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
GROUP BY provider
ORDER BY total_credits DESC;


-- ============================================================================
-- 3. CREDITS PER CREATOR (TOP 20 BY COST)
-- ============================================================================

SELECT
    creator_address,
    SUM(credits_estimated) as total_credits,
    COUNT(*) as total_scans,
    ROUND(AVG(credits_estimated), 0) as avg_credits_per_scan,
    SUM(cache_hit_wallet) as wallet_cache_hits,
    ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1) as cache_hit_rate_pct,
    ROUND(AVG(wallet_scan_pages), 2) as avg_pages_per_wallet,
    SUM(empty_wallet_scan) as empty_scans
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
  AND creator_address IS NOT NULL
GROUP BY creator_address
ORDER BY total_credits DESC
LIMIT 20;


-- ============================================================================
-- 4. DAILY CREDIT TREND (LAST 30 DAYS)
-- ============================================================================

SELECT
    DATE(created_at) as date,
    SUM(credits_estimated) as total_credits,
    COUNT(*) as total_calls,
    COUNT(DISTINCT creator_address) as unique_creators,
    ROUND(AVG(credits_estimated), 0) as avg_credits_per_call,
    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count,
    SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) as rate_limit_count
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-30 days')
GROUP BY DATE(created_at)
ORDER BY date ASC;


-- ============================================================================
-- 5. CACHE HIT RATES (24 HOURS)
-- ============================================================================

SELECT
    ROUND(100.0 * SUM(cache_hit_creator) / COUNT(*), 1) as creator_cache_hit_pct,
    ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1) as wallet_cache_hit_pct,
    ROUND(100.0 * SUM(cache_hit_funder) / COUNT(*), 1) as funder_cache_hit_pct,
    COUNT(*) as total_calls,
    SUM(cache_hit_creator) as creator_cache_hits,
    SUM(cache_hit_wallet) as wallet_cache_hits,
    SUM(cache_hit_funder) as funder_cache_hits
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours');


-- ============================================================================
-- 6. AVERAGE SCAN DEPTH (PAGES PER WALLET)
-- ============================================================================

SELECT
    ROUND(AVG(CASE WHEN wallet_scan_pages > 0 THEN wallet_scan_pages ELSE 1 END), 2) as avg_pages,
    COUNT(*) as total_scans,
    SUM(CASE WHEN wallet_scan_pages = 1 THEN 1 ELSE 0 END) as single_page_scans,
    SUM(CASE WHEN wallet_scan_pages BETWEEN 2 AND 3 THEN 1 ELSE 0 END) as two_three_page_scans,
    SUM(CASE WHEN wallet_scan_pages > 3 THEN 1 ELSE 0 END) as deep_scans
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
  AND provider = 'helius_enhanced';


-- ============================================================================
-- 7. RPC FALLBACK MONITORING
-- ============================================================================

SELECT
    ROUND(100.0 * SUM(rpc_fallback) / COUNT(*), 1) as fallback_rate_pct,
    SUM(rpc_fallback) as fallback_count,
    COUNT(*) as total_calls,
    method,
    provider
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
  AND rpc_fallback = 1
GROUP BY method, provider
ORDER BY fallback_count DESC;


-- ============================================================================
-- 8. RATE LIMIT ANALYSIS
-- ============================================================================

SELECT
    DATE(created_at) as date,
    provider,
    SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) as rate_limit_count,
    COUNT(*) as total_calls,
    ROUND(100.0 * SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) as rate_limit_pct
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
GROUP BY DATE(created_at), provider
ORDER BY date DESC, rate_limit_pct DESC;


-- ============================================================================
-- 9. EMPTY WALLET ANALYSIS
-- ============================================================================

SELECT
    ROUND(100.0 * SUM(empty_wallet_scan) / COUNT(*), 1) as empty_wallet_pct,
    SUM(empty_wallet_scan) as empty_count,
    COUNT(*) as total_scans
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours');


-- ============================================================================
-- 10. CREDITS PER CREATOR (DETAILED BREAKDOWN)
-- ============================================================================

SELECT
    creator_address,
    section,
    provider,
    SUM(credits_estimated) as section_credits,
    COUNT(*) as call_count,
    ROUND(AVG(latency_ms), 0) as avg_latency_ms
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours')
  AND creator_address IS NOT NULL
GROUP BY creator_address, section, provider
ORDER BY creator_address, section_credits DESC;


-- ============================================================================
-- 11. LATENCY PERCENTILES
-- ============================================================================

SELECT
    provider,
    method,
    COUNT(*) as call_count,
    ROUND(AVG(latency_ms), 0) as avg_latency_ms,
    ROUND(MIN(latency_ms), 0) as min_latency_ms,
    ROUND(MAX(latency_ms), 0) as max_latency_ms,
    ROUND((
        SELECT latency_ms FROM wallet_scan_metrics w2
        WHERE w2.provider = w1.provider
          AND w2.method = w1.method
          AND created_at >= datetime('now', '-24 hours')
        ORDER BY latency_ms
        LIMIT 1 OFFSET (COUNT(*) / 2)
    ), 0) as median_latency_ms
FROM wallet_scan_metrics w1
WHERE created_at >= datetime('now', '-24 hours')
GROUP BY provider, method
ORDER BY avg_latency_ms DESC;


-- ============================================================================
-- 12. BEFORE/AFTER COMPARISON (FOR MEASURING OPTIMIZATIONS)
-- ============================================================================

-- Run this query with two different date ranges to measure optimization impact

WITH before_period AS (
    SELECT
        COUNT(*) as call_count,
        SUM(credits_estimated) as total_credits,
        ROUND(AVG(credits_estimated), 0) as avg_credits,
        ROUND(AVG(latency_ms), 0) as avg_latency_ms,
        ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1) as cache_hit_pct,
        ROUND(AVG(wallet_scan_pages), 2) as avg_pages
    FROM wallet_scan_metrics
    WHERE created_at >= '2026-03-01' AND created_at < '2026-03-04'
),
after_period AS (
    SELECT
        COUNT(*) as call_count,
        SUM(credits_estimated) as total_credits,
        ROUND(AVG(credits_estimated), 0) as avg_credits,
        ROUND(AVG(latency_ms), 0) as avg_latency_ms,
        ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1) as cache_hit_pct,
        ROUND(AVG(wallet_scan_pages), 2) as avg_pages
    FROM wallet_scan_metrics
    WHERE created_at >= '2026-03-04'
)
SELECT
    'Before' as period,
    before_period.total_credits,
    before_period.avg_credits,
    before_period.cache_hit_pct,
    before_period.avg_pages,
    before_period.avg_latency_ms
FROM before_period
UNION ALL
SELECT
    'After' as period,
    after_period.total_credits,
    after_period.avg_credits,
    after_period.cache_hit_pct,
    after_period.avg_pages,
    after_period.avg_latency_ms
FROM after_period;


-- ============================================================================
-- 13. HEALTH CHECK - KEY METRICS AT A GLANCE
-- ============================================================================

SELECT
    (SELECT COUNT(*) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) as total_calls_24h,
    (SELECT SUM(credits_estimated) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) as total_credits_24h,
    (SELECT ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) as cache_hit_rate_pct,
    (SELECT ROUND(100.0 * SUM(rate_limited) / COUNT(*), 1) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) as rate_limit_rate_pct,
    (SELECT ROUND(AVG(wallet_scan_pages), 2) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) as avg_pages_per_wallet,
    (SELECT COUNT(DISTINCT creator_address) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) as unique_creators,
    CASE
        WHEN (SELECT COUNT(*) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours')) > 0
        THEN (SELECT ROUND(100.0 * SUM(cache_hit_wallet) / COUNT(*), 1) FROM wallet_scan_metrics WHERE created_at >= datetime('now', '-24 hours'))
        ELSE 0
    END as health_status;
