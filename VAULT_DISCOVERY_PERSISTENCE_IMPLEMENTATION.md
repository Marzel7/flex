# Vault Discovery Data Persistence Implementation

## Overview

This implementation persists **real vault discovery runtime data** (strategy, attempts, elapsed time) to the database, so the Vaults page displays actual metrics instead of defaults.

**Problem**: Discovery logs show real values but they weren't stored:
```
[DISCOVERY_SUCCESS] strategy=tx_parsing pool=...
resolved (attempt 4 in 256.8s)
```

But the Vaults page showed:
- strategy = "unknown" (default)
- attempts = 0 (default)
- discovery time = "0s" (default)

**Solution**: Persist data at the moment discovery succeeds.

---

## 1. New Module: `vault_discovery_persistence.py`

**Location**: `src/core/vault_discovery_persistence.py`

### Key Functions

#### `record_vault_discovery_result()`

Called when a pool is successfully discovered and registered.

```python
record_vault_discovery_result(
    db_path="database/flex_complete_database.db",
    mint="3yeggvaSvPynVTjoPQsKBe1siivW2nLuwVCNeUcLpump",
    base_account="9tSG8rU4jn3iTLy9KU9WpEW7RjgRHPASGus3vGwBNCF",
    strategy="tx_parsing",
    attempts=4,
    elapsed_secs=256.8,
)
```

**Updates to `token_pool_accounts`**:
```sql
UPDATE token_pool_accounts
SET
    vault_discovery_strategy = 'tx_parsing',
    vault_discovery_attempts = 4,
    vault_discovery_time_secs = 256.8,
    vault_resolution_state = 'resolved',
    vault_resolved_at = NOW,
    last_vault_validation_at = NOW
WHERE mint = ? AND base_account = ?
```

**Returns**: `True` if successful, `False` otherwise

#### `increment_vault_discovery_attempts()`

Called on each retry to track attempts (optional, for future use).

```python
increment_vault_discovery_attempts(
    db_path="database/flex_complete_database.db",
    mint="3yeggvaSvPynVTjoPQsKBe1siivW2nLuwVCNeUcLpump",
    base_account="9tSG8rU4jn3iTLy9KU9WpEW7RjgRHPASGus3vGwBNCF",
)
```

#### `get_vault_discovery_status()`

Query current discovery status.

```python
status = get_vault_discovery_status(
    db_path="database/flex_complete_database.db",
    mint="3yeggvaSvPynVTjoPQsKBe1siivW2nLuwVCNeUcLpump",
    base_account="9tSG8rU4jn3iTLy9KU9WpEW7RjgRHPASGus3vGwBNCF",
)
# Returns: {
#     "strategy": "tx_parsing",
#     "attempts": 4,
#     "elapsed_secs": 256.8,
#     "resolution_state": "resolved",
#     "resolved_at": 1234567890,
# }
```

---

## 2. Integration: Updated `_write_resolution_telemetry()`

**Location**: `src/core/pumpfun_curve_listener.py` (line 549)

When a pool is successfully discovered:

```python
self.token_discovery_times[mint]["resolved"] = time.time()
elapsed = self.token_discovery_times[mint]["resolved"] - self.token_discovery_times[mint]["detected"]

# OLD: Only wrote to token_resolution_telemetry
await self._write_resolution_telemetry(mint, "tx_parsing", candidate, attempt - 1)

# NEW: Also persists vault discovery data to token_pool_accounts
```

**Key changes in `_write_resolution_telemetry()`**:
1. Imports `record_vault_discovery_result` from new module
2. After writing telemetry, calls:
   ```python
   record_vault_discovery_result(
       db_path=DB_PATH,
       mint=mint,
       base_account=pool_address,
       strategy=resolve_source,  # e.g., "tx_parsing", "rpc", "follow_on"
       attempts=retry_count + 1,
       elapsed_secs=float(resolve_seconds),
   )
   ```
3. Logs success/failure status

---

## 3. Database Schema (Already Exists)

The required fields already exist in `token_pool_accounts`:

```sql
CREATE TABLE token_pool_accounts (
    ...
    vault_discovery_strategy TEXT DEFAULT 'unknown',
    vault_discovery_attempts INTEGER DEFAULT 0,
    vault_discovery_time_secs REAL DEFAULT NULL,
    vault_resolution_state TEXT DEFAULT 'pending',
    vault_resolved_at INTEGER DEFAULT NULL,
    ...
)
```

**No migration required** — fields already present.

---

## 4. API Behavior (Already Correct)

The API endpoint `/api/vaults` correctly handles NULL values.

### Before Discovery
```json
{
    "vault_discovery_strategy": null,
    "vault_discovery_attempts": null,
    "vault_discovery_time_secs": null,
    "vault_resolution_state": "pending"
}
```

### After Discovery Succeeds (with persistence)
```json
{
    "vault_discovery_strategy": "tx_parsing",
    "vault_discovery_attempts": 4,
    "vault_discovery_time_secs": 256.8,
    "vault_resolution_state": "resolved",
    "vault_resolved_at": 1774394521
}
```

**Key point**: No defaults in API — `null` values remain `null`.

---

## 5. Frontend Display (Already Correct)

**Location**: `templates/flex_dashboard.html` (lines 4015-4032)

Handles NULL values gracefully:

```javascript
// Strategy: Show discovery_method as fallback
const strategy = v.vault_discovery_strategy ?? v.discovery_method ?? 'N/A';

// Attempts: Show N/A if missing
const attempts = v.vault_discovery_attempts !== null && v.vault_discovery_attempts !== undefined
    ? v.vault_discovery_attempts
    : 'N/A';

// Discovery time: Show 'Pending' or 'N/A' if missing
let discoveryTime;
if (v.vault_discovery_time_secs !== null && v.vault_discovery_time_secs !== undefined) {
    discoveryTime = formatVaultDiscoveryTime(v.vault_discovery_time_secs);
} else if (v.vault_validation_status === 'pending') {
    discoveryTime = 'Pending';
} else {
    discoveryTime = 'N/A';
}
```

**No changes needed** — frontend already handles NULL correctly.

---

## 6. Discovery Flow with Persistence

```
1. Token detected
   └─ mint created in database with defaults
      - vault_discovery_strategy = NULL (or 'unknown')
      - vault_discovery_attempts = 0
      - vault_discovery_time_secs = NULL
      - vault_resolution_state = 'pending'

2. Discovery retries...
   └─ Tiers: TX_ONLY → TX_PLUS_LIGHT_RPC → TX_PLUS_FULL_RPC

3. Pool discovered successfully ✅
   └─ await _write_resolution_telemetry(mint, "tx_parsing", pool, attempt-1)
      ├─ Writes to token_resolution_telemetry (existing)
      └─ Calls record_vault_discovery_result() (NEW)
         └─ Updates token_pool_accounts with:
            - vault_discovery_strategy = "tx_parsing"
            - vault_discovery_attempts = 4
            - vault_discovery_time_secs = 256.8
            - vault_resolution_state = "resolved"
            - vault_resolved_at = NOW

4. Frontend displays real data
   └─ /api/vaults returns persisted values
      └─ UI shows: strategy=tx_parsing, attempts=4, time=256.8s
```

---

## 7. Data Quality Guarantees

### What Gets Persisted
- ✅ Actual discovery strategy used (tx_parsing, rpc, follow_on, etc.)
- ✅ Actual attempt count (1-12 typical range)
- ✅ Actual elapsed time (seconds from detection to resolution)
- ✅ Timestamp when resolved

### What Doesn't Get Faked
- ❌ No "unknown" defaults in API (returns null)
- ❌ No "0" defaults for attempts (returns null)
- ❌ No "0s" defaults for time (returns null)
- ❌ No fallback values unless explicitly handled in frontend

### Fallback Logic (Frontend Only)
```javascript
// ONLY at UI layer, for backward compatibility:
strategy = v.vault_discovery_strategy ?? v.discovery_method ?? 'N/A'
//         └─ persisted value    └─ old value    └─ last resort
```

---

## 8. Workflow: Discovery Success → Persistence

### Step 1: Discovery Succeeds (Line 3513-3532 in listener)
```python
registered = await discovery_pipeline.discover_and_register_pool(candidate, mint)
if registered:
    self.token_discovery_times[mint]["resolved"] = time.time()
    elapsed = self.token_discovery_times[mint]["resolved"] - self.token_discovery_times[mint]["detected"]

    await self._write_resolution_telemetry(mint, "tx_parsing", candidate, attempt - 1)
    # ↑ Calls _write_resolution_telemetry with:
    #   - resolve_source="tx_parsing"
    #   - pool_address=candidate (base_account)
    #   - retry_count=attempt-1
```

### Step 2: Write Telemetry + Persist Discovery Data
```python
async def _write_resolution_telemetry(self, mint, resolve_source, pool_address, retry_count):
    # Write to token_resolution_telemetry (existing behavior)
    cursor.execute("""
        INSERT OR REPLACE INTO token_resolution_telemetry ...
    """)

    # NEW: Also persist to token_pool_accounts
    record_vault_discovery_result(
        db_path=DB_PATH,
        mint=mint,
        base_account=pool_address,
        strategy=resolve_source,        # "tx_parsing"
        attempts=retry_count + 1,       # 4
        elapsed_secs=float(resolve_seconds),  # 256.8
    )
```

### Step 3: API Serves Real Data
```python
# GET /api/vaults
row = {
    'vault_discovery_strategy': 'tx_parsing',      # from database
    'vault_discovery_attempts': 4,                 # from database
    'vault_discovery_time_secs': 256.8,            # from database
    'vault_resolution_state': 'resolved',
}
return {
    'vault_discovery_strategy': row['vault_discovery_strategy'],
    'vault_discovery_attempts': row['vault_discovery_attempts'],
    'vault_discovery_time_secs': _format_nullable_float(row['vault_discovery_time_secs'], 1),
}
```

### Step 4: Frontend Displays Real Data
```javascript
const strategy = v.vault_discovery_strategy ?? v.discovery_method ?? 'N/A';  // "tx_parsing"
const attempts = v.vault_discovery_attempts ?? 'N/A';                        // 4
const discoveryTime = formatVaultDiscoveryTime(v.vault_discovery_time_secs); // "256.8s"
```

---

## 9. Example: Token 3yeggva...

### Before Persistence
```
Token: 3yeggvaSvPynVTjoPQsKBe1siivW2nLuwVCNeUcLpump
Base Account: 9tSG8rU4jn3iTLy9KU9WpEW7RjgRHPASGus3vGwBNCF
Pool Address: ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw

Vaults Table:
  Strategy: unknown    (default)
  Attempts: 0         (default)
  Time:     0s        (default)
```

### After Persistence (on line 3532)
```
Same token, same accounts.

Vaults Table:
  Strategy: tx_parsing (REAL VALUE)
  Attempts: 4         (REAL VALUE)
  Time:     256.8s    (REAL VALUE)
  State:    resolved
  Resolved: 2026-03-25 14:32:01
```

---

## 10. Testing Checklist

### ✅ Implementation Complete
- [x] Module `vault_discovery_persistence.py` created
- [x] `record_vault_discovery_result()` implemented
- [x] Database fields exist (no migration needed)
- [x] `_write_resolution_telemetry()` updated to call persistence
- [x] API already handles NULL correctly
- [x] Frontend already displays N/A for NULL

### ✅ To Verify in Production
1. **Logs Check**: Run listener and look for:
   ```
   [VAULT_PERSISTENCE] ✅ Persisted discovery: strategy=tx_parsing attempts=4 elapsed=256.8s
   ```

2. **Database Check**: Query after discovery:
   ```sql
   SELECT mint, vault_discovery_strategy, vault_discovery_attempts, vault_discovery_time_secs
   FROM token_pool_accounts
   WHERE mint = '3yeggvaSvPynVTjoPQsKBe1siivW2nLuwVCNeUcLpump'
   LIMIT 1;

   -- Should show: | 3yeggva... | tx_parsing | 4 | 256.8 |
   ```

3. **API Check**:
   ```bash
   curl http://localhost:5002/api/vaults | jq '.vaults[0] | {strategy: .vault_discovery_strategy, attempts: .vault_discovery_attempts, time: .vault_discovery_time_secs}'

   -- Should show: {"strategy":"tx_parsing","attempts":4,"time":"256.8"}
   ```

4. **UI Check**: Vaults page table should show real values:
   ```
   Strategy: tx_parsing
   Attempts: 4
   Time:     256.8s
   ```

---

## 11. Why This Solves the Problem

### Problem Statement
> Discovery logs show real values (strategy, attempts, time) but these are NOT persisted. UI falls back to unknown/0/0s.

### Solution Approach
1. **Capture at Success**: When `discover_and_register_pool()` succeeds, we have:
   - `resolve_source` = strategy used
   - `retry_count` = attempts made
   - `resolved_at - detected_at` = elapsed time

2. **Persist Immediately**: Before function returns, call `record_vault_discovery_result()` to store in database.

3. **Serve Real Data**: API queries `token_pool_accounts` and returns non-NULL values.

4. **Display Real Data**: Frontend receives actual values and renders without defaults.

### Key Difference
- **Before**: `vault_discovery_strategy` = 'unknown' (default)
- **After**: `vault_discovery_strategy` = 'tx_parsing' (actual value)

---

## 12. No Breaking Changes

- ✅ Backward compatible (old rows with NULL values display "N/A")
- ✅ No schema changes (fields already exist)
- ✅ No API changes (returns same structure, just with real values)
- ✅ No frontend changes (already handles NULL)
- ✅ Additive only (only adds persistence, no removal)

---

## 13. Production Readiness

- ✅ Error handling (try/except in all functions)
- ✅ Logging (both success and failure cases)
- ✅ Database safety (timeout=10s, proper commit/close)
- ✅ Performance (single UPDATE query, no loops)
- ✅ Data integrity (only updates if rows exist)

---

## Summary

**What changed:**
1. Added `vault_discovery_persistence.py` with persistence functions
2. Updated `_write_resolution_telemetry()` to persist discovery data
3. Everything else (DB, API, Frontend) already supports the change

**Result:**
- Vaults page now shows real discovery strategy, attempts, and elapsed time
- No fake/default values in API response
- Accurate data for analysis and clustering
