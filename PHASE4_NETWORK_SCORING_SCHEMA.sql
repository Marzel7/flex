-- Phase 4: Network Scoring Schema Migration
--
-- Creates network_scores table to store precomputed scores
-- Runs idempotently as part of build_networks_release() Phase G

-- Drop existing table if upgrading
DROP TABLE IF EXISTS network_scores_prev;

-- Create main network_scores table
CREATE TABLE IF NOT EXISTS network_scores (
    network_name TEXT PRIMARY KEY,
    score INTEGER NOT NULL DEFAULT 0,  -- 0-100 scale
    score_version INTEGER NOT NULL DEFAULT 1,  -- Track scoring rule updates
    score_components_json TEXT,  -- JSON with {connectivity, lifecycle, evidence} breakdown
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Foreign key to networks_release (optional, for referential integrity)
    FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_network_scores_score
    ON network_scores(score DESC);

CREATE INDEX IF NOT EXISTS idx_network_scores_computed_at
    ON network_scores(computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_scores_name
    ON network_scores(network_name);

-- Grant basic permissions (if using access control)
-- GRANT SELECT ON network_scores TO public;
