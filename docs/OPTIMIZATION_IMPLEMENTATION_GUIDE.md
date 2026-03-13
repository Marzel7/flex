# Token Price System Optimization — Implementation Guide

## Quick Reference

This guide helps you apply the optimization patch to reduce 429 rate-limit errors and improve UI responsiveness.

**Full patch details:** See `TOKEN_PRICE_OPTIMIZATION_PATCH.md`

---

## What's Being Fixed

| Problem | Solution |
|---------|----------|
| 429 rate-limit errors | Reduce batch size (20→10), faster HIGH→MEDIUM downgrade, per-source backoff |
| Slow symbol display | Make `/symbol/<mint>` cache-first instead of fetch-first |
| UI flicker/rebuild | Patch rows in-place instead of full table rebuild every 30s |
| Repeated token registration | Frontend tracks registered mints, only registers new ones |
| Blank cells on upstream failure | Prefer stale cache + stale badge over empty/error state |
| Unclear data freshness | Add `is_stale`, `cached_at`, `fetched_at`, `source` to responses |

---

## Files to Change

### 1. `src/apis/price_api.py`

**What to change:**

- **Lines 24–81**: Replace `get_token_symbol()` with cache-first implementation
  - Add `_metadata_fetch_in_progress` set to prevent thundering herd
  - Check cache first; return immediately if fresh
  - Only fetch upstream on cache miss/expiry
  - Return stale cache on upstream error
  - Include response metadata: `cached_at`, `fetched_at`, `source`, `is_stale`

- **Lines 649–685**: Update `register_tokens_batch()` to be idempotent
  - Check if mint already tracked and active
  - Return `deduplicated` and `skipped` counts
  - Log for observability

**Impact:** ~60 lines changed, mostly refactored

**Test**: Call `/api/price/symbol/{mint}` twice rapidly; should return from cache on second call

---

### 2. `src/core/price_worker.py`

**What to change:**

- **Lines 26–70**: Add `first_fetch_at` and `last_fetch_success_at` columns to `tracked_tokens` table schema

- **Lines 71–90**: Modify `register_token()` to be idempotent
  - Check if already registered + active
  - Skip INSERT/UPDATE if so
  - Return `True` either way

- **Lines 187–215**: Modify `BackgroundPriceWorker.__init__()`
  - Change `batch_size` from 20 to 10
  - Add `self.source_backoff` dict for per-source tracking
  - Add stats: `high_priority_downgrades`, `backoff_events`

- **Add new methods** (before `_run_loop`):
  - `_should_downgrade_to_medium()`: Check if HIGH token should move to MEDIUM
  - `_downgrade_high_to_medium()`: Update DB and stats

- **Lines 247–276**: Modify `_refresh_cycle()` to handle downgrades and backoff decay
  - Call `_should_downgrade_to_medium()` for each HIGH token
  - Decay source backoff windows
  - Update logging to include downgrade count

- **Lines 310–337**: Modify `_get_tokens_for_refresh()` to cap HIGH at 5 tokens
  - Change `high_priority = self.registry.get_tracked_tokens('HIGH')`
  - Add slice: `[:5]` to limit to 5 per cycle

- **Lines 339–410**: Modify `_batch_fetch_prices()` to track first fetch and handle 429s
  - Update DB: set `last_fetch_success_at` on successful fetch
  - Detect 429 errors and trigger source backoff
  - Exponentially increase wait time: 1s → 2s → 4s → 8s

**Impact:** ~200 lines added/modified

**Test**:
- Start worker, watch logs for "Prefetch cycle X: ... downgrades=N"
- Trigger 429 error, watch logs for "Rate limit triggered; activating backoff"

---

### 3. `src/core/price_service.py`

**What to change:**

- **After imports, before classes**: Add global source backoff state
  - `_source_backoff` dict
  - `_get_source_backoff()` function
  - `_is_source_backed_off()` function

- **Lines ~300–400** (estimate, find `get_token_prices_sync` method):
  - Modify to respect backoff state
  - Skip sources in backoff window
  - Prefer cached prices during backoff
  - Mark stale responses: `is_stale=True`, `source='cached'`
  - Return unavailable if no data at all

**Impact:** ~50 lines added/modified

**Test**:
- Call `/api/price/batch` after triggering 429
- Should prefer cached prices and skip backed-off sources

---

### 4. `src/core/main.py`

**What to change:**

- **Add near top of price-related JS** (around line 3900):
  - `registeredMints = new Set()`
  - `rowByMint = new Map()`
  - Update interval IDs for separate list/price refresh

- **Replace `loadTokens()` function** (~30 lines):
  - Only register NEW mints (not already in `registeredMints`)
  - Build/update `rowByMint` map
  - Create rows once, patch them on subsequent calls
  - No full table rebuild

- **Add new `patchTokenPrice()` function** (~20 lines):
  - Update only changed cells: price, market cap, peak
  - Apply fade effect
  - Add/remove `stale` CSS class if `is_stale=true`

- **Replace price refresh logic** (~20 lines):
  - Separate intervals:
    - `tokenListRefreshInterval = 60s` (load/update token list)
    - `priceRefreshInterval = 15s` (refresh prices only)
  - New `refreshVisiblePrices()` function
  - Call `/api/price/batch` with all visible mints

- **Add CSS for stale badge** (~10 lines):
  - Style for `.stale` class
  - Optional: fade color, `[stale]` badge text

**Impact:** ~100 lines changed

**Test**:
- Load dashboard
- Watch console for registration: should say `Registered 25, deduplicated 0` first time, then `Registered 0, deduplicated 25` on refresh
- Prices should update every 15s without table rebuild
- Token list should update every 60s

---

## Implementation Order

### Phase 1 (Quick Wins) — ~2 hours

1. **Update `src/apis/price_api.py`**
   - Cache-first symbol endpoint
   - Idempotent batch registration
   - **Test**: Hit `/api/price/symbol/{mint}` twice; check response metadata

2. **Update `src/core/price_worker.py` (part 1)**
   - Add DB schema columns
   - Idempotent `register_token()`
   - Reduce batch size to 10
   - **Test**: Worker starts, batch size is 10

3. **Update `src/core/main.py` (part 1)**
   - Add `registeredMints` Set
   - Modify `loadTokens()` to only register new mints
   - **Test**: Dashboard loads; registration shows `deduplicated > 0` on refresh

### Phase 2 (Stability) — ~4 hours

4. **Update `src/core/price_worker.py` (part 2)**
   - Add `_should_downgrade_to_medium()` and `_downgrade_high_to_medium()`
   - Modify `_refresh_cycle()` for downgrade handling
   - Modify `_get_tokens_for_refresh()` to cap HIGH at 5
   - Modify `_batch_fetch_prices()` for 429 handling
   - **Test**: High tokens downgrade after 60s; 429 triggers backoff

5. **Update `src/core/price_service.py`**
   - Add source backoff state
   - Modify `get_token_prices_sync()` to respect backoff
   - Prefer stale cache during failures
   - **Test**: Trigger 429; system prefers cache and shows stale badge

### Phase 3 (UX Cleanup) — ~2 hours

6. **Update `src/core/main.py` (part 2)**
   - Add `rowByMint` Map
   - Add `patchTokenPrice()` function
   - Add separate list/price refresh loops
   - Add CSS for stale indicator
   - **Test**: Dashboard prices update every 15s without flicker; list updates every 60s

---

## Checklist

### Before Implementation

- [ ] Read `TOKEN_PRICE_OPTIMIZATION_PATCH.md` completely
- [ ] Back up current database
- [ ] Create feature branch: `git checkout -b optimize/price-system`

### Phase 1

- [ ] Edit `src/apis/price_api.py` — symbol endpoint
- [ ] Edit `src/apis/price_api.py` — batch register
- [ ] Edit `src/core/price_worker.py` — schema, batch size, idempotent register
- [ ] Edit `src/core/main.py` — add `registeredMints`, modify `loadTokens()`
- [ ] Test: Dashboard loads, registration deduped on refresh
- [ ] Commit: "optimization: Phase 1 — cache-first symbols, idempotent registration"

### Phase 2

- [ ] Edit `src/core/price_worker.py` — downgrade logic, 429 handling
- [ ] Edit `src/core/price_service.py` — source backoff
- [ ] Trigger 429 manually (reduce batch further, spam requests) and verify backoff activates
- [ ] Commit: "optimization: Phase 2 — source backoff, faster HIGH downgrade"

### Phase 3

- [ ] Edit `src/core/main.py` — row patching, separate refresh loops, CSS
- [ ] Test: Prices update without table flicker; list updates separately
- [ ] Monitor logs for downgrade counts, backoff events
- [ ] Commit: "optimization: Phase 3 — in-place row patching, separate refresh loops"

### After Implementation

- [ ] Run full dashboard test (new launches, price updates, symbol display)
- [ ] Monitor logs for errors, backoff triggers, downgrade counts
- [ ] Run for 24 hours; check for 429 count (should drop significantly)
- [ ] Merge to main when stable

---

## Monitoring & Validation

### Key Metrics to Watch

**Worker Stats** (check logs or `/api/price/health`)

```json
{
  "worker_stats": {
    "cycles": 1234,
    "tokens_prefetched": 45678,
    "api_calls": 543,
    "cache_hits": 12345,
    "high_priority_downgrades": 200,  // NEW: should be non-zero
    "backoff_events": 5               // NEW: should be low (0-5 in 24h)
  }
}
```

**Expected behavior:**
- `high_priority_downgrades` > 0 (tokens moving from HIGH → MEDIUM)
- `backoff_events` low (< 5 per day = success)
- `cache_hits` high relative to `api_calls`

**Dashboard behavior:**

- [ ] Prices update every 15 seconds without table flicker
- [ ] Token list updates every 60 seconds
- [ ] Symbols load once per new token, not reloaded
- [ ] On refresh, registration returns `deduplicated > 0`
- [ ] Stale prices show `[stale]` badge when upstream fails

**API responses:**

- [ ] `/api/price/symbol/{mint}` includes `source`, `cached_at`, `fetched_at`
- [ ] First call is faster (cache miss); second call is instant (cache hit)
- [ ] `/api/price/batch/register` returns `deduplicated` count

---

## Rollback Plan

If something breaks:

```bash
# Revert last 3 commits (all phases)
git reset --hard HEAD~3

# Or revert specific phase
git revert <commit-hash>

# Restart worker
./scripts/restart.sh
```

---

## Common Issues & Fixes

### Issue: Symbols still loading from upstream every time

**Cause**: `cache_ttl` logic not working as expected

**Fix**: Verify cache-first logic in `get_token_symbol()`:
```python
# Must return immediately if fresh cache hit
if mint in _metadata_cache and (now - cached_time) < cache_ttl:
    return jsonify(result)  # No upstream call
```

### Issue: Dashboard still rebuilds table every 30s

**Cause**: `loadTokens()` being called too frequently

**Fix**: Verify two separate intervals:
```javascript
setInterval(loadTokens, 60000);         // 60s list refresh
setInterval(refreshVisiblePrices, 15000); // 15s price refresh
```

### Issue: 429s still happening

**Cause**: Batch size not reduced or backoff not activated

**Fix**: Check:
1. `batch_size = 10` in `price_worker.py`
2. 429 detection logic in `_batch_fetch_prices()`
3. Logs should show "Rate limit triggered" when 429 occurs

### Issue: Stale prices never show

**Cause**: `is_stale` flag not being set

**Fix**: Verify in `price_service.py`:
```python
# On network error or source backoff:
price.is_stale = True
price.source = 'cached'
```

---

## Performance Targets

After optimization, you should see:

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| 429s per hour | 10-20 | 0-2 | < 1 |
| Batch size | 20 | 10 | 10 |
| HIGH tokens per cycle | All | ≤5 | ≤5 |
| Symbol endpoint latency | 100-500ms | 1-5ms | <10ms |
| Table rebuild frequency | 30s | Never | Never |
| Price update latency | 30s | 15s | 15s |
| Cache hit ratio | 40% | 70%+ | 70%+ |

---

## Next Steps After Optimization

1. **Monitor for 1 week**: Watch for 429s, performance, any issues
2. **Tune parameters** if needed:
   - `batch_size`: If still seeing 429s, reduce to 5
   - `high_priority_max_age`: If too aggressive, increase to 90s
   - `high_priority_max_tokens`: If spikes are common, reduce to 3
3. **Consider Phase 4** (optional):
   - Persistent metadata cache (IndexedDB in browser)
   - Server-side request deduplication
   - Adaptive refresh based on upstream availability
4. **Document findings** for future reference

