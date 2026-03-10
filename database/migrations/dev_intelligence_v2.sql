-- ============================================================================
-- Dev Intelligence Graph v2 — Launch Predictions & Organization Reputation
-- Date: 2026-03-10
-- Depends on: dev_intelligence_graph.sql (dev_organizations, dev_organization_members)
-- ============================================================================

-- ============================================================================
-- Table 1: org_launch_predictions
-- ============================================================================
-- Stores launch probability prediction for each organization, one row per day.
-- UNIQUE(organization_id, prediction_date) enables INSERT OR REPLACE idempotency,
-- allowing daily re-runs to overwrite today's prediction while preserving history.
CREATE TABLE IF NOT EXISTS org_launch_predictions (
    prediction_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    prediction_date         TEXT NOT NULL,          -- ISO date string: 'YYYY-MM-DD'

    -- Composite score
    launch_probability      REAL DEFAULT 0,         -- 0-100 final weighted score

    -- Signal components (raw normalized points, stored for explainability)
    signal_recency          REAL DEFAULT 0,         -- 0-30 points: days since last funding
    signal_scale            REAL DEFAULT 0,         -- 0-20 points: org size composite
    signal_launch_rate      REAL DEFAULT 0,         -- 0-20 points: historical launches/day
    signal_funding_velocity REAL DEFAULT 0,         -- 0-15 points: SOL/active_day
    signal_coordination     REAL DEFAULT 0,         -- 0-10 points: avg_composite_weight
    signal_network_risk     REAL DEFAULT 0,         -- 0-5 points: avg rug_probability

    -- Signal inputs (raw numbers for debugging/audit)
    days_since_last_funding REAL,                   -- days since last_activity_ts
    org_token_count         INTEGER DEFAULT 0,      -- current org.token_count
    org_creator_count       INTEGER DEFAULT 0,      -- current org.creator_count
    org_wallet_count        INTEGER DEFAULT 0,      -- current org.wallet_count
    avg_tokens_launched     REAL DEFAULT 0,         -- AVG(dev_reputation.tokens_launched) for org creators
    funding_velocity_sol    REAL DEFAULT 0,         -- total_volume_sol / active_days
    avg_composite_weight    REAL DEFAULT 0,         -- from farm_clusters or dev_organizations.cluster_strength
    avg_rug_probability     REAL DEFAULT 0,         -- AVG(token_analysis.rug_probability) for org tokens

    -- Metadata
    computed_at             REAL NOT NULL,          -- unix timestamp when this prediction was computed

    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, prediction_date)
);

CREATE INDEX IF NOT EXISTS idx_org_predictions_org_id
    ON org_launch_predictions(organization_id);
CREATE INDEX IF NOT EXISTS idx_org_predictions_probability
    ON org_launch_predictions(launch_probability DESC);
CREATE INDEX IF NOT EXISTS idx_org_predictions_date
    ON org_launch_predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_org_predictions_org_date
    ON org_launch_predictions(organization_id, prediction_date DESC);

-- ============================================================================
-- Table 2: org_reputation
-- ============================================================================
-- Stores cumulative reputation metrics per organization.
-- UNIQUE(organization_id) enforces one row per org; daily re-runs overwrite via INSERT OR REPLACE.
CREATE TABLE IF NOT EXISTS org_reputation (
    reputation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,

    -- Lifetime token counts (derived from joining dev_organization_members + token_analysis)
    total_tokens_launched   INTEGER DEFAULT 0,      -- count of member_type='token' in org
    tokens_above_2x         INTEGER DEFAULT 0,      -- tokens reaching 2x ATH (from dev_reputation.tokens_above_2x sum)
    tokens_above_10x        INTEGER DEFAULT 0,      -- tokens reaching 10x ATH (from dev_reputation.tokens_above_10x sum)
    rug_count               INTEGER DEFAULT 0,      -- token nodes with rug_probability > 0.7

    -- Rate metrics (null-safe: default 0 if total_tokens_launched = 0)
    success_rate            REAL DEFAULT 0,         -- tokens_above_2x / total_tokens_launched (0-1)
    rug_rate                REAL DEFAULT 0,         -- rug_count / total_tokens_launched (0-1)

    -- Composite reputation score
    reputation_score        REAL DEFAULT 0,         -- 0-1 scale: success - rugs + volume bonus

    -- Metadata
    computed_at             REAL NOT NULL,          -- unix timestamp when computed

    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_org_reputation_score
    ON org_reputation(reputation_score DESC);
CREATE INDEX IF NOT EXISTS idx_org_reputation_rug_rate
    ON org_reputation(rug_rate DESC);
CREATE INDEX IF NOT EXISTS idx_org_reputation_org_id
    ON org_reputation(organization_id);

-- ============================================================================
-- View: vw_high_probability_launches
-- ============================================================================
-- High-probability upcoming launches with reputation context.
-- Joins to the LATEST prediction row per org (max prediction_date).
CREATE VIEW IF NOT EXISTS vw_high_probability_launches AS
SELECT
    olp.organization_id,
    do_.operator_wallet,
    do_.token_count,
    do_.creator_count,
    do_.wallet_count,
    do_.cluster_strength,
    olp.launch_probability,
    olp.prediction_date,
    olp.signal_recency,
    olp.signal_scale,
    olp.signal_launch_rate,
    olp.days_since_last_funding,
    orep.reputation_score,
    orep.rug_rate,
    orep.success_rate,
    orep.total_tokens_launched
FROM org_launch_predictions olp
JOIN dev_organizations do_ ON olp.organization_id = do_.organization_id
LEFT JOIN org_reputation orep ON olp.organization_id = orep.organization_id
WHERE olp.launch_probability >= 50
  AND olp.prediction_date = (
      SELECT MAX(olp2.prediction_date)
      FROM org_launch_predictions olp2
      WHERE olp2.organization_id = olp.organization_id
  )
ORDER BY olp.launch_probability DESC;
