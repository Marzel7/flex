-- FLEX V2 Phase 2 Migration: RPC Response Cache
-- Safe to run against production (CREATE IF NOT EXISTS, idempotent)
--
-- Adds SQLite-based caching for RPC responses to reduce redundant API calls.
-- Targets: getTransaction (24h TTL), getSignaturesForAddress (1h TTL),
--          helius_enhanced_addresses_transactions (1h TTL)
-- Expected savings: 30-35% additional RPC reduction on top of Phase 1 cursors

CREATE TABLE IF NOT EXISTS rpc_response_cache (
    cache_key        TEXT PRIMARY KEY,              -- Deterministic cache key (method:params hash)
    response_json    TEXT NOT NULL,                 -- JSON-serialized RPC response dict
    method           TEXT NOT NULL,                 -- RPC method name for stats queries
    cached_at        REAL NOT NULL,                 -- Unix timestamp (time.time())
    ttl_seconds      INTEGER NOT NULL,              -- Per-method TTL (300s to 86400s)
    hit_count        INTEGER NOT NULL DEFAULT 0     -- Track hit frequency for monitoring
);

-- Primary lookup by cache_key (is PRIMARY KEY, implicit btree index)
-- Secondary indexes for cleanup and stats

CREATE INDEX IF NOT EXISTS idx_rpc_cache_expiry
ON rpc_response_cache(cached_at)
WHERE cached_at IS NOT NULL;

-- Per-method statistics for monitoring dashboard
CREATE INDEX IF NOT EXISTS idx_rpc_cache_method
ON rpc_response_cache(method);

-- Verification query (can be run to validate table)
-- SELECT COUNT(*) as total_cached,
--        SUM(hit_count) as total_hits,
--        COUNT(DISTINCT method) as methods_cached
-- FROM rpc_response_cache;
