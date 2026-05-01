CREATE TABLE IF NOT EXISTS infra_wallets (
    address TEXT PRIMARY KEY,
    type TEXT,
    label TEXT,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_infra_wallets_type
    ON infra_wallets(type);
