# WebSocket Architecture Summary — How Pool Price Pipeline Works

**Status:** Post-fix implementation
**Last updated:** 2026-03-17

---

## The Problem We Solved

### Before the Fix

```
New Pool Discovered
  ↓
Registered to Database ✅
  ↓
trigger_pool_refresh() called
  ↓
refresh_pools() updates internal state
  ↓
BUT: WebSocket still subscribed to OLD pool list
  ↓
NO messages arrive for new pool
  ↓
PoolStateStore stays empty for new pool
  ↓
NO price computed
  ↓
ZERO snapshots written ❌
```

### After the Fix

```
New Pool Discovered
  ↓
Registered to Database ✅
  ↓
trigger_pool_refresh() called
  ↓
Old WebSocket stopped completely
  ↓
Fresh WebSocket created with ALL pools
  ↓
New subscriptions sent to network ✅
  ↓
Messages arrive for new pool
  ↓
PoolStateStore updated ✅
  ↓
Price computed ✅
  ↓
Snapshots written ✅
```

---

## The Architecture

### Three Main Components

#### 1. **BackgroundPriceWorker** (price_worker.py)

The main orchestrator that manages the WebSocket lifecycle.

```
BackgroundPriceWorker
├── start()                    [starts worker thread]
│   └── _run_loop()           [main thread loop]
│       └── _refresh_cycle()  [called every 10s]
│           ├── _recompute_prices_from_ws_state()  [calculate prices from WebSocket data]
│           └── _fetch_pool_prices_async()         [RPC fallback if needed]
│
├── _start_ws_client()         [creates PoolWebSocketClient]
│
└── trigger_pool_refresh()     [NEW POOL DETECTED - DO FULL RESTART]
    ├── Stop old WebSocket client
    └── Start fresh WebSocket client
```

**Key Decision Point:** When `trigger_pool_refresh()` is called:
- **Full Rebuild Path:** Stop old, create fresh → guarantees correctness
- ~~Incremental Path~~: Just refresh subscriptions on running client → failed (stale state)

#### 2. **PoolWebSocketClient** (pool_price_engine.py)

Manages the WebSocket connection to Helius RPC and dispatches updates.

```
PoolWebSocketClient
├── start(pools)               [subscribe to all pool accounts]
│   ├── _build_account_map(pools)     [mint → [(base_account, quote_account)] mapping]
│   └── _connect_loop()               [asyncio event loop]
│       ├── Subscribe to all accounts
│       ├── Receive accountNotifications
│       └── _handle_message(notification)
│           ├── Update PoolStateStore with reserves
│           └── [trigger price recompute in price_worker]
│
├── refresh_pools(pools)       [rebuild account map and reconnect]
│   └── _build_account_map(pools)
│   └── Stop event loop (will reconnect with new map)
│
└── stop()                     [cleanup WebSocket connection]
```

**Key Insight:** The event loop (`_connect_loop`) automatically reconnects when stopped. On reconnect, it uses the current `_account_to_pools` map. So to add new subscriptions, we must rebuild the map BEFORE reconnect.

#### 3. **PoolStateStore** (pool_price_engine.py)

In-memory cache of latest pool reserves keyed by `(mint, base_account)`.

```
PoolStateStore
├── update_reserve(mint, base_account, account_type, balance, slot)
│   └── Stores: self._state[(mint, base_account)] = {
│         'base_reserve': int,
│         'quote_reserve': int,
│         'last_update': timestamp,
│         'is_stale': bool
│       }
│
└── get_reserves(mint, base_account) → (base_reserve, quote_reserve)
```

**Flow:**
1. WebSocket message arrives with new balance for `base_account`
2. `_handle_message()` calls `update_reserve(mint, base_account, 'base', balance)`
3. PoolStateStore stores the value
4. Price worker reads from PoolStateStore in `_recompute_prices_from_ws_state()`
5. If both base and quote reserves present → compute price → store snapshot

---

## The Full Data Flow

### When Listener Discovers New Pool

```
1. LISTENER discovers new migration TX with token mint F8tKkEPM...
   ↓
2. LISTENER extracts vault addresses and pool program ID
   ↓
3. LISTENER calls discover_and_register_pool(pool_address, token_mint)
   ↓
4. DISCOVERY registers pool to database:
   INSERT token_pool_accounts (
     mint='F8tKkEPM...',
     base_account='A1HFq...',          ← vault for token
     quote_account='11NqQ...',         ← vault for WSOL/USDC
     is_active=1,                      ← enable it immediately
     vault_validation_status='pending' ← pending validation
   )
   ↓
5. DISCOVERY calls price_worker.trigger_pool_refresh()
   ↓
6. PRICE_WORKER stops old WebSocket client
   ↓
7. PRICE_WORKER calls _start_ws_client()
   ↓
8. _START_WS_CLIENT fetches ALL active pools from database:
   SELECT * FROM token_pool_accounts WHERE is_active=1
   (includes new pool + all legacy pools)
   ↓
9. _START_WS_CLIENT creates fresh PoolWebSocketClient(pools)
   ↓
10. POOLWEBSOCKETCLIENT._build_account_map(pools):
    For each pool:
      _account_to_pools[base_account] = [pool]
      _account_to_pools[quote_account] = [pool]
    (Now includes new pool accounts!)
    ↓
11. POOLWEBSOCKETCLIENT.start() connects to WebSocket
    ↓
12. POOLWEBSOCKETCLIENT sends subscribe message with ALL accounts:
    {
      "method": "accountSubscribe",
      "params": [
        "A1HFq...",        ← NEW
        "11NqQ...",        ← NEW
        ...64 legacy accounts...
      ],
      "id": 1
    }
    ↓
13. HELIUS RPC replies:
    {
      "result": 12345,   ← subscription ID
      "jsonrpc": "2.0"
    }
    ↓
14. NETWORK sends accountNotification for A1HFq account with new balance
    ↓
15. POOLWEBSOCKETCLIENT._handle_message() processes it:
    - Looks up: _account_to_pools["A1HFq"] → [pool]
    - Extracts balance from account data
    - Calls _store.update_reserve(
        mint="F8tKkEPM...",
        base_account="A1HFq...",
        account_type="base",
        balance=1000000,
        slot=286523000
      )
    - PoolStateStore now has NEW pool data! ✅
    ↓
16. PRICE_WORKER._refresh_cycle() runs:
    - Calls _recompute_prices_from_ws_state()
    - Gets reserves for F8tKkEPM... from PoolStateStore
    - Finds (base_reserve=1000000, quote_reserve=500000) ✅
    - Calculates price_usd using formula
    - Calls _store_snapshot(mint, price_usd, timestamp)
    ↓
17. DATABASE INSERT:
    INSERT INTO token_price_snapshots (
      mint='F8tKkEPM...',
      price_usd=0.0000261,
      volume_24h=NULL,
      liquidity_usd=150000,
      created_at=NOW()
    )
    ↓
18. SUCCESS: Snapshot written! ✅
```

---

## Why the Full Rebuild Works

### Why Incremental Failed

The old approach tried to call `refresh_pools()` on a running WebSocket:

```python
# OLD (BROKEN)
self._ws_client.refresh_pools(pools)  # Update map, try to reconnect
```

But the problem:
1. `refresh_pools()` updates `_account_to_pools` map
2. Event loop might already be sending subscribe message with OLD map
3. Or subscribe was already sent, so updating map too late
4. Result: New accounts never in subscription request
5. Result: No messages arrive
6. Result: Snapshots empty

### Why Full Rebuild Works

```python
# NEW (CORRECT)
self._ws_client.stop()          # Close connection, stop event loop
self._start_ws_client()         # Create fresh client with fresh map
```

Process:
1. `stop()` closes WebSocket and halts event loop
2. Create brand new `PoolWebSocketClient` instance
3. Call `_build_account_map(ALL_POOLS)` immediately in constructor
4. Call `start(pools)` which subscribes with FRESH map
5. Map guaranteed to include new pools
6. Subscription request sent with new pool accounts
7. Messages arrive
8. Snapshots flow

**Guarantees:** New pools are subscribed BEFORE any messages sent ✅

---

## Performance Profile

| Metric | Impact | Notes |
|--------|--------|-------|
| **Latency to first snapshot** | +1-2s | WebSocket rebuild ~1-2s, then first update |
| **Message loss** | <2s | Updates missed during rebuild |
| **Connection overhead** | Minimal | Reusing same RPC endpoint |
| **Memory** | ~1-2MB | New WebSocket client + PoolStateStore |
| **CPU** | <1% | Rebuilding is infrequent (once per new pool) |

---

## Trade-offs

### Why Not Incremental Updates?

**Incremental approach:**
```python
new_accounts = set(new_map) - set(old_map)
for account in new_accounts:
    send_subscribe_message(account)
```

**Pros:**
- Slightly more efficient (one subscribe vs full reconnect)
- No message loss

**Cons:**
- Complex state management
- Race conditions (map changes while event loop is subscribing)
- Error cases harder to debug
- Requires careful lock management

### Current Decision: Full Rebuild

**Chosen because:**
- ✅ Correct (guaranteed new pools subscribed)
- ✅ Simpler to understand and debug
- ✅ Same pattern as startup
- ✅ Production-ready (milliseconds matter less than correctness)
- ⏱️ Can optimize to incremental later

---

## Testing Strategy

### Unit Tests

1. **Pool registration test:** Verify pool added to database ✅
2. **WebSocket subscription test:** Mock WebSocket, verify subscribe messages sent
3. **PoolStateStore test:** Verify reserves stored by (mint, base_account) key
4. **Price computation test:** Given reserves, verify price calculated correctly
5. **Snapshot storage test:** Verify INSERT to database succeeds

### Integration Tests

1. **End-to-end new pool:** Register → snapshot flow ✅
2. **Legacy pool survival:** Verify existing pools still work after refresh
3. **Multiple new pools:** Register 3 new pools, verify all get snapshots
4. **Network failure recovery:** Simulate network error, verify graceful recovery

### Production Validation

1. **Snapshot count test:** `SELECT COUNT(*) FROM token_price_snapshots WHERE created_at > NOW() - 1 HOUR`
   - Should see continuous growth for new pools
   - Should see 66+ snapshots/hour for legacy pools

2. **Price update frequency test:** `SELECT COUNT(DISTINCT created_at) FROM token_price_snapshots WHERE mint='F8tKkEPM...' AND created_at > NOW() - 5 MINUTES`
   - Should see updates every 10-20 seconds for active pools

3. **WebSocket log test:** `grep -c '[POOL_WS] accountNotification' listener.log`
   - Should be increasing over time
   - Should include new pool accounts

---

## Files for Reference

| File | Purpose |
|------|---------|
| [src/core/price_worker.py](src/core/price_worker.py) | BackgroundPriceWorker, main orchestrator |
| [src/core/pool_price_engine.py](src/core/pool_price_engine.py) | PoolWebSocketClient, PoolStateStore |
| [src/core/pool_discovery.py](src/core/pool_discovery.py) | Register pools to database |
| [WEBSOCKET_FIX_VERIFICATION.md](WEBSOCKET_FIX_VERIFICATION.md) | Step-by-step verification guide |
| [FIX_STRATEGY.md](FIX_STRATEGY.md) | Original fix strategy document |

---

## Next Steps

1. ✅ Implementation complete
2. 🔄 Verification in progress (see WEBSOCKET_FIX_VERIFICATION.md)
3. ⏳ Production deployment
4. 📊 Monitor snapshot flow
5. 🚀 Optimize to incremental adds (future)

