-- Migration: Add persistent metrics reset state table
-- Date: 2026-03-06
-- Purpose: Track reset baselines persistently across process restarts

CREATE TABLE IF NOT EXISTS metrics_reset_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    helius_baseline INTEGER DEFAULT 0,
    local_baseline INTEGER DEFAULT 0,
    notes TEXT,

    -- For tracking which process initiated the reset
    initiated_by TEXT,

    -- Index for fast "latest reset" lookups
    UNIQUE(reset_at)
);

-- Create index for quick "latest reset" queries
CREATE INDEX IF NOT EXISTS idx_metrics_reset_state_reset_at
ON metrics_reset_state(reset_at DESC);

-- Seed with initial reset state (epoch: before any data)
INSERT OR IGNORE INTO metrics_reset_state (id, reset_at, helius_baseline, local_baseline, notes)
VALUES (1, datetime('1970-01-01'), 0, 0, 'Initial reset state - epoch');
