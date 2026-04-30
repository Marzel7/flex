-- Creator Outbound Intelligence
-- Safe to run multiple times (IF NOT EXISTS throughout).

-- Queue: which creators to RPC-scan for outbound transfers
CREATE TABLE IF NOT EXISTS creator_outbound_queue (
    creator_address  TEXT PRIMARY KEY,
    priority         INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    -- pending | scanning | done | failed
    scanned_at       INTEGER,
    next_attempt_at  INTEGER NOT NULL DEFAULT 0,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_coq_status_priority
    ON creator_outbound_queue(status, priority DESC, next_attempt_at);

-- Classified outbound relationships (derived from creator_outgoing_transfers)
CREATE TABLE IF NOT EXISTS creator_outbound_classifications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_address     TEXT NOT NULL,
    recipient_address   TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,
    -- return_to_funder | shared_payout_wallet | creator_to_upstream_hub | large_outbound
    amount_sol          REAL,
    tx_count            INTEGER DEFAULT 1,
    confidence          REAL DEFAULT 1.0,
    first_seen          INTEGER,
    last_seen           INTEGER,
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE (creator_address, recipient_address, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_coc_creator
    ON creator_outbound_classifications(creator_address);
CREATE INDEX IF NOT EXISTS idx_coc_recipient
    ON creator_outbound_classifications(recipient_address);
CREATE INDEX IF NOT EXISTS idx_coc_type
    ON creator_outbound_classifications(relationship_type);
