-- FLEX V2 Phase 3 Migration: Transfer Indexing
-- Safe to run against production (CREATE IF NOT EXISTS, idempotent)
--
-- Adds local database indexing of SOL transfers parsed from transactions.
-- Enables SQL-based funding analysis (eliminates 90-95% of remaining RPC calls).
-- Expected savings: 90-95% reduction on getSignaturesForAddress + getTransaction calls
--
-- Combined with Phase 1 + Phase 2: 98%+ total RPC reduction vs baseline
--
-- Schema note: uniqueness is on (signature, source, destination) not signature alone.
-- One on-chain transaction can contain multiple native SOL transfers with different
-- counterparties; a per-signature UNIQUE constraint would silently drop all but the
-- first transfer in such transactions.

CREATE TABLE IF NOT EXISTS transfer_index (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signature           TEXT NOT NULL,                       -- Transaction signature
    source              TEXT NOT NULL,                       -- Source wallet address
    destination         TEXT NOT NULL,                       -- Destination wallet address
    amount_lamports     INTEGER NOT NULL,                    -- Transfer amount in lamports (avoid floats)
    amount_sol          REAL GENERATED ALWAYS AS (amount_lamports / 1e9) STORED,  -- SOL equivalent
    slot                INTEGER NOT NULL DEFAULT 0,          -- Slot number (0 when unavailable from feed)
    block_time          INTEGER NOT NULL,                    -- Unix timestamp
    indexed_at          REAL NOT NULL,                       -- When indexed (time.time())
    is_valid            BOOLEAN NOT NULL DEFAULT 1,          -- Validation flag (for cleanup)
    transfer_type       TEXT DEFAULT 'standard',             -- 'standard', 'spl_token', 'rent', etc.

    -- Composite uniqueness: one row per (tx, sender, recipient) pair
    UNIQUE (signature, source, destination),

    -- Constraints
    CHECK (amount_lamports > 0),
    CHECK (block_time > 0)
);

-- Strategic indexing for common funding queries
-- Index 1: Find all funders for a destination (critical path)
CREATE INDEX IF NOT EXISTS idx_transfer_destination_time
ON transfer_index(destination, block_time DESC);

-- Index 2: Find all creators funded by a source (critical path)
CREATE INDEX IF NOT EXISTS idx_transfer_source_time
ON transfer_index(source, block_time DESC);

-- Index 3: Time-range queries (30-day windows, etc.)
CREATE INDEX IF NOT EXISTS idx_transfer_block_time
ON transfer_index(block_time DESC);

-- Index 4: Cleanup queries (expired entries)
CREATE INDEX IF NOT EXISTS idx_transfer_indexed_at
ON transfer_index(indexed_at);

-- Index 5: Validation queries
CREATE INDEX IF NOT EXISTS idx_transfer_valid
ON transfer_index(is_valid) WHERE is_valid = 1;

-- Composite index for common combined queries (source + time range)
CREATE INDEX IF NOT EXISTS idx_transfer_source_amount_time
ON transfer_index(source, amount_lamports DESC, block_time DESC);

-- Verification query (can be run to validate table)
-- SELECT
--     COUNT(*) as total_transfers,
--     COUNT(CASE WHEN is_valid = 1 THEN 1 END) as valid_transfers,
--     MIN(block_time) as earliest_block,
--     MAX(block_time) as latest_block,
--     COUNT(DISTINCT source) as unique_sources,
--     COUNT(DISTINCT destination) as unique_destinations
-- FROM transfer_index;
