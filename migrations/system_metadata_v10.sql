-- Phase 10: System Metadata Table Migration
-- Version: 10
-- Created: February 27, 2026
-- Description: Adds system_metadata table for version tracking and build recording

-- ============================================================================
-- System Metadata Table (IDEMPOTENT)
-- ============================================================================
-- Stores version information, last build metadata, and system state
-- Used for operational tracking and release governance

CREATE TABLE IF NOT EXISTS system_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Required Metadata Keys
-- ============================================================================
-- The following keys are updated at the end of every successful build:
--
-- SYSTEM_VERSION - Current release version (e.g., "1.0.0")
-- SCHEMA_VERSION - Database schema version number (e.g., "10")
-- BUILD_PIPELINE_VERSION - Build pipeline version (e.g., "10")
-- LAST_BUILD_VERSION - Latest build_version from network_scores
-- LAST_BUILD_AT - ISO 8601 timestamp of last build
-- LAST_BUILD_DURATION_MS - Total build time in milliseconds
-- LAST_BUILD_NETWORKS_PROCESSED - Count of networks in last build
-- LAST_BUILD_ALERTS_INSERTED - Count of alerts inserted in last build
-- LAST_BUILD_ESCALATIONS_SET - Count of alerts escalated in last build

-- Safety guarantees:
-- ✅ All entries idempotent (INSERT OR REPLACE)
-- ✅ No performance impact (single table, indexed primary key)
-- ✅ Only updated on successful builds
-- ✅ Backward compatible (new table, no schema changes)

-- ============================================================================
-- Verification Query
-- ============================================================================
-- Check metadata after build:
--   SELECT key, value, updated_at FROM system_metadata ORDER BY key;
--
-- Check specific keys:
--   SELECT value FROM system_metadata WHERE key = 'SYSTEM_VERSION';
--   SELECT value FROM system_metadata WHERE key = 'LAST_BUILD_AT';
