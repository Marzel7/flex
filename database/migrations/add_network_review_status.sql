CREATE TABLE IF NOT EXISTS network_review_status (
    network_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at INTEGER,
    notes TEXT,
    decision_reason TEXT,
    last_evidence_hash TEXT,
    last_evidence_summary TEXT,
    first_seen_at INTEGER,
    last_seen_at INTEGER,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_network_review_status_status
ON network_review_status(status);
