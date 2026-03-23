# Architecture Duplication Issues - Production Design Flaw

**Date:** March 23, 2026
**Severity:** HIGH - Design flaw causing invalid pool registration
**Root Cause:** Two independent pool discovery paths + two WebSocket ownership paths

---

## The Problem

The codebase has **two competing pool detection systems**:

### Path 1: Debug Detector (pool_detector.py)
```
Transaction → pool_detector.py (minimal)
  ↓
  Returns: "first AMM-owned account found"
  ↓
  Writes to token_pool_accounts (PRODUCTION)
```

**Issue:** Uses simple heuristic, finds wrong accounts, registers invalid pools

### Path 2: Listener (pumpfun_curve_listener.py)
```
Transaction → pumpfun_curve_listener.py (stronger)
  ↓
  Candidate extraction + validation + best-pool selection
  ↓
  Should write to token_pool_accounts (AUTHORITATIVE)
```

**Issue:** Exists but may not be the actual writer

---

## Evidence of Duplication

### Problem 1: Invalid Pool Registration
Latest pool in database:
```
mint: 39xZZ7Qa3HCaxa3dmC8F1dmtRPTxuWLZrbWdsEAbpump
base_account: 3rFFqNakPc3duoWsMudEwbUhkhhuXzMog5iAQ568XWpm
quote_account: GGSQn6BGMiwk6ugXChKvAsLjDc1Gj7cjz14GD1wNcPWS
vault_validation_status: pending
```

**Check:** Does this account exist on-chain?
```
✗ Account doesn't exist
```

**Cause:** Debug detector registered invalid account before listener's stronger validation could run

### Problem 2: WebSocket Ownership Duplication

**Listener side:**
```python
# pumpfun_curve_listener.py
[Should start WebSocket client]
[Shares via singleton get_pool_state()]
```

**Worker side:**
```python
# price_worker.py
def _start_ws_client(self):
    """Start WebSocket if not already running"""
    if not self._ws_started:
        self._ws_client = PoolWebSocketClient(...)
        self._ws_client.start()
        self._ws_started = True

def _refresh_cycle(self):
    if not self._ws_client and pools:
        self._start_ws_client()  # Fallback startup
```

**Issue:** Worker has fallback logic to start WebSocket if listener didn't. This creates:
- Two potential startup paths
- Race conditions
- Duplication of WebSocket management

---

## Current Data Flow (Wrong)

```
TRANSACTION
  ↓
  ├─→ pool_detector.py (DEBUG PATH)
  │   ├─ Returns: first AMM account
  │   └─ Writes: token_pool_accounts ❌ (invalid account)
  │
  └─→ pumpfun_curve_listener.py (LISTENER PATH)
      ├─ Candidate extraction (proper)
      ├─ Validation (proper)
      ├─ Best selection (proper)
      └─ Should write: token_pool_accounts (never happens?)
```

**Result:** Invalid accounts from debug detector persist in DB → price worker rejects them → 100% fallback pricing

---

## Intended Architecture (Should Be)

```
TRANSACTION
  ↓
pumpfun_curve_listener.py (AUTHORITATIVE)
  ├─ Candidate extraction
  ├─ Validation
  ├─ Best-pool selection
  └─ Writes ONLY validated pools → token_pool_accounts
       ↓
price_worker.py
  ├─ Reads from token_pool_accounts (ONLY source)
  ├─ Bootstrap reserves from RPC
  ├─ Compute prices
  └─ Store to database
```

---

## What Needs to Change

### Issue 1: Remove Debug Detector from Production Path

**Current state:**
```python
# pool_detector.py
def detect_pool_from_transaction(tx_data):
    """DEBUG SHORTCUT: Return first AMM-owned account"""
    return first_amm_account  # TOO SIMPLE
```

This gets called and registered directly to token_pool_accounts.

**Fix:**
- Mark `pool_detector.py` as DEBUG ONLY
- Do NOT write its output to token_pool_accounts
- Only use listener's validated path for production writes

### Issue 2: Remove WebSocket Fallback from Worker

**Current state:**
```python
# price_worker.py
def _refresh_cycle(self):
    if not self._ws_client and pools:
        self._start_ws_client()  # Fallback
```

This allows worker to start its own WebSocket if listener didn't.

**Fix:**
- Remove fallback startup from worker
- Worker only reads from shared PoolStateStore
- Listener exclusively owns WebSocket lifecycle
- If listener hasn't started WebSocket, worker just logs warning

### Issue 3: Ensure Listener Writes Validated Pools

**Required:**
- Listener's pool discovery must be the ONLY path writing to token_pool_accounts
- Mark pools as `vault_validation_status='validated'` BEFORE writing
- Debug detector output goes to logs only, never to DB

---

## Impact on Price Worker

**Current state (broken):**
```
Price Worker reads token_pool_accounts
  ↓
Finds 80 invalid pools (from debug detector)
  ↓
RPC returns: account doesn't exist
  ↓
Bootstrap skips all: "0 mints, 80 missing RPC data"
  ↓
Price computation: 0 pools available
  ↓
100% fallback to DexScreener
```

**After fix (correct):**
```
Price Worker reads token_pool_accounts
  ↓
Finds only validated pools (from listener)
  ↓
RPC returns: real reserves
  ↓
Bootstrap populates: "X mints, Y pools with liquidity"
  ↓
Price computation: uses on-chain reserves
  ↓
>90% on-chain pricing
```

**The worker is NOT broken. The data feeding it is broken.**

---

## Files to Fix

1. **pool_detector.py**
   - Mark as DEBUG ONLY
   - Add comment: "DO NOT use for production pool registration"
   - Remove any writes to token_pool_accounts

2. **pumpfun_curve_listener.py**
   - Ensure pool discovery writes to token_pool_accounts
   - Mark pools with `vault_validation_status='validated'`
   - Verify listener owns WebSocket startup

3. **price_worker.py**
   - Remove `_start_ws_client()` fallback from `_refresh_cycle()`
   - Add check: if `_ws_client` not started, log warning (don't start)
   - Worker reads ONLY from PoolStateStore singleton
   - Worker reads ONLY from token_pool_accounts rows

---

## Verification

After fixes:
```bash
# Check that only validated pools are in DB
sqlite3 database/flex_complete_database.db "
  SELECT COUNT(*) as total,
         SUM(CASE WHEN vault_validation_status = 'validated' THEN 1 ELSE 0 END) as validated,
         SUM(CASE WHEN vault_validation_status = 'pending' THEN 1 ELSE 0 END) as pending
  FROM token_pool_accounts
"

# Expected: validated >> pending (no pending pools from debug detector)
```

After listener starts:
```bash
# Check bootstrap logs
tail -f listener.log | grep "Bootstrapped"

# Expected: "X mints (Y pools with liquidity, Z missing RPC data)"
# Should have some pools with liquidity now
```

---

## Summary

**Current situation:**
- Two pool detection paths compete
- Debug detector (simple, invalid) writes to production DB
- Listener (validated) exists but may not be the writer
- Price worker rejects invalid pools correctly
- Result: 100% fallback because no valid pools reach price worker

**Root cause:**
- pool_detector.py should be DEBUG ONLY, not write production data
- Listener should be the sole source for token_pool_accounts
- WebSocket lifecycle should be listener-exclusive

**Solution:**
- Remove debug detector from production write path
- Ensure listener writes only validated pools
- Remove worker's WebSocket fallback startup
- Let worker read only what listener provides

**Outcome:**
- Valid pools in token_pool_accounts
- Bootstrap finds real reserves
- On-chain pricing works (>90%)
- No 100% fallback

This is an **architectural fix**, not a price worker bug. The price worker is working correctly - it's just being fed invalid data.
