-- FLEX Webhook-First Low-RPC Architecture Schema
-- Created: 2026-03-03
-- Tables: sol_transfers, address_activity, work_queue

-- ============================================================================
-- TABLE: sol_transfers
-- Purpose: Deduplicated SOL transfers extracted from Helius RAW webhooks
-- ============================================================================
CREATE TABLE IF NOT EXISTS sol_transfers (
    signature TEXT PRIMARY KEY,
    slot INTEGER NOT NULL,
    block_time INTEGER NOT NULL,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    lamports INTEGER NOT NULL,
    amount_sol REAL NOT NULL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sol_transfers_source ON sol_transfers(source);
CREATE INDEX IF NOT EXISTS idx_sol_transfers_destination ON sol_transfers(destination);
CREATE INDEX IF NOT EXISTS idx_sol_transfers_block_time ON sol_transfers(block_time DESC);
CREATE INDEX IF NOT EXISTS idx_sol_transfers_received_at ON sol_transfers(received_at DESC);

-- ============================================================================
-- TABLE: address_activity
-- Purpose: Rolling statistics for each address seen in transfers
-- Updated by webhook handler and worker
-- ============================================================================
CREATE TABLE IF NOT EXISTS address_activity (
    address TEXT PRIMARY KEY,
    last_seen_at INTEGER NOT NULL,
    tx_5m INTEGER DEFAULT 0,
    tx_1h INTEGER DEFAULT 0,
    tx_24h INTEGER DEFAULT 0,
    sol_in_5m REAL DEFAULT 0.0,
    sol_in_1h REAL DEFAULT 0.0,
    sol_in_24h REAL DEFAULT 0.0,
    sol_out_5m REAL DEFAULT 0.0,
    sol_out_1h REAL DEFAULT 0.0,
    sol_out_24h REAL DEFAULT 0.0,
    last_processed_at INTEGER,
    last_rpc_fetch_at INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_address_activity_last_seen ON address_activity(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_address_activity_updated ON address_activity(updated_at DESC);

-- ============================================================================
-- TABLE: work_queue
-- Purpose: Priority queue for addresses to analyze
-- Locked rows won't be picked up until locked_until expires
-- ============================================================================
CREATE TABLE IF NOT EXISTS work_queue (
    address TEXT PRIMARY KEY,
    priority REAL DEFAULT 0.0,
    reason TEXT,
    next_run_at INTEGER DEFAULT 0,
    locked_until INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_work_queue_priority ON work_queue(priority DESC);
CREATE INDEX IF NOT EXISTS idx_work_queue_next_run ON work_queue(next_run_at ASC);
CREATE INDEX IF NOT EXISTS idx_work_queue_locked ON work_queue(locked_until ASC);

-- ============================================================================
-- PRAGMA Settings (to be applied at runtime in Python)
-- ============================================================================
-- PRAGMA journal_mode = WAL;
-- PRAGMA synchronous = NORMAL;
-- PRAGMA busy_timeout = 30000;
-- PRAGMA cache_size = -64000;
