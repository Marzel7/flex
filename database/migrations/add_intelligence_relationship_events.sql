-- Intelligence relationship events: tracks newly discovered relationships after scans

CREATE TABLE IF NOT EXISTS intelligence_relationship_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type         TEXT NOT NULL,
    source_type        TEXT,
    source_address     TEXT,
    target_type        TEXT,
    target_address     TEXT,
    relationship_type  TEXT NOT NULL,
    confidence_score   REAL,
    risk_level         TEXT,
    reason_codes       TEXT,
    scan_source        TEXT,
    scan_id            TEXT,
    created_at         INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_ire_created_at
    ON intelligence_relationship_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ire_source
    ON intelligence_relationship_events(source_type, source_address);

CREATE INDEX IF NOT EXISTS idx_ire_relationship_type
    ON intelligence_relationship_events(relationship_type);

-- Uniqueness guard: prevents duplicate events for same relationship pair
CREATE UNIQUE INDEX IF NOT EXISTS idx_ire_dedup
    ON intelligence_relationship_events(relationship_type, source_address, target_address);
