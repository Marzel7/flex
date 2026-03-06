-- ============================================================================
-- HELIUS OPTIMIZATION: Prefilter + Budget Guard + Tombstones
-- ============================================================================
-- Migration to add columns for tracking optimizations
-- Run this once: sqlite3 flex_complete_database.db < helius_optimization_schema.sql

-- ============================================================================
-- 1) Extend wallet_scan_metrics with optimization tracking
-- ============================================================================

ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS budget_exhausted INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS deep_scan_pages INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS tombstone_skip INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS prefilter_shortlist INTEGER DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS funder_inbound_sol REAL DEFAULT 0;
ALTER TABLE wallet_scan_metrics ADD COLUMN IF NOT EXISTS funder_inbound_count INTEGER DEFAULT 0;

-- Indexes for optimization metrics
CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_budget_exhausted
  ON wallet_scan_metrics(budget_exhausted, created_at);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_deep_scan_pages
  ON wallet_scan_metrics(deep_scan_pages, created_at);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_tombstone_skip
  ON wallet_scan_metrics(tombstone_skip, created_at);

CREATE INDEX IF NOT EXISTS idx_wallet_scan_metrics_prefilter
  ON wallet_scan_metrics(prefilter_shortlist, created_at);

-- ============================================================================
-- 2) Extend wallet_analysis_state with tombstone fields
-- ============================================================================

ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS tombstone_type TEXT;
  -- 'empty' = no meaningful transfers
  -- 'shallow' = classified with high confidence, no deep scan needed
  -- NULL = not tombstoned

ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS tombstone_created_at TEXT;
ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS tombstone_ttl_days INTEGER DEFAULT 14;
  -- How many days before tombstone expires

ALTER TABLE wallet_analysis_state ADD COLUMN IF NOT EXISTS scan_count INTEGER DEFAULT 0;
  -- How many times this wallet has been scanned (for 3-strike rule)

-- Indexes for tombstone lookup
CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_tombstone
  ON wallet_analysis_state(wallet_address, tombstone_type, tombstone_created_at);

CREATE INDEX IF NOT EXISTS idx_wallet_analysis_state_scan_count
  ON wallet_analysis_state(wallet_address, scan_count);

-- ============================================================================
-- 3) Creator funder summary table (for prefilter stats)
-- ============================================================================

CREATE TABLE IF NOT EXISTS creator_funder_summary (
    creator_address TEXT NOT NULL,
    funder_address TEXT NOT NULL,
    inbound_total_sol REAL DEFAULT 0,
    inbound_tx_count INTEGER DEFAULT 0,
    funder_type TEXT,  -- 'cex', 'infra', 'bot', 'unknown'
    shortlist_rank INTEGER,  -- 1 = top by SOL, NULL = not shortlisted
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (creator_address, funder_address)
);

CREATE INDEX IF NOT EXISTS idx_creator_funder_summary_rank
  ON creator_funder_summary(creator_address, shortlist_rank)
  WHERE shortlist_rank IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_creator_funder_summary_type
  ON creator_funder_summary(creator_address, funder_type);

-- ============================================================================
-- 4) Per-creator budget tracking (for current extraction runs)
-- ============================================================================

CREATE TABLE IF NOT EXISTS creator_extraction_budget (
    creator_address TEXT PRIMARY KEY,
    max_credits INTEGER DEFAULT 250,
    credits_spent INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    budget_exhausted INTEGER DEFAULT 0,
    funder_count INTEGER DEFAULT 0,
    recipient_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_creator_extraction_budget_exhausted
  ON creator_extraction_budget(budget_exhausted, started_at);

-- ============================================================================
-- 5) Views for optimization metrics reporting
-- ============================================================================

CREATE VIEW IF NOT EXISTS v_optimization_metrics_24h AS
  SELECT
      COUNT(*) as total_calls,
      SUM(credits_estimated) as total_credits,
      SUM(deep_scan_pages) as total_deep_pages,
      SUM(tombstone_skip) as tombstone_skips,
      SUM(budget_exhausted) as budget_exhausted_count,
      ROUND(100.0 * SUM(tombstone_skip) / COUNT(*), 1) as tombstone_skip_pct,
      ROUND(100.0 * SUM(budget_exhausted) / COUNT(*), 1) as budget_exhausted_pct,
      ROUND(AVG(deep_scan_pages), 2) as avg_deep_pages,
      ROUND(AVG(CASE WHEN deep_scan_pages = 1 THEN 1 ELSE 0 END) * 100, 1) as pct_single_page_scans
  FROM wallet_scan_metrics
  WHERE created_at >= datetime('now', '-24 hours');

CREATE VIEW IF NOT EXISTS v_funder_prefilter_summary AS
  SELECT
      creator_address,
      COUNT(*) as total_funders,
      SUM(CASE WHEN shortlist_rank IS NOT NULL THEN 1 ELSE 0 END) as shortlisted_funders,
      ROUND(100.0 * SUM(CASE WHEN shortlist_rank IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as shortlist_pct,
      ROUND(SUM(inbound_total_sol), 2) as total_inbound_sol,
      ROUND(AVG(inbound_total_sol), 4) as avg_inbound_sol,
      COUNT(CASE WHEN funder_type = 'cex' THEN 1 END) as cex_count,
      COUNT(CASE WHEN funder_type = 'infra' THEN 1 END) as infra_count,
      COUNT(CASE WHEN funder_type = 'unknown' THEN 1 END) as unknown_count
  FROM creator_funder_summary
  GROUP BY creator_address;

CREATE VIEW IF NOT EXISTS v_tombstone_stats_24h AS
  SELECT
      tombstone_type,
      COUNT(*) as count,
      ROUND(AVG(CAST(scan_count AS FLOAT)), 1) as avg_scans_before_tombstone,
      MIN(tombstone_created_at) as oldest,
      MAX(tombstone_created_at) as newest
  FROM wallet_analysis_state
  WHERE tombstone_type IS NOT NULL
    AND tombstone_created_at >= datetime('now', '-24 hours')
  GROUP BY tombstone_type;

-- ============================================================================
-- 6) Sample queries for monitoring optimization impact
-- ============================================================================

-- Query 1: Get optimization metrics for last 24h
-- SELECT * FROM v_optimization_metrics_24h;

-- Query 2: Estimate credits saved by tombstoning
-- SELECT
--     (SELECT COUNT(*) FROM wallet_scan_metrics WHERE tombstone_skip=1 AND created_at >= datetime('now', '-24 hours')) as skipped_scans,
--     (SELECT AVG(credits_estimated) FROM wallet_scan_metrics WHERE section='funder_incoming' AND created_at >= datetime('now', '-7 days')) as avg_credits_per_funder_scan,
--     ROUND(
--         (SELECT COUNT(*) FROM wallet_scan_metrics WHERE tombstone_skip=1 AND created_at >= datetime('now', '-24 hours')) *
--         (SELECT AVG(credits_estimated) FROM wallet_scan_metrics WHERE section='funder_incoming' AND created_at >= datetime('now', '-7 days')),
--         0
--     ) as estimated_credits_saved;

-- Query 3: Budget exhaustion tracking
-- SELECT
--     creator_address,
--     max_credits,
--     credits_spent,
--     ROUND(100.0 * credits_spent / max_credits, 1) as pct_budget_used,
--     funder_count,
--     ROUND(CAST(credits_spent) / funder_count, 1) as credits_per_funder
-- FROM creator_extraction_budget
-- WHERE credits_spent >= max_credits * 0.5  -- Budget at least 50% used
-- ORDER BY pct_budget_used DESC
-- LIMIT 20;

-- Query 4: Prefilter effectiveness
-- SELECT
--     creator_address,
--     total_funders,
--     shortlisted_funders,
--     shortlist_pct,
--     ROUND(total_inbound_sol, 2) as total_inbound_sol
-- FROM v_funder_prefilter_summary
-- WHERE total_funders >= 10  -- Only creators with 10+ funders
-- ORDER BY shortlist_pct DESC
-- LIMIT 20;

-- Query 5: 1-page vs multi-page breakdown
-- SELECT
--     (SELECT COUNT(*) FROM wallet_scan_metrics WHERE deep_scan_pages=1 AND section='funder_incoming' AND created_at >= datetime('now', '-24 hours')) as single_page_scans,
--     (SELECT COUNT(*) FROM wallet_scan_metrics WHERE deep_scan_pages > 1 AND section='funder_incoming' AND created_at >= datetime('now', '-24 hours')) as multi_page_scans,
--     (SELECT SUM(credits_estimated) FROM wallet_scan_metrics WHERE deep_scan_pages=1 AND section='funder_incoming' AND created_at >= datetime('now', '-24 hours')) as single_page_credits,
--     (SELECT SUM(credits_estimated) FROM wallet_scan_metrics WHERE deep_scan_pages > 1 AND section='funder_incoming' AND created_at >= datetime('now', '-24 hours')) as multi_page_credits;
