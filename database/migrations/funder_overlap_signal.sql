-- FLEX Funder Overlap Signal Migration
-- Detects coordination between funding wallets based on shared creator destinations
-- Creates 1 table with 4 indexes and 2 views
-- Unique constraint: (funder_a, funder_b) — one row per unique wallet pair

-- ============================================================================
-- TABLE: funder_overlap
-- Wallet pair overlap analysis
-- Measures coordination via shared creator funding patterns
-- ============================================================================
CREATE TABLE IF NOT EXISTS funder_overlap (
    overlap_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_a            TEXT NOT NULL,                  -- First funder wallet
    funder_b            TEXT NOT NULL,                  -- Second funder wallet (lexicographically > funder_a)
    shared_creators     INTEGER DEFAULT 0,              -- Number of creators funded by both
    overlap_ratio       REAL DEFAULT 0,                 -- shared_creators / min(creator_counts)
    funder_a_creators   INTEGER DEFAULT 0,              -- Total creators funded by funder_a
    funder_b_creators   INTEGER DEFAULT 0,              -- Total creators funded by funder_b
    coordination_level  TEXT,                           -- 'very_strong'|'high'|'medium'|'low'
    detected_at         INTEGER NOT NULL,               -- Unix timestamp (when computed)
    UNIQUE(funder_a, funder_b)
);

-- Index: overlap_ratio queries (main signal)
CREATE INDEX IF NOT EXISTS idx_fo_overlap_ratio
ON funder_overlap(overlap_ratio DESC);

-- Index: funder_a lookups
CREATE INDEX IF NOT EXISTS idx_fo_funder_a
ON funder_overlap(funder_a);

-- Index: funder_b lookups
CREATE INDEX IF NOT EXISTS idx_fo_funder_b
ON funder_overlap(funder_b);

-- Index: shared creator queries
CREATE INDEX IF NOT EXISTS idx_fo_shared_creators
ON funder_overlap(shared_creators DESC);

-- Index: coordination level queries
CREATE INDEX IF NOT EXISTS idx_fo_coordination_level
ON funder_overlap(coordination_level);

-- ============================================================================
-- VIEW: vw_high_coordination_wallets
-- Funder wallets with high coordination activity (>= 0.75 overlap)
-- Indicates strong dev farm or coordinated team activity
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_high_coordination_wallets AS
SELECT
    funder_a,
    funder_b,
    shared_creators,
    overlap_ratio,
    coordination_level,
    detected_at
FROM funder_overlap
WHERE overlap_ratio >= 0.75
ORDER BY overlap_ratio DESC, shared_creators DESC;

-- ============================================================================
-- VIEW: vw_very_strong_wallet_pairs
-- Wallet pairs with perfect or near-perfect coordination
-- Strong indicator of single organization with wallet rotation
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_very_strong_wallet_pairs AS
SELECT
    funder_a,
    funder_b,
    shared_creators,
    overlap_ratio,
    funder_a_creators,
    funder_b_creators
FROM funder_overlap
WHERE overlap_ratio = 1.0
AND shared_creators >= 3
ORDER BY shared_creators DESC;

-- ============================================================================
-- VIEW: vw_funder_network_connectivity
-- Aggregated wallet connectivity showing dev farm ecosystem structure
-- Groups wallets by their overlap relationships
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_funder_network_connectivity AS
SELECT
    wallet,
    partner_count,
    high_coordination_partners,
    avg_overlap,
    max_overlap
FROM (
    -- Funder A perspective
    SELECT
        funder_a as wallet,
        COUNT(*) as partner_count,
        SUM(CASE WHEN coordination_level IN ('high', 'very_strong') THEN 1 ELSE 0 END) as high_coordination_partners,
        AVG(overlap_ratio) as avg_overlap,
        MAX(overlap_ratio) as max_overlap
    FROM funder_overlap
    GROUP BY funder_a
    UNION ALL
    -- Funder B perspective
    SELECT
        funder_b as wallet,
        COUNT(*) as partner_count,
        SUM(CASE WHEN coordination_level IN ('high', 'very_strong') THEN 1 ELSE 0 END) as high_coordination_partners,
        AVG(overlap_ratio) as avg_overlap,
        MAX(overlap_ratio) as max_overlap
    FROM funder_overlap
    GROUP BY funder_b
)
GROUP BY wallet
ORDER BY high_coordination_partners DESC, max_overlap DESC;
