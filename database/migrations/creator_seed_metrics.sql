-- FLEX Creator Seed Concentration Metrics Migration
-- Tracks seed funding patterns for creators to detect coordinated preparation
-- Creates 1 table with 4 indexes
-- Unique constraint: (creator_wallet, organization_id) — one row per creator per org

-- ============================================================================
-- TABLE: creator_seed_metrics
-- Creator-level seed funding analysis
-- Measures concentration and coordination of seed funding before launch
-- ============================================================================
CREATE TABLE IF NOT EXISTS creator_seed_metrics (
    metric_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_wallet      TEXT NOT NULL,                  -- Creator's wallet address
    organization_id     INTEGER,                         -- FK to dev_organizations
    avg_seed_amount     REAL DEFAULT 0,                 -- Average SOL per seed transaction
    seed_stddev         REAL DEFAULT 0,                 -- Std dev of seed amounts
    seed_concentration  REAL DEFAULT 0,                 -- 1 - (stddev/avg), normalized 0-1
    funding_wallet_count INTEGER DEFAULT 0,             -- Unique wallets funding this creator
    funding_time_window INTEGER DEFAULT 0,              -- Hours from first to last seed funding
    seed_count          INTEGER DEFAULT 0,              -- Number of seed funding transactions
    total_seed_amount   REAL DEFAULT 0,                 -- Sum of all seed amounts
    created_at          INTEGER NOT NULL,               -- Unix timestamp (when computed)
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(creator_wallet, organization_id)
);

-- Index: creator lookups
CREATE INDEX IF NOT EXISTS idx_csm_creator
ON creator_seed_metrics(creator_wallet);

-- Index: organization lookups
CREATE INDEX IF NOT EXISTS idx_csm_org_id
ON creator_seed_metrics(organization_id);

-- Index: high concentration queries (for launch signal)
CREATE INDEX IF NOT EXISTS idx_csm_concentration
ON creator_seed_metrics(seed_concentration DESC);

-- Index: multi-wallet queries (coordination signal)
CREATE INDEX IF NOT EXISTS idx_csm_wallet_count
ON creator_seed_metrics(funding_wallet_count DESC);

-- ============================================================================
-- VIEW: vw_org_seed_concentration
-- Organization-level seed concentration aggregates
-- Used to compute organization_seed_concentration signal (0-100)
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_org_seed_concentration AS
SELECT
    organization_id,
    COUNT(*) as creators_with_seed,
    AVG(seed_concentration) as avg_concentration,
    SUM(CASE WHEN seed_concentration >= 0.6 THEN 1 ELSE 0 END) as high_conc_creators,
    AVG(funding_wallet_count) as avg_wallets,
    SUM(total_seed_amount) as total_seed_amount,
    MAX(created_at) as last_computed
FROM creator_seed_metrics
WHERE seed_count > 0
GROUP BY organization_id;

-- ============================================================================
-- VIEW: vw_high_seed_concentration
-- Creators with high funding concentration (>= 0.6)
-- Indicates organized, coordinated seed funding before launch
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_high_seed_concentration AS
SELECT
    creator_wallet,
    organization_id,
    seed_concentration,
    funding_wallet_count,
    funding_time_window,
    seed_count,
    total_seed_amount
FROM creator_seed_metrics
WHERE seed_concentration >= 0.6
AND seed_count >= 2
ORDER BY seed_concentration DESC;
