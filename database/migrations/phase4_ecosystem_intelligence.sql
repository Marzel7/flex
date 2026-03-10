-- Phase 4: Advanced Farm Intelligence — Ecosystem Detection & Launch Wave Analysis
-- Tables for multi-hop coordination, ecosystem tracking, and launch wave patterns

-- 1. Dev Farm Ecosystems (2+ funders sharing creators)
CREATE TABLE IF NOT EXISTS dev_farm_ecosystems (
    ecosystem_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_1            TEXT NOT NULL,
    funder_2            TEXT NOT NULL,
    shared_creators     INTEGER DEFAULT 0,
    shared_transfers    INTEGER DEFAULT 0,
    avg_amount_f1       REAL DEFAULT 0,
    avg_amount_f2       REAL DEFAULT 0,
    earliest_funding    INTEGER,
    latest_funding      INTEGER,
    coordination_span_days REAL DEFAULT 0,
    creators_list       TEXT,                  -- JSON array of shared creator addresses
    coordination_score  REAL DEFAULT 0,        -- 0-100 composite score
    ecosystem_size      INTEGER DEFAULT 0,    -- Total creators in extended ecosystem
    is_active_ecosystem BOOLEAN DEFAULT 1,
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,
    UNIQUE(funder_1, funder_2)
);

CREATE INDEX IF NOT EXISTS idx_ecosystems_coordination_score
    ON dev_farm_ecosystems(coordination_score DESC);
CREATE INDEX IF NOT EXISTS idx_ecosystems_shared_creators
    ON dev_farm_ecosystems(shared_creators DESC);
CREATE INDEX IF NOT EXISTS idx_ecosystems_active
    ON dev_farm_ecosystems(is_active_ecosystem, coordination_score DESC);

-- 2. Launch Waves (coordinated 1-hour bursts of 4+ creators)
CREATE TABLE IF NOT EXISTS launch_waves (
    wave_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    wave_hour           INTEGER NOT NULL UNIQUE,  -- epoch time / 3600
    wave_start_ts       INTEGER,
    wave_end_ts         INTEGER,
    funder_count        INTEGER DEFAULT 0,
    creator_count       INTEGER DEFAULT 0,
    transfer_count      INTEGER DEFAULT 0,
    avg_amount          REAL DEFAULT 0,
    min_amount          REAL DEFAULT 0,
    max_amount          REAL DEFAULT 0,
    amount_stddev       REAL DEFAULT 0,
    funders_list        TEXT,                  -- JSON array
    creators_list       TEXT,                  -- JSON array
    wave_intensity      REAL DEFAULT 0,        -- 0-100 composite score
    coordination_signal REAL DEFAULT 0,        -- Multi-funder coordination strength
    pump_fun_confidence REAL DEFAULT 0,        -- Pump.fun pattern match (0-100)
    is_pump_fun_wave    BOOLEAN DEFAULT 0,
    is_verified_launch  BOOLEAN DEFAULT 0,
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_waves_pump_fun_confidence
    ON launch_waves(pump_fun_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_waves_creator_count
    ON launch_waves(creator_count DESC);
CREATE INDEX IF NOT EXISTS idx_waves_intensity
    ON launch_waves(wave_intensity DESC);

-- 3. Ecosystem Member Tracking (leaders, hubs, contributors)
CREATE TABLE IF NOT EXISTS ecosystem_member_tracking (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ecosystem_id        INTEGER NOT NULL,
    member_address      TEXT NOT NULL,
    member_type         TEXT NOT NULL,        -- 'funder' or 'creator'
    connections_in_ecosystem INTEGER DEFAULT 0,
    transfer_count_in_ecosystem INTEGER DEFAULT 0,
    avg_amount_in_ecosystem REAL DEFAULT 0,
    first_activity_ts   INTEGER,
    last_activity_ts    INTEGER,
    active_days         REAL DEFAULT 0,
    is_ecosystem_leader BOOLEAN DEFAULT 0,   -- Funds most creators in ecosystem
    is_ecosystem_hub    BOOLEAN DEFAULT 0,   -- Funded by most funders in ecosystem
    ecosystem_importance_score REAL DEFAULT 0, -- 0-100
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY(ecosystem_id) REFERENCES dev_farm_ecosystems(ecosystem_id),
    UNIQUE(ecosystem_id, member_address)
);

CREATE INDEX IF NOT EXISTS idx_ecosystem_members_importance
    ON ecosystem_member_tracking(ecosystem_importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_ecosystem_members_leader
    ON ecosystem_member_tracking(is_ecosystem_leader);
CREATE INDEX IF NOT EXISTS idx_ecosystem_members_hub
    ON ecosystem_member_tracking(is_ecosystem_hub);

-- 4. Launch Wave Creators (per-creator launch probability in wave)
CREATE TABLE IF NOT EXISTS launch_wave_creators (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    wave_id             INTEGER NOT NULL,
    creator_wallet      TEXT NOT NULL,
    funder_count_in_wave INTEGER DEFAULT 0,
    simultaneous_funders INTEGER DEFAULT 0,  -- Funded by multiple wallets in same hour
    wave_launch_probability REAL DEFAULT 0,  -- 0-100
    predicted_launch_ts INTEGER,
    expected_token_mint TEXT,
    same_hour_funders_count INTEGER DEFAULT 0,
    sequential_funding  BOOLEAN DEFAULT 0,   -- Multiple sequential transfers vs. parallel
    token_launched      BOOLEAN DEFAULT 0,
    actual_launch_ts    INTEGER,
    prediction_accuracy REAL,                -- Actual launch within predicted window (0-100)
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY(wave_id) REFERENCES launch_waves(wave_id),
    UNIQUE(wave_id, creator_wallet)
);

CREATE INDEX IF NOT EXISTS idx_wave_creators_probability
    ON launch_wave_creators(wave_launch_probability DESC);
CREATE INDEX IF NOT EXISTS idx_wave_creators_launched
    ON launch_wave_creators(token_launched);

-- 5. Ecosystem Evolution Log (change tracking for ecosystem timeline)
CREATE TABLE IF NOT EXISTS ecosystem_evolution_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ecosystem_id        INTEGER NOT NULL,
    event_type          TEXT NOT NULL,       -- 'ecosystem_created', 'member_joined', 'coordination_changed', 'merged'
    event_details       TEXT,                -- JSON object with event context
    funder_count_at_event INTEGER,
    creator_count_at_event INTEGER,
    coordination_score_at_event REAL,
    event_ts            REAL NOT NULL,
    FOREIGN KEY(ecosystem_id) REFERENCES dev_farm_ecosystems(ecosystem_id)
);

CREATE INDEX IF NOT EXISTS idx_evolution_ecosystem_id
    ON ecosystem_evolution_log(ecosystem_id, event_ts DESC);

-- Views for convenience queries

-- All high-confidence ecosystems (coordination_score > 60)
CREATE VIEW IF NOT EXISTS vw_high_confidence_ecosystems AS
SELECT
    ecosystem_id,
    funder_1,
    funder_2,
    shared_creators,
    coordination_score,
    ecosystem_size,
    coordination_span_days,
    updated_at
FROM dev_farm_ecosystems
WHERE coordination_score > 60
  AND is_active_ecosystem = 1
ORDER BY coordination_score DESC;

-- All pump.fun waves (high confidence, 4+ creators)
CREATE VIEW IF NOT EXISTS vw_pump_fun_waves AS
SELECT
    wave_id,
    wave_start_ts,
    creator_count,
    funder_count,
    pump_fun_confidence,
    wave_intensity,
    transfer_count,
    creators_list
FROM launch_waves
WHERE is_pump_fun_wave = 1
  AND creator_count >= 4
ORDER BY wave_start_ts DESC;
