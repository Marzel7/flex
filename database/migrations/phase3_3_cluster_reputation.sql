-- Phase 3.3: Dev Farm Detection + Developer Reputation
-- Creates wallet_clusters and dev_reputation tables for transfer_index-native clustering
-- and per-developer scoring from rug history + token success metrics

-- Dev farm clusters (transfer_index-native, 90-day window)
CREATE TABLE IF NOT EXISTS wallet_clusters (
    cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_wallet       TEXT NOT NULL UNIQUE,
    creator_addresses   TEXT NOT NULL,      -- JSON array of creator addresses
    creator_count       INTEGER NOT NULL,
    confidence_score    REAL DEFAULT 0,     -- 0-100 scale
    avg_transfer_sol    REAL DEFAULT 0,
    transfer_stddev     REAL DEFAULT 0,
    days_active         INTEGER DEFAULT 0,
    first_transfer_ts   INTEGER,
    last_transfer_ts    INTEGER,
    has_burst           BOOLEAN DEFAULT 0,  -- 2+ creators funded in same 1-hour window
    wallet_age_days     REAL DEFAULT 0,     -- age of funder wallet in transfer_index (from first block_time)
    detected_at         REAL NOT NULL,      -- When cluster was detected (unix timestamp)
    updated_at          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wallet_clusters_confidence ON wallet_clusters(confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_clusters_funder ON wallet_clusters(funder_wallet);
CREATE INDEX IF NOT EXISTS idx_wallet_clusters_detected ON wallet_clusters(detected_at DESC);

-- Per-developer reputation (rug history + success rate merged from existing tables)
CREATE TABLE IF NOT EXISTS dev_reputation (
    wallet              TEXT PRIMARY KEY,
    tokens_launched     INTEGER DEFAULT 0,
    tokens_rugged       INTEGER DEFAULT 0,
    tokens_above_2x     INTEGER DEFAULT 0,
    tokens_above_10x    INTEGER DEFAULT 0,
    rug_rate            REAL DEFAULT 0,     -- tokens_rugged / tokens_launched (null-safe)
    success_rate        REAL DEFAULT 0,     -- tokens_above_2x / tokens_launched (null-safe)
    reputation_score    REAL DEFAULT 50,    -- 0-100, higher = better reputation
    first_seen_ts       INTEGER,            -- first block_time in transfer_index for this wallet
    wallet_age_days     REAL DEFAULT 0,     -- age in days at detection time
    cluster_id          INTEGER,            -- FK to wallet_clusters (if wallet is a dev farm)
    last_updated        REAL NOT NULL,
    FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_dev_reputation_score ON dev_reputation(reputation_score ASC);
CREATE INDEX IF NOT EXISTS idx_dev_reputation_rug ON dev_reputation(rug_rate DESC);
CREATE INDEX IF NOT EXISTS idx_dev_reputation_cluster ON dev_reputation(cluster_id);

-- Detection run log
CREATE TABLE IF NOT EXISTS cluster_detection_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at         REAL NOT NULL,
    clusters_found      INTEGER DEFAULT 0,
    reputations_updated INTEGER DEFAULT 0,
    duration_ms         REAL DEFAULT 0,
    status              TEXT DEFAULT 'success',  -- 'success', 'skipped', 'error'
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_detection_log_time ON cluster_detection_log(detected_at DESC);
