-- Human-readable labels for canonical Network_N identifiers.
-- Canonical internal links continue to use networks_release.network_name.

ALTER TABLE networks_release ADD COLUMN display_name TEXT;
ALTER TABLE networks_release ADD COLUMN display_name_reason TEXT;
ALTER TABLE networks_release ADD COLUMN display_name_source TEXT;

CREATE TABLE IF NOT EXISTS network_display_names (
    network_name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    reason TEXT,
    source_address TEXT,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
