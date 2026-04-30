-- Intelligence Refresh: watchlist / triage for stale and high-risk targets
-- Safe to run multiple times (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS intelligence_refresh_candidates (
    target_type             TEXT NOT NULL,  -- 'creator' | 'funder'
    target_address          TEXT NOT NULL,
    priority                INTEGER NOT NULL DEFAULT 0,
    reason_codes            TEXT NOT NULL DEFAULT '[]',  -- JSON list
    status                  TEXT NOT NULL DEFAULT 'watchlist',
    -- watchlist | approved | scanning | complete | failed | ignored
    rpc_allowed             INTEGER NOT NULL DEFAULT 0,
    last_local_refresh_at   INTEGER,
    last_rpc_scan_at        INTEGER,
    next_eligible_scan_at   INTEGER NOT NULL DEFAULT 0,
    attempts                INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    created_at              INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at              INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (target_type, target_address)
);

CREATE INDEX IF NOT EXISTS idx_irc_status_priority
    ON intelligence_refresh_candidates(status, priority DESC, next_eligible_scan_at);

CREATE INDEX IF NOT EXISTS idx_irc_rpc_allowed
    ON intelligence_refresh_candidates(rpc_allowed, status, priority DESC);

-- Daily RPC budget tracker (one row per UTC date per budget_key)
CREATE TABLE IF NOT EXISTS intelligence_refresh_rpc_budget (
    budget_date     TEXT NOT NULL,   -- YYYY-MM-DD UTC
    budget_key      TEXT NOT NULL,   -- 'creator_scans' | 'funder_scans' | 'rpc_calls'
    used            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (budget_date, budget_key)
);
