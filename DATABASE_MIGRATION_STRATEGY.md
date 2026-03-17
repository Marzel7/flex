# PumpSwap Database Migration & Validation Strategy

**Date:** 2026-03-17
**Database:** flex_complete_database.db
**Legacy Rows:** 63 (all pre-pipeline)
**Strategy:** Safe co-existence of legacy and new pipeline data

---

## Executive Summary

The 63 existing pool rows were created before the new discovery pipeline was deployed. They lack:
- `pool_address` (NEW field)
- `discovery_method` (NEW tracking)
- Telemetry entries (NEW table)

**Solution:** Separate legacy and new data, normalize program IDs, optionally backfill missing data, and validate only new pipeline rows.

**Timeline:** Can be done in-place with zero downtime using flags.

---

## 1. Legacy Data Identification Strategy

### 1.1 Characteristics of Legacy Rows

Legacy rows were created before fixes and exhibit:

```
✗ pool_address IS NULL                    → Column existed but not populated
✗ discovery_method IN ('unknown', NULL)   → New tracking didn't exist
✗ pool_program IN ('pumpfun_v1', 'pumpswap', 'unknown')  → Label format, not ID
✗ vault_validation_status IS NULL         → May be pending (old state)
✓ base_account & quote_account exist      → Minimal required data
```

### 1.2 Marking Legacy Rows

Add a flag column to mark rows created before the new pipeline:

```sql
-- Add flag column
ALTER TABLE token_pool_accounts
ADD COLUMN IF NOT EXISTS is_legacy INTEGER DEFAULT 0;

-- Mark all existing rows as legacy
-- These are rows without pool_address or with invalid program IDs
UPDATE token_pool_accounts
SET is_legacy = 1
WHERE pool_address IS NULL
   OR discovery_method IS NULL
   OR pool_program NOT IN (
       'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',  -- PumpSwap
       '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',  -- Raydium v4
       '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',  -- PumpFun V1
       'whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco',    -- Orca
       'Liq7fJg2yVHhbPPqqEDSVGMtPVaYYkSBPP8Y63QNhJS'   -- Meteora
   );

-- Verify
SELECT is_legacy, COUNT(*) as count
FROM token_pool_accounts
GROUP BY is_legacy;

-- Expected: is_legacy=1 → 63, is_legacy=0 → 0 (initially)
```

### 1.3 Timestamp-Based Identification (Alternative)

If flag approach is problematic, use deployment timestamp:

```sql
-- Deployment of fixes was 2026-03-17 ~17:30:00 UTC
-- Rows with created_at before this are legacy

SELECT COUNT(*)
FROM token_pool_accounts
WHERE created_at < 1710777000;  -- Unix timestamp for 2026-03-17 17:30:00

-- Rows after this timestamp are new pipeline
SELECT COUNT(*)
FROM token_pool_accounts
WHERE created_at >= 1710777000;
```

---

## 2. Program Normalization Strategy

### 2.1 Mapping Legacy Labels to Program IDs

Legacy data uses inconsistent formats:

| Legacy Value | Canonical ID | Description |
|---|---|---|
| `pumpswap` | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | PumpSwap (uses Raydium layout) |
| `pumpfun_v1` | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | PumpFun V1 |
| `raydium_v4` | `675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K` | Raydium AMM v4 |
| `unknown` | NULL or infer | Try to infer from vault owner |
| `pumpfun` | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | Legacy label for PumpFun V1 |

### 2.2 Normalization SQL

```sql
-- Create mapping table (optional, for audit trail)
CREATE TABLE IF NOT EXISTS program_id_mapping (
    legacy_label TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    description TEXT,
    created_at INTEGER
);

INSERT INTO program_id_mapping VALUES
    ('pumpswap', 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA', 'PumpSwap', strftime('%s', 'now')),
    ('pumpfun_v1', '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', 'PumpFun V1', strftime('%s', 'now')),
    ('raydium_v4', '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K', 'Raydium AMM v4', strftime('%s', 'now')),
    ('pumpfun', '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', 'Legacy PumpFun label', strftime('%s', 'now'));

-- Normalize pool_program values
UPDATE token_pool_accounts
SET pool_program = (
    SELECT canonical_id FROM program_id_mapping
    WHERE program_id_mapping.legacy_label = token_pool_accounts.pool_program
)
WHERE pool_program IN ('pumpswap', 'pumpfun_v1', 'raydium_v4', 'pumpfun');

-- Verify normalization
SELECT DISTINCT pool_program
FROM token_pool_accounts
WHERE pool_program IS NOT NULL
ORDER BY pool_program;

-- Check remaining 'unknown' values
SELECT COUNT(*) as unknown_count
FROM token_pool_accounts
WHERE pool_program = 'unknown' OR pool_program IS NULL;
```

### 2.3 Handling 'unknown' Program IDs

For rows with `pool_program = 'unknown'`, attempt inference:

```python
# Pseudocode for inference job
def infer_pool_program(base_vault: str, quote_vault: str) -> Optional[str]:
    """Infer pool program from vault owners."""

    # Fetch vault account info from RPC
    base_owner = fetch_account_owner(base_vault)

    # Match against known program IDs
    KNOWN_PROGRAMS = {
        'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA': 'SPL Token Program',
        'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA': 'pumpswap',
        '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K': 'raydium_v4',
    }

    # If vaults are SPL token accounts, pool program is likely
    # the account creator or can be inferred from migration TX

    if base_owner == SPL_TOKEN_PROGRAM:
        # Check migration TX or use default
        return 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'  # Default PumpSwap

    return None
```

---

## 3. Backfill Strategy

### 3.1 Pool Address Backfill

For rows with `pool_address IS NULL`, re-run discovery to populate it.

#### Conditions for Backfill:

```sql
-- Rows eligible for backfill (have enough data)
SELECT
    mint,
    base_account,
    quote_account,
    pool_program
FROM token_pool_accounts
WHERE pool_address IS NULL
  AND base_account IS NOT NULL
  AND quote_account IS NOT NULL
  AND pool_program IS NOT NULL
  AND is_legacy = 1
LIMIT 20;
```

#### Backfill Worker Pseudocode:

```python
class BackfillWorker:
    """Backfill missing pool_address for legacy rows."""

    def __init__(self, db_path: str, rpc_url: str):
        self.db = Database(db_path)
        self.rpc = RpcClient(rpc_url)
        self.stats = {'processed': 0, 'updated': 0, 'failed': 0}

    async def backfill_pool_addresses(self, limit: int = 100):
        """Backfill pool_address for legacy rows."""

        # Get eligible rows
        rows = self.db.query("""
            SELECT mint, base_account, quote_account, pool_program
            FROM token_pool_accounts
            WHERE pool_address IS NULL
              AND base_account IS NOT NULL
              AND quote_account IS NOT NULL
              AND is_legacy = 1
            LIMIT ?
        """, (limit,))

        for row in rows:
            try:
                # Option 1: Try vault inference
                pool_addr = await self._infer_from_vaults(
                    row['base_account'],
                    row['quote_account'],
                    row['pool_program']
                )

                if pool_addr:
                    # Update row
                    self.db.execute("""
                        UPDATE token_pool_accounts
                        SET pool_address = ?
                        WHERE mint = ?
                    """, (pool_addr, row['mint']))

                    self.stats['updated'] += 1
                else:
                    self.stats['failed'] += 1

                self.stats['processed'] += 1

            except Exception as e:
                logger.error(f"Error processing {row['mint']}: {e}")
                self.stats['failed'] += 1

        return self.stats

    async def _infer_from_vaults(self, base_vault: str,
                                  quote_vault: str,
                                  pool_program: str) -> Optional[str]:
        """Infer pool address from vault accounts."""

        # For PumpSwap/Raydium pools, pool account is deterministic
        # Can derive from vault pair + program ID

        # Try to find pool account that owns both vaults
        # (simplified - real implementation would use PDAs or on-chain lookup)

        # For now, skip backfill if no other data available
        return None

    def report(self):
        """Print backfill statistics."""
        print(f"Backfill Report:")
        print(f"  Processed: {self.stats['processed']}")
        print(f"  Updated: {self.stats['updated']}")
        print(f"  Failed: {self.stats['failed']}")
```

#### Running Backfill:

```bash
# Option 1: Simple SQL approach (if pool_address can be derived)
# This requires domain knowledge of how pools are created

# Option 2: Async backfill worker
python3 << 'EOF'
import asyncio
from backfill_worker import BackfillWorker

async def main():
    worker = BackfillWorker(
        db_path="database/flex_complete_database.db",
        rpc_url=os.getenv("HELIUS_RPC_URL")
    )
    stats = await worker.backfill_pool_addresses(limit=63)
    worker.report()

asyncio.run(main())
EOF
```

### 3.2 Discovery Method Backfill

For legacy rows, set discovery_method based on what data we have:

```sql
-- If row has migration_tx and was validated, likely from TX parsing
UPDATE token_pool_accounts
SET discovery_method = 'tx_parsing'
WHERE is_legacy = 1
  AND discovery_method IS NULL
  AND vault_validation_status = 'validated'
LIMIT 30;

-- Remaining legacy validated rows likely from RPC or vault inference
UPDATE token_pool_accounts
SET discovery_method = 'rpc_multipool_discovery'
WHERE is_legacy = 1
  AND discovery_method IS NULL
  AND vault_validation_status = 'validated';

-- Unvalidated legacy rows mark as unknown
UPDATE token_pool_accounts
SET discovery_method = 'unknown'
WHERE is_legacy = 1
  AND discovery_method IS NULL;
```

---

## 4. Invalid Row Handling Strategy

### 4.1 Identifying Invalid Rows

Invalid rows that cannot be fixed:

```sql
-- Rows with impossible state (base == quote)
SELECT COUNT(*) as invalid_count
FROM token_pool_accounts
WHERE base_account = quote_account;
-- Result: 25

-- Rows with zero vaults
SELECT COUNT(*)
FROM token_pool_accounts
WHERE base_account = '11111111111111111111111111111111'
   OR quote_account = '11111111111111111111111111111111';
```

### 4.2 Quarantine Strategy

Instead of deleting, mark as inactive:

```sql
-- Add is_active flag
ALTER TABLE token_pool_accounts
ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1;

-- Quarantine invalid rows
UPDATE token_pool_accounts
SET is_active = 0,
    is_legacy = 1
WHERE base_account = quote_account
   OR base_account = '11111111111111111111111111111111'
   OR quote_account = '11111111111111111111111111111111';

-- Verify quarantine
SELECT COUNT(*) as inactive_count
FROM token_pool_accounts
WHERE is_active = 0;
```

### 4.3 Audit Trail

Create an audit table for tracking changes:

```sql
CREATE TABLE IF NOT EXISTS migration_audit (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    action TEXT NOT NULL,  -- 'marked_legacy', 'normalized_program', 'backfilled', 'quarantined'
    change_details TEXT,
    applied_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

-- Log quarantine action
INSERT INTO migration_audit (mint, action, change_details, applied_at, created_at)
SELECT
    mint,
    'quarantined',
    'base_account == quote_account',
    strftime('%s', 'now'),
    strftime('%s', 'now')
FROM token_pool_accounts
WHERE base_account = quote_account
  AND is_active = 1;

UPDATE token_pool_accounts
SET is_active = 0
WHERE base_account = quote_account;
```

---

## 5. Telemetry Validation Strategy

### 5.1 Why Telemetry Table is Empty

The `token_resolution_telemetry` table is empty because:

1. **Table is NEW** — Created as part of the fixes
2. **Old code didn't write to it** — No historical writes
3. **Writes happen on NEW registrations** — Not retroactively

### 5.2 Telemetry Debugging Checklist

```bash
#!/bin/bash
# Telemetry verification script

echo "=== Telemetry Validation Checklist ==="
echo ""

# 1. Table exists
echo "[1] Checking telemetry table exists..."
sqlite3 database/flex_complete_database.db \
  ".schema token_resolution_telemetry" | head -3

# 2. Row count
echo ""
echo "[2] Checking row count..."
TELEMETRY_COUNT=$(sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM token_resolution_telemetry")
echo "  Rows in table: $TELEMETRY_COUNT"

# 3. Check if code writes to it
echo ""
echo "[3] Checking if listener writes to telemetry..."
grep -n "token_resolution_telemetry" src/core/pumpfun_curve_listener.py | head -5

# 4. Check recent writes
echo ""
echo "[4] Checking for recent telemetry writes..."
sqlite3 database/flex_complete_database.db << 'EOF'
SELECT
    COUNT(*) as total,
    MAX(created_at) as last_write,
    CASE
        WHEN MAX(created_at) > strftime('%s', 'now') - 3600
            THEN 'Recent (< 1 hour)'
        WHEN MAX(created_at) > strftime('%s', 'now') - 86400
            THEN 'Old (< 24 hours)'
        ELSE 'Stale (> 24 hours)'
    END as status
FROM token_resolution_telemetry;
EOF

# 5. Sample telemetry rows
echo ""
echo "[5] Sample telemetry rows..."
sqlite3 database/flex_complete_database.db \
  "SELECT mint, detected_at, resolved_at, resolve_source FROM token_resolution_telemetry LIMIT 5"

echo ""
echo "=== Expected Behavior ==="
echo "✓ Table exists: token_resolution_telemetry"
echo "✓ Code writes: 5+ grep matches in listener"
echo "✓ Writes active: Recent rows for new registrations"
echo "✓ Rows empty: Normal until first new registration"
```

### 5.3 Enable Telemetry for Legacy Rows (Optional)

If you want to backfill telemetry for legacy rows:

```sql
-- Insert historical telemetry records
-- Mark as resolved with estimated resolution
INSERT INTO token_resolution_telemetry (
    mint,
    detected_at,
    resolved_at,
    resolve_seconds,
    resolve_source,
    retry_count,
    pool_address,
    created_at,
    updated_at
)
SELECT
    tpa.mint,
    CASE
        WHEN tpa.created_at IS NOT NULL
            THEN tpa.created_at
        ELSE strftime('%s', 'now')
    END as detected_at,
    CASE
        WHEN tpa.vault_validation_status = 'validated'
            THEN tpa.created_at + 5  -- Assume 5 second resolution
        ELSE NULL
    END as resolved_at,
    CASE
        WHEN tpa.vault_validation_status = 'validated'
            THEN 5.0
        ELSE NULL
    END as resolve_seconds,
    COALESCE(tpa.discovery_method, 'unknown') as resolve_source,
    0 as retry_count,
    tpa.pool_address,
    strftime('%s', 'now') as created_at,
    strftime('%s', 'now') as updated_at
FROM token_pool_accounts tpa
WHERE tpa.is_legacy = 1
  AND NOT EXISTS (
      SELECT 1 FROM token_resolution_telemetry
      WHERE mint = tpa.mint
  );
```

---

## 6. Updated Validation Rules

### 6.1 Validation Query Pattern

All validation should exclude legacy rows or use new pipeline filters:

```python
class ValidationQueryBuilder:
    """Build validation queries that separate legacy from new data."""

    @staticmethod
    def query_new_pipeline_only(base_query: str) -> str:
        """Add filter for new pipeline rows only."""
        return f"{base_query} WHERE is_legacy = 0"

    @staticmethod
    def query_legacy_only(base_query: str) -> str:
        """Add filter for legacy rows only."""
        return f"{base_query} WHERE is_legacy = 1"

    @staticmethod
    def query_active_only(base_query: str) -> str:
        """Add filter for active (non-quarantined) rows."""
        return f"{base_query} WHERE is_active = 1"
```

### 6.2 Discovery Validation (New Pipeline Only)

```sql
-- Discovery validation for NEW pipeline rows only
SELECT
    COUNT(*) as total_new_pools,
    COUNT(CASE WHEN pool_address IS NOT NULL THEN 1 END) as with_pool_address,
    COUNT(CASE WHEN discovery_method NOT IN ('unknown', NULL) THEN 1 END) as with_discovery_method,
    COUNT(CASE WHEN base_account != quote_account THEN 1 END) as with_valid_vaults,
    COUNT(CASE WHEN pool_program IN (
        'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
        '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
        '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
    ) THEN 1 END) as with_valid_program,
    ROUND(100.0 * COUNT(CASE WHEN pool_address IS NOT NULL THEN 1 END) /
        COUNT(*), 1) as pool_address_pct,
    ROUND(100.0 * COUNT(CASE WHEN discovery_method NOT IN ('unknown', NULL) THEN 1 END) /
        COUNT(*), 1) as discovery_method_pct
FROM token_pool_accounts
WHERE is_legacy = 0
  AND is_active = 1;
```

### 6.3 Vault Validation (New Pipeline Only)

```sql
SELECT
    COUNT(*) as total_new_pools,
    COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) as validated,
    COUNT(CASE WHEN vault_validation_status = 'pending' THEN 1 END) as pending,
    ROUND(100.0 * COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) /
        COUNT(*), 1) as validation_rate_pct
FROM token_pool_accounts
WHERE is_legacy = 0
  AND is_active = 1;
```

### 6.4 Registration Validation (New Pipeline Only)

```sql
SELECT
    COUNT(*) as total_new_pools,
    COUNT(pool_address) as has_pool_address,
    COUNT(base_account) as has_base_account,
    COUNT(quote_account) as has_quote_account,
    COUNT(discovery_method) as has_discovery_method,
    COUNT(pool_score) as has_pool_score,
    ROUND(100.0 * COUNT(pool_address) / COUNT(*), 1) as pool_address_pct,
    ROUND(100.0 * COUNT(discovery_method) / COUNT(*), 1) as discovery_method_pct
FROM token_pool_accounts
WHERE is_legacy = 0
  AND is_active = 1;
```

### 6.5 Telemetry Validation (New Pipeline Only)

```sql
SELECT
    COUNT(*) as total_detected,
    COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) as resolved,
    ROUND(100.0 * COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
        COUNT(*), 1) as resolution_rate_pct,
    ROUND(AVG(resolve_seconds), 2) as avg_resolve_seconds,
    COUNT(CASE WHEN resolve_source = 'tx_parsing' THEN 1 END) as tx_parsing_count,
    COUNT(CASE WHEN resolve_source = 'vault_inference' THEN 1 END) as vault_inference_count,
    COUNT(CASE WHEN resolve_source = 'rpc_multipool_discovery' THEN 1 END) as rpc_count
FROM token_resolution_telemetry
WHERE created_at > strftime('%s', 'now') - 86400;
```

---

## 7. Post-Migration Health Checks

### 7.1 Validation Baseline

```sql
-- 1. Legacy vs New breakdown
SELECT
    'Legacy rows' as category,
    COUNT(*) as count,
    COUNT(CASE WHEN is_active = 1 THEN 1 END) as active
FROM token_pool_accounts
WHERE is_legacy = 1
UNION ALL
SELECT
    'New pipeline rows',
    COUNT(*),
    COUNT(CASE WHEN is_active = 1 THEN 1 END)
FROM token_pool_accounts
WHERE is_legacy = 0;

-- Expected:
-- Legacy: 63 active (or fewer if quarantined)
-- New: 0 → N as new registrations occur
```

### 7.2 Expected Health Metrics

```sql
-- 2. Discovery completeness (NEW rows only)
WITH discovery_stats AS (
    SELECT
        COUNT(*) as total,
        COUNT(CASE WHEN pool_address IS NOT NULL THEN 1 END) as with_pool_address,
        COUNT(CASE WHEN discovery_method NOT IN ('unknown', NULL) THEN 1 END) as with_method
    FROM token_pool_accounts
    WHERE is_legacy = 0 AND is_active = 1
)
SELECT
    ROUND(100.0 * with_pool_address / NULLIF(total, 0), 1) as pool_address_pct,
    ROUND(100.0 * with_method / NULLIF(total, 0), 1) as discovery_method_pct,
    CASE
        WHEN total = 0 THEN 'No new data yet'
        WHEN pool_address_pct >= 99 AND discovery_method_pct >= 90 THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM discovery_stats;

-- 3. Vault validation rate (NEW rows only)
SELECT
    COUNT(*) as total_new_pools,
    COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) as validated,
    ROUND(100.0 * COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) /
        COUNT(*), 1) as validation_rate_pct,
    CASE
        WHEN validation_rate_pct >= 95 THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM token_pool_accounts
WHERE is_legacy = 0 AND is_active = 1;

-- 4. Telemetry resolution rate
SELECT
    COUNT(*) as total_detected,
    COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) as resolved,
    ROUND(100.0 * COUNT(CASE WHEN resolved_at IS NOT NULL THEN 1 END) /
        COUNT(*), 1) as resolution_rate_pct,
    CASE
        WHEN total_detected = 0 THEN 'Awaiting first registration'
        WHEN resolution_rate_pct >= 95 THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM token_resolution_telemetry
WHERE created_at > strftime('%s', 'now') - 86400;

-- 5. Snapshot coverage (NEW pools only)
SELECT
    COUNT(DISTINCT tpa.mint) as new_pools_registered,
    COUNT(DISTINCT tps.mint) as pools_with_snapshots,
    COUNT(DISTINCT CASE WHEN tps.source = 'pool' THEN tps.mint END) as with_pool_source,
    ROUND(100.0 * COUNT(DISTINCT tps.mint) / NULLIF(COUNT(DISTINCT tpa.mint), 0), 1) as coverage_pct,
    CASE
        WHEN coverage_pct >= 90 THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM token_pool_accounts tpa
LEFT JOIN token_price_snapshots tps ON (tpa.mint = tps.mint)
WHERE tpa.is_legacy = 0 AND tpa.is_active = 1;
```

### 7.3 Health Check Script

```bash
#!/bin/bash
# Post-migration health check

echo "=== Post-Migration Health Checks ==="
echo ""

DB="database/flex_complete_database.db"

# 1. Data separation
echo "[1] Legacy vs New Data Separation"
sqlite3 $DB << 'EOF'
SELECT is_legacy, COUNT(*) FROM token_pool_accounts GROUP BY is_legacy;
EOF

# 2. Program normalization
echo ""
echo "[2] Program ID Normalization"
sqlite3 $DB << 'EOF'
SELECT DISTINCT pool_program FROM token_pool_accounts
WHERE pool_program NOT IN (
    'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
    '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
    '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
);
EOF

# 3. Invalid row quarantine
echo ""
echo "[3] Quarantined Invalid Rows"
sqlite3 $DB "SELECT COUNT(*) FROM token_pool_accounts WHERE is_active = 0"

# 4. New pipeline readiness
echo ""
echo "[4] New Pipeline Readiness"
python3 validation_harness.py --check all

# 5. Telemetry status
echo ""
echo "[5] Telemetry Status"
sqlite3 $DB "SELECT COUNT(*) as telemetry_rows FROM token_resolution_telemetry"
```

---

## 8. Production Rollout Strategy

### 8.1 Phase 1: Preparation (Zero Downtime)

**Goal:** Mark legacy data without affecting operations

```bash
# 1. Back up database
cp database/flex_complete_database.db database/flex_complete_database.db.backup

# 2. Add migration columns
sqlite3 database/flex_complete_database.db << 'EOF'
ALTER TABLE token_pool_accounts ADD COLUMN IF NOT EXISTS is_legacy INTEGER DEFAULT 0;
ALTER TABLE token_pool_accounts ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 1;
EOF

# 3. Mark legacy rows
sqlite3 database/flex_complete_database.db << 'EOF'
UPDATE token_pool_accounts
SET is_legacy = 1
WHERE pool_address IS NULL
   OR discovery_method IS NULL;
EOF

# 4. Verify
sqlite3 database/flex_complete_database.db \
  "SELECT is_legacy, COUNT(*) FROM token_pool_accounts GROUP BY is_legacy"
```

### 8.2 Phase 2: Normalization (Low Risk)

**Goal:** Standardize program IDs

```bash
# 1. Create mapping table
sqlite3 database/flex_complete_database.db << 'EOF'
CREATE TABLE IF NOT EXISTS program_id_mapping (
    legacy_label TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL
);

INSERT OR IGNORE INTO program_id_mapping VALUES
    ('pumpswap', 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'),
    ('pumpfun_v1', '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'),
    ('raydium_v4', '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K');
EOF

# 2. Normalize
sqlite3 database/flex_complete_database.db << 'EOF'
UPDATE token_pool_accounts
SET pool_program = (
    SELECT canonical_id FROM program_id_mapping
    WHERE program_id_mapping.legacy_label = token_pool_accounts.pool_program
)
WHERE pool_program IN ('pumpswap', 'pumpfun_v1', 'raydium_v4');
EOF

# 3. Verify
sqlite3 database/flex_complete_database.db \
  "SELECT DISTINCT pool_program FROM token_pool_accounts"
```

### 8.3 Phase 3: Quarantine (Safe)

**Goal:** Isolate invalid rows

```bash
sqlite3 database/flex_complete_database.db << 'EOF'
UPDATE token_pool_accounts
SET is_active = 0,
    is_legacy = 1
WHERE base_account = quote_account
   OR base_account = '11111111111111111111111111111111'
   OR quote_account = '11111111111111111111111111111111';
EOF
```

### 8.4 Phase 4: Backfill (Optional)

**Goal:** Populate missing fields

```bash
# 1. Run backfill worker for pool_address
python3 << 'EOF'
import asyncio
from backfill_worker import BackfillWorker

async def main():
    worker = BackfillWorker(
        db_path="database/flex_complete_database.db",
        rpc_url=os.getenv("HELIUS_RPC_URL")
    )
    stats = await worker.backfill_pool_addresses(limit=63)
    print(f"Backfill result: {stats}")

asyncio.run(main())
EOF

# 2. Update discovery_method
sqlite3 database/flex_complete_database.db << 'EOF'
UPDATE token_pool_accounts
SET discovery_method = 'tx_parsing'
WHERE is_legacy = 1
  AND discovery_method IS NULL
  AND vault_validation_status = 'validated'
LIMIT 30;

UPDATE token_pool_accounts
SET discovery_method = 'unknown'
WHERE is_legacy = 1
  AND discovery_method IS NULL;
EOF
```

### 8.5 Phase 5: Validation (Confirmation)

**Goal:** Verify migration succeeded

```bash
# Run validation on new pipeline rows only
python3 validation_harness.py --check all

# Expected: All new-pipeline checks pass
# Legacy checks can fail (expected)
```

### 8.6 Phase 6: Deployment (Production)

**Goal:** Deploy fixed listener with new validation rules

```bash
# 1. Update validation queries to use is_legacy=0 filter
# (Already done in validation_harness.py)

# 2. Deploy fixed listener
git checkout main
git pull
source .env
python3 -m src.core.pumpfun_curve_listener

# 3. Monitor telemetry
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*) FROM token_resolution_telemetry WHERE created_at > strftime('%s', 'now') - 60"

# 4. Confirm new registrations have all required fields
sqlite3 database/flex_complete_database.db << 'EOF'
SELECT COUNT(*) as new_pools
FROM token_pool_accounts
WHERE is_legacy = 0
  AND pool_address IS NOT NULL
  AND discovery_method NOT IN ('unknown', NULL);
EOF
```

---

## 9. Rollback Plan

If anything goes wrong:

```bash
# Restore from backup
cp database/flex_complete_database.db.backup database/flex_complete_database.db

# Drop migration columns (if needed)
sqlite3 database/flex_complete_database.db << 'EOF'
ALTER TABLE token_pool_accounts DROP COLUMN is_legacy;
ALTER TABLE token_pool_accounts DROP COLUMN is_active;
EOF

# Redeploy previous code
git checkout <previous-working-commit>
source .env
python3 -m src.core.pumpfun_curve_listener
```

---

## 10. Summary

| Phase | Action | Risk | Timeline |
|---|---|---|---|
| 1 | Mark legacy rows | None | 1 min |
| 2 | Normalize program IDs | Low | 1 min |
| 3 | Quarantine invalid | None | 1 min |
| 4 | Backfill pool_address | Low | 5-10 min |
| 5 | Validate | None | 1 min |
| 6 | Deploy listener | None | Immediate |

**Total Time:** ~20 minutes
**Downtime:** Zero
**Risk Level:** Very Low (all operations are safe, reversible)

---

## 11. Verification Checklist

After migration:

- [ ] Legacy rows marked (63 rows with is_legacy=1)
- [ ] Program IDs normalized (no 'pumpswap' / 'pumpfun_v1' labels)
- [ ] Invalid rows quarantined (25 rows with is_active=0)
- [ ] Pool addresses backfilled (as many as possible)
- [ ] Discovery methods populated (most legacy rows have a method)
- [ ] Telemetry table ready (new registrations write to it)
- [ ] Validation passes for new pipeline rows
- [ ] Listener deployed and running
- [ ] New registrations have all required fields
- [ ] Backups created and tested

**Status:** Ready to proceed with migration.

---

## Appendix: SQL Cheat Sheet

```sql
-- Quick commands for migration

-- 1. Check status
SELECT is_legacy, is_active, COUNT(*) FROM token_pool_accounts GROUP BY is_legacy, is_active;

-- 2. Identify legacy by characteristic
SELECT COUNT(*) FROM token_pool_accounts WHERE pool_address IS NULL;

-- 3. Find invalid pools
SELECT mint FROM token_pool_accounts WHERE base_account = quote_account;

-- 4. Check program IDs
SELECT DISTINCT pool_program FROM token_pool_accounts;

-- 5. Telemetry status
SELECT COUNT(*) FROM token_resolution_telemetry;

-- 6. Recent new registrations
SELECT COUNT(*) FROM token_pool_accounts WHERE is_legacy = 0;

-- 7. Discovery method distribution
SELECT discovery_method, COUNT(*) FROM token_pool_accounts WHERE is_legacy = 0 GROUP BY discovery_method;

-- 8. Validation rate (new pipeline)
SELECT
    COUNT(*) as total,
    COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) as validated,
    ROUND(100.0 * COUNT(CASE WHEN vault_validation_status = 'validated' THEN 1 END) / COUNT(*), 1) as pct
FROM token_pool_accounts
WHERE is_legacy = 0;
```
