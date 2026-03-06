-- RPC Metrics Schema Extensions
-- Adds monitoring fields to wallet_scan_metrics table for efficiency tracking

-- Extend wallet_scan_metrics table with new fields
-- All changes are backwards compatible (new columns with defaults)

ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS creator_address TEXT;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS wallet_scan_pages INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS cache_hit_creator INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS cache_hit_wallet INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS cache_hit_funder INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS rpc_fallback INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS rate_limited INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS empty_wallet_scan INTEGER DEFAULT 0;

-- Add indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_creator
    ON wallet_scan_metrics(creator_address);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_provider_method
    ON wallet_scan_metrics(provider, method);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_created_at_provider
    ON wallet_scan_metrics(created_at, provider);

-- Optional: Create a view for quick access to aggregated metrics
CREATE VIEW IF NOT EXISTS v_metrics_summary_24h AS
SELECT
    DATE(created_at) as date,
    provider,
    method,
    COUNT(*) as call_count,
    SUM(credits_estimated) as total_credits,
    ROUND(AVG(latency_ms), 0) as avg_latency_ms,
    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count,
    SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) as rate_limit_count,
    SUM(CASE WHEN rpc_fallback = 1 THEN 1 ELSE 0 END) as fallback_count,
    ROUND(AVG(CASE WHEN wallet_scan_pages > 0 THEN wallet_scan_pages ELSE 0 END), 2) as avg_pages
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-1 day')
GROUP BY DATE(created_at), provider, method
ORDER BY date DESC, total_credits DESC;

-- View for creator efficiency
CREATE VIEW IF NOT EXISTS v_creator_efficiency AS
SELECT
    creator_address,
    COUNT(*) as total_scans,
    SUM(credits_estimated) as total_credits,
    ROUND(AVG(credits_estimated), 0) as avg_credits_per_scan,
    ROUND(AVG(latency_ms), 0) as avg_latency_ms,
    SUM(cache_hit_wallet) as wallet_cache_hits,
    SUM(rpc_fallback) as fallback_count,
    SUM(empty_wallet_scan) as empty_scans,
    ROUND(AVG(wallet_scan_pages), 2) as avg_pages_per_wallet
FROM wallet_scan_metrics
WHERE creator_address IS NOT NULL
GROUP BY creator_address
ORDER BY total_credits DESC;
