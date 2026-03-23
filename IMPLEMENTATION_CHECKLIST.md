# Pool Pricing Fix - Implementation Checklist

## Problem
PoolStateStore initialized to (0,0) → price computation fails → 100% fallback to DexScreener

## Root Cause
WebSocket is delta-only. If initial state is zero and no trades occur, it stays zero forever.

## Solution Architecture
```
RPC bootstrap → PoolStateStore (REAL values) → WebSocket updates → Price computation
```

---

## Implementation Steps

### Step 1: Update `price_worker.py` initialization

**Replace this:**
```python
# OLD: Initialize with zeros
self._pool_state.update_reserve(mint, base_account, "base", 0)
self._pool_state.update_reserve(mint, base_account, "quote", 0)
```

**With this:**
```python
# NEW: Bootstrap from RPC
reserves_dict = await fetcher.fetch_reserves(pools)
(base_raw, quote_raw) = reserves_dict.get((mint, base_account), (0, 0))
self._pool_state.update_reserve(mint, base_account, "base", base_raw)
self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)
```

**Location:** `src/core/price_worker.py` → `_initialize_pool_state_sync()` method

---

### Step 2: Fix pool readiness check in `pool_price_engine.py`

**Replace this:**
```python
# OLD: Just checks if not None
if has_base and has_quote and not was_ready:
    print(f"[POOL_STATE] ✅ READY: {mint[:8]}...")
```

**With this:**
```python
# NEW: Checks if > 0 (has actual liquidity)
if (
    self._state[pool_id]["base_reserve"] is not None
    and self._state[pool_id]["base_reserve"] > 0
    and self._state[pool_id]["quote_reserve"] is not None
    and self._state[pool_id]["quote_reserve"] > 0
    and not was_ready
):
    logger.info(
        f"[POOL_STATE] ✅ READY: {mint[:8]}... "
        f"(base={self._state[pool_id]['base_reserve']}, "
        f"quote={self._state[pool_id]['quote_reserve']})"
    )
    self._state[pool_id]["was_ready"] = True
```

**Location:** `src/core/pool_price_engine.py` → `PoolStateStore.update_reserve()` method (lines 410-412)

---

### Step 3: Add periodic resync task

**Add to `price_worker.py`:**
```python
async def _periodic_pool_resync(self):
    """Re-fetch reserves every 3 minutes to repair any stale state."""
    while self.running:
        try:
            await asyncio.sleep(180)
            reserves = await fetcher.fetch_reserves(pools)

            repaired = 0
            for (mint, base), (base_raw, quote_raw) in reserves.items():
                self._pool_state.update_reserve(mint, base, "base", base_raw)
                self._pool_state.update_reserve(mint, base, "quote", quote_raw)
                repaired += 1

            if repaired > 0:
                logger.info(f"[POOL_RESYNC] Repaired {repaired} pools")

        except Exception as e:
            logger.error(f"[POOL_RESYNC] Error: {e}")
```

**Call in `start()`:**
```python
asyncio.create_task(self._periodic_pool_resync())
```

---

### Step 4: Verify correct startup sequence

**In `price_worker.py` `start()` method, ensure order is:**

```
1. Load pools from database
2. Bootstrap reserves from RPC (BEFORE WebSocket)
3. Start WebSocket subscriptions
4. Start price computation loop
5. Start periodic resync task (background)
```

---

## Testing Checklist

### ✅ Initialization
- [ ] Listener starts and logs reserves bootstrap
- [ ] Logs show: `[PRICE_BOOTSTRAP] Fetched N pool reserves`
- [ ] Pools show real values, not (0,0)

### ✅ Pool Readiness
- [ ] Only pools with `base > 0 AND quote > 0` are marked ✅ READY
- [ ] Logs like `[POOL_STATE] ✅ READY: 6RnxhUqh... (base=123456789, quote=987654321)`

### ✅ Price Computation
- [ ] Price worker runs successfully (no silent failures)
- [ ] Logs show `[PRICE_DEBUG] Computing price: base_raw=123456789, quote_raw=987654321`
- [ ] Prices computed successfully (not all onchain_failed)

### ✅ WebSocket Updates
- [ ] When pools trade, WebSocket updates flow through
- [ ] Logs show `[POOL_WS_DEBUG] Got balance XXX for ACCOUNT...`
- [ ] Reserves update in PoolStateStore

### ✅ Periodic Resync
- [ ] Every 3 min: `[POOL_RESYNC] Running periodic resync...`
- [ ] Zero-liquidity pools are detected and skipped
- [ ] No errors in resync loop

---

## Validation Commands

```bash
# 1. Check initialization logs
tail -f listener.log | grep PRICE_BOOTSTRAP

# 2. Check pool readiness
tail -f listener.log | grep "POOL_STATE.*READY"

# 3. Check price computation
tail -f listener.log | grep "Computing price"

# 4. Check for fallbacks
tail -f listener.log | grep "onchain_failed" | wc -l
# Should be LOW (not 100%)

# 5. Check resync
tail -f listener.log | grep POOL_RESYNC
```

---

## Expected Results After Fix

### Before
```
[POOL_STATE_DEBUG] 📝 Storing base_reserve=0 for 6RnxhUqh
[POOL_STATE_DEBUG] State after update: base=0, quote=0
[POOL_STATE] ✅ READY: 6RnxhUqh... both reserves!  ← LIE
[PRICE_DEBUG] Computing price: base_raw=0, quote_raw=0
[PRICE_FALLBACK] mint=6RnxhUqh... reason=onchain_failed (fallback_rate=100.00%)
```

### After
```
[PRICE_BOOTSTRAP] Fetched 72 pool reserves
[PRICE_BOOTSTRAP] 6RnxhUqh... → base=123456789, quote=987654321
[POOL_STATE] ✅ READY: 6RnxhUqh... (base=123456789, quote=987654321)  ← TRUTH
[PRICE_DEBUG] Computing price: base_raw=123456789, quote_raw=987654321
[PRICE_DEBUG] ✓ price computed: $0.00045
[PRICE_PERSIST] mint=6RnxhUqh... price=$0.00045 source=pool
```

---

## Key Principle (Remember)

**RPC = Initial state (bootstrap)**
**WebSocket = Updates only (deltas)**

If you break this, the system WILL fail silently.

---

## Files to Modify

1. `src/core/price_worker.py` - Bootstrap logic + periodic resync
2. `src/core/pool_price_engine.py` - Readiness condition fix

**Reference:** `POOL_PRICING_FIXES.py` has complete production-safe functions ready to use.
