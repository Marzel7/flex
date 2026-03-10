-- Dev Intelligence Graph Schema
-- Multi-layer graph: wallet → creator → token
-- Detects developer organizations (2+ wallets, 2+ creators, 1+ tokens)

CREATE TABLE IF NOT EXISTS dev_organizations (
    organization_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_wallet         TEXT NOT NULL,
    cluster_size            INTEGER DEFAULT 0,
    wallet_count            INTEGER DEFAULT 0,
    creator_count           INTEGER DEFAULT 0,
    token_count             INTEGER DEFAULT 0,
    token_list              TEXT,                    -- JSON array of mint addresses
    creator_list            TEXT,                    -- JSON array of creator addresses
    wallet_list             TEXT,                    -- JSON array of wallet addresses
    organization_score      REAL DEFAULT 0,          -- 0-1 composite score
    degree_centrality       REAL DEFAULT 0,          -- operator's degree centrality
    betweenness_centrality  REAL DEFAULT 0,          -- operator's betweenness centrality
    pagerank_score          REAL DEFAULT 0,          -- operator's PageRank
    total_volume_sol        REAL DEFAULT 0,          -- total SOL transferred within org
    avg_edge_weight         REAL DEFAULT 0,          -- avg composite_weight on SOL edges
    cluster_strength        REAL DEFAULT 0,          -- 0-100 coordination strength
    farm_cluster_id         INTEGER,                 -- FK to farm_clusters if linked
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,
    UNIQUE(operator_wallet)
);

CREATE TABLE IF NOT EXISTS dev_organization_members (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    member_address          TEXT NOT NULL,
    member_type             TEXT NOT NULL,           -- 'wallet', 'creator', 'token'
    degree_centrality       REAL DEFAULT 0,
    betweenness_centrality  REAL DEFAULT 0,
    pagerank_score          REAL DEFAULT 0,
    token_count             INTEGER DEFAULT 0,       -- tokens launched by this member
    total_volume_sol        REAL DEFAULT 0,          -- SOL sent by this member
    role_confidence         REAL DEFAULT 0,          -- 0-1
    detected_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, member_address)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_dev_orgs_score
    ON dev_organizations(organization_score DESC);
CREATE INDEX IF NOT EXISTS idx_dev_orgs_operator
    ON dev_organizations(operator_wallet);
CREATE INDEX IF NOT EXISTS idx_dev_orgs_token_count
    ON dev_organizations(token_count DESC);
CREATE INDEX IF NOT EXISTS idx_dev_orgs_detected_at
    ON dev_organizations(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_dev_org_members_org
    ON dev_organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_dev_org_members_address
    ON dev_organization_members(member_address);

-- Views
CREATE VIEW IF NOT EXISTS vw_high_value_orgs AS
SELECT
    organization_id,
    operator_wallet,
    cluster_size,
    wallet_count,
    creator_count,
    token_count,
    organization_score,
    betweenness_centrality,
    total_volume_sol,
    cluster_strength,
    detected_at
FROM dev_organizations
WHERE organization_score >= 0.4
ORDER BY organization_score DESC;

CREATE VIEW IF NOT EXISTS vw_org_operators AS
SELECT
    do_.organization_id,
    do_.operator_wallet,
    do_.organization_score,
    do_.betweenness_centrality,
    do_.cluster_size,
    do_.token_count,
    dom.member_type,
    dom.degree_centrality AS operator_degree
FROM dev_organizations do_
JOIN dev_organization_members dom
    ON do_.organization_id = dom.organization_id
    AND do_.operator_wallet = dom.member_address
ORDER BY do_.organization_score DESC;
