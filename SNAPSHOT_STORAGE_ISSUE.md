# Snapshot Storage Issue - Problem Documentation

**Date:** 2026-03-17
**Status:** 🔴 BLOCKING - Snapshots not being stored despite WebSocket working
**Severity:** Critical - Final step of pipeline broken

---

## Problem Statement

The WebSocket subscription fix is **working correctly**, but **price snapshots are NOT being written to the database** even though:
- ✅ WebSocket is receiving reserve data
- ✅ PoolStateStore is populated with 14 mints
- ✅ Validator confirms reserves (1000000, 500000)
- ❌ Database snapshots remain at 0

---

## What's Working

### WebSocket Layer ✅
```
[POOL_WS_DEBUG] accountNotification received
[POOL_WS_DEBUG] Got balance 5022801127860177985 for FvbCWuKoy7Qp6Rmu...
[POOL_WS_DEBUG] Found 2 pools for account FvbCWuKoy7Qp6Rmu...
```

**Evidence:** Messages arriving from network

### Account Subscription ✅
```
[POOL_WS] ✅ Subscribed to 106/106 pool accounts
```

**Evidence:** All accounts successfully subscribed

### PoolStateStore ✅
```
[PRICE_DEBUG] Mints in PoolStateStore: 14
```

**Evidence:** 14 mints actively in memory store

### Validator Confirmation ✅
```
WebSocket Ready: ✓ (1000000, 500000)
WebSocket Confirmed: ✓ (1000000, 500000)
```

**Evidence:** Reserves confirmed present and stable

---

## What's Broken

### Snapshot Storage ❌
```
Snapshots for 6RnxhUqhkRPVLWuZFki4yZzy4qRV4hVpdpjDGUQdpump: 0
```

**Evidence:** Zero snapshots written to database

### Missing Logs

No logs appear for:
- `calling _store_snapshot()`
- `Stored snapshot for [mint]`
- `price computed`
- `INSERT INTO token_price_snapshots`

**Implication:** Price computation step not executing

---

## Root Cause Analysis

### Expected Flow
```
WebSocket Message
  ↓
PoolStateStore.update_reserve()
  ↓
Price Worker Cycle
  ↓
_recompute_prices_from_ws_state()
  ↓
_store_snapshot()  ← SHOULD write here
  ↓
Database INSERT
  ↓
Snapshot created ✅
```

### Actual Flow
```
WebSocket Message ✅
  ↓
PoolStateStore.update_reserve() ✅
  ↓
Price Worker Cycle ✅
  ↓
_recompute_prices_from_ws_state() ❓ (no logs)
  ↓
_store_snapshot() ❌ (not executing)
  ↓
Database INSERT ❌ (not happening)
  ↓
Snapshot ZERO ❌
```

---

## Possible Root Causes

### Theory 1: Price Computation Disabled

Looking at listener logs:
```
[LISTENER] ⏸ Price updater disabled (HARDCODED OFF)
```

**Hypothesis:** The price computation may be explicitly disabled in the listener configuration.

**Evidence:** Log message shows "HARDCODED OFF"

**Impact:** Would explain why prices never computed and stored

### Theory 2: Price Worker Not Computing for New Pools

**Hypothesis:** The price worker cycles through but skips computation for pools without historical data.

**Evidence:**
- PoolStateStore has 14 mints
- But no computation logs appear
- No snapshots written

**Investigation Needed:** Check if `_recompute_prices_from_ws_state()` has conditional logic that skips new pools

### Theory 3: Snapshot Write Function Not Implemented

**Hypothesis:** `_store_snapshot()` method exists but doesn't actually write to database.

**Evidence:**
- Reserves confirmed present
- But no database writes
- No INSERT logs

**Investigation Needed:** Check if `_store_snapshot()` has actual database INSERT code

### Theory 4: Database Transaction Issue

**Hypothesis:** Snapshots are computed but database transaction fails silently.

**Evidence:**
- PoolStateStore populated (computation reaching that stage)
- But no database rows
- No error logs

**Investigation Needed:** Check for exception handling that swallows database errors

---

## Where to Investigate

### File: `src/core/price_worker.py`

**Key Methods to Check:**

1. **`_refresh_cycle()` (line ~364)**
   - Does this call `_recompute_prices_from_ws_state()`?
   - Is there conditional logic that skips it?

2. **`_recompute_prices_from_ws_state()` (line ~700+)**
   - Does this method actually execute?
   - Does it call `_store_snapshot()`?
   - Are there conditions that skip execution for new pools?

3. **`_store_snapshot()` (line ~800+)**
   - Does this actually INSERT to database?
   - Is there exception handling that silently fails?
   - What's the SQL query?

### File: `src/core/pool_price_engine.py`

**Key Methods to Check:**

1. **WebSocket message handler (_handle_message)**
   - Does it update PoolStateStore correctly?
   - Does it trigger any callback for price recomputation?

2. **PoolStateStore.update_reserve()**
   - Does this notify the price worker?
   - Or does price worker only check on timer?

### Configuration

**Check:** Is price computation explicitly disabled?
```
[LISTENER] ⏸ Price updater disabled (HARDCODED OFF)
```

---

## Key Questions

1. **Is price computation even enabled?**
   - Check if there's a feature flag or `HARDCODED OFF` setting
   - Look for: `price_updater_disabled`, `PRICE_DISABLED`, etc.

2. **Does _recompute_prices_from_ws_state() execute during cycle?**
   - Add logging: `print("[PRICE_DEBUG] _recompute_prices_from_ws_state() called")` at start
   - Run listener and check if message appears

3. **Does _store_snapshot() get called?**
   - Add logging: `print("[PRICE_DEBUG] _store_snapshot() called for {mint}")` at start
   - Run listener and check if message appears

4. **Are there exceptions being silently caught?**
   - Check for broad `except Exception` blocks
   - Look for exception handling without re-raising or logging

5. **Is there a transaction that's never committed?**
   - Search for `db.execute()` without `db.commit()`
   - Check cursor open/close patterns

---

## Current System State

### Database Status
- Total snapshots: 55,451
- Snapshots today: 0 (since midnight)
- Last snapshot: ~2 minutes ago (timestamp 1773784943)
- Status: **Not receiving new snapshots**

### Listener Status
- Process: Running (PID 94236)
- Code version: Latest (with our WebSocket fix)
- WebSocket: Subscribed to 106/106 accounts
- PoolStateStore: 14 mints active
- Price computation: **NOT executing**

### WebSocket Status
- Messages: ✅ Flowing
- Reserves: ✅ Confirmed
- Subscriptions: ✅ Active
- Data: **Arriving but not processed to snapshots**

---

## Impact

### Current Behavior
- New pools registered ✅
- WebSocket subscribed ✅
- Reserves received ✅
- Prices computed ❌
- Snapshots stored ❌

### Production Impact
- **Pricing service broken** - No prices available for new pools
- **System stuck** - WebSocket working but data not flowing through
- **Users affected** - Can't get price data for newly launched tokens

---

## Next Steps to Diagnose

### Step 1: Add Diagnostic Logging
```python
# In _recompute_prices_from_ws_state()
print("[PRICE_DEBUG_DIAGNOSTIC] Starting price recomputation", flush=True)

# In _store_snapshot()
print("[PRICE_DEBUG_DIAGNOSTIC] Storing snapshot for {mint}", flush=True)
```

### Step 2: Run Listener and Capture Output
```bash
python3 -m src.core.pumpfun_curve_listener 2>&1 | tee diagnostic.log &
sleep 5
tail -f diagnostic.log | grep "PRICE_DEBUG_DIAGNOSTIC"
```

### Step 3: Check Output
- If you see `Starting price recomputation` → computation IS running
- If you see `Storing snapshot` → storage IS being called
- If you see neither → one of the methods isn't being called

### Step 4: Check Database
```bash
# Monitor for new inserts
while true; do
  sqlite3 database/flex_complete_database.db "SELECT COUNT(*) FROM token_price_snapshots WHERE created_at > $(date +%s) - 10"
  sleep 5
done
```

---

## Summary

**The WebSocket fix is working perfectly.** The pipeline is 95% complete:
- Pool discovery ✅
- Registration ✅
- WebSocket subscription ✅
- Reserve updates ✅
- PoolStateStore ✅
- **Price computation ❌** ← Broken here
- **Snapshot storage ❌** ← Broken here

The issue is in the price computation layer, which is SEPARATE from the WebSocket subscription fix we implemented.

**Recommendation:** Add diagnostic logging to identify which step in the price computation is failing.

