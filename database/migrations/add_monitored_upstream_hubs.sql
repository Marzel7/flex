-- Significant upstream hubs detected by SecondHopExpansionBuilder

CREATE TABLE IF NOT EXISTS monitored_upstream_hubs (
    upstream_address  TEXT PRIMARY KEY,
    confidence_score  REAL,
    networks_bridged  INTEGER,
    funders_bridged   INTEGER,
    risk_level        TEXT,
    reason_codes      TEXT DEFAULT '[]',
    status            TEXT DEFAULT 'active',
    discovered_at     INTEGER DEFAULT (strftime('%s','now')),
    last_expanded_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_muh_status
    ON monitored_upstream_hubs(status);

CREATE INDEX IF NOT EXISTS idx_muh_confidence
    ON monitored_upstream_hubs(confidence_score DESC);
