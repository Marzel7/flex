-- Creator Activity Redesign  (refined)
-- Additive alongside existing tables: creator_funders, creator_funding_queue, token_analysis.

-- ---------------------------------------------------------------------------
-- creator_profile
-- One canonical row per creator wallet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creator_profile (
    creator_address          TEXT PRIMARY KEY,

    -- Coverage / scan state
    history_status           TEXT NOT NULL DEFAULT 'unknown'
                                 CHECK (history_status IN ('unknown','partial','baselined','stale')),
    coverage_mode            TEXT NOT NULL DEFAULT 'forward_only'
                                 CHECK (coverage_mode IN ('forward_only','full')),

    -- Classification (populated by existing analysis after baseline)
    classification_status    TEXT,

    -- Token launch tracking
    token_count_seen         INTEGER NOT NULL DEFAULT 0,
    last_launch_at           INTEGER,
    last_create_tx_signature TEXT,

    -- Activity timing
    first_seen_at            INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_activity_at         INTEGER,

    -- Scan timestamps (used for staleness checks and baseline_delay metric)
    last_full_scan_at        INTEGER,
    baselined_at             INTEGER,   -- when history_status first became 'baselined'
    last_incremental_scan_at INTEGER,

    -- Webhook / subscription
    webhook_status           TEXT CHECK (webhook_status IN ('active','stopped','error')),
    webhook_started_at       INTEGER,

    created_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at               INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_creator_profile_history_status
    ON creator_profile(history_status);

CREATE INDEX IF NOT EXISTS idx_creator_profile_last_launch
    ON creator_profile(last_launch_at DESC);


-- ---------------------------------------------------------------------------
-- creator_activity_state
-- Durable stream / reconcile cursor.  Updated by stream events and reconcile jobs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creator_activity_state (
    creator_address          TEXT PRIMARY KEY,

    -- Stream cursor
    last_seen_signature      TEXT,
    last_seen_slot           INTEGER,
    last_seen_at             INTEGER,   -- wall-clock time of last stream event (gap time detection)

    -- Scan bounds (set by baseline / reconcile jobs)
    oldest_scanned_signature TEXT,
    newest_scanned_signature TEXT,

    -- Reconcile state
    last_reconciled_at       INTEGER,
    needs_reconcile          INTEGER NOT NULL DEFAULT 0 CHECK (needs_reconcile IN (0,1)),
    last_gap_detected_at     INTEGER,

    -- Resumable baseline cursor (JSON: {"signature": "...", "slot": 123})
    resume_cursor            TEXT,

    -- Stream health
    stream_health_status     TEXT CHECK (stream_health_status IN
                                     ('healthy','lagging','gap_detected','unknown')),

    created_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at               INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_creator_activity_state_needs_reconcile
    ON creator_activity_state(needs_reconcile)
    WHERE needs_reconcile = 1;


-- ---------------------------------------------------------------------------
-- creator_activity_jobs
-- Creator-centric Tier B job queue.
-- Only two active job types: baseline and incremental_reconcile.
-- Partial unique index enforces at most ONE pending/running job per (creator, type).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creator_activity_jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_address  TEXT    NOT NULL,
    job_type         TEXT    NOT NULL
                         CHECK (job_type IN ('baseline','incremental_reconcile')),
    status           TEXT    NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','running','complete','failed')),
    priority         INTEGER NOT NULL DEFAULT 100,   -- lower = runs sooner
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    locked_at        INTEGER,
    started_at       INTEGER,
    completed_at     INTEGER,
    error            TEXT,
    source_mint      TEXT,
    source_reason    TEXT,
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Enforce one active job per (creator, type) at the DB layer.
-- INSERT will raise IntegrityError if a pending/running job already exists.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_creator_activity_jobs_active
    ON creator_activity_jobs(creator_address, job_type)
    WHERE status IN ('pending','running');

-- Worker poll index
CREATE INDEX IF NOT EXISTS idx_creator_activity_jobs_worker
    ON creator_activity_jobs(status, priority, next_attempt_at)
    WHERE status = 'pending';


-- ---------------------------------------------------------------------------
-- Compatibility view
-- Joins creator_profile + creator_funders for callers that still need funder counts.
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
    cp.first_seen_at,
    cp.baselined_at,
    CASE WHEN cp.baselined_at IS NOT NULL AND cp.first_seen_at IS NOT NULL
         THEN cp.baselined_at - cp.first_seen_at
         ELSE NULL
    END AS baseline_delay_seconds,
    COUNT(cf.funder_address) AS funder_count,
    SUM(cf.amount_sol)       AS total_funded_sol
FROM creator_profile cp
LEFT JOIN creator_funders cf ON cf.creator_address = cp.creator_address
GROUP BY cp.creator_address;
