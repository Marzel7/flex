-- FLEX Dev Intelligence V3.1 Enhancements — Behavioral Modeling
-- Adds momentum, cadence, and expansion tracking tables
-- Additive only: no breaking changes to v3

-- ============================================================================
-- TABLE 1: org_momentum_history
-- Historical momentum tracking for trend analysis
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_momentum_history (
    momentum_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    recorded_date           TEXT NOT NULL,          -- 'YYYY-MM-DD'
    activity_24h            INTEGER DEFAULT 0,
    activity_7d_avg         REAL DEFAULT 0,
    momentum                REAL DEFAULT 0,         -- -1 to 1
    momentum_signal         REAL DEFAULT 0,         -- -100 to 100
    trend                   TEXT,                   -- 'accelerating'|'stable'|'decelerating'
    recorded_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, recorded_date)
);
CREATE INDEX IF NOT EXISTS idx_omh_org_date ON org_momentum_history(organization_id, recorded_date DESC);
CREATE INDEX IF NOT EXISTS idx_omh_trend ON org_momentum_history(trend);
CREATE INDEX IF NOT EXISTS idx_omh_momentum ON org_momentum_history(momentum DESC);

-- ============================================================================
-- TABLE 2: org_launch_cadence
-- Launch interval tracking and pattern detection
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_launch_cadence (
    cadence_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    analysis_date           TEXT NOT NULL,          -- 'YYYY-MM-DD'
    launches_detected       INTEGER DEFAULT 0,
    launch_dates            TEXT,                   -- JSON array of dates
    intervals               TEXT,                   -- JSON array of interval days
    average_interval        REAL DEFAULT 0,         -- days
    interval_variability    REAL DEFAULT 0,         -- 0-1 (predictability)
    days_since_last_launch  INTEGER DEFAULT 0,
    cadence_score           REAL DEFAULT 0,         -- 0-100
    due_for_launch          INTEGER DEFAULT 0,     -- 0 or 1
    prediction_confidence   REAL DEFAULT 0,         -- 0-1
    detected_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, analysis_date)
);
CREATE INDEX IF NOT EXISTS idx_olc_org_date ON org_launch_cadence(organization_id, analysis_date DESC);
CREATE INDEX IF NOT EXISTS idx_olc_due ON org_launch_cadence(due_for_launch, cadence_score DESC);
CREATE INDEX IF NOT EXISTS idx_olc_confidence ON org_launch_cadence(prediction_confidence DESC);

-- ============================================================================
-- TABLE 3: org_expansion_events
-- Team growth tracking and new creator detection
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_expansion_events (
    expansion_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    event_date              TEXT NOT NULL,          -- 'YYYY-MM-DD'
    current_creator_count   INTEGER DEFAULT 0,
    creators_added_24h      INTEGER DEFAULT 0,
    creators_added_7d       INTEGER DEFAULT 0,
    expansion_rate          REAL DEFAULT 0,         -- new/total ratio
    expansion_score         REAL DEFAULT 0,         -- 0-100
    expansion_signal        TEXT,                   -- 'rapid'|'normal'|'stable'|'shrinking'
    new_creators            TEXT,                   -- JSON array of wallet addresses
    first_activity_creators TEXT,                   -- JSON array (24h active)
    team_size_change_7d     INTEGER DEFAULT 0,
    detected_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, event_date)
);
CREATE INDEX IF NOT EXISTS idx_oee_org_date ON org_expansion_events(organization_id, event_date DESC);
CREATE INDEX IF NOT EXISTS idx_oee_signal ON org_expansion_events(expansion_signal);
CREATE INDEX IF NOT EXISTS idx_oee_score ON org_expansion_events(expansion_score DESC);

-- ============================================================================
-- TABLE 4: org_enhanced_launch_windows
-- Enhanced launch predictions combining base signals with behaviors
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_enhanced_launch_windows (
    enhanced_window_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    prediction_date         TEXT NOT NULL,          -- 'YYYY-MM-DD'
    base_prob_launch_24h    REAL DEFAULT 0,         -- original v3 score
    enhanced_prob_launch_24h REAL DEFAULT 0,        -- with behavioral boost
    momentum_signal         REAL DEFAULT 0,         -- -100 to 100
    cadence_score           REAL DEFAULT 0,         -- 0-100
    expansion_score         REAL DEFAULT 0,         -- 0-100
    data_quality_score      REAL DEFAULT 0,         -- 0-100 (completeness)
    enhancement_factor      REAL DEFAULT 1,         -- multiplier (enhanced/base)
    combined_confidence     REAL DEFAULT 0,         -- 0-1 (all signals converge)
    computed_at             REAL NOT NULL,
    FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
    UNIQUE(organization_id, prediction_date)
);
CREATE INDEX IF NOT EXISTS idx_oelw_org_date ON org_enhanced_launch_windows(organization_id, prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_oelw_enhanced ON org_enhanced_launch_windows(enhanced_prob_launch_24h DESC);
CREATE INDEX IF NOT EXISTS idx_oelw_confidence ON org_enhanced_launch_windows(combined_confidence DESC);

-- ============================================================================
-- VIEWS for V3.1 Analysis
-- ============================================================================

-- High-momentum orgs (likely to launch soon)
CREATE VIEW IF NOT EXISTS vw_momentum_driven_launches AS
SELECT olw.organization_id, do_.operator_wallet,
       olw.prob_launch_24h, omh.momentum_signal, omh.trend,
       omh.activity_24h, omh.activity_7d_avg,
       olw.prediction_date
FROM org_launch_windows olw
JOIN org_momentum_history omh ON olw.organization_id = omh.organization_id
  AND olw.prediction_date = omh.recorded_date
JOIN dev_organizations do_ ON olw.organization_id = do_.organization_id
WHERE omh.momentum > 0.3  -- Positive momentum
  AND omh.trend = 'accelerating'
ORDER BY omh.momentum_signal DESC;

-- Orgs due for cadence-based launch
CREATE VIEW IF NOT EXISTS vw_cadence_due_launches AS
SELECT olc.organization_id, do_.operator_wallet,
       olc.days_since_last_launch, olc.average_interval,
       olc.cadence_score, olc.prediction_confidence,
       olc.launch_dates,
       olc.analysis_date
FROM org_launch_cadence olc
JOIN dev_organizations do_ ON olc.organization_id = do_.organization_id
WHERE olc.due_for_launch = 1
  AND olc.prediction_confidence >= 0.6
ORDER BY olc.cadence_score DESC;

-- Rapidly expanding orgs (team growth = launch prep)
CREATE VIEW IF NOT EXISTS vw_expansion_driven_launches AS
SELECT oee.organization_id, do_.operator_wallet,
       oee.current_creator_count, oee.creators_added_7d,
       oee.expansion_score, oee.expansion_signal,
       oee.team_size_change_7d,
       oee.event_date
FROM org_expansion_events oee
JOIN dev_organizations do_ ON oee.organization_id = do_.organization_id
WHERE oee.expansion_signal IN ('rapid', 'normal')
  AND oee.creators_added_7d >= 2
ORDER BY oee.expansion_score DESC;

-- Convergence of all signals (highest confidence)
CREATE VIEW IF NOT EXISTS vw_high_confidence_launches_v31 AS
SELECT oelw.organization_id, do_.operator_wallet,
       oelw.enhanced_prob_launch_24h,
       oelw.momentum_signal, oelw.cadence_score, oelw.expansion_score,
       oelw.combined_confidence,
       oelw.enhancement_factor,
       oelw.prediction_date
FROM org_enhanced_launch_windows oelw
JOIN dev_organizations do_ ON oelw.organization_id = do_.organization_id
WHERE oelw.combined_confidence >= 0.7  -- High convergence
  AND oelw.enhanced_prob_launch_24h >= 70
ORDER BY oelw.combined_confidence DESC, oelw.enhanced_prob_launch_24h DESC;
