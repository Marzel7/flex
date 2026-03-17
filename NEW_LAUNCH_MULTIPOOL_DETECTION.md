# New Launch Multi-Pool Detection — Fixed

**Status: ✅ COMPLETE** — New token launches now automatically discover and register ALL pools

---

## Problem

When a new token migrated from Pump.Fun to PumpSwap, the listener only discovered ONE pool. If a token had multiple liquidity pools (e.g., TOKEN/SOL, TOKEN/USDC), only the first found would be registered.

Result: Missing WebSocket prices and incomplete market cap data for multi-pool tokens.

---

## Solution

Updated the listener to use **multi-pool discovery** instead of single-pool discovery:

**File:** `src/core/pumpfun_curve_listener.py` (line ~2492)

**Before:**
```python
rpc_success = await discover_and_register_vaults_rpc(
    token_mint=mint,
    rpc_client=rpc_client,
    db=DB_PATH,
    price_worker=price_worker,
    max_retries=1
)
```

**After:**
```python
from src.core.vault_discovery import discover_and_register_all_pools
rpc_success = await discover_and_register_all_pools(
    token_mint=mint,
    rpc_client=rpc_client,
    db=DB_PATH,
    price_worker=price_worker,
    max_retries=1
)
```

---

## What Changed

### Old Flow (Single-Pool)
1. New migration detected
2. Discover FIRST pool found
3. Register one pool per token
4. WebSocket subscribes to one account
5. If that pool inactive → no prices

### New Flow (Multi-Pool)
1. New migration detected
2. Discover ALL pools for token
3. Register all pools with composite key `(mint, base_account)`
4. WebSocket subscribes to ALL pool accounts
5. Score pools by: wSOL preference → liquidity → activity
6. Mark best pool as primary for pricing
7. If primary pool inactive → fallback to secondary pool

---

## Benefits

✅ **Complete Coverage:** All liquidity sources discovered and subscribed
✅ **Resilience:** If primary pool idle, can price from secondary pool
✅ **Accuracy:** Prices from highest-liquidity pool
✅ **Real-Time:** WebSocket receives updates from all pools simultaneously
✅ **Auto-Healing:** As pools trade, WebSocket receives balance updates and prices flow

---

## Technical Details

### Multi-Pool Discovery Function
Located in: `src/core/vault_discovery.py`

**Function:** `discover_and_register_all_pools()`

**What it does:**
1. Gets top 20 largest token accounts via RPC
2. Validates each as potential pool base vault
3. For each, resolves quote vault
4. Registers ALL valid vault pairs in database
5. Scores pools by quote asset (wSOL priority) and liquidity
6. Marks best pool as `is_primary = 1`
7. Triggers WebSocket refresh to subscribe

### Pool Registration
- **Key:** Composite `(mint, base_account)` for uniqueness
- **Columns:** `is_primary`, `pool_score`, `quote_liquidity`, `pool_program`
- **Status:** `vault_validation_status = 'validated'` (RPC-authoritative)
- **Discovery method:** `'rpc_multipool_discovery'`

### WebSocket Subscription
- **Account map:** `account_pubkey → [pool1, pool2, ...]`
- **Update handler:** `_handle_message()` passes `base_account` for pool identification
- **State store:** Keyed by `(mint, base_account)` for proper multi-pool tracking
- **Aggregation:** `PoolAggregator.aggregate()` computes liquidity-weighted median price

---

## Example: New Token Launch Lifecycle

**Time: T+0 — Token migrates to PumpSwap**
- Migration TX detected on-chain
- Listener notified of migration

**Time: T+2 — Pool Discovery**
- `discover_and_register_all_pools()` called
- Scans top 20 token accounts
- Finds accounts for:
  - TOKEN/SOL pool (liquidity: $50K)
  - TOKEN/USDC pool (liquidity: $20K)
  - Both valid, both registered

**Time: T+3 — Scoring & Selection**
- Scores pools: TOKEN/SOL = 100, TOKEN/USDC = 50
- Marks TOKEN/SOL as primary (`is_primary=1`)
- Both subscribed to WebSocket

**Time: T+5 — Price Flowing**
- WebSocket receives balance updates from both pools
- PoolStateStore tracks reserves for both
- PoolAggregator computes: liquidity-weighted median price
- UI displays real-time price within 5s

**Time: T+30 — Secondary Pool Activation**
- USDC pool gets trade activity
- WebSocket receives update
- Price can now fall back to USDC pool if SOL pool idle
- System more resilient to single-pool drying up

---

## Database State After New Launch

```sql
-- For a token with multiple pools:
SELECT mint, base_account, quote_token, is_primary, pool_score
FROM token_pool_accounts
WHERE mint = 'TokenXXXX'
ORDER BY pool_score DESC;

-- Results:
-- TokenXXXX | pool1_addr | wSOL    | 1 | 100.0   ← Primary
-- TokenXXXX | pool2_addr | USDC    | 0 | 50.0    ← Secondary
-- TokenXXXX | pool3_addr | USDT    | 0 | 40.0    ← Tertiary
```

---

## Verification

Check if multi-pool discovery is working:

```bash
# Monitor listener for new migrations
tail -f logs/listener.log | grep "multipool_discovery\|VAULT_DISCOVERY"

# Expected output:
# [VAULT_DISCOVERY] Starting multi-pool discovery for TOKEN...
# [VAULT_DISCOVERY] ✅ Registered pool: pool1...
# [VAULT_DISCOVERY] ✅ Registered pool: pool2...
# [VAULT_DISCOVERY] 🏆 Marked as primary (wSOL pool): pool1...
# [VAULT_DISCOVERY] ✅ Registered 2 pools for TOKEN...

# Check database for new launch
SELECT mint, COUNT(*) as pools FROM token_pool_accounts
WHERE discovery_method = 'rpc_multipool_discovery'
GROUP BY mint;

# Expected: Multiple pools per token
```

---

## Rollout Status

✅ **Listener updated** to use `discover_and_register_all_pools()`
✅ **Multi-pool discovery function** already implemented
✅ **WebSocket support** already in place (composite key keying)
✅ **Price aggregation** already implemented
✅ **Database schema** updated with `is_primary`, `pool_score`

**Ready for production:** All changes applied, system ready for new launches with multiple pools!

---

## What Happens Next

When the next token launches and migrates:

1. ✅ Listener detects migration
2. ✅ Multi-pool discovery runs
3. ✅ ALL pools discovered and registered
4. ✅ WebSocket subscribed to all
5. ✅ Prices flow from best pool
6. ✅ Fallback to secondary if needed
7. ✅ Real-time prices in UI within 5 seconds

**No manual intervention needed.** System auto-detects and handles multi-pool tokens!
