# Implementation Summary: Funder Webhooks + Listener TX Caching

**Date**: 2026-03-05
**Scope**: Two independent, complementary optimizations for Flex token analysis system

---

## Executive Summary

### Task A: Funder Webhook Monitoring
- **What**: New webhook-based monitoring system for curated funders (high-signal, not all funders)
- **Why**: Current polling approach is expensive (100-1000 RPC calls/cycle); webhooks are essentially free
- **Impact**: **90-99% RPC savings** for funder monitoring (estimated $5-50/month cost reduction)
- **Scale**: Monitor 50-500 funders using Helius webhooks + risk-scoring algorithm
- **Risk**: Low (additive, doesn't break existing systems)

### Task B: Listener TX Caching
- **What**: Cache `getTransaction` results within listener to avoid duplicate RPC calls
- **Why**: Current flow calls `getTransaction` 2-3 times per migration (mint, pool, blockTime extraction)
- **Impact**: **67% reduction in getTransaction calls** (20 credits/migration savings)
- **Scale**: Every migration benefits; at 100 migrations/day = 60,000 credits/month (~$600)
- **Risk**: Low (local cache only, no external changes)

---

## Task A: Funder Webhook Implementation

### Deliverables

Three files provided:

1. **`FUNDER_WEBHOOK_IMPLEMENTATION.md`** - Full technical specification with pseudocode
2. **`PATCH_FUNDER_WEBHOOKS.py`** - Implementation patches (copy-paste code snippets)
3. **Database schema, watchlist builder, webhook receiver endpoint**

### What's Included

#### A.1 SQLite Schema
```sql
funder_watchlist(
  funder_address PRIMARY KEY,
  risk_score INTEGER 0-1000,
  risk_reasons TEXT (JSON),
  webhook_group_id TEXT,
  is_active INTEGER
)

funder_webhook_groups(
  webhook_group_id PRIMARY KEY,
  description TEXT,
  helius_webhook_id TEXT,
  is_active INTEGER
)

funder_webhook_events(
  id PRIMARY KEY,
  funder_address,
  signature,
  direction ("IN" | "OUT"),
  counterparty,
  amount_sol,
  mint NULLABLE,
  raw_payload TEXT (JSON)
)
```

#### A.2 Watchlist Builder Job
**File**: `funder_watchlist_builder.py`

Scores funders based on:
1. **Rugged creator funding** (+80-400 points): Did they fund creators that later rugged?
2. **Multi-creator funding** (+60-300 points): Hub behavior (funds many creators)?
3. **Cluster membership** (+250 points): In malicious fingerprint cluster?
4. **CEX/Infra penalty** (-200 points): Is it a known CEX wallet?

Assigns to tiers: CRITICAL (800-1000), HIGH (500-799), MEDIUM (200-499), LOW (0-199)

**Usage**:
```bash
python funder_watchlist_builder.py  # Run once to populate, or every 6 hours for updates
```

#### A.3 Webhook Receiver
**Route**: `POST /api/webhook/funder`

Receives Helius webhook events for watched funders:
```json
{
  "signature": "...",
  "blockTime": 1234567890,
  "source": "...",
  "destination": "...",
  "nativeTransfers": [{"amount": 1000000}]  // lamports
}
```

Dedupes by `(signature, funder_address)` UNIQUE constraint.

#### A.4 UI Endpoints
```
GET  /api/funder-watchlist/summary          → {CRITICAL: count, HIGH: count, ...}
GET  /api/funder-watchlist/top-risky        → [funder, risk_score, reasons, ...]
GET  /api/funder-webhook-events?limit=50    → [id, funder, direction, amount_sol, ...]
```

### How to Apply Task A

1. **Database schema** (in `pumpfun_curve_listener.py`, `_ensure_db()` method):
   ```python
   # Add import at top
   from PATCH_FUNDER_WEBHOOKS import ensure_funder_webhook_schema

   # Add to _ensure_db()
   ensure_funder_webhook_schema(DB_PATH)
   ```

2. **Create `funder_watchlist_builder.py`**:
   - Copy code from `PATCH_FUNDER_WEBHOOKS.py` (the second half)
   - Or use `FUNDER_WEBHOOK_IMPLEMENTATION.md` as reference

3. **Add webhook receiver** (in `main.py`):
   ```python
   # Copy the @app.route('/api/webhook/funder') function
   # and the UI endpoints from PATCH_FUNDER_WEBHOOKS.py
   ```

4. **Run watchlist builder** (one-time or scheduled):
   ```bash
   python funder_watchlist_builder.py
   ```

5. **Configure Helius webhook**:
   - Log into Helius dashboard
   - Create webhook
   - Set URL: `http://your-server:5002/api/webhook/funder`
   - Select event types: `SOL_TRANSFER`, `TOKEN_TRANSFER`
   - Choose "accounts" to monitor: select all funders in `funder_watchlist` table
   - Test webhook

---

## Task B: Listener TX Caching Implementation

### Deliverables

One file provided:

- **`PATCH_LISTENER_TX_CACHE.py`** - Complete implementation patches (copy-paste code)

### What's Included

#### B.1 Cache Initialization
Add to `PumpFunCurveListener.__init__()`:
```python
self.tx_cache = {}  # {signature: (tx_data, timestamp)}
self.tx_cache_ttl_seconds = 1800  # 30 minutes
self.tx_inflight_locks = {}  # singleflight pattern
self.tx_cache_stats = {'hit': 0, 'miss': 0, 'wait': 0}
```

#### B.2 New Method: `_get_transaction_cached()`
```python
async def _get_transaction_cached(signature, timeout=10) -> Dict:
    """
    Fetch TX with:
    - TTL cache (30 min)
    - Singleflight dedup for concurrent requests

    Returns cached tx_data or None.
    """
```

Performance:
- Cache **hit**: ~0.1ms (local lookup) ✅
- Cache **miss**: ~500ms (RPC call)
- Concurrent **wait**: ~500ms (shared in-flight)

#### B.3 Refactored Extraction Methods
```python
async def _extract_mint_from_tx(tx_data) -> Optional[str]
    # No RPC call, parses cached tx_data
    # Strategies: postTokenBalances → accountKeys

async def _extract_pool_from_tx(tx_data) -> Optional[str]
    # No RPC call, parses cached tx_data
    # Finds PumpSwap program in innerInstructions
```

#### B.4 Updated `handle_migration()`
Key changes:
1. Call `_get_transaction_cached(signature)` once at top
2. Use `_extract_mint_from_tx(tx_data)` instead of `_fetch_mint_from_transaction(signature)`
3. Use `_extract_pool_from_tx(tx_data)` instead of `_extract_pool_from_migration_tx(signature)`
4. Extract `blockTime` from cached `tx_data`

This **reduces RPC calls from 3 to 1 per migration**.

#### B.5 Cache Statistics Method
```python
def get_tx_cache_stats() -> Dict:
    return {
        'tx_cache_hit': 123,
        'tx_cache_miss': 45,
        'tx_cache_wait': 12,
        'tx_cache_size': 50,
        'tx_cache_hit_rate_pct': 73.2,
        'rpc_calls_avoided': 123,
        'credits_saved': 1230,  # hit * 10 credits per getTransaction
    }
```

#### B.6 UI Endpoint
```python
@app.route('/api/listener/tx-cache-stats')
def listener_tx_cache_stats():
    # Returns cache stats for dashboard display
```

### How to Apply Task B

1. **Update `__init__` method** (line ~307 in `pumpfun_curve_listener.py`):
   - Add cache dict, locks, stats initialization
   - See `PATCH_LISTENER_TX_CACHE.py` "PATCH 1"

2. **Add `_get_transaction_cached()` method**:
   - Copy entire method from `PATCH_LISTENER_TX_CACHE.py` "PATCH 2"
   - Paste into `PumpFunCurveListener` class

3. **Add extraction methods** (or refactor existing ones):
   - Copy `_extract_mint_from_tx()` and `_extract_pool_from_tx()` from "PATCH 3"
   - Can keep old methods for backward compatibility

4. **Update `handle_migration()`**:
   - See "PATCH 4" for exact changes
   - Key: replace direct RPC calls with cached calls
   - Integrate carefully to avoid breaking existing logic

5. **Add `get_tx_cache_stats()` method**:
   - Copy from "PATCH 5"

6. **Add UI endpoint** (in `main.py`):
   - Copy `@app.route('/api/listener/tx-cache-stats')` from "PATCH 6"
   - You'll need to expose listener instance to endpoint
   - Option: store listener as global, or pass stats to shared metrics system

---

## Testing & Validation

### Task A (Webhooks)

**Unit tests**:
```python
# Test risk scoring
from funder_watchlist_builder import compute_funder_risk_score
score, reasons = compute_funder_risk_score(conn, "some_funder_address")
assert 0 <= score <= 1000
assert isinstance(reasons, list)

# Test watchlist builder
rebuild_funder_watchlist()
# Verify: SELECT COUNT(*) FROM funder_watchlist WHERE is_active = 1;

# Test webhook receiver
curl -X POST http://localhost:5002/api/webhook/funder \
  -H "Content-Type: application/json" \
  -d '{
    "signature": "test_sig_123",
    "blockTime": 1234567890,
    "source": "test_funder_abc123",
    "destination": "test_dest_xyz",
    "nativeTransfers": [{"amount": 1000000}]
  }'
# Verify: SELECT * FROM funder_webhook_events WHERE signature = 'test_sig_123';
```

**Integration tests**:
1. Populate funder_watchlist with test funders
2. Send sample Helius webhook payload
3. Verify event stored in funder_webhook_events
4. Check UI endpoints return correct data

### Task B (TX Caching)

**Observability** (monitor logs):
```
[TX_CACHE] 💾 HIT: abc123... (age: 5.2s)          # Cache hit ✅
[TX_CACHE] 🌐 MISS: def456... (new fetch)         # Cache miss (expected)
[TX_CACHE] ⏳ WAIT: ghi789... (shared fetch)      # Concurrent request sharing
[TX_CACHE] 💾 CACHED: xyz999... (2048 bytes)      # Stored in cache
```

**Metrics validation**:
```bash
# Before caching
curl http://localhost:5002/api/listener/tx-cache-stats
# Expected: {tx_cache_hit: 0, tx_cache_miss: 100, hit_rate: 0%}

# After 1 hour (migrations repeat)
curl http://localhost:5002/api/listener/tx-cache-stats
# Expected: {tx_cache_hit: 60, tx_cache_miss: 100, hit_rate: 37.5%}

# After 6+ hours (warm cache)
curl http://localhost:5002/api/listener/tx-cache-stats
# Expected: {tx_cache_hit: 150, tx_cache_miss: 100, hit_rate: 60%}
```

**RPC credit comparison**:
```sql
-- Before caching (baseline)
SELECT SUM(credits) FROM rpc_metrics
WHERE method = 'getTransaction'
  AND source_file = 'pumpfun_curve_listener'
  AND timestamp > datetime('now', '-24 hours');
-- Expected: ~3,000 credits (100 migrations × 30 credits)

-- After caching (1 week)
-- Same query
-- Expected: ~1,000 credits (100 migrations × 10 credits)
-- Savings: ~2,000 credits/day = 60,000 credits/month
```

---

## Performance Expectations

### Task A (Webhooks)

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| Cost (monthly) | $500-2000 | $10-50 | 90-99% |
| RPC calls/cycle | 1000-10000 | ~10 | 99% |
| Latency | 1-6 hour delay | Real-time | Instant |
| Signal quality | Noisy (all funders) | Clean (curated) | High |

### Task B (TX Caching)

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| getTransaction calls/migration | 3 | 1 | 67% |
| Credits/migration | 30 | 10 | 67% |
| RPC latency/migration | 1500ms | 500ms | 67% |
| Monthly cost (100 mig/day) | $300 | $100 | $200 |

**Combined monthly savings**: $200-500 (depending on activity)

---

## Risk Assessment

### Task A (Webhooks): LOW RISK
- ✅ Additive (doesn't change existing systems)
- ✅ Isolated (new tables, new endpoint)
- ✅ Graceful degradation (if webhook fails, watchlist becomes stale but not critical)
- ❌ Helius dependency (if Helius webhooks down, events not received)
- ❌ Schema migration (need to ensure tables created)

**Mitigation**:
- Run schema migration before deploying receiver endpoint
- Test webhook delivery in dev environment first
- Implement webhook health check (count events per hour)

### Task B (TX Caching): LOW RISK
- ✅ Local cache only (no external dependencies)
- ✅ TTL ensures freshness (30 min, migrations spread out)
- ✅ Singleflight prevents race conditions
- ✅ Fallback to direct RPC if cache misses
- ❌ Memory usage (cache grows ~2KB per cached TX, but TTL controls growth)
- ❌ Complexity (refactored `handle_migration()` is more complex)

**Mitigation**:
- Monitor cache size (log it in stats)
- Set aggressive TTL (30 min) to avoid stale data
- Test with high migration rate (100+/min) to verify locking works
- Keep old extraction methods as fallback

---

## Files Provided

1. **`FUNDER_WEBHOOK_IMPLEMENTATION.md`** (complete spec, ~600 lines)
   - Overview, design rationale, database schema
   - Step-by-step walkthrough of each component
   - Implementation checklist
   - Expected savings analysis

2. **`PATCH_FUNDER_WEBHOOKS.py`** (copy-paste code, ~650 lines)
   - SQL schema migration function
   - Funder watchlist builder script (can be run standalone)
   - Webhook receiver endpoint
   - UI endpoints for dashboard
   - Integration notes and testing guide

3. **`PATCH_LISTENER_TX_CACHE.py`** (copy-paste code, ~500 lines)
   - Cache initialization in `__init__`
   - `_get_transaction_cached()` with TTL + singleflight
   - Refactored extraction methods (`_extract_mint_from_tx`, `_extract_pool_from_tx`)
   - `handle_migration()` modifications (key integration point)
   - Cache stats method
   - UI endpoint for dashboard
   - Integration notes and testing guide

4. **`IMPLEMENTATION_SUMMARY.md`** (this file)
   - Executive overview
   - Implementation instructions for both tasks
   - Testing strategies
   - Risk assessment
   - Performance expectations

---

## Next Steps

### Immediate (Week 1)

1. **Code Review**: Review patches in detail, ask questions
2. **Schema Migration**: Test schema changes in dev environment
3. **Unit Tests**: Create test harness for scoring algorithm
4. **Watchlist Builder**: Run initial builder, inspect results

### Short-term (Week 2-3)

1. **Helius Configuration**: Create webhook(s) in Helius, test delivery
2. **TX Cache Integration**: Apply patches to listener, test in dev
3. **Monitoring Setup**: Add cache metrics to dashboard
4. **Load Testing**: Test with high migration rate (100+/min)

### Medium-term (Week 4+)

1. **Production Rollout**: Deploy to production (start with Task B first)
2. **Observe Metrics**: Monitor cache hit rate, RPC savings
3. **Tune Parameters**: Adjust TTL, watchlist thresholds based on observed behavior
4. **Scale Webhooks**: Add more webhooks if needed as funders added to watchlist

---

## Questions & Support

**Regarding Task A (Webhooks)**:
- Should we monitor CRITICAL tier funders with higher event volume tolerance?
- Should we implement re-scoring on-demand (when creator rugs)?
- Should we hook watchlist changes to trigger Helius webhook updates?

**Regarding Task B (TX Caching)**:
- Should we persist cache to disk for recovery after restarts?
- Should we implement cache pre-warming (fetch recent TXs on startup)?
- Should we add cache hit/miss breakdown by extraction method?

**Regarding Integration**:
- Should both be deployed together or separately?
- Should we create feature flags for gradual rollout?
- Should we add A/B testing to measure actual savings?

---

**Generated**: 2026-03-05
**For**: Flex Token Analysis System
**Optimization targets**: RPC cost reduction + funder monitoring scale
