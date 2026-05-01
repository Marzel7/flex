CREATE TABLE IF NOT EXISTS token_prediction_scores (
    mint TEXT PRIMARY KEY,
    creator_address TEXT,
    network_name TEXT,
    prediction_score INTEGER,
    risk_level TEXT,
    prediction_label TEXT NOT NULL DEFAULT 'PENDING_CREATOR',
    prediction_status TEXT NOT NULL DEFAULT 'COMPLETE',
    prediction_confidence TEXT NOT NULL DEFAULT 'HIGH',
    data_completeness REAL NOT NULL DEFAULT 1.0,
    reason_codes TEXT,
    explanation_json TEXT,
    creator_score INTEGER NOT NULL DEFAULT 0,
    network_score INTEGER NOT NULL DEFAULT 0,
    funding_score INTEGER NOT NULL DEFAULT 0,
    outcome_history_score INTEGER NOT NULL DEFAULT 0,
    liquidation_score INTEGER NOT NULL DEFAULT 0,
    predicted_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_tps_prediction_score ON token_prediction_scores(prediction_score DESC);
CREATE INDEX IF NOT EXISTS idx_tps_risk_level ON token_prediction_scores(risk_level);
CREATE INDEX IF NOT EXISTS idx_tps_creator ON token_prediction_scores(creator_address);
CREATE INDEX IF NOT EXISTS idx_tps_network ON token_prediction_scores(network_name);
CREATE INDEX IF NOT EXISTS idx_tps_label ON token_prediction_scores(prediction_label);
CREATE INDEX IF NOT EXISTS idx_tps_status ON token_prediction_scores(prediction_status);
CREATE INDEX IF NOT EXISTS idx_tps_confidence ON token_prediction_scores(prediction_confidence);

CREATE TABLE IF NOT EXISTS token_rescore_queue (
    mint TEXT PRIMARY KEY,
    reason TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_token_rescore_queue_created_at
    ON token_rescore_queue(created_at);

CREATE TABLE IF NOT EXISTS token_prediction_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL,
    creator_address TEXT,
    event_type TEXT NOT NULL,
    prediction_score INTEGER,
    risk_level TEXT,
    prediction_label TEXT,
    observed_outcome TEXT,
    market_cap REAL,
    peak_market_cap REAL,
    liquidity_removed INTEGER NOT NULL DEFAULT 0,
    g_level TEXT,
    event_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_tpe_mint ON token_prediction_events(mint, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_tpe_event_type ON token_prediction_events(event_type, event_at DESC);

CREATE TABLE IF NOT EXISTS token_prediction_outcomes (
    mint TEXT PRIMARY KEY,
    prediction_score INTEGER,
    prediction_label TEXT,
    predicted_risk_level TEXT,
    actual_outcome TEXT,
    actual_g_level TEXT,
    dumped_within_60s INTEGER NOT NULL DEFAULT 0,
    dumped_within_5m INTEGER NOT NULL DEFAULT 0,
    liquidity_removed INTEGER NOT NULL DEFAULT 0,
    survived_30m INTEGER NOT NULL DEFAULT 0,
    survived_2h INTEGER NOT NULL DEFAULT 0,
    peak_market_cap REAL,
    final_market_cap REAL,
    prediction_correct INTEGER,
    resolved_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tpo_prediction_correct ON token_prediction_outcomes(prediction_correct);
CREATE INDEX IF NOT EXISTS idx_tpo_actual_outcome ON token_prediction_outcomes(actual_outcome);
CREATE INDEX IF NOT EXISTS idx_tpo_resolved_at ON token_prediction_outcomes(resolved_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_token_prediction_creator_resolved
AFTER UPDATE OF earliest_tx_creator, pf_ws_creator ON token_analysis
WHEN COALESCE(OLD.earliest_tx_creator, OLD.pf_ws_creator, '') = ''
 AND COALESCE(NEW.earliest_tx_creator, NEW.pf_ws_creator, '') != ''
BEGIN
    INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
    VALUES (NEW.mint, 'creator_resolved', strftime('%s','now'));
END;

CREATE TRIGGER IF NOT EXISTS trg_token_prediction_funding_inserted
AFTER INSERT ON creator_funders
BEGIN
    INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
    SELECT mint, 'funding_extracted', strftime('%s','now')
    FROM token_analysis
    WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = NEW.creator_address;
END;

CREATE TRIGGER IF NOT EXISTS trg_token_prediction_creator_risk_inserted
AFTER INSERT ON creator_risk_scores
BEGIN
    INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
    SELECT mint, 'creator_risk_updated', strftime('%s','now')
    FROM token_analysis
    WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = NEW.creator_address;
END;

CREATE TRIGGER IF NOT EXISTS trg_token_prediction_creator_risk_updated
AFTER UPDATE ON creator_risk_scores
BEGIN
    INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
    SELECT mint, 'creator_risk_updated', strftime('%s','now')
    FROM token_analysis
    WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = NEW.creator_address;
END;

CREATE TRIGGER IF NOT EXISTS trg_token_prediction_network_assigned
AFTER INSERT ON network_membership
BEGIN
    INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
    SELECT mint, 'network_assigned', strftime('%s','now')
    FROM token_analysis
    WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = NEW.creator_address;
END;
