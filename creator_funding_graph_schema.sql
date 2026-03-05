-- Creator Funding Graph Cache Schema
-- Purpose: Cache creator → funder relationships to avoid repeated scans
-- Date: 2026-03-05

-- Main cache table
CREATE TABLE IF NOT EXISTS creator_funding_graph (
    creator_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    inbound_sol REAL DEFAULT 0.0,
    inbound_tx_count INTEGER DEFAULT 1,
    PRIMARY KEY (creator_address, funder_address)
);

-- Index for fast creator lookups
CREATE INDEX IF NOT EXISTS idx_creator_graph_creator
ON creator_funding_graph(creator_address);

-- Index for cache expiration cleanup (TTL-based)
CREATE INDEX IF NOT EXISTS idx_creator_graph_last_seen
ON creator_funding_graph(last_seen);

-- Index for finding recent updates
CREATE INDEX IF NOT EXISTS idx_creator_graph_first_seen
ON creator_funding_graph(first_seen);

-- View: Creator funding statistics (last 24 hours)
CREATE VIEW IF NOT EXISTS v_creator_graph_stats_24h AS
SELECT
    COUNT(DISTINCT creator_address) as creators_cached,
    COUNT(*) as total_relationships,
    SUM(inbound_sol) as total_inbound_sol,
    SUM(inbound_tx_count) as total_transactions,
    ROUND(AVG(inbound_sol), 4) as avg_inbound_sol,
    ROUND(AVG(inbound_tx_count), 1) as avg_tx_count
FROM creator_funding_graph
WHERE last_seen >= datetime('now', '-24 hours');

-- View: Creator with most funders
CREATE VIEW IF NOT EXISTS v_creator_graph_top_creators AS
SELECT
    creator_address,
    COUNT(*) as funder_count,
    SUM(inbound_sol) as total_inbound_sol,
    SUM(inbound_tx_count) as total_tx_count,
    datetime(MAX(last_seen), 'localtime') as last_updated
FROM creator_funding_graph
WHERE last_seen >= datetime('now', '-7 days')
GROUP BY creator_address
ORDER BY funder_count DESC
LIMIT 100;

-- View: Funder usage across creators
CREATE VIEW IF NOT EXISTS v_creator_graph_frequent_funders AS
SELECT
    funder_address,
    COUNT(*) as creator_count,
    SUM(inbound_sol) as total_inbound_sol,
    SUM(inbound_tx_count) as total_tx_count,
    datetime(MAX(last_seen), 'localtime') as last_updated
FROM creator_funding_graph
WHERE last_seen >= datetime('now', '-7 days')
GROUP BY funder_address
ORDER BY creator_count DESC
LIMIT 100;

-- Note: Schema migration applied successfully
