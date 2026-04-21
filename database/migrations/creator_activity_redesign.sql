-- Creator Activity Redesign
-- Introduces creator-centric state alongside the existing token-centric tables.
-- All existing tables (creator_funders, creator_funding_queue, token_analysis) are left intact.

-- ---------------------------------------------------------------------------
-- creator_profile
-- One row per unique creator wallet.  Summary, classification, coverage meta.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creator_profile (
    creator_address          TEXT PRIMARY KEY,
    history_status           TEXT NOT NULL DEFAULT 'unknown'
                                 CHECK (history_status IN ('unknown','partial','baselined','stale')),
    coverage_mode            TEXT NOT NULL DEFAULT 'forward_only'
                                 CHECK (coverage_mode IN ('forward_only','full')),
    classification_status    TEXT,               -- e.g. 'clean','suspicious','cex_funded'
    token_count_seen         INTEGER NOT NULL DEFAULT 0,
    first_seen_at            INTEGER,            -- unix epoch
    last_seen_at             INTEGER,
    last_launch_at           INTEGER,
    last_create_tx_signature TEXT,
    last_activity_at         INTEGER,
    last_full_scan_at        INTEGER,
    last_incremental_scan_at INTEGER,
    webhook_started_at       INTEGER,
    webhook_status           TEXT,               -- 'active','stopped','error'
    baseline_version         INTEGER NOT NULL DEFAULT 1,
    created_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at               INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_creator_profile_history_status
    ON creator_profile(history_status);

CREATE INDEX IF NOT EXISTS idx_creator_profile_last_launch
    ON creator_profile(last_launch_at DESC);

CREATE INDEX IF NOT EXISTS idx_creator_profile_webhook_status
    ON creator_profile(webhook_status);


-- ---------------------------------------------------------------------------
-- creator_activity_state
-- Durable stream / reconcile cursor state.  One row per creator.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creator_activity_state (
    creator_address         TEXT PRIMARY KEY,
    last_seen_signature     TEXT,
    last_seen_slot          INTEGER,
    oldest_scanned_signature TEXT,
    newest_scanned_signature TEXT,
    last_reconciled_at      INTEGER,
    needs_backfill          INTEGER NOT NULL DEFAULT 0
                                CHECK (needs_backfill IN (0,1)),
    needs_reconcile         INTEGER NOT NULL DEFAULT 0
                                CHECK (needs_reconcile IN (0,1)),
    last_gap_detected_at    INTEGER,
    resume_cursor           TEXT,   -- JSON blob: {"signature": "...", "slot": 123}
    stream_health_status    TEXT    CHECK (stream_health_status IN
                                        ('healthy','lagging','gap_detected','unknown')),
    created_at              INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at              INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_creator_activity_state_needs_reconcile
    ON creator_activity_state(needs_reconcile)
    WHERE needs_reconcile = 1;

CREATE INDEX IF NOT EXISTS idx_creator_activity_state_needs_backfill
    ON creator_activity_state(needs_backfill)
    WHERE needs_backfill = 1;


-- ---------------------------------------------------------------------------
-- creator_activity_jobs
-- Creator-centric background job queue.  At most one active heavy job per creator.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creator_activity_jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_address  TEXT    NOT NULL,
    job_type         TEXT    NOT NULL
                         CHECK (job_type IN ('baseline','incremental_reconcile','backfill','refresh')),
    status           TEXT    NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','running','complete','failed','cancelled')),
    priority         INTEGER NOT NULL DEFAULT 100,  -- lower = higher priority
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    locked_at        INTEGER,
    started_at       INTEGER,
    completed_at     INTEGER,
    error            TEXT,
    source_mint      TEXT,   -- which mint triggered this job
    source_reason    TEXT,   -- e.g. 'curve_complete','migration','repeat_launch'
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Prevent duplicate pending/running heavy jobs for the same creator+type.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_creator_activity_jobs_active
    ON creator_activity_jobs(creator_address, job_type)
    WHERE status IN ('pending','running');

CREATE INDEX IF NOT EXISTS idx_creator_activity_jobs_worker
    ON creator_activity_jobs(status, priority, next_attempt_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_creator_activity_jobs_creator
    ON creator_activity_jobs(creator_address, status);


-- ---------------------------------------------------------------------------
-- Additive changes to token_analysis (safe ALTER TABLE — no-op if columns exist)
-- ---------------------------------------------------------------------------
-- These are applied at runtime by the migration helper; listed here for reference.
--
-- ALTER TABLE token_analysis ADD COLUMN creator_profile_resolved INTEGER DEFAULT 0;
-- ALTER TABLE token_analysis ADD COLUMN creator_activity_job_id  INTEGER;


-- ---------------------------------------------------------------------------
-- Compatibility view: maps old creator_funders COUNT pattern to creator_profile
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_creator_funding_summary AS
SELECT
    cp.creator_address,
    cp.history_status,
    cp.coverage_mode,
    cp.classification_status,
    cp.token_count_seen,
    cp.last_full_scan_at,
    cp.last_incremental_scan_at,
    COUNT(cf.funder_address) AS funder_count,
    SUM(cf.amount_sol)       AS total_funded_sol
FROM creator_profile cp
LEFT JOIN creator_funders cf ON cf.creator_address = cp.creator_address
GROUP BY cp.creator_address;
