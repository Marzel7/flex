# Pool Discovery Vault Extraction Bug - Invalid Vault Addresses

**Date:** March 23, 2026
**Severity:** CRITICAL - Prevents any discovered pools from being validated
**Root Cause:** Extracted vault addresses don't exist on-chain, so pools stay "pending" forever

---

## The Problem

When the listener discovers a pool (e.g., `ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw`), it extracts base_account and quote_account from the pool's binary data. **These extracted addresses don't exist on-chain**, causing validation to fail and the pool to remain "pending" indefinitely.

### Example: Most Recent Pool Discovery

**Token:** `8o7TmS1FM3mJSDqTHwgvGhtYczxPXevJpBmPso6Spump`
**Discovery Method:** `standard_extraction` (from pool binary)
**Pool Address (EXISTS):** `ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw`
**Pool Owner:** `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (PumpSwap)
**Pool Status:** ✅ Exists on-chain, 643 bytes, PumpSwap program

**Extracted Vault Addresses (DON'T EXIST):**
- **base_account:** `3rFFqNakPc3duoWsMudEwbUhkhhuXzMog5iAQ568XWpm` ❌ NULL (not found on-chain)
- **quote_account:** `GGSQn6BGMiwk6ugXChKvAsLjDc1Gj7cjz14GD1wNcPWS` ❌ NULL (not found on-chain)

**Result:** Pool registered as `pending` because vaults don't exist → validation fails → price worker skips pool → 100% fallback

---

## Pattern: All Standard Extraction Pools Have Same Wrong Vaults

Database query shows ALL pools discovered via `standard_extraction` use the EXACT SAME vault addresses:

```sql
SELECT DISTINCT base_account, quote_account
FROM token_pool_accounts
WHERE discovery_method='standard_extraction'
```

**Result:**
```
base_account: 3rFFqNakPc3duoWsMudEwbUhkhhuXzMog5iAQ568XWpm
quote_account: GGSQn6BGMiwk6ugXChKvAsLjDc1Gj7cjz14GD1wNcPWS
```

This proves the extraction is broken — it's pulling the same pair of addresses from every pool struct, regardless of the actual pool's vaults.

---

## Database Impact: 150 Pools, 102 Pending

```
Total pools: 150
Validated: 48
Pending: 102

All pending pools have:
- vault_validation_status = 'pending'
- base_account = 3rFFqNakPc3duoWsMudEwbUhkhhuXzMog5iAQ568XWpm (doesn't exist)
- quote_account = GGSQn6BGMiwk6ugXChKvAsLjDc1Gj7cjz14GD1wNcPWS (doesn't exist)
```

---

## Root Cause: Incorrect Offset Extraction

**File:** `src/core/pool_discovery.py` → `_extract_raydium_amm()` (lines 174-452)

The method tries to extract vault pubkeys at two offset pairs:

```python
vault_pairs = [
    (72, 104, "Raydium AMM v4 standard"),      # offsets 72 and 104
    (232, 264, "PumpSwap documented offsets"), # offsets 232 and 264
]
```

For the pool `ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw`:
- Owner: `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (PumpSwap)
- Data: 643 bytes (base64 encoded)

**The extraction reads 32-byte chunks from these offsets and decodes them as Solana pubkeys.**

The problem: **These offsets (72, 104, 232, 264) don't correspond to vault account addresses in PumpSwap pool structs.**

---

## Why Validation Fails

**File:** `src/core/pool_discovery.py` → `register_pool_to_db()` (lines 656-710)

When a pool is registered:

```python
is_valid, error_msg = self.validate_pool_registration(
    pool_address, base_account, quote_account, pool_program
)
```

Then checks if vaults exist:

```python
base_info = await self._fetch_account(base_account)  # Returns null for non-existent account
quote_info = await self._fetch_account(quote_account)  # Returns null

if not base_info or not quote_info:
    vault_status = "pending"
    vault_error = "vaults not yet created on-chain"
```

Since the accounts don't exist, `vault_status` stays `"pending"` forever.

---

## Why Price Worker Ignores Pending Pools

**File:** `src/core/pool_price_engine.py` → `get_active_pools()` (commit eac21d6)

```python
def get_active_pools(self) -> List[Dict]:
    """✅ Only returns: vault_validation_status = 'validated'"""
    with sqlite3.connect(self.db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM token_pool_accounts
            WHERE is_active = 1
            AND vault_validation_status = 'validated'  # ← Only validated!
            ORDER BY created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]
```

**Result:** Price worker only bootstraps reserves for the 48 validated pools, ignores the 102 pending ones.

---

## Current System State

```
152 Total Pools
├─ 48 Validated
│  └─ Price worker bootstraps reserves for these ✅
│  └─ Prices computed from on-chain reserves
│  └─ Mix of sources (some fallback, some on-chain)
│
└─ 102 Pending (with non-existent vaults)
   └─ Price worker completely ignores these ❌
   └─ All prices fall back to DexScreener
   └─ Cannot move to "validated" without correct vault addresses
```

**System health: 100% fallback pricing** because pending pools have invalid vaults and are unreachable.

---

## The Affected Pool

**Most recent pool discovery (for reference):**

```
Mint: 8o7TmS1FM3mJSDqTHwgvGhtYczxPXevJpBmPso6Spump
Pool Address: ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw
Status: pending
Created: 2026-03-23 16:30:06 UTC (timestamp 1774282606)

Extracted (wrong):
  base_account: 3rFFqNakPc3duoWsMudEwbUhkhhuXzMog5iAQ568XWpm (NULL on-chain)
  quote_account: GGSQn6BGMiwk6ugXChKvAsLjDc1Gj7cjz14GD1wNcPWS (NULL on-chain)
  discovery_method: standard_extraction
  pool_address: ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw (EXISTS on-chain ✓)
```

---

## What Should Happen

For PumpSwap pools, the vault extraction needs to:
1. Decode the pool struct correctly based on PumpSwap's actual layout
2. Extract the actual vault account addresses from the correct offsets
3. Validate those vaults exist and are owned by the SPL Token program
4. Only then register the pool

Currently it's reading garbage from fixed offsets and extracting fake addresses.

---

## Action Required

To fix the 102 pending pools:
1. **Implement correct PumpSwap pool struct decoding** with proper offset values
2. **Extract actual vault addresses** from the discovered pool accounts
3. **Run listener on a new PumpFun migration** to test the fix
4. **Validate** that extracted vaults exist on-chain before registering
5. **Monitor** that vault_validation_status changes from "pending" to "validated"

---

## Code Location

**Primary Issue:** `src/core/pool_discovery.py`
- `_extract_raydium_amm()` - lines 174-452 (incorrect offset values)
- `extract_pool_reserves()` - lines 49-116 (calls extraction)
- `register_pool_to_db()` - lines 645-762 (validates vaults exist)

**Secondary Impact:** `src/core/pool_price_engine.py`
- `get_active_pools()` - lines 46-66 (filters to validated only)

---

## Summary

- ✅ Listener correctly identifies pool accounts (e.g., `ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw`)
- ❌ Listener incorrectly extracts vault addresses from pool binary data (reads wrong offsets)
- ❌ Extracted vaults don't exist on-chain (are fake addresses)
- ❌ Pool validation fails → stays "pending" forever
- ❌ Price worker ignores pending pools
- ❌ Result: 100% fallback pricing for all pending pools

**This is a critical architectural bug that prevents any new pools from being validated and priced on-chain.**

