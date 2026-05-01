CREATE TABLE IF NOT EXISTS creator_resolution_queue (
    mint TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 100,
    reason TEXT,
    source TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    locked_until INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    resolved_creator TEXT,
    create_tx_signature TEXT,
    migrated_at INTEGER,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    resolved_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_creator_resolution_queue_status
ON creator_resolution_queue(status, priority DESC, next_attempt_at, locked_until);

CREATE INDEX IF NOT EXISTS idx_creator_resolution_queue_updated
ON creator_resolution_queue(updated_at DESC);
