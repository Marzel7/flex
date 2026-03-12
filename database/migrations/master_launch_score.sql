-- FLEX Master Launch Score Migration
-- Unified launch alert scoring system combining 8 predictive signals
-- Creates 1 table with 3 indexes and 2 views

-- ============================================================================
-- TABLE: master_launch_signals
-- Unified launch alert score and component breakdown per organization
-- UNIQUE constraint: (organization_id) — one row per org, overwrites daily
-- ============================================================================
CREATE TABLE IF NOT EXISTS master_launch_signals (
    signal_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,                  -- FK to dev_organizations
    launch_probability      REAL DEFAULT 0,                    -- 0-1, normalized signal
    launch_wave_score       REAL DEFAULT 0,                    -- 0-1, normalized signal
    seed_concentration      REAL DEFAULT 0,                    -- 0-1, normalized signal
    funder_overlap_score    REAL DEFAULT 0,                    -- 0-1, normalized signal
    organization_momentum   REAL DEFAULT 0,                    -- 0-1, normalized signal
    creator_reuse_score     REAL DEFAULT 0,                    -- 0-1, normalized signal
    operator_activity_score REAL DEFAULT 0,                    -- 0-1, normalized signal
    reputation_adjustment   REAL DEFAULT 0,                    -- 0-1, normalized signal
    master_launch_score     REAL DEFAULT 0,                    -- Final composite 0-1
    alert_level             TEXT,                              -- 'LOW'|'WATCH'|'HIGH'|'CRITICAL'
    computed_at             REAL NOT NULL,                     -- Unix timestamp
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id)
);

-- Index: organization lookups
CREATE INDEX IF NOT EXISTS idx_mls_org_id
ON master_launch_signals(organization_id);

-- Index: score ranking (for alert sorting)
CREATE INDEX IF NOT EXISTS idx_mls_score
ON master_launch_signals(master_launch_score DESC);

-- Index: alert level filtering
CREATE INDEX IF NOT EXISTS idx_mls_alert
ON master_launch_signals(alert_level);

-- ============================================================================
-- VIEW: vw_critical_launches
-- Organizations with CRITICAL alert level (score >= 0.75)
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_critical_launches AS
SELECT
    mls.organization_id,
    do_.operator_wallet,
    do_.token_count,
    do_.creator_count,
    mls.master_launch_score,
    mls.launch_probability,
    mls.launch_wave_score,
    mls.seed_concentration,
    mls.funder_overlap_score,
    mls.organization_momentum,
    mls.creator_reuse_score,
    mls.operator_activity_score,
    mls.reputation_adjustment,
    mls.computed_at
FROM master_launch_signals mls
JOIN dev_organizations do_ ON mls.organization_id = do_.organization_id
WHERE mls.alert_level = 'CRITICAL'
ORDER BY mls.master_launch_score DESC;

-- ============================================================================
-- VIEW: vw_launch_watchlist
-- Organizations with HIGH or CRITICAL alerts
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_launch_watchlist AS
SELECT
    mls.organization_id,
    do_.operator_wallet,
    do_.token_count,
    do_.creator_count,
    mls.master_launch_score,
    mls.alert_level,
    mls.launch_probability,
    mls.launch_wave_score,
    mls.seed_concentration,
    mls.funder_overlap_score,
    mls.organization_momentum,
    mls.creator_reuse_score,
    mls.operator_activity_score,
    mls.reputation_adjustment
FROM master_launch_signals mls
JOIN dev_organizations do_ ON mls.organization_id = do_.organization_id
WHERE mls.alert_level IN ('HIGH', 'CRITICAL')
ORDER BY mls.master_launch_score DESC;
