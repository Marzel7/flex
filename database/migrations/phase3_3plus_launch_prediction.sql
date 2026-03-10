-- Phase 3.3+ Migration: Launch Prediction & Enhanced Detection
-- Date: March 10, 2026
-- Purpose: Add creator_reuse, launch_watchlist, and launch history tables

-- ============================================================================
-- Table 1: creator_reuse
-- ============================================================================
-- Tracks creators funded by multiple wallets (coordination signal)

CREATE TABLE IF NOT EXISTS creator_reuse (
    -- Primary key
    creator_wallet          TEXT PRIMARY KEY,

    -- Reuse metrics
    funder_count            INTEGER DEFAULT 0,
    transfer_count          INTEGER DEFAULT 0,
    avg_funding_sol         REAL DEFAULT 0,

    -- Funding sources
    funder_list             TEXT,                   -- JSON array or comma-separated

    -- Timing metrics
    first_funded_ts         INTEGER,
    last_funded_ts          INTEGER,
    active_days             REAL DEFAULT 0,

    -- Risk scoring
    reuse_score             REAL DEFAULT 0,         -- 0-40 (10 per funder tier)
    is_pump_fun_target      BOOLEAN DEFAULT 0,      -- in pump.fun pattern
    cluster_id              INTEGER,

    -- Timestamps
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_creator_reuse_funder_count
    ON creator_reuse(funder_count DESC);
CREATE INDEX IF NOT EXISTS idx_creator_reuse_score
    ON creator_reuse(reuse_score DESC);
CREATE INDEX IF NOT EXISTS idx_creator_reuse_cluster
    ON creator_reuse(cluster_id);
CREATE INDEX IF NOT EXISTS idx_creator_reuse_updated
    ON creator_reuse(updated_at DESC);

-- ============================================================================
-- Table 2: launch_watchlist
-- ============================================================================
-- Identifies creators likely to launch tokens soon with multi-factor scoring

CREATE TABLE IF NOT EXISTS launch_watchlist (
    -- Primary key
    creator_wallet          TEXT PRIMARY KEY,

    -- Identification
    cluster_id              INTEGER,
    primary_funder          TEXT,

    -- Scoring components (each 0-30 points)
    reuse_score             REAL DEFAULT 0,
    farm_confidence_score   REAL DEFAULT 0,
    recency_score           REAL DEFAULT 0,
    reputation_score        REAL DEFAULT 0,

    -- Composite probability (0-100)
    launch_probability      REAL DEFAULT 0,
    risk_level              TEXT DEFAULT 'LOW',    -- CRITICAL|HIGH|MEDIUM|LOW|MINIMAL

    -- Funding metrics
    funder_count            INTEGER DEFAULT 0,
    funding_days_active     REAL DEFAULT 0,
    last_funding_ts         INTEGER,

    -- Expected launch window
    expected_launch_day     INTEGER DEFAULT 0,      -- 1-7 days

    -- Confidence metrics
    signal_count            INTEGER DEFAULT 0,      -- how many signals triggered

    -- Timestamps
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_launch_watchlist_probability
    ON launch_watchlist(launch_probability DESC);
CREATE INDEX IF NOT EXISTS idx_launch_watchlist_risk
    ON launch_watchlist(risk_level);
CREATE INDEX IF NOT EXISTS idx_launch_watchlist_updated
    ON launch_watchlist(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_launch_watchlist_cluster
    ON launch_watchlist(cluster_id);

-- ============================================================================
-- Table 3: launch_detection_history
-- ============================================================================
-- Audit trail and accuracy tracking for launch predictions

CREATE TABLE IF NOT EXISTS launch_detection_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Prediction
    creator_wallet          TEXT NOT NULL,
    predicted_probability   REAL NOT NULL,
    predicted_risk_level    TEXT NOT NULL,
    predicted_launch_day    INTEGER,

    -- Actual launch (populated when token detected)
    token_mint              TEXT,
    actual_launch_ts        INTEGER,
    launch_detected         BOOLEAN DEFAULT 0,

    -- Accuracy metrics
    days_to_actual_launch   INTEGER,
    prediction_accuracy     REAL,

    -- Detection metadata
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,

    FOREIGN KEY(creator_wallet) REFERENCES launch_watchlist(creator_wallet)
);

CREATE INDEX IF NOT EXISTS idx_launch_history_accuracy
    ON launch_detection_history(prediction_accuracy DESC);
CREATE INDEX IF NOT EXISTS idx_launch_history_detected
    ON launch_detection_history(launch_detected);
CREATE INDEX IF NOT EXISTS idx_launch_history_creator
    ON launch_detection_history(creator_wallet);
CREATE INDEX IF NOT EXISTS idx_launch_history_timestamp
    ON launch_detection_history(detected_at DESC);

-- ============================================================================
-- Enhancement: wallet_clusters column additions (ALTER TABLE)
-- ============================================================================
-- Add pump.fun specific metrics to existing wallet_clusters table

-- Note: SQLite has limited ALTER TABLE support. These columns might not
-- exist yet depending on when Phase 3.3 was deployed.

-- IF wallet_clusters.is_pumpfun doesn't exist, uncomment:
-- ALTER TABLE wallet_clusters ADD COLUMN is_pumpfun BOOLEAN DEFAULT 0;
-- ALTER TABLE wallet_clusters ADD COLUMN pumpfun_confidence REAL DEFAULT 0;

-- ============================================================================
-- View: vw_launch_candidates (optional, for convenience)
-- ============================================================================
-- High-probability launch candidates with all metrics

CREATE VIEW IF NOT EXISTS vw_launch_candidates AS
SELECT
    lw.creator_wallet,
    lw.launch_probability,
    lw.risk_level,
    lw.expected_launch_day,
    lw.funder_count,
    lw.funding_days_active,
    lw.cluster_id,
    wc.confidence_score AS cluster_confidence,
    wc.funder_wallet AS primary_cluster_funder,
    dr.reputation_score,
    dr.rug_rate,
    cr.reuse_score,
    lw.signal_count,
    datetime(lw.updated_at, 'unixepoch') AS last_updated
FROM launch_watchlist lw
LEFT JOIN wallet_clusters wc ON lw.cluster_id = wc.cluster_id
LEFT JOIN dev_reputation dr ON lw.creator_wallet = dr.wallet
LEFT JOIN creator_reuse cr ON lw.creator_wallet = cr.creator_wallet
WHERE lw.launch_probability >= 20
ORDER BY lw.launch_probability DESC;

-- ============================================================================
-- View: vw_critical_launches (optional, for alerts)
-- ============================================================================
-- Immediate launch risk (>75% probability, funded in last 24h)

CREATE VIEW IF NOT EXISTS vw_critical_launches AS
SELECT
    lw.creator_wallet,
    lw.launch_probability,
    lw.expected_launch_day,
    ROUND((julianday('now') - julianday(datetime(lw.last_funding_ts, 'unixepoch'))) * 24, 1) AS hours_since_funding,
    lw.funder_count,
    wc.funder_wallet AS primary_funder,
    dr.reputation_score,
    lw.signal_count
FROM launch_watchlist lw
LEFT JOIN wallet_clusters wc ON lw.cluster_id = wc.cluster_id
LEFT JOIN dev_reputation dr ON lw.creator_wallet = dr.wallet
WHERE lw.launch_probability > 75
  AND lw.last_funding_ts > (julianday('now') - 1) * 86400
ORDER BY lw.launch_probability DESC;
