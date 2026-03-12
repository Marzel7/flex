-- FLEX Launch Wave Detection — Multi-Token Launch Pattern Recognition
-- Detects when organizations are preparing multiple simultaneous launches
-- Additive migration: fully backward compatible with v3/v3.1

-- ============================================================================
-- TABLE: organization_launch_waves
-- Multi-launch wave detection and scoring
-- UNIQUE(organization_id, wave_date) — one per org per day
-- ============================================================================
CREATE TABLE IF NOT EXISTS organization_launch_waves (
    wave_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    wave_date               TEXT NOT NULL,          -- 'YYYY-MM-DD'
    wave_score              REAL DEFAULT 0,         -- 0-100 composite score
    wave_type               TEXT,                   -- 'imminent_multi_launch'|'preparation_phase'|'early_signals'|'no_wave'
    wave_confidence         REAL DEFAULT 0,         -- 0-1 signal convergence
    new_creators_signal     REAL DEFAULT 0,         -- 0-100 from new creators
    funding_burst_signal    REAL DEFAULT 0,         -- 0-100 from funding bursts
    momentum_signal         REAL DEFAULT 0,         -- 0-100 from activity momentum
    operator_spike_signal   REAL DEFAULT 0,         -- 0-100 from operator activity
    creator_reuse_signal    REAL DEFAULT 0,         -- 0-100 from creator re-engagement
    new_creators_count      INTEGER DEFAULT 0,      -- Number of new creators in 24h
    burst_count             INTEGER DEFAULT 0,      -- Number of funding bursts in 24h
    operator_activity_spike REAL DEFAULT 0,         -- Activity ratio (24h vs 7d avg)
    creator_reuse_rate      REAL DEFAULT 0,         -- Percentage of creators re-engaged
    detected_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, wave_date)
);
CREATE INDEX IF NOT EXISTS idx_olw_org_date ON organization_launch_waves(organization_id, wave_date DESC);
CREATE INDEX IF NOT EXISTS idx_olw_wave_score ON organization_launch_waves(wave_score DESC);
CREATE INDEX IF NOT EXISTS idx_olw_wave_type ON organization_launch_waves(wave_type);
CREATE INDEX IF NOT EXISTS idx_olw_confidence ON organization_launch_waves(wave_confidence DESC);

-- ============================================================================
-- VIEW: vw_imminent_launch_waves
-- Organizations with imminent multi-launch waves (score >= 80)
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_imminent_launch_waves AS
SELECT olw.organization_id, do_.operator_wallet, do_.creator_count, do_.token_count,
       olw.wave_score, olw.wave_type, olw.wave_confidence,
       olw.new_creators_count, olw.burst_count,
       olw.momentum_signal, olw.operator_spike_signal, olw.creator_reuse_signal,
       olw.wave_date
FROM organization_launch_waves olw
JOIN dev_organizations do_ ON olw.organization_id = do_.organization_id
WHERE olw.wave_score >= 80
  AND olw.wave_date = date('now')
ORDER BY olw.wave_score DESC;

-- ============================================================================
-- VIEW: vw_preparation_phase_waves
-- Organizations in preparation phase (score 60-79)
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_preparation_phase_waves AS
SELECT olw.organization_id, do_.operator_wallet, do_.creator_count, do_.token_count,
       olw.wave_score, olw.wave_type, olw.wave_confidence,
       olw.new_creators_count, olw.burst_count,
       olw.momentum_signal, olw.operator_spike_signal, olw.creator_reuse_signal,
       olw.wave_date
FROM organization_launch_waves olw
JOIN dev_organizations do_ ON olw.organization_id = do_.organization_id
WHERE olw.wave_score BETWEEN 60 AND 79
  AND olw.wave_date = date('now')
ORDER BY olw.wave_score DESC;

-- ============================================================================
-- VIEW: vw_high_confidence_waves
-- Waves where multiple signals converge (confidence >= 0.7)
-- ============================================================================
CREATE VIEW IF NOT EXISTS vw_high_confidence_waves AS
SELECT olw.organization_id, do_.operator_wallet, do_.creator_count, do_.token_count,
       olw.wave_score, olw.wave_type, olw.wave_confidence,
       olw.new_creators_count, olw.burst_count,
       olw.momentum_signal, olw.operator_spike_signal, olw.creator_reuse_signal,
       olw.wave_date
FROM organization_launch_waves olw
JOIN dev_organizations do_ ON olw.organization_id = do_.organization_id
WHERE olw.wave_confidence >= 0.7
  AND olw.wave_score >= 60
  AND olw.wave_date = date('now')
ORDER BY olw.wave_confidence DESC, olw.wave_score DESC;
