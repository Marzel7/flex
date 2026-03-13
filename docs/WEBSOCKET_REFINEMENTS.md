# WebSocket Pool Upgrade — Refinements & Safety Improvements

**Date:** March 13, 2026 (Post-implementation enhancements)
**Status:** ✅ Complete

---

## Overview

Added five safety refinements to the WebSocket pool subscription layer:

1. **Stale WebSocket Detection** — Triggers fallback polling if no events >2 minutes
2. **Event Deduplication** — Skips duplicate updates from same Solana block slot
3. **Pool Inactivity Detection** — Marks pools with no updates >5 minutes as stale
4. **Adaptive Fallback Polling** — Polls faster (30s) when WS is unhealthy
5. **Extended Health Metrics** — Track deduplication rate and stale pool count

---

## 1. Stale WebSocket Detection

### Problem
If WebSocket silently stalls (connection idle, events stop flowing), the system could operate on stale reserve data without knowing it.

### Solution
Track time since last event. If no events for >2 minutes, automatically trigger fallback RPC poll (every 30s instead of 60s).

### Implementation
**PoolWebSocketClient:**
```python
WS_STALE_THRESHOLD = 120  # 2 minutes

# Track last event received
self._last_event_received = time.time()
self.stats['is_stale'] = False  # New stat

# On message receipt
self._last_event_received = time.time()
self.stats['is_stale'] = False
```

**BackgroundPriceWorker._fetch_pool_prices():**
```python
# Check for stale WS
if self._ws_client:
    time_since_last_event = now - self._ws_client._last_event_received
    if time_since_last_event > self._ws_client.WS_STALE_THRESHOLD:
        ws_is_stale = True
        if now - self._last_fallback_poll >= 30:
            logger.warning(f"WS stale for {time_since_last_event:.0f}s")

# Adaptive polling
poll_interval = 30 if ws_is_stale else 60
if now - self._last_fallback_poll >= poll_interval:
    asyncio.run(self._fetch_pool_prices_async())
```

### Monitoring
Check `ws.is_stale` in health endpoint:
```bash
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.is_stale'
# → false (healthy) or true (stale, polling more frequently)
```

---

## 2. Event Deduplication

### Problem
Solana can send multiple `accountNotification` events for the same account in the same slot (block). This wastes processing and creates duplicate state updates.

### Solution
Track the slot (block height) of each update. Skip if we've already processed that slot for that account.

### Implementation
**PoolStateStore.update_reserve():**
```python
def update_reserve(self, mint: str, account_type: str, raw_balance: int, slot: Optional[int] = None) -> bool:
    """
    Returns True if update was applied, False if deduplicated.
    """
    with self._lock:
        if mint not in self._state:
            self._state[mint] = {
                "base_reserve": None,
                "quote_reserve": None,
                "last_slot": None,
                ...
            }

        # Dedup: skip if same slot seen recently
        if slot is not None and self._state[mint]["last_slot"] == slot:
            return False  # Deduplicated

        # Apply update
        self._state[mint][f"{account_type}_reserve"] = raw_balance
        self._state[mint]["last_slot"] = slot
        return True  # Applied
```

**PoolWebSocketClient._handle_message():**
```python
# Extract slot from notification
slot = params.get("result", {}).get("context", {}).get("slot")

# Update returns False if deduplicated
if not self._store.update_reserve(mint, account_type, balance, slot):
    self.stats["events_deduplicated"] += 1
    return
```

### Monitoring
Check deduplication rate:
```bash
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.events_deduplicated'
# → N (should be >0 if receiving many events per slot)
```

High dedup rate indicates either:
- Heavy trading activity (many updates per block) — normal
- Slow processing (events backing up) — watch for this

---

## 3. Pool Inactivity Detection

### Problem
A registered pool might stop receiving swaps (dead pool, low liquidity). If WS is the only source, stale reserves could persist indefinitely.

### Solution
Mark pools with no updates >5 minutes as "stale". `get_reserves()` returns None for stale pools, triggering fallback to external APIs.

### Implementation
**PoolStateStore:**
```python
STALE_POOL_THRESHOLD = 300  # 5 minutes

def mark_stale_pools(self, now: Optional[float] = None) -> List[str]:
    """
    Mark pools inactive >5 min as stale.
    Returns list of mints that became stale.
    """
    stale_mints = []
    with self._lock:
        for mint, state in self._state.items():
            if not state["is_stale"] and now - state["last_update"] > 300:
                state["is_stale"] = True
                stale_mints.append(mint)
    return stale_mints

def get_reserves(self, mint: str) -> Optional[Tuple[int, int]]:
    """Return reserves only if not stale."""
    with self._lock:
        s = self._state.get(mint)
        if s and s["base_reserve"] is not None and s["quote_reserve"] is not None and not s["is_stale"]:
            return (s["base_reserve"], s["quote_reserve"])
    return None
```

**BackgroundPriceWorker._fetch_pool_prices():**
```python
# Check and mark stale pools
stale_mints = self._pool_state.mark_stale_pools(now)
# (stale pools fall back to Dexscreener → Jupiter → Birdeye)
```

### Monitoring
If a pool becomes stale:
1. Check if it's still active on-chain (Pump.Fun, Raydium, Orca)
2. Verify swaps are happening
3. Or de-register the pool if dead

No explicit metric exposed; check logs:
```
Marked 1 pools as stale (no updates >5 min): [EPjFWaLb3od...]
```

---

## 4. Adaptive Fallback Polling

### Problem
Fixed 60s fallback poll is too slow if WS stalls. Price could be wrong for up to 60s.

### Solution
When WS is stale, poll every 30s instead of 60s. As soon as WS recovers, revert to 60s.

### Implementation
Already shown above in "Stale WebSocket Detection" — `poll_interval = 30 if ws_is_stale else 60`.

### Benefit
- Normal: 500 baseline calls/hour + 30 fallback calls = ~530 calls/hour
- WS down: 60 fallback calls = ~60 calls/hour (already low)
- WS stale: 120 fallback calls = ~120 calls/hour (short spike)

No additional cost, faster recovery.

---

## 5. Extended Health Metrics

### New Stats

**PoolWebSocketClient.stats:**
```python
{
    "connected": bool,              # WS TCP connected
    "subscriptions": int,           # Number of active subscriptions
    "events_received": int,         # All events (including deduplicated)
    "events_decoded": int,          # Successfully decoded
    "events_deduplicated": int,     # NEW — skipped (same slot)
    "reconnects": int,              # How many times reconnected
    "last_event_at": float,         # Unix timestamp of last event
    "is_stale": bool,               # NEW — no events >2 min
}
```

### Health Endpoint Response
```json
{
  "pool_stats": {
    "pools_registered": 5,
    "pool_prices_cached": 4,
    "pool_prices_fetched_last_cycle": 4,
    "pool_attempted": 120,
    "pool_success": 115,
    "pool_fail": 5,
    "ws": {
      "connected": true,
      "subscriptions": 10,
      "events_received": 1250,
      "events_deduplicated": 50,         // NEW
      "events_decoded": 1200,
      "reconnects": 0,
      "last_event_at": 1710350000,
      "is_stale": false                 // NEW
    }
  }
}
```

---

## Monitoring Dashboard Recommendations

Track these on a dashboard:

| Metric | Good | Warning | Bad |
|--------|------|---------|-----|
| `ws.connected` | `true` | N/A | `false` |
| `ws.is_stale` | `false` | N/A | `true` — polling fallback active |
| `events_received` | increasing | flat for >2 min | 0 |
| `events_decoded` | similar to received | slightly lower | much lower → decode errors |
| `events_deduplicated` | 5-20% of received | >30% | N/A |
| `reconnects` | 0 | <1/hour | >1/hour → instability |

---

## Testing Refinements

### 1. Test Stale Detection
```bash
# Pause network for 2+ minutes
(wait)

# Check health — should show is_stale=true
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws.is_stale'
# → true

# Check logs
grep "WS stale for" logs/dev_intelligence.log
# → "WS stale for 120s — triggering fallback poll"

# Verify fallback poll runs every 30s now (not 60s)
grep "Pool fallback poll" logs/dev_intelligence.log | wc -l
# → Should see entries more frequently

# Resume network
(resume)

# Should auto-recover within 2 minutes
```

### 2. Test Deduplication
```bash
# Monitor during active trading
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws | {events_received, events_deduplicated}'

# Dedup rate = events_deduplicated / events_received
# Normal: 5-20%
```

### 3. Test Pool Staleness
```bash
# Find a low-volume pool, wait 5+ minutes
(wait)

# Check health
curl http://localhost:5002/api/price/health | jq '.pool_stats.ws'

# Logs should show
grep "Marked.*pools as stale" logs/dev_intelligence.log
# → "Marked 1 pools as stale (no updates >5 min)"

# Verify pool falls back to external APIs
curl http://localhost:5002/api/price/DEAD_POOL_MINT | jq '.source'
# → "dexscreener" or "jupiter" (not "pool")
```

---

## Code Changes Summary

| File | Change | Lines |
|------|--------|-------|
| `src/core/pool_price_engine.py` | PoolStateStore: dedup, staleness tracking; PoolWebSocketClient: stale detection | +40 |
| `src/core/price_worker.py` | Adaptive polling, stale pool detection | +20 |
| **Total** | | **+60** |

---

## Backwards Compatibility

All refinements are:
- ✅ Additive (no breaking changes)
- ✅ Automatic (no config changes needed)
- ✅ Defensive (improve reliability without affecting normal operation)

Existing behavior unchanged; these only activate under failure conditions.

---

## Future Enhancements

These refinements lay groundwork for:

1. **Automatic Pool Health Dashboard** — visualize stale, inactive, and high-dedup pools
2. **Smart Pool Culling** — auto-disable persistently stale pools (no swaps for days)
3. **Liquidity-Weighted Price** — if multiple pools available, use liquidity-adjusted median
4. **Program Subscriptions** — auto-detect new Raydium/Pump pools instead of manual registration

---

See **[WEBSOCKET_POOL_UPGRADE.md](WEBSOCKET_POOL_UPGRADE.md)** for original architecture.
See **[WEBSOCKET_QUICK_START.md](WEBSOCKET_QUICK_START.md)** for operations guide.
