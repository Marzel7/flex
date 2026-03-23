# WebSocket Reserve Update Issue - Root Cause Analysis

## Problem Statement
Pool reserves in PoolStateStore remain at `0` despite WebSocket subscriptions being active. Price computation fails because `base_raw=0, quote_raw=0` for all pools.

## Root Cause Identified

### The Issue
The initialization code in `price_worker.py` (lines 281-282) sets ALL pool reserves to 0:

```python
self._pool_state.update_reserve(mint, base_account, "base", 0)
self._pool_state.update_reserve(mint, base_account, "quote", 0)
```

This creates the state:
```
[POOL_STATE_DEBUG] 📝 Storing base_reserve=0 for 6RnxhUqh... slot=None
[POOL_STATE_DEBUG] State after update: base=0, quote=None
[POOL_STATE_DEBUG] 📝 Storing quote_reserve=0 for 6RnxhUqh... slot=None
[POOL_STATE_DEBUG] State after update: base=0, quote=0
[POOL_STATE] ✅ READY: 6RnxhUqh... both reserves!
```

### Why WebSocket Updates Never Arrive
1. **PoolStateStore initialization:** Sets all 72 pools to `base=0, quote=0`
2. **WebSocket subscriptions are lazy:** Helius `accountSubscribe` only sends notifications when accounts **change on-chain**
3. **No active trading:** Without trades on these pools, the reserve accounts don't mutate
4. **Result:** WebSocket subscriptions are active but receive no notifications, so reserves stay at 0

### Evidence from Logs
```
[POOL_WS] ✅ Subscribed to 132/132 pool accounts
[POOL_WS] 🔄 _receive_loop started, waiting for account notifications...
[POOL_WS_DEBUG] No balance extracted from account  ← Never logged (no messages received)
[POOL_WS_DEBUG] ✅ Got balance...  ← Never logged (no messages received)
```

The `_receive_loop` is running but **never receives any accountNotification messages** because no accounts are changing.

## Why This Matters

### Price Computation Pipeline Breakdown
```
PoolStateStore (base=0, quote=0)
    ↓
Price Worker reads reserves: (0, 0)
    ↓
Cannot compute price from zero liquidity
    ↓
Falls back 100% to DexScreener
    ↓
[PRICE_FALLBACK] reason=onchain_failed
```

## Solution Implemented

### Change in `price_worker.py` `_initialize_pool_state_sync()`

**Before:**
```python
# Initialize with zero reserves - WebSocket will populate real values
self._pool_state.update_reserve(mint, base_account, "base", 0)
self._pool_state.update_reserve(mint, base_account, "quote", 0)
```

**After:**
```python
# Fetch real reserves from RPC on initialization
reserves_dict = asyncio.run(fetcher.fetch_reserves(pools))

# Populate PoolStateStore with fetched reserves
(base_raw, quote_raw) = reserves_dict.get((mint, base_account), (0, 0))
self._pool_state.update_reserve(mint, base_account, "base", base_raw)
self._pool_state.update_reserve(mint, base_account, "quote", quote_raw)
```

### How This Fixes It

1. **Initialization:** Fetches REAL on-chain reserves via RPC (not 0)
2. **WebSocket ready:** Starts with correct state, WebSocket updates will flow through immediately when accounts change
3. **Price computation:** Can now compute prices from real data
4. **WebSocket updates:** When accounts change on-chain, accountNotifications update the reserves further
5. **Result:** Prices computed from on-chain data, with real-time updates from WebSocket

## Technical Details

### What accountSubscribe Does
- Helius listens to specified accounts
- Only sends notifications when account data **changes**
- This is by design (efficiency/bandwidth)
- For inactive pools (no trades), no notifications = no updates

### Why Zero Initialization Was Wrong
- Assumes WebSocket will populate real values
- But WebSocket **only sends deltas**, not initial state
- If accounts never change after initialization, they stay 0 forever
- This breaks the entire on-chain pricing pipeline

### The Fix's Dependency
The fix requires `fetcher.fetch_reserves()` to work. This method:
- Calls RPC `getMultipleAccounts` for all pool accounts
- Decodes SPL token balances from account data
- Returns dict of `{(mint, base_account): (base_raw, quote_raw)}`
- Falls back to 0 if RPC fails (graceful degradation)

## Testing

To verify the fix:
1. Start listener/price worker
2. Check initialization logs:
   ```
   [PRICE_INIT] Fetching real reserves for 72 pools from RPC...
   [PRICE_INIT] ✅ Fetched reserves for 72 pool pairs
   [PRICE_INIT] Pool 6RnxhUqh...: base=123456789, quote=987654321
   ```
3. Monitor price computation:
   ```
   [PRICE_DEBUG] Computing price: base_raw=123456789, quote_raw=987654321
   [PRICE_DEBUG] ✓ price computed: $0.00045
   ```
4. Watch WebSocket updates (when pools trade):
   ```
   [POOL_WS_DEBUG] ✅ Got balance 999999999 for 6RnxhUqh...
   [POOL_STATE_DEBUG] 📝 Storing quote_reserve=999999999
   ```

## Impact

- **Before:** 100% fallback to DexScreener, no on-chain pricing
- **After:** Real on-chain prices from pool reserves, updated in real-time by WebSocket when accounts change

## Files Modified

- `src/core/price_worker.py`: `_initialize_pool_state_sync()` method
- `src/core/pool_price_engine.py`: Added debug logging to WebSocket message handler and PoolStateStore
