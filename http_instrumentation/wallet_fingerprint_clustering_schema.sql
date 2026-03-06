-- Wallet Fingerprint Clustering Optimization
-- Purpose: Avoid rescanning the same wallet across multiple creators
-- Added to pipeline: Between FunderPrefilter and TwoPassScanner

-- Main fingerprint cache table
CREATE TABLE IF NOT EXISTS wallet_fingerprints (
    wallet_address TEXT PRIMARY KEY,
    wallet_type TEXT NOT NULL,                    -- 'cex', 'infra', 'bot', 'unknown'
    fingerprint_hash TEXT,                         -- Hash of tx patterns
    confidence REAL NOT NULL DEFAULT 0.5,          -- 0.0-1.0 confidence score
    tx_sample_hash TEXT,                           -- Hash of first page transactions
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scan_count INTEGER DEFAULT 1,

    -- Optimization metadata
    skip_reason TEXT,                              -- 'cached', 'refreshed', 'full_scan'
    last_scanner_version TEXT,                     -- Version that last updated
    CONSTRAINT confidence_range CHECK(confidence >= 0.0 AND confidence <= 1.0)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_type
    ON wallet_fingerprints(wallet_type);

CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_confidence
    ON wallet_fingerprints(confidence DESC);

CREATE INDEX IF NOT EXISTS idx_wallet_fingerprints_last_seen
    ON wallet_fingerprints(last_seen DESC);

-- Fingerprint usage statistics view
CREATE VIEW IF NOT EXISTS v_fingerprint_stats_24h AS
SELECT
    COUNT(DISTINCT wallet_address) as total_fingerprints,
    SUM(CASE WHEN last_seen >= datetime('now', '-24 hours') THEN 1 ELSE 0 END) as active_24h,
    SUM(CASE WHEN confidence >= 0.9 THEN 1 ELSE 0 END) as high_confidence,
    SUM(CASE WHEN confidence >= 0.7 AND confidence < 0.9 THEN 1 ELSE 0 END) as medium_confidence,
    SUM(CASE WHEN confidence < 0.7 THEN 1 ELSE 0 END) as low_confidence,
    ROUND(AVG(scan_count), 1) as avg_scans_per_wallet,
    ROUND(AVG(confidence), 3) as avg_confidence
FROM wallet_fingerprints;

-- Fingerprint distribution by type
CREATE VIEW IF NOT EXISTS v_fingerprint_by_type AS
SELECT
    wallet_type,
    COUNT(*) as count,
    ROUND(AVG(confidence), 3) as avg_confidence,
    ROUND(AVG(scan_count), 1) as avg_scans,
    SUM(CASE WHEN last_seen >= datetime('now', '-7 days') THEN 1 ELSE 0 END) as active_7d
FROM wallet_fingerprints
GROUP BY wallet_type
ORDER BY count DESC;

-- High-value wallets (scanned frequently)
CREATE VIEW IF NOT EXISTS v_frequent_wallets AS
SELECT
    wallet_address,
    wallet_type,
    confidence,
    scan_count,
    last_seen,
    CASE
        WHEN confidence >= 0.9 THEN 'SKIP'
        WHEN confidence >= 0.7 THEN 'REFRESH'
        ELSE 'FULL_SCAN'
    END as recommended_action
FROM wallet_fingerprints
WHERE scan_count >= 3
ORDER BY scan_count DESC
LIMIT 100;

-- Add columns to existing wallet_scan_metrics table if it exists
-- (assumes schema from helius_optimization_schema.sql has been applied)
-- These track fingerprint cache effectiveness

-- Note: If wallet_scan_metrics table doesn't exist yet, these columns
-- will be added via helius_optimization_schema.sql update

-- Migration info (for tracking what's been applied)
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    migration_name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

-- Record this migration
INSERT OR IGNORE INTO schema_migrations (migration_id, migration_name, notes)
VALUES (
    'wallet_fingerprint_clustering_v1',
    'Wallet Fingerprint Clustering Optimization',
    'Adds wallet_fingerprints table and views for cross-creator wallet reuse detection'
);
