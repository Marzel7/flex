-- ============================================================================
-- FLEX System - Version 1.0 (Phase 10) - Baseline Schema
-- ============================================================================
-- Complete database schema for Flex network analysis system
-- Includes all tables, indexes, and constraints through Phase 10
--
-- Date: February 27, 2026
-- Schema Version: 10
-- System Version: 1.0.0
--
-- This file serves as the authoritative baseline schema for Version 1.0.
-- All future migrations should be incremental changes to this schema.
--
-- ============================================================================

-- ============================================================================
-- CORE TABLES: Network Structure & State
-- ============================================================================

CREATE TABLE IF NOT EXISTS networks_release (
    network_name TEXT PRIMARY KEY,
    network_size INTEGER,
    network_risk_level TEXT,
    network_type TEXT,
    has_cex_funder INTEGER DEFAULT 0,
    has_infra_funder INTEGER DEFAULT 0,
    cex_funder_count INTEGER DEFAULT 0,
    infra_funder_count INTEGER DEFAULT 0,
    stability_state TEXT,
    build_version INTEGER,
    last_built_at TEXT
);

CREATE TABLE IF NOT EXISTS network_membership (
    network_name TEXT,
    creator_address TEXT,
    PRIMARY KEY (network_name, creator_address)
);

CREATE TABLE IF NOT EXISTS network_scores (
    network_name TEXT PRIMARY KEY,
    score INTEGER DEFAULT 0,
    score_version INTEGER DEFAULT 1,
    smoothed_score REAL,
    stability_coeff REAL,
    stability_trend REAL,
    trend_direction TEXT,
    risk_band TEXT,
    build_version INTEGER
);

CREATE TABLE IF NOT EXISTS network_score_history (
    network_name TEXT,
    score INTEGER DEFAULT 0,
    smoothed_score REAL,
    stability_coeff REAL,
    stability_trend REAL,
    trend_direction TEXT,
    risk_band TEXT,
    build_version INTEGER,
    PRIMARY KEY (network_name, build_version)
);

-- ============================================================================
-- EVIDENCE TABLE: Network Coordination Evidence (Phase F)
-- ============================================================================

CREATE TABLE IF NOT EXISTS network_evidence (
    network_name TEXT PRIMARY KEY,
    total_edges INTEGER DEFAULT 0,
    total_evidence_txs INTEGER DEFAULT 0,
    average_confidence REAL DEFAULT 0.0,
    high_confidence_edges INTEGER DEFAULT 0,
    medium_confidence_edges INTEGER DEFAULT 0,
    low_confidence_edges INTEGER DEFAULT 0,
    earliest_evidence_time INTEGER,
    latest_evidence_time INTEGER,
    evidence_span_days INTEGER,
    unique_bridge_funders INTEGER DEFAULT 0,
    bridge_funder_list TEXT,
    evidence_risk_score REAL DEFAULT 0.0,
    evidence_version INTEGER DEFAULT 1,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_changed_at TIMESTAMP,
    FOREIGN KEY(network_name) REFERENCES networks_release(network_name)
        ON DELETE CASCADE
);

-- ============================================================================
-- MONITORING TABLES: Alerts & Escalation (Phases 4E, 7C-7E, 8B)
-- ============================================================================

CREATE TABLE IF NOT EXISTS network_alerts (
    alert_id TEXT PRIMARY KEY,
    network_name TEXT,
    alert_type TEXT,
    severity TEXT,
    message TEXT,
    created_at TEXT,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    suppressed_until TEXT,
    suppressed_at TEXT,
    suppression_reason TEXT,
    is_escalated INTEGER DEFAULT 0,
    escalated_at TEXT,
    escalation_reason TEXT,
    escalation_rule TEXT,
    operator_note TEXT,
    build_version INTEGER
);

-- ============================================================================
-- SYSTEM METADATA TABLE (Phase 10)
-- ============================================================================

CREATE TABLE IF NOT EXISTS system_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- SETTINGS TABLE: Configuration & Feature Flags
-- ============================================================================

CREATE TABLE IF NOT EXISTS listener_settings (
    setting_name TEXT PRIMARY KEY,
    setting_value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES: Performance Optimization (Phase 9A + Prior Phases)
-- ============================================================================

-- Network access indexes
CREATE INDEX IF NOT EXISTS idx_network_scores_network ON network_scores(network_name);
CREATE INDEX IF NOT EXISTS idx_network_membership_network ON network_membership(network_name);
CREATE INDEX IF NOT EXISTS idx_network_membership_creator ON network_membership(creator_address);

-- Score history indexes (Phase 9A)
CREATE INDEX IF NOT EXISTS idx_network_score_history_network ON network_score_history(network_name);
CREATE INDEX IF NOT EXISTS idx_network_score_history_build ON network_score_history(build_version);
CREATE INDEX IF NOT EXISTS idx_network_score_history_build_version ON network_score_history(build_version DESC);
CREATE INDEX IF NOT EXISTS idx_network_score_history_network_build ON network_score_history(network_name, build_version DESC);

-- Alert indexes (Phase 8B + 9A)
CREATE INDEX IF NOT EXISTS idx_network_alerts_network ON network_alerts(network_name);
CREATE INDEX IF NOT EXISTS idx_network_alerts_created ON network_alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_network_alerts_created_at_desc ON network_alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_network_alerts_acknowledged_created ON network_alerts(acknowledged, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_network_alerts_escalated_created ON network_alerts(is_escalated, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_network_alerts_severity_type_created ON network_alerts(severity, alert_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_network_alerts_suppressed_until ON network_alerts(suppressed_until);

-- Network score indexes (Phase 9A)
CREATE INDEX IF NOT EXISTS idx_network_scores_risk_band ON network_scores(risk_band, smoothed_score DESC);
CREATE INDEX IF NOT EXISTS idx_network_scores_trend ON network_scores(stability_trend DESC);

-- Evidence indexes (Phase F)
CREATE INDEX IF NOT EXISTS idx_network_evidence_risk ON network_evidence(evidence_risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_network_evidence_updated ON network_evidence(last_updated_at DESC);

-- System metadata indexes
CREATE INDEX IF NOT EXISTS idx_system_metadata_key ON system_metadata(key);

-- ============================================================================
-- SCHEMA VERIFICATION
-- ============================================================================
-- To verify this schema is current:
--   sqlite3 database.db ".schema"
--
-- To check version:
--   SELECT value FROM system_metadata WHERE key = 'SCHEMA_VERSION';
--   Expected: 10
--
-- To verify key tables:
--   .tables
--   Expected: networks_release, network_scores, network_score_history,
--             network_alerts, network_evidence, system_metadata, listener_settings

-- ============================================================================
-- NOTES
-- ============================================================================
-- 1. All columns use DEFAULT values where appropriate (idempotency)
-- 2. All indexes use IF NOT EXISTS (idempotency)
-- 3. Foreign keys enabled (network_evidence → networks_release)
-- 4. No DROP statements (additive only)
-- 5. Timestamps use TEXT (ISO 8601) for cross-database compatibility
-- 6. All schema changes through Phase 10 captured here
--
-- Future schema versions should build incrementally on this baseline.
