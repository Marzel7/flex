# Cache Event Logging Guide

The RPC Savings Dashboard has a "Cache Efficiency Panel" that shows credits saved by caching optimizations. For this panel to populate, you must record cache events whenever a cache layer prevents an RPC call.

## Overview

The system tracks two types of metric events:

1. **Actual RPC calls** - Recorded by existing code when a real HTTP call is made
   - `cache_action="none"` (no caching involved)
   - `credits_saved=0`

2. **Cache optimization events** - Recorded when a cache hit/refresh avoids an RPC
   - `cache_action="skip"` (cache hit prevented RPC)
   - `cache_action="refresh"` (partial refresh instead of full fetch)
   - `cache_action="full_scan"` (cache full scan avoided RPC)
   - `credits_saved=N` (credits that would have been spent)
   - `optimization_layer="tx_cache"` or `"wallet_cache"` etc

**Without cache event logging, the dashboard shows empty tables and 0% savings.**

## How to Log Cache Events

### Import the Helper

```python
from rpc_metrics_recorder import record_cache_event
```

### Call When Cache Prevents RPC

Whenever your code decides NOT to make an RPC call due to a cache, log it:

```python
# Example: Transaction cache hit
if tx_in_cache(tx_sig):
    record_cache_event(
        section="funder_incoming",
        provider="helius_rpc",
        method="getTransaction",
        source_file="funder_incoming_extractor.py",
        cache_action="skip",
        credits_saved=10,  # getTransaction costs 10 credits
        optimization_layer="tx_cache",
    )
    return cached_tx
else:
    # Real RPC call happens here (already recorded by existing code)
    tx = await helius_api.get_transaction(tx_sig)
    return tx
```

## Common Patterns

### Pattern 1: Transaction Cache Hit

**File**: `funder_incoming_extractor.py` or wherever getTransaction is called

```python
from rpc_metrics_recorder import record_cache_event

def get_transaction_with_cache(tx_sig):
    # Check cache first
    cached = tx_cache.get(tx_sig)
    if cached:
        # Log the cache hit
        record_cache_event(
            section="funder_incoming",
            provider="helius_rpc",
            method="getTransaction",
            source_file="funder_incoming_extractor.py",
            cache_action="skip",
            credits_saved=10,
            optimization_layer="tx_cache",
        )
        return cached

    # No cache, make real RPC (already instrumented)
    tx = helius_api.get_transaction(tx_sig)
    tx_cache.set(tx_sig, tx)
    return tx
```

### Pattern 2: Wallet Balance Refresh (Partial vs Full)

**File**: `creator_outgoing_extractor.py` or `realtime_creator_funding_extractor.py`

```python
async def get_wallet_balance_optimized(wallet_addr):
    cache = wallet_balance_cache.get(wallet_addr)

    if cache and cache["age"] < 60:  # Less than 60 seconds old
        # Use cached value, avoid full scan
        record_cache_event(
            section="creator_funding",
            provider="helius_rpc",
            method="getBalance",
            source_file="realtime_creator_funding_extractor.py",
            cache_action="skip",
            credits_saved=1,
            optimization_layer="wallet_cache",
        )
        return cache["balance"]

    elif cache and cache["age"] < 300:  # 5 min old - refresh only
        # Refresh stale cache instead of full scan
        record_cache_event(
            section="creator_funding",
            provider="helius_rpc",
            method="getBalance",
            source_file="realtime_creator_funding_extractor.py",
            cache_action="refresh",
            credits_saved=0,  # getBalance costs 1, refresh costs 1, so 0 saved
            optimization_layer="wallet_cache",
        )
        # Make refresh call (already recorded as RPC)
        new_balance = await helius_api.get_balance(wallet_addr)
        cache["balance"] = new_balance
        cache["age"] = 0
        return new_balance

    # Cache too old, full fetch required
    # (This is recorded as a normal RPC call by existing instrumentation)
    return await helius_api.get_balance(wallet_addr)
```

### Pattern 3: Batch Operation Optimization

**File**: Any batch processing code that decides to batch vs individual calls

```python
async def get_token_accounts_optimized(wallet_addr, token_mint):
    # Decision point: batch call (cheaper) vs individual calls (expensive)

    # Option A: Use batch endpoint (1 credit) instead of getProgramAccounts (10 credits)
    record_cache_event(
        section="creator_funding",
        provider="helius_rpc",
        method="getProgramAccounts",
        source_file="creator_outgoing_extractor.py",
        cache_action="skip",
        credits_saved=9,  # Saved 9 credits by using batch
        optimization_layer="batch_optimization",
    )

    # Make the cheaper batch call
    return await helius_api.get_token_accounts(wallet_addr, token_mint)
```

## Field Reference

### cache_action Values
- `"skip"` - Cache hit prevented RPC entirely
- `"refresh"` - Partial refresh of cached data instead of full fetch
- `"full_scan"` - Full scan avoided (e.g., using smaller query)
- `"none"` - No caching involved (default for actual RPC calls)

### optimization_layer Values
- `"tx_cache"` - Transaction cache hit
- `"wallet_cache"` - Wallet/balance cache hit
- `"batch_optimization"` - Batch call used instead of individual
- `"full_scan_avoided"` - Query optimized to avoid full scan
- `"none"` - No optimization layer (default)

### credits_saved Calculation

The credits saved should be: `cost_if_rpc_made - cost_of_optimization`

Examples:
- Skip getTransaction entirely: `credits_saved=10`
- Use batch instead of getProgramAccounts: `credits_saved=9` (batch=1, full=10)
- Refresh instead of full scan: `credits_saved=0` (if both cost the same)

## Dashboard Impact

When properly instrumented, the dashboard will show:

**Cache Efficiency Panel:**
- Event counts by cache_action (Skip, Refresh, Full Scan)
- Total credits saved

**Top Optimization Layers:**
- Which optimization layers saved the most credits
- Hit counts per layer

**RPC Savings Card:**
- Updates "Savings %" based on credits_saved metrics
- Shows actual optimization ROI

## Testing

Check if events are being recorded:

```sql
SELECT cache_action, optimization_layer, COUNT(*), SUM(credits_saved)
FROM rpc_metrics
WHERE timestamp > strftime('%s','now') - 86400
  AND cache_action != 'none'
GROUP BY cache_action, optimization_layer
ORDER BY SUM(credits_saved) DESC;
```

If query returns rows, dashboard panels will populate. If empty, no cache events are being logged.

## Integration Checklist

- [ ] Identify all RPC-calling code (funder extractors, creator extractors, etc)
- [ ] Find cache/optimization decision points
- [ ] Add `record_cache_event()` calls before returning cached values
- [ ] Test with `SELECT` query above
- [ ] Verify dashboard panels populate
- [ ] Monitor savings over time

## Notes

- Only call `record_cache_event()` when a cache prevents an actual RPC call
- Do not call it for cache writes/updates
- Do not call it for failed cache operations (those are real RPCs)
- The credits_saved should be the actual cost difference, not guessed
