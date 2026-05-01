CREATE TABLE IF NOT EXISTS creator_risk_scores (
    creator_address TEXT PRIMARY KEY,
    operator_score REAL NOT NULL DEFAULT 0,
    outcome_score REAL NOT NULL DEFAULT 0,
    g_score REAL NOT NULL DEFAULT 0,
    liquidation_score REAL NOT NULL DEFAULT 0,
    final_score INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'LOW',
    risk_level TEXT NOT NULL DEFAULT 'LOW',
    migrated_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    g7_percentage REAL NOT NULL DEFAULT 0,
    liquidation_count INTEGER NOT NULL DEFAULT 0,
    top_reasons TEXT,
    reason_codes TEXT,
    explanation_json TEXT,
    scored_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS network_risk_scores (
    network_name TEXT PRIMARY KEY,
    operator_score REAL NOT NULL DEFAULT 0,
    outcome_score REAL NOT NULL DEFAULT 0,
    g_score REAL NOT NULL DEFAULT 0,
    liquidation_score REAL NOT NULL DEFAULT 0,
    final_score INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'NOISE_OR_INFRA',
    risk_level TEXT NOT NULL DEFAULT 'LOW',
    creator_count INTEGER NOT NULL DEFAULT 0,
    migrated_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    g7_percentage REAL NOT NULL DEFAULT 0,
    liquidation_count INTEGER NOT NULL DEFAULT 0,
    top_reasons TEXT,
    reason_codes TEXT,
    explanation_json TEXT,
    scored_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_creator_risk_scores_final
    ON creator_risk_scores(final_score DESC);

CREATE INDEX IF NOT EXISTS idx_creator_risk_scores_category
    ON creator_risk_scores(category);

CREATE INDEX IF NOT EXISTS idx_network_risk_scores_final
    ON network_risk_scores(final_score DESC);

CREATE INDEX IF NOT EXISTS idx_network_risk_scores_category
    ON network_risk_scores(category);

-- Added: last migration timestamp per creator/network
ALTER TABLE creator_risk_scores ADD COLUMN last_migrated_at INTEGER;
ALTER TABLE network_risk_scores ADD COLUMN last_migrated_at INTEGER;

CREATE TABLE IF NOT EXISTS risk_score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operator_score REAL NOT NULL DEFAULT 0,
    outcome_score REAL NOT NULL DEFAULT 0,
    g_score REAL NOT NULL DEFAULT 0,
    liquidation_score REAL NOT NULL DEFAULT 0,
    final_score INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason_codes TEXT,
    explanation_json TEXT,
    actual_outcome_json TEXT,
    scored_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_risk_score_history_entity
    ON risk_score_history(entity_type, entity_id, scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_risk_score_history_scored_at
    ON risk_score_history(scored_at DESC);
