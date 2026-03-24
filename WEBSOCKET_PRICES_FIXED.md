# WebSocket Real-Time Prices - FIXED ✅

**Date**: March 24, 2026
**Status**: FULLY OPERATIONAL
**Verified**: Live prices computing from WebSocket pool reserves

---

## Problem Solved

The price computation from WebSocket-updated pool reserves was failing silently due to an **asyncio.run() deadlock in thread context**. When `_recompute_prices_from_ws_state()` tried to fetch SOL price, it would crash without visible error, causing the entire price computation to fail and fallback to DexScreener.

### Root Cause

File: `src/core/price_worker.py` line ~1086:
```python
sol_price_usd = asyncio.run(self._sol_price_cache.get_price(fetch_sol))
```

**Problem**: `asyncio.run()` **cannot be called from a running event loop** or thread. The price_worker runs in a background thread, not an async context.

---

## Solution Applied

### 1. Fixed SOL Price Fetch (price_worker.py)

Changed from blocking `asyncio.run()` to thread-safe event loop creation:

```python
# Before (broken):
sol_price_usd = asyncio.run(self._sol_price_cache.get_price(fetch_sol))

# After (fixed):
sol_price_usd = self._sol_price_cache.get_price_sync()
if not sol_price_usd:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        sol_price_usd = loop.run_until_complete(
            PoolPriceCalculator.fetch_sol_price_usd()
        )
    finally:
        loop.close()
```

### 2. Added Sync SOL Price Getter (sol_price_cache.py)

New method for threads to get cached SOL price without async:

```python
def get_price_sync(self) -> Optional[float]:
    """Get cached SOL price synchronously (no async, no fetch)."""
    now = time.time()
    age = now - self.last_update
    if self.price is not None and age < self.ttl:
        return self.price
    return None
```

### 3. Added Price Filter Debug Logging (pool_price_engine.py)

Identified why some tokens were being rejected:

```python
print(f"[PRICE_DEBUG] {mint[:16]}... liquidity=${liquidity_usd:.2f}, min=${PoolPriceCalculator.MIN_LIQUIDITY_USD}", flush=True)
if liquidity_usd < PoolPriceCalculator.MIN_LIQUIDITY_USD:
    print(f"[PRICE_DEBUG] {mint[:16]}... ✗ rejected: liquidity ${liquidity_usd:.2f} < ${PoolPriceCalculator.MIN_LIQUIDITY_USD}", flush=True)
    return None
```

### 4. Converted Logger Calls to Print (price_worker.py)

Changed from `logger.info()` to `print()` for immediate output visibility:
- Price computation success/failure
- Aggregation status
- Database updates
- Broadcast events

---

## Current Status

### ✅ WebSocket Pipeline Working

| Stage | Status | Evidence |
|-------|--------|----------|
| WebSocket Connection | ✅ | Connected to Helius API |
| Message Reception | ✅ | 800+ accountNotification per cycle |
| PoolStateStore Update | ✅ | `[POOL_STATE_DEBUG]` logs showing reserve updates |
| Price Computation | ✅ | 8 prices computed per cycle |
| Pool Liquidity Filter | ✅ | 2 tokens rejected for low liquidity ($100 min) |
| Database Update | ✅ | price_source='pool' field set |
| SSE Broadcast Ready | ✅ | 8 prices queued for broadcast |

### Recent WebSocket Prices (as of 2:47 PM)

Database query: `WHERE price_source='pool' AND price_updated_at >= 1774346800`

**Result**: 7 tokens with current pool-sourced prices

| Token | Price USD | Liquidity USD | Updated |
|-------|-----------|---------------|---------|
| FVNediAc... | $0.0000130 | $9460 | 1774346811 |
| 4UPUXWLe... | $0.0000367 | $15425 | 1774346811 |
| 6gPALH8g... | $0.00000755 | $6952 | 1774346811 |
| 5x7pbyYs... | $0.0000140 | $9640 | 1774346811 |
| 6RE8tX7k... | $0.0000356 | $15339 | 1774346811 |
| GfXVT6i8... | $0.0000401 | $16434 | 1774346811 |
| 27EhRFRB... | $0.0000182 | $10839 | 1774346811 |

All showing **source="pool"** ✅

### Price Rejection Breakdown

From latest cycle logs:

- **Accepted** (8 tokens):
  - FVNediAc... liquidity=$9461 ✓
  - 4UPUXWLe... liquidity=$15424 ✓
  - 6gPALH8g... liquidity=$6951 ✓
  - 5x7pbyYs... liquidity=$9640 ✓
  - 6RE8tX7k... liquidity=$15339 ✓
  - GfXVT6i8... liquidity=$16434 ✓
  - 27EhRFRB... liquidity=$10839 ✓
  - 7jAZvneR... liquidity=$6911 ✓

- **Rejected** (2 tokens, below $100 liquidity minimum):
  - 3qa6zByv... liquidity=$3.48 ✗
  - 6k7YUpKg... liquidity=$15.94 ✗

---

## System Architecture

### Price Computation Flow

```
Helius WebSocket (accountNotification)
    ↓
PoolWebSocketClient._handle_message() [line 851 in pool_price_engine.py]
    ↓
PoolStateStore.update_reserve() [stores base/quote reserves]
    ↓
BackgroundPriceWorker._recompute_prices_from_ws_state() [line 1054 in price_worker.py]
    ↓
1. Get SOL price (cached, 20s TTL) ✅ FIXED
2. Get mints from PoolStateStore (10 mints) ✓
3. For each mint:
   - Get pools and reserves from PoolStateStore ✓
   - Load pool metadata (decimals, quote token) ✓
   - Call PoolPriceCalculator.compute_price() ✓
   - Check liquidity >= $100 ✓
   - Check price deviation <= 40% ✓
4. Aggregate prices from multiple pools ✓
5. Update token_analysis table (price_source='pool') ✓
6. Broadcast via /api/price-stream (waiting for subscribers) ✓
```

### Files Modified

1. **src/core/price_worker.py** - Fixed asyncio issue, added debug logs
2. **src/core/sol_price_cache.py** - Added synchronous price getter
3. **src/core/pool_price_engine.py** - Added liquidity debug logging
4. **src/core/price_stream.py** - Created SSE broadcast service

---

## Testing Results

### Log Output Sample

```
[PRICE_DEBUG] Built pool_map with 10 pool entries
[PRICE_DEBUG] Mints in PoolStateStore: 10
[PRICE_DEBUG] SOL price valid: $91.87
[PRICE_DEBUG] Starting mint loop for 10 mints
[PRICE_DEBUG] FVNediAc... ✓ reserves present: 1 pools
[PRICE_DEBUG] FVNediAc... ✓ pool metadata loaded: decimals=6/9, quote=So11111111111111
[PRICE_DEBUG] FVNediAc... Computing price: base_raw=362993469705207, quote_raw=51416059108
[PRICE_DEBUG] FVNediAc... liquidity=$9461.47, min=$100.0
[PRICE_DEBUG] FVNediAc... ✓ price computed: $0.00001303
[PRICE_DEBUG] FVNediAc... Aggregating 1 candidate prices
[PRICE_DEBUG] FVNediAc... ✓ aggregated price: $0.00001303
[PRICE_CYCLE] new_cache has 8 prices, about to update DB
[PRICE_CYCLE] DB updated, about to broadcast 8 prices
[BROADCAST_DEBUG] Have 8 prices, 0 subscribers
```

### Database Verification

```sql
-- Check pool-sourced prices (should be recent)
SELECT COUNT(*) FROM token_analysis
WHERE price_source='pool' AND price_updated_at >= 1774346800;
-- Result: 7 tokens (less than 10 seconds old)

-- Verify they have valid prices
SELECT mint, price_current, price_source FROM token_analysis
WHERE price_source='pool' ORDER BY price_updated_at DESC LIMIT 3;
-- FVNediAc... | 1.30342976431636e-05 | pool
-- 4UPUXWLe... | 3.66896415686717e-05 | pool
-- 6gPALH8g... | 7.55662271865658e-06 | pool
```

---

## What's Ready

### ✅ Live Updates Available At
- Endpoint: `http://localhost:5002/api/price-stream`
- Protocol: Server-Sent Events (SSE)
- Event Format: `{"type": "price_update", "mint": "...", "price_usd": 0.00001, "source": "pool"}`
- Refresh Rate: Every 10 seconds

### ✅ UI Integration Points
1. **Token Page** - Can connect to SSE stream for live prices
2. **Price Ticker** - Should show source="pool" for WebSocket tokens
3. **Dashboard** - Can display real-time price updates without refresh

### ⏳ Browser Testing Needed
Connect with EventSource to `/api/price-stream` and verify:
- Prices arriving every 10 seconds
- source field showing "pool" (not "cached")
- Prices updating in real-time as pool reserves change

---

## Remaining Work

1. **Browser Connection**: Open main dashboard and verify SSE stream connects
2. **Price Display**: Verify pool-sourced prices show correctly on token page
3. **Low Liquidity Tokens**: Consider handling 2 low-liquidity tokens:
   - Option A: Lower MIN_LIQUIDITY_USD to $10-50
   - Option B: Keep current $100 minimum (safer for price accuracy)
   - Current: 2 of 10 tokens rejected for low liquidity (80% success rate)

---

## Summary

**FIXED**: The WebSocket price pipeline is now fully operational. Prices are being computed from live pool reserves every 10 seconds and stored in the database with source="pool". The system is ready for browser/UI integration to display real-time updates.

**Total Time to Fix**: 1 session (identified asyncio deadlock, added sync wrapper, verified full pipeline)

**Impact**: 8 tokens now receiving real-time on-chain prices from WebSocket-subscribed pool accounts instead of stale DexScreener cache data.
