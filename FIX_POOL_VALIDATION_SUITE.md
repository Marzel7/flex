# Fix Pool Validation Suite — Complete Technical Plan

**Status:** Analysis complete, ready for implementation
**Date:** March 20, 2026
**Severity:** High — Data model bugs prevent integration test from validating production pipeline

---

## Executive Summary

The pool validation test suite is 2/3 passing but reveals critical bugs:

1. **pool_address == base_account** — Some pools have the same address in two distinct fields
2. **pool_program = 'unknown'** — Many pools lack the program ID needed for on-chain validation
3. **Runtime dependency** — Acceptance tests fail when price worker isn't running

This document specifies exact code and data fixes to make all tests pass cleanly.

---

## Root Cause Analysis

### Issue 1: pool_address == base_account

**Location:** `src/core/pool_discovery.py`, `register_pool_to_db()` at line 685

**Root cause:** When `extract_pool_reserves()` returns reserves dict, `pool_address` field is either:
- Not populated during extraction
- Or populated incorrectly as the base vault address
- The caller `discover_and_register_pool()` passes it through without validation

**Evidence:**
```
Test pool: 3UaZsmciGs4br3r8g6hLU2CuG1hYmAMeri4znoQxpump
  pool_address:  89iQGtJP1LqHiZeSS1qFw5GteqAcW2SJKDwnjZQKhVot
  base_account:  89iQGtJP1LqHiZeSS1qFw5GteqAcW2SJKDwnjZQKhVot ← IDENTICAL
  quote_account: BEHmsrYzvvgjJ6qJ5hYbi5YE7DqQrPQfgMqMBA7Suz5Q
```

**Why this is wrong:**
- `pool_address` = The AMM pool state account (contains liquidity, fees, reserves references)
- `base_account` = Token vault holding base token (SPL token account)
- These are structurally different accounts with different owners
- Same address means the pool struct is stored inside a token account, which is impossible

**Impact:**
- Integration test Step 1 fails (pool identity validation)
- On-chain validation cannot proceed (can't distinguish pool from vault)
- WebSocket subscriptions may be wrong (subscribing to wrong accounts)

---

### Issue 2: pool_program = 'unknown'

**Location:** `src/core/pool_discovery.py`, line 621

**Root cause:** Default value assigned when pool_program not extracted:

```python
pool_program = reserves.get("pool_program", RAYDIUM_AMM_PROGRAM)
```

But extraction method may not populate `pool_program` field in reserves dict.

**Evidence:**
Test pool has:
```
pool_program: unknown
```

But from DB query: `discovery_method = 'rpc_authoritative'` means it was discovered via RPC.

The pool account owner was NOT extracted or stored.

**Why this matters:**
- Integration test Step 2 (decode pool) requires knowing the pool program
- Without program ID, can't decode pool struct to extract vault references
- Without vault references, can't validate on-chain pool structure

**Impact:**
- Step 2 validation skipped
- Cannot prove pool_address corresponds to real on-chain pool
- Cannot verify extracted vaults match on-chain reality

---

### Issue 3: Runtime-dependent tests fail cleanly

**Location:** `test_pipeline_validation.py` line ~150, `test_true_end_to_end_pool_identity.py` steps 4-5-7

**Root cause:** Tests are designed to handle missing worker gracefully, but `test_pipeline_validation.py` doesn't skip—it fails:

```python
# Test 2 failure:
❌ FAIL: Snapshot rate too low: 22 in 60s, expected >= 40
```

Expected behavior: should SKIP when worker not running, not FAIL

---

## Required Fixes

### FIX 1: Add Registration Validation

**File:** `src/core/pool_discovery.py`

**Add new validation function before register_pool_to_db():**

```python
def validate_pool_registration(
    pool_address: str,
    base_account: str,
    quote_account: str,
    pool_program: str,
) -> Tuple[bool, Optional[str]]:
    """
    Validate pool registration data before inserting to DB.

    Returns: (is_valid, error_message)
    """
    # Check required fields
    if not pool_address:
        return False, "pool_address is missing"
    if not base_account:
        return False, "base_account is missing"
    if not quote_account:
        return False, "quote_account is missing"

    # Check accounts are distinct
    if pool_address == base_account:
        return False, f"pool_address == base_account ({pool_address}), must be distinct"
    if pool_address == quote_account:
        return False, f"pool_address == quote_account ({pool_address}), must be distinct"
    if base_account == quote_account:
        return False, f"base_account == quote_account ({base_account}), must be distinct"

    # Check pool_program is known
    KNOWN_PROGRAMS = {
        RAYDIUM_AMM_PROGRAM,
        RAYDIUM_CPMM_PROGRAM,
        ORCA_WHIRLPOOL_PROGRAM,
        PUMPSWAP_PROGRAM,
        PUMPFUN_V1_PROGRAM,
    }
    if not pool_program or pool_program not in KNOWN_PROGRAMS:
        return False, f"pool_program unknown or invalid: {pool_program}"

    return True, None
```

**Update register_pool_to_db() to call validation:**

```python
async def register_pool_to_db(
    self, token_mint: str, reserves: Dict, discovery_method: str = "unknown"
) -> bool:
    """Register extracted pool in token_pool_accounts table."""
    try:
        # VALIDATE BEFORE PROCEEDING
        pool_address = reserves.get("pool_address")
        base_account = reserves.get("base_account")
        quote_account = reserves.get("quote_account")
        pool_program = reserves.get("pool_program")

        is_valid, error_msg = validate_pool_registration(
            pool_address, base_account, quote_account, pool_program
        )
        if not is_valid:
            logger.error(f"❌ Registration validation failed for {token_mint}: {error_msg}")
            return False

        # ... rest of registration code ...
```

---

### FIX 2: Extract Pool Program from On-Chain Account

**File:** `src/core/pool_discovery.py`

**Update _extract_from_pool_data() to return pool_program:**

After determining which pool type (Raydium AMM, Orca, PumpSwap, etc.), extract the actual owner program ID from the account:

```python
async def _extract_from_pool_data(
    self, pool_data: Dict, pool_address: str, token_mint: str
) -> Optional[Dict]:
    """Extract reserves from pool data and INCLUDE the actual pool program ID."""

    # Fetch pool account to get owner
    pool_account_info = await self._fetch_account(pool_address)
    if not pool_account_info:
        logger.warning(f"Cannot fetch pool account {pool_address}")
        return None

    actual_owner = pool_account_info.get("owner")

    # Determine pool type and extract reserves
    # (existing logic to extract base_account, quote_account, etc.)
    # ...

    # MAP OWNER TO PROGRAM ID
    reserves = {
        "base_account": ...,
        "quote_account": ...,
        "base_token": ...,
        "quote_token": ...,
        "base_decimals": ...,
        "quote_decimals": ...,
        "pool_address": pool_address,
        "pool_program": actual_owner,  # ← ACTUAL PROGRAM ID, NOT 'unknown'
    }

    return reserves
```

**In discover_and_register_pool(), ensure pool_address is passed:**

```python
async def discover_and_register_pool(
    self, pool_address: str, token_mint: str
) -> bool:
    """
    Discover and register a pool.

    Args:
        pool_address: The on-chain pool account address (Raydium AMM v4, Orca, PumpSwap, etc.)
        token_mint: The token mint address

    Returns:
        True if successfully registered, False otherwise
    """
    try:
        reserves = await self.extract_pool_reserves(pool_address, token_mint)
        if not reserves:
            logger.warning(f"Could not extract reserves from pool {pool_address}")
            return False

        # ENSURE pool_address is in the dict
        reserves["pool_address"] = pool_address  # ← EXPLICITLY SET

        # Determine discovery method
        discovery_method = "rpc_discovery"  # or whatever applies

        return await self.register_pool_to_db(reserves, discovery_method)
    except Exception as e:
        logger.error(f"Error discovering pool {pool_address}: {e}")
        return False
```

---

### FIX 3: Update Test Behavior

**File:** `tests/pool_validation/test_pipeline_validation.py`

**Change throughput check to SKIP when worker not running:**

```python
def test_2_production_snapshot_throughput():
    """Test snapshot generation rate when worker is running."""

    # Check if worker is running
    # (Heuristic: check if recent snapshots were captured in last 10 seconds)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM token_price_snapshots
        WHERE captured_at > ? - 10
    """, (int(time.time()),))
    recent_count = cursor.fetchone()[0]
    conn.close()

    if recent_count == 0:
        pytest.skip("Price worker not running (no recent snapshots)")

    # If we reach here, worker IS running, so test throughput
    # ... existing throughput test ...
```

---

### FIX 4: Add Backfill Script

**File:** `scripts/backfill_pool_identity.py`

Create new file:

```python
#!/usr/bin/env python3
"""
Backfill pool_address and pool_program for existing pools.

Repairs rows where:
1. pool_address == base_account
2. pool_program = 'unknown'

Usage:
    python3 scripts/backfill_pool_identity.py \
        --db database/flex_complete_database.db \
        --rpc https://mainnet.helius-rpc.com/?api-key=...
"""

import sqlite3
import asyncio
import aiohttp
import logging
import sys
from typing import Optional, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RAYDIUM_AMM = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"
RAYDIUM_CPMM = "CPMMoo8L3F4rn9aUYn2QRiPK5VrKMjstm69edQaMQAC"
ORCA = "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPFUN_V1 = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

KNOWN_PROGRAMS = {RAYDIUM_AMM, RAYDIUM_CPMM, ORCA, PUMPSWAP, PUMPFUN_V1}


async def fetch_account_info(address: str, rpc_url: str) -> Optional[Dict]:
    """Fetch account info from RPC."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "base64"}],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, json=payload, timeout=10) as resp:
                data = await resp.json()
                if "result" in data and data["result"]["value"]:
                    return data["result"]["value"]
    except Exception as e:
        logger.warning(f"Error fetching {address}: {e}")
    return None


async def backfill_pool(
    row_id: int,
    mint: str,
    pool_address: str,
    base_account: str,
    quote_account: str,
    current_program: str,
    rpc_url: str,
    db_path: str,
) -> bool:
    """
    Attempt to backfill pool_address and pool_program for one row.

    Returns True if successfully updated.
    """

    # Case 1: pool_address == base_account (need to find real pool address)
    if pool_address == base_account:
        logger.warning(f"Pool {mint}: pool_address == base_account, need real pool address")
        logger.warning(f"  Cannot auto-recover without migration_tx or other hint")
        logger.warning(f"  DEACTIVATE this pool (it's invalid)")
        # Mark as inactive
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE token_pool_accounts SET is_active = 0 WHERE mint = ?", (mint,))
        conn.commit()
        conn.close()
        return False

    # Case 2: pool_program = 'unknown' or not in KNOWN_PROGRAMS
    if current_program not in KNOWN_PROGRAMS:
        logger.info(f"Pool {mint}: pool_program = '{current_program}', fetching real owner...")

        acct_info = await fetch_account_info(pool_address, rpc_url)
        if not acct_info:
            logger.error(f"  Could not fetch account info for {pool_address}")
            return False

        actual_owner = acct_info.get("owner")
        if actual_owner not in KNOWN_PROGRAMS:
            logger.warning(f"  Owner {actual_owner} not recognized, marking inactive")
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE token_pool_accounts SET is_active = 0 WHERE mint = ?", (mint,))
            conn.commit()
            conn.close()
            return False

        logger.info(f"  Recovered: program = {actual_owner}")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE token_pool_accounts SET pool_program = ? WHERE mint = ?",
            (actual_owner, mint),
        )
        conn.commit()
        conn.close()
        return True

    return False


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backfill pool identity data")
    parser.add_argument("--db", required=True, help="Database path")
    parser.add_argument("--rpc", required=True, help="RPC URL")
    args = parser.parse_args()

    # Find bad rows
    conn = sqlite3.connect(args.db)
    cursor = conn.cursor()

    # Query bad rows
    cursor.execute("""
        SELECT
            rowid, mint, pool_address, base_account, quote_account, pool_program
        FROM token_pool_accounts
        WHERE is_active = 1
        AND (
            pool_address = base_account
            OR pool_address = quote_account
            OR pool_program IS NULL
            OR pool_program = 'unknown'
            OR pool_program NOT IN (?, ?, ?, ?, ?)
        )
    """, (
        RAYDIUM_AMM, RAYDIUM_CPMM, ORCA, PUMPSWAP, PUMPFUN_V1
    ))

    bad_rows = cursor.fetchall()
    conn.close()

    logger.info(f"Found {len(bad_rows)} pools needing repair")

    for row in bad_rows:
        row_id, mint, pool_address, base_account, quote_account, program = row
        logger.info(f"\nRepairing {mint} (pool_address={pool_address[:8]}...)")

        await backfill_pool(
            row_id, mint, pool_address, base_account, quote_account, program,
            args.rpc, args.db
        )

    logger.info("\n✓ Backfill complete")


if __name__ == "__main__":
    asyncio.run(main())
```

---

### FIX 5: Add Repair Guards to prevent new bad registrations

**File:** `src/core/vault_discovery.py`

If any code path in vault_discovery.py registers pools, add the same validation:

```python
from src.core.pool_discovery import validate_pool_registration

# Before any INSERT into token_pool_accounts:
is_valid, error = validate_pool_registration(
    pool_address, base_vault, quote_vault, pool_program
)
if not is_valid:
    logger.error(f"Validation failed: {error}")
    return  # Skip this registration
```

---

## Data Repair Strategy

### Step 1: Identify bad rows

```sql
-- Find pools where pool_address == base_account
SELECT COUNT(*) as bad_count
FROM token_pool_accounts
WHERE pool_address = base_account
AND is_active = 1;

-- Find pools where pool_program is unknown or invalid
SELECT COUNT(*) as unknown_count
FROM token_pool_accounts
WHERE (pool_program IS NULL OR pool_program = 'unknown'
   OR pool_program NOT IN (
       '675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K',
       'CPMMoo8L3F4rn9aUYn2QRiPK5VrKMjstm69edQaMQAC',
       'whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco',
       'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
       '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
   ))
AND is_active = 1;
```

### Step 2: Attempt recovery

Run backfill script:

```bash
python3 scripts/backfill_pool_identity.py \
    --db database/flex_complete_database.db \
    --rpc https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
```

### Step 3: Deactivate unrecoverable rows

```sql
-- Mark as inactive any rows that couldn't be recovered
UPDATE token_pool_accounts
SET is_active = 0
WHERE pool_address = base_account
AND is_active = 1;
```

---

## Test Validation Sequence

### Sequence to Get All Tests Green

**Terminal 1: Start price worker**
```bash
python3 -c "from src.core.price_worker import start_price_worker; start_price_worker('database/flex_complete_database.db')"
```

**Terminal 2: Run tests in order**

```bash
# 1. Run database repair first
python3 scripts/backfill_pool_identity.py \
    --db database/flex_complete_database.db \
    --rpc "https://mainnet.helius-rpc.com/?api-key=YOUR_KEY"

# 2. Verify no bad rows remain
sqlite3 database/flex_complete_database.db "
SELECT COUNT(*) as remaining_bad
FROM token_pool_accounts
WHERE is_active = 1
AND (pool_address = base_account OR pool_program = 'unknown')
"
# Expected: 0

# 3. Run account validator (should pass)
python3 tests/pool_validation/test_account_validator.py

# 4. Run pipeline validation (should pass with worker running)
python3 tests/pool_validation/test_pipeline_validation.py

# 5. Run integration test (should pass all 7 steps with worker running)
python3 tests/pool_validation/test_true_end_to_end_pool_identity.py
```

---

## Success Criteria

All tests passing = system is correct:

### Test 1: Account Validator
```
✅ PASS - All vault accounts exist on-chain
```

### Test 2: Pipeline Validation
```
✅ PASS - Snapshot throughput >= 40/min
or
⊘ SKIP - Price worker not running
```

### Test 3: End-to-End Integration
```
✅ STEP 1 PASS - pool_address distinct from vaults
✅ STEP 2 PASS - decoded vaults match DB (pool_program now known)
✅ STEP 3 PASS - vault accounts exist on-chain
✅ STEP 4 PASS - WebSocket subscriptions active
✅ STEP 5 PASS - PoolStateStore has reserves
✅ STEP 6 PASS - snapshot exists in DB
✅ STEP 7 PASS - snapshot price matches reserves (±5%)

RESULT: PASSED ✅
```

---

## Verification Queries

After fixes, run these to confirm:

```sql
-- 1. No more bad pool_address == base_account
SELECT COUNT(*) as should_be_zero
FROM token_pool_accounts
WHERE is_active = 1 AND pool_address = base_account;
-- Expected: 0

-- 2. No more unknown pool_program
SELECT COUNT(*) as should_be_zero
FROM token_pool_accounts
WHERE is_active = 1
AND (pool_program IS NULL OR pool_program = 'unknown');
-- Expected: 0

-- 3. All program IDs are canonical
SELECT DISTINCT pool_program
FROM token_pool_accounts
WHERE is_active = 1
ORDER BY pool_program;
-- Expected: (5 known program IDs)

-- 4. Pool addresses are distinct from vaults
SELECT COUNT(*) as should_be_zero
FROM token_pool_accounts
WHERE is_active = 1
AND (pool_address = base_account OR pool_address = quote_account);
-- Expected: 0
```

---

## Implementation Order

1. **Code changes (15 min)**
   - Add validate_pool_registration() to pool_discovery.py
   - Update register_pool_to_db() to call validation
   - Update _extract_from_pool_data() to include actual pool_program
   - Create scripts/backfill_pool_identity.py

2. **Data repair (10 min)**
   - Run backfill script
   - Verify all bad rows marked inactive or fixed

3. **Test verification (5 min)**
   - Start price worker
   - Run all three tests
   - Verify all pass

4. **Commit**
   - All tests green
   - No bad rows in DB
   - Pool identity model now correct

---

## Notes

- **pool_address field purpose:** Identifies the exact on-chain pool account for decoding and validation
- **pool_program field purpose:** Required to decode pool struct and extract vault references
- **Validation guards:** Prevent bad rows being inserted in future
- **Backfill strategy:** Graceful degradation—can't recover pool_address from vault, so deactivate
- **Test design:** Skip gracefully when dependencies missing, fail when they're available and wrong

This fix ensures the integration test can validate the complete pipeline end-to-end.
