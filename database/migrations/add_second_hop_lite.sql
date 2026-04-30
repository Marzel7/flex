-- Phase 2 Lite: second-hop funder RPC expansion
-- Safe to run multiple times (IF NOT EXISTS throughout).

-- Queue of funders to scan via RPC
CREATE TABLE IF NOT EXISTS second_hop_lite_queue (
    funder_address   TEXT PRIMARY KEY,
    priority         INTEGER NOT NULL DEFAULT 0,
    reason_codes     TEXT NOT NULL DEFAULT '[]',   -- JSON list
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending | scanning | done | failed | skipped
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    rpc_calls_used   INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    scanned_at       INTEGER,
    next_attempt_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_shlq_status_priority
    ON second_hop_lite_queue(status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_shlq_next_attempt
    ON second_hop_lite_queue(next_attempt_at)
    WHERE status IN ('pending', 'failed');

-- Cache of completed RPC scans (TTL-based)
CREATE TABLE IF NOT EXISTS funder_rpc_scan_cache (
    funder_address          TEXT PRIMARY KEY,
    scanned_at              INTEGER NOT NULL DEFAULT (unixepoch()),
    expires_at              INTEGER NOT NULL,
    upstream_json           TEXT,       -- JSON: {address: {sol, count, first_ts, last_ts}}
    inbound_upstream_count  INTEGER NOT NULL DEFAULT 0,
    rpc_calls_used          INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'ok',   -- ok | error | partial
    error                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_frsc_expires
    ON funder_rpc_scan_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_frsc_scanned
    ON funder_rpc_scan_cache(scanned_at DESC);

-- RPC call log for auditing / rate limiting
CREATE TABLE IF NOT EXISTS second_hop_lite_rpc_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at      INTEGER NOT NULL DEFAULT (unixepoch()),
    funder_address TEXT,
    call_type      TEXT,       -- getSignaturesForAddress | getTransaction
    success        INTEGER NOT NULL DEFAULT 0,
    latency_ms     INTEGER,
    error          TEXT,
    cache_hit      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shlrl_called_at
    ON second_hop_lite_rpc_log(called_at DESC);
CREATE INDEX IF NOT EXISTS idx_shlrl_funder
    ON second_hop_lite_rpc_log(funder_address);
CREATE INDEX IF NOT EXISTS idx_shlrl_cache_hit
    ON second_hop_lite_rpc_log(cache_hit, called_at DESC);

-- Upstream account classification cache (owner program lookup via getAccountInfo)
CREATE TABLE IF NOT EXISTS upstream_account_classification (
    address         TEXT PRIMARY KEY,
    owner_program   TEXT,
    classification  TEXT NOT NULL DEFAULT 'unknown', -- wallet | program_account | token_account | unknown
    checked_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_uac_classification
    ON upstream_account_classification(classification);
