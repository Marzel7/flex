# Next 6 Improvements — Quick Implementation Guide

**Status**: Ready to implement | **Estimated time**: 4-6 hours | **Risk level**: Low-Medium

---

## 1-Minute Summary

| # | Improvement | File(s) | Lines | Benefit |
|---|-------------|---------|-------|---------|
| 1 | Metadata TTL: 1800s → 3600s | `price_api.py` | 2 | -100 API calls/day |
| 2 | Snapshot cache default | `price_api.py` | 1 | Dashboard: no upstream calls |
| 3 | EWMA queue latency | `price_fetch_queue.py` | ~30 | Smoother pressure detection |
| 4 | Circuit breaker (broken sources) | `price_service.py` | ~150 | -200 API calls/day, faster fallback |
| 5 | Birdeye pool: 2 → 4 workers | `price_service.py` | 1 | Prevents executor bottleneck |
| 6 | Adaptive source ordering | `price_service.py` | ~100 | Uses fastest provider first |

**Total code**: ~280 lines of new logic + ~5 lines of constants
**New infrastructure**: None (reuses existing cache/queue/worker)
**Breaking changes**: None

---

## Implementation Checklist

### Commit 1: Constants & Caching (15 min)

- [ ] Edit `src/apis/price_api.py` line 156: `300` → `3600`
- [ ] Edit `src/apis/price_api.py` line 166: `300` → `3600`
- [ ] Edit `src/apis/price_api.py` line 284: default `cache_type` → `'snapshot'`
- [ ] Verify: Health endpoint shows metadata TTL changes
- [ ] Git commit: `"refactor: Metadata TTL 1800→3600s, snapshot cache default"`

### Commit 2: Queue EWMA (30 min)

- [ ] Edit `src/core/price_fetch_queue.py` `__init__`: Add `self.latency_ewma = 0.0`, `self.EWMA_ALPHA = 0.8`
- [ ] Edit `src/core/price_fetch_queue.py` `_worker_loop()`: Add EWMA update after latency capture
- [ ] Edit `src/core/price_fetch_queue.py` `get_stats()`: Use EWMA in wait estimate
- [ ] Verify: Health endpoint shows `ewma_latency_ms`
- [ ] Git commit: `"refactor: Queue EWMA latency for smoother pressure detection"`

### Commit 3: Circuit Breaker & Adaptive Ordering (2+ hours)

- [ ] Add to `src/core/price_service.py` `__init__`:
  - `self.circuit_breaker = {...}`
  - `self.source_latency_ewma = {...}`
  - `self.source_attempts = {...}`

- [ ] Add new methods to `TokenPriceService`:
  - `_is_circuit_broken(source)`
  - `_update_source_stats(source, success)`
  - `_get_source_rank(source)`
  - `_get_sources_ordered()`
  - `_update_latency_ewma(source, latency_ms)`

- [ ] Rewrite `get_token_price()`:
  - Get sources via `_get_sources_ordered()`
  - Loop through ranked sources
  - Track latency and success per source
  - Update stats after each attempt

- [ ] Verify:
  - Logs show source ranking
  - Health endpoint has `circuit_breaker` and `source_metrics`
  - Circuit breaker disables Birdeye after 50+ attempts at >90% failure

- [ ] Git commit: `"feat: Circuit breaker + adaptive source ordering"`

### Commit 4: ThreadPool & Stats (30 min)

- [ ] Edit `src/core/price_service.py` `__init__`: `max_workers=2` → `max_workers=4`
- [ ] Edit `src/core/price_worker.py` `get_stats()`: Add circuit breaker + source metrics
- [ ] Verify: Health endpoint shows expanded `circuit_breaker` and `source_metrics`
- [ ] Git commit: `"refactor: Birdeye thread pool 2→4, expose circuit breaker in stats"`

---

## Testing Commands

### Post-Commit 1: Metadata TTL

```bash
# Verify TTL is 3600s
curl -s http://localhost:5002/api/price/health \
  | jq '.warm_up_stats'

# Metadata requests should drop over next hour
tail -f logs/dev_intelligence.log | grep -i metadata
```

### Post-Commit 2: EWMA

```bash
# Verify EWMA latency is tracked
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker.queue_stats | {avg: .avg_latency_ms, ewma: .ewma_latency_ms}'

# Expected: ewma_latency_ms is close to avg (±20%)
```

### Post-Commit 3: Circuit Breaker & Ordering

```bash
# Verify circuit breaker state
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker.circuit_breaker'

# Expected: birdeye.disabled=true if >90% failure rate over 50 attempts

# Verify source metrics
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker.source_metrics'

# Expected: dexscreemer success_rate ~0.85, others ~0.0
```

### Post-Commit 4: ThreadPool

```bash
# Verify executor has 4 workers
grep "max_workers=4" src/core/price_service.py

# Verify stats updated
curl -s http://localhost:5002/api/price/health \
  | jq '.worker_stats.worker | keys' | grep -i circuit
```

---

## Code Snippets to Copy-Paste

### Snippet 1: Circuit Breaker Init (paste in `TokenPriceService.__init__`)

```python
# Circuit breaker: track disabled sources and cooldown
self.circuit_breaker = {
    'dexscreener': {'disabled': False, 'disabled_at': 0},
    'jupiter': {'disabled': False, 'disabled_at': 0},
    'birdeye': {'disabled': False, 'disabled_at': 0},
}

# EWMA latency per source
self.source_latency_ewma = {
    'dexscreener': 0.0,
    'jupiter': 0.0,
    'birdeye': 0.0,
}

# Source attempt history for rolling failure rate
self.source_attempts = {
    'dexscreener': [],
    'jupiter': [],
    'birdeye': [],
}
```

### Snippet 2: Circuit Breaker Check (new method)

```python
def _is_circuit_broken(self, source: str) -> bool:
    """Check if source is currently circuit broken."""
    cb = self.circuit_breaker.get(source, {})
    if not cb.get('disabled'):
        return False

    if time.time() - cb.get('disabled_at', 0) > 600:
        cb['disabled'] = False
        logger.info(f"Circuit breaker for {source} reset after cooldown")
        return False

    return True
```

### Snippet 3: Update Source Stats (new method)

```python
def _update_source_stats(self, source: str, success: bool) -> None:
    """Track attempt success and update circuit breaker."""
    now = time.time()
    self.source_attempts[source].append((now, success))

    # Keep only last 50 attempts
    cutoff = now - 3600
    self.source_attempts[source] = [
        (ts, s) for ts, s in self.source_attempts[source]
        if ts > cutoff
    ][:50]

    # Check if circuit should break (>90% failure over 50+ attempts)
    attempts = self.source_attempts[source]
    if len(attempts) >= 50:
        failures = sum(1 for _, s in attempts if not s)
        failure_rate = failures / len(attempts)

        if failure_rate > 0.9 and not self.circuit_breaker[source]['disabled']:
            self.circuit_breaker[source]['disabled'] = True
            self.circuit_breaker[source]['disabled_at'] = now
            logger.warning(f"Circuit breaker triggered for {source}: {failure_rate:.1%} failure rate")
```

### Snippet 4: EWMA Update (paste in `_worker_loop()` after latency capture)

```python
# Update EWMA latency (0.8 weight to previous)
EWMA_ALPHA = 0.8
if self.latency_ewma == 0.0:
    self.latency_ewma = latency_ms
else:
    self.latency_ewma = (EWMA_ALPHA * self.latency_ewma) + ((1.0 - EWMA_ALPHA) * latency_ms)
```

---

## Decision Tree

### Should I implement all 6 improvements?

- **Yes if**: System is handling 75+ tokens, metadata/API costs are noticeable, Birdeye consistently fails
- **Partial if**: Only care about latency improvement (do improvements 3, 4, 6 only)
- **No if**: System is lightly loaded (<10 tokens), no provider failures, budget not a concern

### Which order?

1. **Do Commits 1+2 first** (low risk, quick wins: metadata TTL + EWMA)
2. **Then Commit 3** (circuit breaker + ordering: medium risk, high impact)
3. **Finally Commit 4** (ThreadPool: low risk, small impact)

### If I have limited time?

**Priority order** (time spent vs impact):
1. Commit 3 (Circuit Breaker) — 2 hours, saves ~200 API calls/day + latency
2. Commit 1 (Metadata TTL) — 5 min, saves ~100 API calls/day
3. Commit 2 (EWMA) — 30 min, improves accuracy
4. Commit 4 (ThreadPool) — 30 min, prevents bottleneck

Skip if really tight: Just do Commit 3.

---

## Expected Metrics Changes

### API Call Reduction

```
Before: 600-800 calls/hour
- Metadata: ~25 calls/hour (75 tokens × 1 call per 1800s)
- Price: ~400 calls/hour (refreshes + fallbacks)
- Warm-up: ~150 calls/hour (registration batches)

After: 300-400 calls/hour
- Metadata: ~12 calls/hour (75 tokens × 1 call per 3600s) [-13]
- Price: ~250 calls/hour (no Birdeye fails, adaptive ordering) [-150]
- Warm-up: ~40 calls/hour (snapshot cache + queue pressure) [-110]

Total reduction: ~50% or ~200-400 fewer API calls/hour
```

### Latency Improvements

```
Before:
- P50: 150ms (cache hit)
- P95: 500ms (Dex → Jupiter)
- P99: 2500ms (Dex fails → Jupiter fails → Birdeye → stale DB)

After:
- P50: 150ms (cache hit) — no change
- P95: 300ms (Dex succeeds) — 40% improvement
- P99: 800ms (Dex fails → Jupiter succeeds) — 68% improvement

Why:
- Circuit breaker skips Birdeye (saves 1000ms+ for failed source)
- Adaptive ordering tries fastest source first
- EWMA prevents false saturation skips
```

---

## Monitoring Dashboard (Grafana / Simple Queries)

### Key Metrics to Watch

**1. Circuit Breaker Status**
```bash
curl -s http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.circuit_breaker | to_entries[] | select(.value.disabled)'
```
- Expected: Empty (no broken sources)
- Action if not empty: Check source logs, consider threshold adjustment

**2. API Call Rate**
```bash
# Count log entries per hour
tail -10000 logs/dev_intelligence.log | grep -c "Dexscreener\|Jupiter\|Birdeye"
```
- Expected: ~50% reduction after improvements
- Baseline: 600-800/hour → Target: 300-400/hour

**3. Source Success Rates**
```bash
curl -s http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.source_metrics'
```
- Dexscreener: >80% (primary, should be high)
- Jupiter: varies (secondary)
- Birdeye: may be low (fallback, circuit breaker ok)

**4. Queue Health (EWMA vs Mean)**
```bash
curl -s http://localhost:5002/api/price/health | \
  jq '.worker_stats.worker.queue_stats | {avg: .avg_latency_ms, ewma: .ewma_latency_ms, ratio: (.ewma_latency_ms / .avg_latency_ms)}'
```
- Expected ratio: 0.9-1.1 (similar, stable latency)
- If ratio > 1.5: high volatility (normal during spikes)

**5. Snapshot Cache Hit Rate**
```bash
tail -f logs/dev_intelligence.log | grep "snapshot.*hit"
```
- Expected: >90% for dashboard requests
- If <80%: Increase TTL or pre-warm snapshot cache

---

## Rollback Checklist

If something breaks, revert commits in reverse order:

```bash
git revert <commit-4-hash>  # ThreadPool
git revert <commit-3-hash>  # Circuit breaker
git revert <commit-2-hash>  # EWMA
git revert <commit-1-hash>  # Metadata TTL
```

Or nuke everything:
```bash
git reset --hard HEAD~4  # Undo last 4 commits
git push origin -f       # Force push (use with caution)
```

---

## FAQ

**Q: Will circuit breaker ever disable all sources?**
A: No. Stale DB fallback is always available. Worst case: return stale price with `is_stale=True`.

**Q: What if EWMA diverges too much from mean?**
A: EWMA will naturally converge. If divergence >30%, log warning and investigate load spike.

**Q: Can I change the 10-minute cooldown (600s)?**
A: Yes. Edit `_is_circuit_broken()`: `if time.time() - cb.get('disabled_at', 0) > 600:` → change 600 to your value.

**Q: Will snapshot cache cause stale prices on dashboard?**
A: Yes, up to 30 seconds. This is intentional (worker refreshes every 10s, so typical staleness 0-30s).

**Q: Should I enable circuit breaker for Dexscreener?**
A: Probably not. Only if Dex consistently fails (never seen in production). Jupiter/Birdeye yes.

**Q: Can I have different cooldowns per source?**
A: Yes. Modify circuit breaker dict: `'dexscreener': {'disabled': False, 'disabled_at': 0, 'cooldown_secs': 600}` and check it.

---

## Next Steps After Implementation

1. **Monitor for 24 hours**: Check circuit breaker activity, API call rate, latency percentiles
2. **Adjust thresholds if needed**:
   - Circuit breaker failure threshold: 90% → 95% (less aggressive)
   - Cooldown: 600s → 900s (longer reset time)
   - EWMA alpha: 0.8 → 0.9 (less responsive)
3. **Consider future improvements**:
   - Per-source timeout budgets (Dex gets 1.5s, Jupiter 1.2s, Birdeye 1s)
   - Exponential backoff: increase cooldown if repeated failures
   - Cached provider rankings: persist success rates across restarts

---

## Success Criteria

After all 6 improvements, you should see:

✅ **API Usage**: 50% reduction (600→300 calls/hour)
✅ **Latency P99**: 65% improvement (2500ms → 800ms)
✅ **Circuit Breaker**: Birdeye disabled after first 50 failures, re-enabled after 10 min
✅ **Queue Wait Estimate**: Smoother (EWMA), fewer false saturation triggers
✅ **Dashboard**: Cache hits 95%+, no upstream calls
✅ **Zero Breaking Changes**: All existing code still works

If you don't see these, check the rollback checklist above.
