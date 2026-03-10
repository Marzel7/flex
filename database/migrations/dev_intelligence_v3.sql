-- FLEX Dev Intelligence Graph V3 — Migration
-- 8 tables, 20+ indexes, 2 views
-- Runs as: sqlite3 database/flex_complete_database.db < database/migrations/dev_intelligence_v3.sql

-- ============================================================================
-- TABLE 1: org_launch_windows
-- Multi-window launch prediction (24h, 72h, 7d)
-- UNIQUE(organization_id, prediction_date) — one row per org per day
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_launch_windows (
    window_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    prediction_date         TEXT NOT NULL,          -- 'YYYY-MM-DD'
    prob_launch_24h         REAL DEFAULT 0,         -- 0-100: burst+recency tight window
    prob_launch_72h         REAL DEFAULT 0,         -- 0-100: recency+velocity+coordination
    prob_launch_7d          REAL DEFAULT 0,         -- 0-100: full org+reputation
    signal_burst_24h        REAL DEFAULT 0,         -- burst_count in 24h window
    signal_recency_24h      REAL DEFAULT 0,         -- hours since last transfer (normalized)
    signal_velocity_72h     REAL DEFAULT 0,         -- SOL moved in 72h
    signal_coordination_72h REAL DEFAULT 0,         -- avg_composite_weight in 72h
    signal_reputation_7d    REAL DEFAULT 0,         -- reputation_score * 100
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, prediction_date)
);
CREATE INDEX IF NOT EXISTS idx_olw_org_id ON org_launch_windows(organization_id);
CREATE INDEX IF NOT EXISTS idx_olw_prob_24h ON org_launch_windows(prob_launch_24h DESC);
CREATE INDEX IF NOT EXISTS idx_olw_prob_72h ON org_launch_windows(prob_launch_72h DESC);
CREATE INDEX IF NOT EXISTS idx_olw_prob_7d ON org_launch_windows(prob_launch_7d DESC);
CREATE INDEX IF NOT EXISTS idx_olw_date ON org_launch_windows(prediction_date DESC);

-- ============================================================================
-- TABLE 2: org_snapshots
-- Daily activity snapshots — full retention
-- UNIQUE(organization_id, snapshot_date) — one row per org per day
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    snapshot_date           TEXT NOT NULL,          -- 'YYYY-MM-DD'
    active_funders          INTEGER DEFAULT 0,      -- org wallets sending SOL in last 24h
    active_creators         INTEGER DEFAULT 0,      -- org creators receiving SOL in last 24h
    burst_count             INTEGER DEFAULT 0,      -- 1h windows with 3+ transfers
    weighted_volume         REAL DEFAULT 0,         -- SUM(amount_sol * time_density) in 24h
    graph_density           REAL DEFAULT 0,         -- 0-1: actual/max possible edges
    launch_count            INTEGER DEFAULT 0,      -- tokens from creator_list launched in last 24h
    rug_count               INTEGER DEFAULT 0,      -- org tokens with rug_probability > 0.7 today
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_os_org_date ON org_snapshots(organization_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_os_date ON org_snapshots(snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_os_active_funders ON org_snapshots(active_funders DESC);

-- ============================================================================
-- TABLE 3: org_risk_scores
-- Composite risk per organization — daily overwrites
-- UNIQUE(organization_id) — latest risk only
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_risk_scores (
    risk_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    risk_score              REAL DEFAULT 0,         -- 0-100
    rug_probability         REAL DEFAULT 0,         -- 0-1 org-level weighted
    instability_score       REAL DEFAULT 0,         -- 0-100 snapshot volatility
    confidence              REAL DEFAULT 0,         -- 0-1 signal strength
    component_rug_prob      REAL DEFAULT 0,         -- rug_prob * 40
    component_instability   REAL DEFAULT 0,         -- instability * 0.25
    component_token_velocity REAL DEFAULT 0,        -- velocity * 0.2
    component_blocked_ratio REAL DEFAULT 0,         -- blocked * 0.15
    blocked_creator_count   INTEGER DEFAULT 0,
    total_creator_count     INTEGER DEFAULT 0,
    token_velocity          REAL DEFAULT 0,         -- token_count / active_days
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id)
);
CREATE INDEX IF NOT EXISTS idx_ors_risk_score ON org_risk_scores(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_ors_rug_probability ON org_risk_scores(rug_probability DESC);

-- ============================================================================
-- TABLE 4: token_outcome_predictions
-- Per-token outcome heuristics
-- UNIQUE(mint) — one row per token
-- ============================================================================
CREATE TABLE IF NOT EXISTS token_outcome_predictions (
    prediction_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                    TEXT NOT NULL,
    prob_rug                REAL DEFAULT 0,         -- 0-1
    prob_2x                 REAL DEFAULT 0,         -- 0-1
    prob_10x                REAL DEFAULT 0,         -- 0-1
    expected_quality_score  REAL DEFAULT 0,         -- 0-100
    signal_rug_prob         REAL DEFAULT 0,         -- token_analysis.rug_probability * 0.40
    signal_creator_risk     REAL DEFAULT 0,         -- creator rug_rate * 0.25
    signal_network_risk     REAL DEFAULT 0,         -- network_risk > 0 ? 0.25 : 0
    signal_blocked          REAL DEFAULT 0,         -- creator_is_blocked ? 0.10 : 0
    creator_wallet          TEXT,                   -- earliest_tx_creator
    organization_id         INTEGER,                -- nullable FK to dev_organizations
    days_since_org_funded   REAL,                   -- for recency_bonus
    computed_at             REAL NOT NULL,
    UNIQUE(mint)
);
CREATE INDEX IF NOT EXISTS idx_top_mint ON token_outcome_predictions(mint);
CREATE INDEX IF NOT EXISTS idx_top_prob_rug ON token_outcome_predictions(prob_rug DESC);
CREATE INDEX IF NOT EXISTS idx_top_quality ON token_outcome_predictions(expected_quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_top_creator ON token_outcome_predictions(creator_wallet);

-- ============================================================================
-- TABLE 5: org_relationships
-- Org-to-org overlap edges
-- UNIQUE(org_id_a, org_id_b) where org_id_a < org_id_b (canonical ordering)
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_relationships (
    relationship_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id_a                INTEGER NOT NULL,       -- always < org_id_b
    org_id_b                INTEGER NOT NULL,
    shared_creator_count    INTEGER DEFAULT 0,
    shared_operator         INTEGER DEFAULT 0,      -- 0 or 1
    indirect_funding_overlap INTEGER DEFAULT 0,     -- 0 or 1
    relationship_strength   REAL DEFAULT 0,         -- 0-100
    relationship_type       TEXT NOT NULL DEFAULT 'independent',
    -- 'sibling' | 'parent_child' | 'independent'
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,
    CHECK(org_id_a < org_id_b),
    FOREIGN KEY(org_id_a) REFERENCES dev_organizations(organization_id),
    FOREIGN KEY(org_id_b) REFERENCES dev_organizations(organization_id),
    UNIQUE(org_id_a, org_id_b)
);
CREATE INDEX IF NOT EXISTS idx_orel_org_a ON org_relationships(org_id_a);
CREATE INDEX IF NOT EXISTS idx_orel_org_b ON org_relationships(org_id_b);
CREATE INDEX IF NOT EXISTS idx_orel_strength ON org_relationships(relationship_strength DESC);

-- ============================================================================
-- TABLE 6: org_families
-- Org groupings from connected-components on relationship graph
-- UNIQUE(organization_id) — one family assignment per org
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_families (
    family_id               INTEGER NOT NULL,       -- logical ID from connected components
    organization_id         INTEGER NOT NULL,
    family_score            REAL DEFAULT 0,         -- avg relationship_strength within family
    hub_org_id              INTEGER,                -- org with highest betweenness in family
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id)
);
CREATE INDEX IF NOT EXISTS idx_of_family_id ON org_families(family_id);
CREATE INDEX IF NOT EXISTS idx_of_hub ON org_families(hub_org_id);
CREATE INDEX IF NOT EXISTS idx_of_score ON org_families(family_score DESC);

-- ============================================================================
-- TABLE 7: org_alerts
-- Polling-based alert log — no UNIQUE, dedup enforced in code
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_alerts (
    alert_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    alert_type              TEXT NOT NULL,
    -- 'funding_burst'|'creator_funded'|'operator_spike'|'watchlist_promotion'|'risk_spike'
    severity                TEXT NOT NULL,          -- 'low'|'medium'|'high'|'critical'
    message                 TEXT NOT NULL,
    signal_value            REAL,
    signal_threshold        REAL,
    created_at              REAL NOT NULL,
    acknowledged_at         REAL,                   -- NULL = unacknowledged
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id)
);
CREATE INDEX IF NOT EXISTS idx_oa_org_type_day ON org_alerts(organization_id, alert_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_oa_severity ON org_alerts(severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_oa_unacked ON org_alerts(acknowledged_at) WHERE acknowledged_at IS NULL;

-- ============================================================================
-- TABLE 8: prediction_features
-- ML feature store — 15 features per entity (populated but not used in v3)
-- UNIQUE(entity_id, entity_type)
-- ============================================================================
CREATE TABLE IF NOT EXISTS prediction_features (
    feature_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id               TEXT NOT NULL,          -- wallet address OR org_id as TEXT
    entity_type             TEXT NOT NULL,          -- 'creator'|'operator'|'organization'
    f_tokens_launched       REAL DEFAULT 0,
    f_rug_rate              REAL DEFAULT 0,
    f_success_rate          REAL DEFAULT 0,
    f_avg_market_cap        REAL DEFAULT 0,
    f_cluster_size          REAL DEFAULT 0,
    f_wallet_count          REAL DEFAULT 0,
    f_creator_count         REAL DEFAULT 0,
    f_total_volume_sol      REAL DEFAULT 0,
    f_avg_composite_weight  REAL DEFAULT 0,
    f_days_since_last_activity REAL DEFAULT 0,
    f_betweenness_centrality REAL DEFAULT 0,
    f_pagerank_score        REAL DEFAULT 0,
    f_organization_score    REAL DEFAULT 0,
    f_launch_prob_7d        REAL DEFAULT 0,
    f_reputation_score      REAL DEFAULT 0,
    computed_at             REAL NOT NULL,
    UNIQUE(entity_id, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_pf_entity ON prediction_features(entity_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_pf_type ON prediction_features(entity_type);

-- ============================================================================
-- VIEW 1: vw_active_orgs_24h
-- Orgs with recent funding activity (for alert polling)
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_active_orgs_24h AS
SELECT os.organization_id, do_.operator_wallet, os.active_funders,
       os.active_creators, os.burst_count, os.weighted_volume,
       os.snapshot_date
FROM org_snapshots os
JOIN dev_organizations do_ ON os.organization_id = do_.organization_id
WHERE os.snapshot_date = date('now')
  AND (os.active_funders > 0 OR os.active_creators > 0)
ORDER BY os.active_funders DESC;

-- ============================================================================
-- VIEW 2: vw_high_risk_orgs
-- Orgs with critical risk scores
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_high_risk_orgs AS
SELECT ors.organization_id, do_.operator_wallet, ors.risk_score,
       ors.rug_probability, ors.instability_score, ors.confidence,
       olp.launch_probability, orep.reputation_score
FROM org_risk_scores ors
JOIN dev_organizations do_ ON ors.organization_id = do_.organization_id
LEFT JOIN org_launch_predictions olp ON ors.organization_id = olp.organization_id
  AND olp.prediction_date = (SELECT MAX(p.prediction_date) FROM org_launch_predictions p WHERE p.organization_id = ors.organization_id)
LEFT JOIN org_reputation orep ON ors.organization_id = orep.organization_id
WHERE ors.risk_score >= 60
ORDER BY ors.risk_score DESC;
