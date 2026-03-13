# Future 6 Improvements — Implementation Guide

**Status**: Ready to implement | **Total time**: 8-12 hours | **Commits**: 5
**Base**: Current production with 4 deployed commits

---

## Quick Reference Table

| # | Improvement | File(s) | Commits | Complexity | Impact | Lines |
|---|-------------|---------|---------|-----------|--------|-------|
| 1 | Circuit breaker persistence | price_service.py | 1 | Medium | High | ~150 |
| 2 | Exponential cooldown | price_service.py | 1 | Low | High | ~30 |
| 3 | Provider timeout budgets | price_service.py | 1 | Medium | Medium | ~80 |
| 4 | Snapshot cache pre-warming | price_worker.py, price_api.py | 1 | Low | Medium | ~60 |
| 5 | Token priority tiers | price_worker.py | 1 | Low | Low | ~40 |
| 6 | Rolling source window | price_service.py | 1 | Low | Low | ~50 |

---

## Implementation Sequence

### Phase 1: Database & Persistence (Commit 1 — 2 hours)

**Goal**: Add circuit breaker persistence + exponential cooldown

**Files**: `src/core/price_service.py`

**Steps**:

1. Add `circuit_breaker_state` table to `_ensure_tables()`
2. Implement `_load_circuit_breaker_state()` in `__init__`
3. Implement `_save_circuit_breaker_state(source)`
4. Implement `_get_exponential_cooldown(break_count)` method
5. Update `_is_circuit_broken()` to use exponential cooldown
6. Update `_update_source_stats()` to increment break_count and persist
7. Test with database persistence

**Verification**:
```bash
# Service creates circuit_breaker_state table
# State loads on startup
# Circuit breaker persists across restarts
# Exponential cooldown: 600s → 1800s → 7200s → 14400s
```

---

### Phase 2: Provider Timeouts (Commit 2 — 1.5 hours)

**Goal**: Enforce per-provider timeout limits

**Files**: `src/core/price_service.py`

**Steps**:

1. Add `PROVIDER_TIMEOUTS` dict (dexscreener: 1.2s, jupiter: 0.8s, birdeye: 1.0s)
2. Implement `_get_timeout_for_provider(source, remaining_budget)`
3. Update `get_token_price()` to pass `timeout` to each provider client
4. Handle `asyncio.TimeoutError` exceptions
5. Test with slow providers (artificially add delay)

**Code Pattern**:
```python
remaining_budget = TOTAL_BUDGET_SECS - elapsed
timeout = self._get_timeout_for_provider('dexscreener', remaining_budget)

try:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        dex_price = await DexscreenerClient.get_price_with_session(mint, session)
except asyncio.TimeoutError:
    logger.debug(f"Provider timeout ({timeout:.1f}s)")
    self.stats['dexscreener_fail'] += 1
```

**Verification**:
```bash
# Timeout = min(provider_limit, remaining_budget)
# Each provider respects own timeout
# No provider can consume full 3s budget
```

---

### Phase 3: Snapshot Cache Pre-Warming (Commit 3 — 1.5 hours)

**Goal**: Worker pre-warms snapshot cache; dashboard reads cache-only

**Files**: `src/core/price_worker.py`, `src/apis/price_api.py`

**Steps**:

1. Implement `_warm_snapshot_cache(tokens)` in BackgroundPriceWorker
2. Call it in `_refresh_cycle()` after price fetches
3. Update `get_price()` endpoint to read snapshot-only
4. Return `cache_source` field indicating 'snapshot', 'stale_db', or 'unavailable'
5. Test dashboard requests don't trigger upstream

**Code Pattern**:
```python
def _warm_snapshot_cache(self, tokens: List[Dict]) -> None:
    """Write fresh prices to snapshot tier for dashboard."""
    cache_warmed = 0
    for token in tokens:
        price = self.price_service.cache.get(token['mint'], 'org')
        if price:
            self.price_service.cache.set(token['mint'], price)  # snapshot tier
            self.price_service._store_snapshot(price)
            cache_warmed += 1
    logger.debug(f"Snapshot cache warmed: {cache_warmed} tokens")

# In get_price() API:
price = service.cache.get(mint, 'snapshot')  # Read snapshot tier only
if not price:
    price = service._get_cached_price(mint)  # Stale DB fallback
```

**Verification**:
```bash
# Dashboard requests hit snapshot cache (0 upstream API calls)
# Logs show "Snapshot cache warmed: N tokens"
# Health endpoint shows high cache hit rate
```

---

### Phase 4: Token Priority Tiers (Commit 4 — 1 hour)

**Goal**: Simplify refresh scheduling using priority_level

**Files**: `src/core/price_worker.py`

**Steps**:

1. Update `_get_tokens_for_refresh()` to use `priority_level` directly
2. Remove activity score computation from refresh loop
3. Update `/api/price/batch/register` to accept `priority_levels` dict
4. Implement `_register_token(mint, priority_level)` method
5. Test registration with explicit priorities

**Code Pattern**:
```python
def _get_tokens_for_refresh(self) -> List[Dict]:
    """Use priority_level directly for refresh scheduling."""
    rows = conn.execute('''
        SELECT mint, priority_level, last_price_update
        FROM tracked_tokens WHERE is_active = 1
    ''').fetchall()

    for mint, priority, last_update in rows:
        interval = self._get_refresh_interval_for_activity(priority.lower())
        if now - last_update >= interval:
            tokens.append({'mint': mint, 'priority_level': priority})

# Registration with priority:
POST /api/price/batch/register
{
  "mints": ["mint1", "mint2"],
  "priority_levels": {"mint1": "HIGH", "mint2": "MEDIUM"}
}
```

**Verification**:
```bash
# Tokens refreshed at correct intervals (HIGH=10s, MEDIUM=30s, LOW=90s, DORMANT=180s)
# No activity scoring computation overhead
# Registration endpoint accepts priority_levels
```

---

### Phase 5: Rolling Source Health Window (Commit 5 — 1.5 hours)

**Goal**: Use 1-hour rolling window for source ranking

**Files**: `src/core/price_service.py`

**Steps**:

1. Update `_update_source_stats()` to keep only 1h attempts (cutoff = now - 3600)
2. Update `_get_source_rank()` to work with rolling window
3. Implement `get_rolling_window_stats()` for monitoring
4. Change circuit break threshold to 20 attempts (instead of 50)
5. Test source ranking adapts to recent changes

**Code Pattern**:
```python
def _update_source_stats(self, source: str, success: bool) -> None:
    """Keep only last 1 hour of attempts."""
    now = time.time()
    self.source_attempts[source].append((now, success))

    # Rolling window: last 3600 seconds only
    cutoff = now - 3600
    self.source_attempts[source] = [
        (ts, s) for ts, s in self.source_attempts[source]
        if ts > cutoff
    ]

    # Break if >90% failure over >=20 attempts
    attempts = self.source_attempts[source]
    if len(attempts) >= 20:
        failures = sum(1 for _, s in attempts if not s)
        failure_rate = failures / len(attempts)
        if failure_rate > 0.9:
            # Trigger circuit break
```

**Verification**:
```bash
# Source ranking changes within minutes (not hours)
# Old failures pruned after 1 hour
# health endpoint shows rolling window stats
```

---

## Database Migration

### Step 1: Auto-Creation

The `_ensure_tables()` method creates the table automatically on first startup:

```python
def _ensure_tables(self) -> None:
    """Create circuit_breaker_state table if not exists."""
    conn = self._get_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS circuit_breaker_state (
            source TEXT PRIMARY KEY,
            disabled INTEGER DEFAULT 0,
            disabled_at INTEGER DEFAULT 0,
            break_count INTEGER DEFAULT 0,
            last_break_at INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
```

### Step 2: Initialize Data

On first load, all sources are initialized as enabled:

```python
def _load_circuit_breaker_state(self) -> None:
    """Load persisted state, or initialize with defaults."""
    try:
        rows = conn.execute(
            'SELECT source, disabled FROM circuit_breaker_state'
        ).fetchall()

        if not rows:
            # First startup: initialize all sources as enabled
            for source in ['dexscreener', 'jupiter', 'birdeye']:
                self._save_circuit_breaker_state(source)
    except Exception as e:
        logger.error(f"Failed to load circuit breaker: {e}")
```

### Step 3: Verification

```bash
# Check table created
sqlite3 database/flex_complete_database.db \
  ".tables" | grep circuit_breaker

# Check initial data
sqlite3 database/flex_complete_database.db \
  "SELECT * FROM circuit_breaker_state;"

# Should show:
# dexscreener|0|0|0|0|...
# jupiter|0|0|0|0|...
# birdeye|0|0|0|0|...
```

---

## Testing Strategy

### Unit Tests

**Circuit Breaker Persistence**:
```python
def test_circuit_breaker_loads_on_startup():
    # Insert disabled state into database
    # Create new TokenPriceService instance
    # Verify circuit_breaker loads as disabled
    assert service.circuit_breaker['birdeye']['disabled'] == True

def test_exponential_cooldown():
    # Verify: break_count=0 → 600s
    # Verify: break_count=1 → 1800s
    # Verify: break_count=2 → 7200s
    # Verify: break_count=3+ → 14400s (capped)
    pass

def test_provider_timeout_respects_budget():
    # Remaining budget = 1.5s
    # Dexscreener timeout = 1.2s
    # Jupiter timeout = 0.8s
    # Actual timeouts: dex=1.2s, jup=0.3s (capped by remaining)
    pass
```

### Integration Tests

**Full Cycle**:
```python
def test_circuit_breaker_persistence_full_cycle():
    # 1. Start service
    # 2. Simulate provider failure (>90% failure)
    # 3. Verify circuit breaks (disabled=True)
    # 4. Stop service
    # 5. Start service again
    # 6. Verify circuit still broken (loaded from DB)
    # 7. Wait for cooldown
    # 8. Verify circuit resets
    pass

def test_snapshot_cache_warms_during_refresh():
    # 1. Register token
    # 2. Worker refresh cycle completes
    # 3. Verify snapshot cache has price
    # 4. Call get_price() endpoint
    # 5. Verify returns from snapshot cache
    # 6. Verify no upstream API calls logged
    pass
```

### Load Tests

```python
# Monitor during high load
watch -n 5 'curl http://localhost:5002/api/price/health | \
  jq ".worker_stats.worker.rolling_window_stats"'

# Verify:
# - Provider timeouts prevent any single source consuming >1.5s
# - Circuit breaker prevents repeated failures
# - Snapshot cache serves all dashboard requests
```

---

## Rollback Procedure

### Per-Commit Rollback

```bash
# Rollback Commit 5 (rolling window)
git revert <commit-5-hash>
bash scripts/restart.sh

# Rollback Commit 4 (priority tiers)
git revert <commit-4-hash>
bash scripts/restart.sh

# ... and so on
```

### Full Rollback (all 5 commits)

```bash
git reset --hard HEAD~5
bash scripts/restart.sh
```

### Database Cleanup (if needed)

```bash
# Drop circuit_breaker_state table (safe, data cached in memory)
sqlite3 database/flex_complete_database.db \
  "DROP TABLE IF EXISTS circuit_breaker_state;"
```

---

## Performance Expectations

### CPU Usage
- **Before**: Activity scoring every cycle (10s) for N tokens
- **After**: Simple priority lookup (O(1) instead of O(n × 10))
- **Savings**: ~15-20% CPU reduction

### Memory Usage
- **Added**: Circuit breaker table row per source (~1KB)
- **Removed**: No activity scoring state
- **Net change**: -5% to -10%

### Latency
- **Dashboard requests**: No change (cache hits)
- **Price resolution**: -5-10% (faster fallback due to better provider ranking)

### API Calls/Hour
- **Before**: 400-500
- **After**: 200-300 (additional 50% reduction from snapshot pre-warming)
- **Savings**: 200-300 fewer calls/day

---

## Monitoring Commands

### Circuit Breaker Persistence

```bash
# Check persisted state
sqlite3 database/flex_complete_database.db \
  "SELECT source, disabled, break_count, disabled_at FROM circuit_breaker_state;"

# Monitor cooldown countdown
watch -n 5 'curl http://localhost:5002/api/price/health | \
  jq ".worker_stats.worker.circuit_breaker"'
```

### Provider Timeouts

```bash
# Check timeout enforcement
tail -f logs/dev_intelligence.log | grep -i "timeout"

# Monitor remaining budget
curl http://localhost:5002/api/price/health | \
  jq ".worker_stats.worker.queue_stats.queue_wait_estimate_ms"
```

### Snapshot Cache

```bash
# Count cache hits vs stale fallbacks
tail -f logs/dev_intelligence.log | grep -i "snapshot\|stale_db\|unavailable"

# Check warming progress
tail -f logs/dev_intelligence.log | grep "Snapshot cache warmed"
```

### Token Priorities

```bash
# Distribution by tier
sqlite3 database/flex_complete_database.db \
  "SELECT priority_level, COUNT(*) FROM tracked_tokens GROUP BY priority_level;"

# Average last update by tier
sqlite3 database/flex_complete_database.db \
  "SELECT priority_level, AVG(strftime('%s', 'now') - last_price_update) as avg_age \
   FROM tracked_tokens GROUP BY priority_level;"
```

### Rolling Window Health

```bash
# Real-time source metrics
curl http://localhost:5002/api/price/health | \
  jq ".worker_stats.worker.rolling_window_stats"

# Verify window size (should be ~1 hour)
curl http://localhost:5002/api/price/health | \
  jq ".worker_stats.worker.rolling_window_stats | \
    map(.attempts_in_window) | add"
```

---

## Configuration Tuning

All thresholds are configurable via environment variables:

```bash
# Provider timeouts (seconds)
export TIMEOUT_DEXSCREENER=1.2
export TIMEOUT_JUPITER=0.8
export TIMEOUT_BIRDEYE=1.0

# Circuit breaker
export CB_FAILURE_THRESHOLD=0.9          # 90% failure rate
export CB_ATTEMPT_WINDOW=3600            # 1 hour
export CB_ATTEMPT_THRESHOLD=20           # min attempts to break

# Priority tiers (seconds)
export REFRESH_HIGH=10
export REFRESH_MEDIUM=30
export REFRESH_LOW=90
export REFRESH_DORMANT=180

# Exponential cooldown
export CB_BASE_COOLDOWN=600              # 10 min
export CB_MAX_EXPONENT=3                 # max 4 hours

bash scripts/restart.sh
```

---

## Success Criteria

After implementing all 5 commits, the system should have:

✅ Circuit breaker state persists across restarts
✅ Exponential cooldown penalizes repeated failures
✅ Per-provider timeouts prevent budget starvation
✅ Snapshot cache pre-warmed, dashboard calls never upstream
✅ Token priority tiers simplify refresh scheduling
✅ Rolling 1h window adapts ranking to recent provider changes
✅ API calls reduced to 200-300/hour (50% vs baseline 400-500)
✅ P99 latency <500ms (vs 800ms baseline)
✅ Zero breaking changes to existing API
✅ Full monitoring visibility for all new features

---

## Next Steps

1. Review architecture doc with team
2. Implement Commit 1 (persistence + exponential)
3. Test circuit breaker recovery
4. Implement Commits 2-5 in parallel (independent)
5. Deploy incrementally to staging
6. Monitor for 48 hours
7. Deploy to production

---

**Estimated completion**: 1-2 weeks (including testing and validation)
**Risk level**: Low (each commit can be rolled back independently)
**Benefit**: Production-grade resilience + 50% fewer API calls
