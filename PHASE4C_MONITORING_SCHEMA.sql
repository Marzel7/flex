-- Phase 4C: Monitoring + Drift Detection Schema Migration
--
-- Adds history and alert tracking for network score monitoring.
-- Runs idempotently as part of build_networks_release() Phase H

-- ========================================================================
-- 1. network_score_history Table
-- ========================================================================
-- Store one row per network per build for historical tracking

CREATE TABLE IF NOT EXISTS network_score_history (
    network_name TEXT NOT NULL,
    build_version INTEGER NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,          -- 0-100 scale
    score_version INTEGER NOT NULL DEFAULT 1,  -- Score model version
    components_json TEXT,                      -- From network_scores.score_components_json
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Composite primary key: one history entry per network per build
    PRIMARY KEY (network_name, build_version),

    -- Foreign keys
    FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
        ON DELETE CASCADE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_nsh_computed_at
    ON network_score_history(computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_nsh_score
    ON network_score_history(score DESC);

CREATE INDEX IF NOT EXISTS idx_nsh_build_version
    ON network_score_history(build_version DESC);

CREATE INDEX IF NOT EXISTS idx_nsh_network_build
    ON network_score_history(network_name, build_version DESC);

-- ========================================================================
-- 2. network_alerts Table
-- ========================================================================
-- Store derived alerts for drift detection and monitoring

CREATE TABLE IF NOT EXISTS network_alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    network_name TEXT NOT NULL,
    build_version INTEGER NOT NULL,
    alert_type TEXT NOT NULL,  -- SCORE_SPIKE, NEW_HIGH_RISK, TYPE_FLIP, LIFECYCLE_FLIP
    severity TEXT NOT NULL,    -- low, medium, high
    message TEXT NOT NULL,     -- Human-readable alert message
    details_json TEXT,         -- JSON with type-specific details (old_value, new_value, delta, etc)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Foreign key to networks_release
    FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
        ON DELETE CASCADE,

    -- Unique constraint: one alert per (network, build, type) to avoid duplicates
    UNIQUE (network_name, build_version, alert_type)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_alerts_created_at
    ON network_alerts(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_type
    ON network_alerts(alert_type);

CREATE INDEX IF NOT EXISTS idx_alerts_severity
    ON network_alerts(severity);

CREATE INDEX IF NOT EXISTS idx_alerts_network
    ON network_alerts(network_name, build_version DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_latest
    ON network_alerts(created_at DESC, severity DESC);
