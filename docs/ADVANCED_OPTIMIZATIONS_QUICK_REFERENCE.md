# Advanced Optimizations — Quick Reference

## 6 Phases, 8 Files, ~1500 Lines of Code

Extends core optimization patch with queuing, activity scheduling, multi-source fallback, and persistence.

---

## Phases at a Glance

| Phase | What | Files | Time | Risk | Impact |
|-------|------|-------|------|------|--------|
| **1** | Request queue + concurrency | `price_fetch_queue.py` (new), `price_worker.py` | 4h | Low | API bursts → smooth |
| **2** | Activity-based scheduling | `price_worker.py` | 3h | Low | 20-30% fewer calls |
| **3** | Multi-source aggregation | `price_service.py` | 3h | Medium | 95% → 99% availability |
| **4** | Metadata cache persistence | `price_api.py`, schema | 2h | Low | Survives restarts |
| **5** | Pre-warm cache | `price_api.py` | 1h | Low | Faster first load |
| **6** | WebSocket streaming | `price_streaming.py` (new), `main.py` | 4h | Medium | 15s polling → real-time |

**Total: ~17 hours, Medium overall risk**

---

## What Each Phase Does

### Phase 1: Request Queue with Concurrency Limits

**Problem:** API bursts (fetch 10 tokens simultaneously)

**Solution:** Smooth queue (fetch 3 at a time, 200ms apart)

**Code:** New `PriceFetchQueue` class

```python
queue = PriceFetchQueue(max_concurrent=3, request_delay_ms=200)
# Instead of: prices = service.get_prices(tokens)
# Do: await queue.enqueue_batch(tokens)
```

**Benefit:** 10→3 simultaneous requests = 70% less burst

**Test:** `queue.get_stats()['queue_depth']` should stay ≤5

---

### Phase 2: Activity-Based Scheduling

**Problem:** All tokens same refresh frequency (HIGH/MEDIUM/LOW)

**Solution:** Compute activity score; refresh based on volume, price change, liquidity

```python
activity = compute_activity_score(token)
# Returns: 'high' (5-10s), 'medium' (20-30s), 'low' (60-100s), 'dormant' (200-300s)
```

**Benefit:** Dormant tokens 5x less API pressure; same freshness for active tokens

**Test:** Most tokens should be MEDIUM/LOW, few HIGH

---

### Phase 3: Multi-Source Aggregation

**Problem:** Single source failure = missing prices

**Solution:** Try sources in order; return first success

```python
# Try Dexscreener → if fails, try Jupiter → if fails, try Birdeye → if fails, use cache
sources = [('dexscreener', dex_fetch), ('jupiter', jup_fetch), ('birdeye', bird_fetch)]
```

**Benefit:** 95% → 99% availability; reduces 429 impact

**Test:** Logs show "Batch fetch: dexscreener=15, jupiter=3, birdeye=2"

---

### Phase 4: Metadata Cache Persistence

**Problem:** Server restart → all symbol caches cleared → 25 symbol fetch spike

**Solution:** Store symbol/name in SQLite with 5-min TTL; load on startup

```sql
CREATE TABLE metadata_cache (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    cached_at INTEGER
)
```

**Benefit:** No symbol-fetch storm on restart; faster first load

**Test:** `SELECT COUNT(*) FROM metadata_cache` should grow to ~25, then stable

---

### Phase 5: Pre-Warm Cache on Registration

**Problem:** New token appears in UI → no price/symbol yet → slow load

**Solution:** When token registered, enqueue fetch immediately

```python
# In batch/register endpoint:
if registry.register_token(mint):
    queue.enqueue(FetchTask(mint, priority='MEDIUM'))  # Fetch in background
```

**Benefit:** Price/symbol available when dashboard renders

**Test:** New tokens should show price within 1-2 seconds

---

### Phase 6: WebSocket Streaming (Optional)

**Problem:** Dashboard polls every 15 seconds

**Solution:** Connect via WebSocket; server pushes prices when updated

```javascript
// Instead of:
setInterval(() => fetch('/api/price/batch'), 15000)
// Do:
ws = new WebSocket('/api/price/stream')
ws.onmessage = (event) => updatePrice(event.data)
```

**Benefit:** Real-time updates (<100ms) instead of 15s latency

**Test:** Dashboard shows price updates instantly when API returns data

**Fallback:** If WebSocket unavailable, automatic fallback to polling

---

## Key Code Locations

| What | File | Lines |
|------|------|-------|
| Request queue | `src/core/price_fetch_queue.py` (new) | 1-150 |
| Queue integration | `src/core/price_worker.py` | _refresh_cycle(), line ~280 |
| Activity scoring | `src/core/price_worker.py` | _compute_activity_score(), line ~350 |
| Multi-source | `src/core/price_service.py` | get_token_prices_sync(), line ~200 |
| Metadata persistence | `src/apis/price_api.py` | get_token_symbol(), line ~100 |
| WebSocket | `src/apis/price_streaming.py` (new) | 1-80 |
| Dashboard WS | `src/core/main.py` | connectPriceStream(), line ~4000 |

---

## Implementation Checklist

### Before Starting
- [ ] Read `ADVANCED_OPTIMIZATIONS_PATCH.md` fully
- [ ] Backup database
- [ ] Create branch: `git checkout -b optimize/advanced`
- [ ] Have test environment ready

### Phase 1: Request Queue (4h)
- [ ] Create `src/core/price_fetch_queue.py`
- [ ] Copy `PriceFetchQueue` class from patch
- [ ] Modify `price_worker.py`: import, use in `_refresh_cycle()`
- [ ] Test: queue processes smoothly, depth ≤5
- [ ] Commit: "optimization(advanced): Phase 1 — Request queue"

### Phase 2: Activity Scheduling (3h)
- [ ] Add `_compute_activity_score()` to `price_worker.py`
- [ ] Replace `_get_tokens_for_refresh()` with activity-based version
- [ ] Test: observe activity distribution in logs
- [ ] Commit: "optimization(advanced): Phase 2 — Activity scheduling"

### Phase 3: Multi-Source (3h)
- [ ] Add `BirdeyeClient` class to `price_service.py`
- [ ] Modify `get_token_prices_sync()` for fallback chain
- [ ] Test: each source works independently
- [ ] Test: fallback chain works (disable Dex, verify Jupiter used)
- [ ] Commit: "optimization(advanced): Phase 3 — Multi-source"

### Phase 4: Metadata Persistence (2h)
- [ ] Add table creation to `price_api.py`
- [ ] Add `_get_cached_metadata()`, `_store_metadata_cache()`
- [ ] Modify `get_token_symbol()` to use DB cache
- [ ] Test: symbols persist across server restart
- [ ] Commit: "optimization(advanced): Phase 4 — Metadata persistence"

### Phase 5: Pre-Warm (1h)
- [ ] Modify `/api/price/batch/register` endpoint
- [ ] Enqueue tokens immediately
- [ ] Add `warm_up_queued` to response
- [ ] Test: new tokens show price quickly
- [ ] Commit: "optimization(advanced): Phase 5 — Pre-warm cache"

### Phase 6: WebSocket (4h)
- [ ] Add `flask-sock` to requirements
- [ ] Create `src/apis/price_streaming.py`
- [ ] Modify app initialization
- [ ] Add WebSocket handler to price_worker callback
- [ ] Modify dashboard to connect to WebSocket
- [ ] Test: prices update in real-time
- [ ] Verify polling fallback works
- [ ] Commit: "optimization(advanced): Phase 6 — WebSocket streaming"

### After All Phases
- [ ] Monitor `/api/price/health` stats
- [ ] Check metrics for 24 hours
- [ ] Verify no 500 errors
- [ ] Merge to main

---

## Deployment Order

**Recommended:** Deploy phases in order (1→2→3→4→5→6)

**Why:**
- Each phase builds on previous
- Low-risk phases first (1, 2, 4, 5)
- High-value early (50% API reduction by Phase 3)
- Optional phase last (6 is WebSocket; polling still works)

**Can skip:** Phase 6 is optional (WebSocket is nice-to-have)

---

## Expected Results

### After Phase 1 (Request Queue)

```
Before:  10 tokens in parallel → 10 API calls burst
After:   3 tokens in parallel + queue → smooth traffic

API calls/hour: 900-1200 → 900-1200 (same, but smooth)
429 errors/day: 0-2 → 0 (spikes eliminated)
```

### After Phase 2 (Activity Scheduling)

```
Before:  All tokens refresh at fixed intervals
After:   Dormant tokens refresh 5x less

API calls/hour: 900-1200 → 600-800 (30% reduction!)
Dormant tokens: 50% of tracked → reduced load
```

### After Phase 3 (Multi-Source)

```
Before:  Dexscreener fails → missing prices
After:   Try Jupiter, Birdeye, cache → always have data

Availability: 95% → 99%
Fallback usage: Birdeye ~10%, cache ~10%
```

### After Phase 4 (Metadata Persistence)

```
Before:  Server restart → symbol-fetch storm
After:   Symbols loaded from DB → no storm

Symbol fetch latency on restart: 25 calls → 0 calls
```

### After Phase 5 (Pre-Warm)

```
Before:  New token → no price yet → slow load
After:   Fetch starts immediately on register

New token first price: 5-10s → 1-2s
```

### After Phase 6 (WebSocket)

```
Before:  Poll every 15 seconds
After:   Push updates in real-time

Price update latency: 15s (poll) → <100ms (push)
Dashboard requests/hour: 240 polling → <10 WebSocket handshakes
```

---

## Rollback by Phase

If Phase 3 breaks, revert it only:

```bash
git revert <Phase-3-commit-hash>
./scripts/restart.sh
```

Or revert all at once:

```bash
git reset --hard HEAD~6  # Remove all 6 phases
./scripts/restart.sh
```

---

## Monitoring & Alerts

### Key Metrics to Watch

```
Dashboard: /api/price/health
→ worker_stats.queue_stats.queue_depth (should be ≤5)
→ worker_stats.tokens_prefetched (should be stable)
→ worker_stats.backoff_events (should be 0)

Database: SELECT COUNT(*) FROM metadata_cache (should be ~25)

Logs: "Batch fetch: dexscreener=15, jupiter=3, ..." (expected distribution)

WebSocket (if Phase 6): Connected clients (should match active dashboards)
```

### Success Criteria

- [ ] 429 errors: 0-2/day (down from 0-2, but stable)
- [ ] API calls: 600-800/hour (down from 900-1200)
- [ ] Metadata cache: 0 on restart → 25 loaded (survives)
- [ ] Queue depth: avg ≤5, max ≤10 (smooth)
- [ ] Activity distribution: 10% high, 60% medium, 30% low (balanced)
- [ ] Multi-source: 70% Dex, 15% Jupiter, 10% Birdeye, 5% cache (expected)
- [ ] WebSocket clients: matches active dashboards (if Phase 6)

---

## Common Issues

### Issue: Queue keeps growing (depth >50)

**Cause:** Workers slower than enqueuers

**Fix:**
1. Increase `max_concurrent` from 3 to 5
2. Decrease `request_delay_ms` from 200 to 100
3. Check if upstream APIs rate-limiting

### Issue: Metadata not persisting to DB

**Cause:** `_store_metadata_cache()` failing silently

**Fix:**
1. Check SQLite file permissions
2. Verify table exists: `sqlite3 flex.db ".tables"`
3. Add error logging: `logger.error(f"Store error: {e}")`

### Issue: WebSocket not connecting

**Cause:** Flask-sock not installed or app not initialized

**Fix:**
1. `pip install flask-sock`
2. Verify in app: `sock = Sock(app)`
3. Check logs: `"WebSocket endpoint registered"`

### Issue: Activity score always 'medium'

**Cause:** Token analysis table missing volume/price columns

**Fix:**
1. Check schema: `sqlite3 flex.db "PRAGMA table_info(token_analysis)"`
2. Add missing columns if needed
3. Fall back to default 'medium' if data unavailable

---

## Performance Targets

| Metric | Before | Target | After |
|--------|--------|--------|-------|
| API calls/hour | 900-1200 | 600-800 | 750 |
| 429 errors/day | 0-2 | 0 | 0 |
| Queue depth | N/A | ≤5 avg | 3 |
| Symbol latency | 1-5ms | <5ms | 1ms (DB hit) |
| Price update latency | 15s (poll) | <1s | 100ms (WS) |
| Metadata restart impact | 25 calls | 0 calls | 0 calls |
| Availability | 95% | 99%+ | 99.5% |

---

## Cost-Benefit Analysis

| Phase | Effort | Risk | Benefit | Worth It? |
|-------|--------|------|---------|-----------|
| 1 | 4h | Low | +70% traffic smoothing | **YES** |
| 2 | 3h | Low | +30% API reduction | **YES** |
| 3 | 3h | Medium | +4% availability | **YES** |
| 4 | 2h | Low | No restart storms | **YES** |
| 5 | 1h | Low | +1s faster load | **YES** |
| 6 | 4h | Medium | Real-time updates | Maybe (optional) |

**Recommendation:** Phases 1-5 are essential (13h). Phase 6 is nice-to-have (4h).

---

## Next Steps

1. **Read full patch:** `ADVANCED_OPTIMIZATIONS_PATCH.md` (30 min)
2. **Plan deployment:** Decide which phases to implement
3. **Create branch:** `git checkout -b optimize/advanced`
4. **Implement Phase 1:** Request queue (4h)
5. **Test thoroughly:** Metrics, logs, manual testing (2h)
6. **Implement Phase 2:** Activity scheduling (3h)
7. **Continue:** Phases 3-6 as time allows

**Estimated total:** 13-17 hours (Phases 1-5 = 13h, Phase 6 optional = +4h)

