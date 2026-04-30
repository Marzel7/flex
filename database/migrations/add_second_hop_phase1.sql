-- Phase 1: Second-hop funder expansion (SQL-only, no RPC)
-- Run once. Safe to re-run (all CREATE IF NOT EXISTS).

-- 1. Upstream links: upstream_wallet → known_funder → creator
CREATE TABLE IF NOT EXISTS funder_upstream_links (
    funder_address          TEXT NOT NULL,
    upstream_address        TEXT NOT NULL,
    transfer_count          INTEGER DEFAULT 1,
    total_sol               REAL DEFAULT 0,
    avg_transfer_sol        REAL DEFAULT 0,
    first_transfer_ts       INTEGER,
    last_transfer_ts        INTEGER,
    source                  TEXT DEFAULT 'transfer_index',
    is_excluded             INTEGER DEFAULT 0,
    funders_touched         INTEGER DEFAULT 0,
    last_seen_network_count INTEGER DEFAULT 0,
    built_at                INTEGER DEFAULT (strftime('%s','now')),
    PRIMARY KEY (funder_address, upstream_address)
);
CREATE INDEX IF NOT EXISTS idx_ful_upstream
    ON funder_upstream_links(upstream_address, is_excluded);
CREATE INDEX IF NOT EXISTS idx_ful_funder
    ON funder_upstream_links(funder_address);
CREATE INDEX IF NOT EXISTS idx_ful_source
    ON funder_upstream_links(source, built_at);

-- 2. Network bridges: upstream wallets that link ≥2 named networks
CREATE TABLE IF NOT EXISTS upstream_network_bridge (
    upstream_address   TEXT NOT NULL,
    network_a          TEXT NOT NULL,
    network_b          TEXT NOT NULL,
    shared_funders     INTEGER DEFAULT 0,
    confidence_score   REAL DEFAULT 0,
    risk_level         TEXT DEFAULT 'LOW',
    reason_codes       TEXT,
    is_excluded        INTEGER DEFAULT 0,
    built_at           INTEGER DEFAULT (strftime('%s','now')),
    PRIMARY KEY (upstream_address, network_a, network_b)
);
CREATE INDEX IF NOT EXISTS idx_unb_network_a
    ON upstream_network_bridge(network_a, confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_unb_network_b
    ON upstream_network_bridge(network_b, confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_unb_upstream
    ON upstream_network_bridge(upstream_address);
CREATE INDEX IF NOT EXISTS idx_unb_confidence
    ON upstream_network_bridge(confidence_score DESC);

-- 3. Per-creator second-hop enrichment
CREATE TABLE IF NOT EXISTS creator_second_hop (
    creator_address    TEXT NOT NULL,
    upstream_address   TEXT NOT NULL,
    via_funder         TEXT NOT NULL,
    confidence_score   REAL DEFAULT 0,
    risk_level         TEXT DEFAULT 'LOW',
    reason_codes       TEXT,
    source             TEXT DEFAULT 'transfer_index',
    built_at           INTEGER DEFAULT (strftime('%s','now')),
    PRIMARY KEY (creator_address, upstream_address, via_funder)
);
CREATE INDEX IF NOT EXISTS idx_csh_creator
    ON creator_second_hop(creator_address, confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_csh_upstream
    ON creator_second_hop(upstream_address);

-- 4. Extend networks_release with second-hop summary columns
-- Safe: duplicate column errors are expected on re-run
ALTER TABLE networks_release ADD COLUMN second_hop_bridge_count   INTEGER DEFAULT 0;
ALTER TABLE networks_release ADD COLUMN max_second_hop_confidence REAL    DEFAULT 0;
