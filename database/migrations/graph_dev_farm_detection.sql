-- Graph-Based Dev Farm Detection Schema
-- Tables for storing results of network clustering analysis

-- Dev farm clusters detected via graph analysis
CREATE TABLE IF NOT EXISTS farm_clusters (
    cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_cluster_id    INTEGER NOT NULL,                  -- ID from graph clustering
    funder_count        INTEGER NOT NULL,                  -- Number of funders
    creator_count       INTEGER NOT NULL,                  -- Number of creators
    ambiguous_count     INTEGER DEFAULT 0,                 -- Wallets with unclear role
    total_wallets       INTEGER NOT NULL,

    -- Cluster composition (JSON arrays)
    funder_list         TEXT NOT NULL,                     -- JSON array of funder addresses
    creator_list        TEXT NOT NULL,                     -- JSON array of creator addresses
    ambiguous_list      TEXT,                              -- JSON array of ambiguous addresses
    all_wallets         TEXT NOT NULL,                     -- JSON array of all addresses

    -- Graph metrics
    cluster_density     REAL DEFAULT 0,                    -- 0-1 (graph density)
    cluster_size        INTEGER DEFAULT 0,                 -- Number of edges
    avg_transfers_per_edge REAL DEFAULT 0,                 -- Average transfers on each edge
    total_transfers     INTEGER DEFAULT 0,                 -- Total edge count
    total_volume_sol    REAL DEFAULT 0,                    -- Total SOL transferred

    -- Classification metrics
    classification_confidence REAL DEFAULT 0,              -- 0-1 (how clear are funders vs creators)
    pattern_regularity  REAL DEFAULT 0,                    -- 0-1 (regularity of transfer timing)

    -- Risk assessment
    farm_risk_score     REAL DEFAULT 0,                    -- 0-100 (dev farm confidence)
    risk_level          TEXT DEFAULT 'LOW',                -- LOW|MEDIUM|HIGH|CRITICAL

    -- Metadata
    detection_method    TEXT DEFAULT 'graph_clustering',   -- How cluster was found
    first_activity_ts   INTEGER,                           -- Earliest transfer in cluster
    last_activity_ts    INTEGER,                           -- Latest transfer in cluster
    active_days         REAL DEFAULT 0,                    -- Days span of activity

    detected_at         REAL NOT NULL,                     -- When cluster was detected
    updated_at          REAL NOT NULL,                     -- Last update time

    UNIQUE(graph_cluster_id)
);

-- Individual wallet details within clusters
CREATE TABLE IF NOT EXISTS farm_cluster_members (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL,
    wallet_address      TEXT NOT NULL,
    wallet_role         TEXT NOT NULL,                     -- 'funder', 'creator', 'ambiguous'

    -- Role-specific metrics
    in_degree           INTEGER DEFAULT 0,                 -- How many wallets fund this wallet
    out_degree          INTEGER DEFAULT 0,                 -- How many wallets this wallet funds
    in_ratio            REAL DEFAULT 0,                    -- in_degree / total_degree
    out_ratio           REAL DEFAULT 0,                    -- out_degree / total_degree
    total_degree        INTEGER DEFAULT 0,                 -- in_degree + out_degree

    -- Activity metrics (within cluster)
    transfers_sent      INTEGER DEFAULT 0,
    transfers_received  INTEGER DEFAULT 0,
    total_sent_sol      REAL DEFAULT 0,
    total_received_sol  REAL DEFAULT 0,

    -- Confidence metrics
    role_confidence     REAL DEFAULT 0,                    -- 0-1 (how sure about this role)
    pattern_regularity  REAL DEFAULT 0,                    -- 0-1 (transfer timing regularity)

    first_activity_ts   INTEGER,
    last_activity_ts    INTEGER,

    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
    UNIQUE(cluster_id, wallet_address)
);

-- Cluster edges (transfers between funders and creators)
CREATE TABLE IF NOT EXISTS farm_cluster_edges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL,
    source_wallet       TEXT NOT NULL,
    dest_wallet         TEXT NOT NULL,

    transfer_count      INTEGER DEFAULT 0,                 -- Number of transfers on this edge
    total_amount_sol    REAL DEFAULT 0,                    -- Total SOL transferred
    avg_amount_sol      REAL DEFAULT 0,

    first_transfer_ts   INTEGER,
    last_transfer_ts    INTEGER,

    detected_at         REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
    UNIQUE(cluster_id, source_wallet, dest_wallet)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_score
    ON farm_clusters(farm_risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_level
    ON farm_clusters(risk_level);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_funder_count
    ON farm_clusters(funder_count DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_creator_count
    ON farm_clusters(creator_count DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_detected_at
    ON farm_clusters(detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_farm_members_cluster
    ON farm_cluster_members(cluster_id);
CREATE INDEX IF NOT EXISTS idx_farm_members_role
    ON farm_cluster_members(wallet_role);
CREATE INDEX IF NOT EXISTS idx_farm_members_wallet
    ON farm_cluster_members(wallet_address);

CREATE INDEX IF NOT EXISTS idx_farm_edges_cluster
    ON farm_cluster_edges(cluster_id);
CREATE INDEX IF NOT EXISTS idx_farm_edges_source
    ON farm_cluster_edges(source_wallet);
CREATE INDEX IF NOT EXISTS idx_farm_edges_dest
    ON farm_cluster_edges(dest_wallet);

-- Views for common queries

-- All high-confidence farms (risk_score >= 70)
CREATE VIEW IF NOT EXISTS vw_high_risk_farms AS
SELECT
    cluster_id,
    funder_count,
    creator_count,
    total_wallets,
    farm_risk_score,
    risk_level,
    total_volume_sol,
    cluster_density
FROM farm_clusters
WHERE farm_risk_score >= 70
  AND funder_count >= 2
  AND creator_count >= 3
ORDER BY farm_risk_score DESC;

-- All funders in detected farms
CREATE VIEW IF NOT EXISTS vw_farm_funders AS
SELECT
    fcm.wallet_address,
    fcm.cluster_id,
    fc.funder_count,
    fc.creator_count,
    fc.farm_risk_score,
    fcm.out_degree,
    fcm.in_degree,
    fcm.total_sent_sol,
    fcm.total_received_sol
FROM farm_cluster_members fcm
JOIN farm_clusters fc ON fcm.cluster_id = fc.cluster_id
WHERE fcm.wallet_role = 'funder'
ORDER BY fc.farm_risk_score DESC, fcm.out_degree DESC;

-- All creators in detected farms
CREATE VIEW IF NOT EXISTS vw_farm_creators AS
SELECT
    fcm.wallet_address,
    fcm.cluster_id,
    fc.funder_count,
    fc.creator_count,
    fc.farm_risk_score,
    fcm.in_degree,
    fcm.out_degree,
    fcm.total_sent_sol,
    fcm.total_received_sol
FROM farm_cluster_members fcm
JOIN farm_clusters fc ON fcm.cluster_id = fc.cluster_id
WHERE fcm.wallet_role = 'creator'
ORDER BY fc.farm_risk_score DESC, fcm.in_degree DESC;
