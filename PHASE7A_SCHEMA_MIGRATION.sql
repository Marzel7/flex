-- Phase 7A: Add Score Smoothing & Stability Coefficient to network_scores
--
-- This migration adds columns to support exponential smoothing and stability
-- coefficient computation in the build pipeline.
--
-- Backward compatible: new columns are optional with defaults
-- Idempotent: ALTER TABLE IF NOT EXISTS prevents errors on re-run

-- Add smoothing columns to network_scores
ALTER TABLE network_scores ADD COLUMN IF NOT EXISTS smoothed_score INTEGER;
ALTER TABLE network_scores ADD COLUMN IF NOT EXISTS stability_coeff REAL;
ALTER TABLE network_scores ADD COLUMN IF NOT EXISTS smoothing_alpha REAL DEFAULT 0.3;
ALTER TABLE network_scores ADD COLUMN IF NOT EXISTS smoothing_version INTEGER DEFAULT 1;
ALTER TABLE network_scores ADD COLUMN IF NOT EXISTS smoothed_updated_at TIMESTAMP;

-- Note: Smoothed values are computed in Phase I of build_networks_release()
-- which runs after Phase G (scoring) and before Phase H (alerts).
--
-- Formula:
--   smoothed_score_t = alpha * raw_score_t + (1 - alpha) * smoothed_score_{t-1}
--   stability_coeff = 1 / (1 + (vol5 / 10)), clamped to [0.1, 1.0]
--
-- where vol5 = average absolute delta over last 5 transitions
