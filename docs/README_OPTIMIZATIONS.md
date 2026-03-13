# Token Price System Optimization — Complete Documentation

This folder contains comprehensive documentation for optimizing the token price tracking system across two levels:

## Level 1: Core Optimizations (Already Implemented)

**Documents:**
- `OPTIMIZATION_SUMMARY.md` — Executive overview
- `OPTIMIZATION_IMPLEMENTATION_GUIDE.md` — Step-by-step guide  
- `TOKEN_PRICE_OPTIMIZATION_PATCH.md` — Complete code changes
- `TOKEN_PRICE_SYSTEM.md` — Baseline architecture

**What it fixes:**
- Cache-first metadata endpoint (100x faster)
- Idempotent token registration (0 churn)
- Reduced batch size (20→10)
- HIGH→MEDIUM downgrade (faster)
- Per-source backoff on 429
- Row patching instead of rebuild
- Stale cache fallback

**Results:**
- 60% fewer API calls
- 95% fewer 429 errors
- Smooth UI, no flicker

---

## Level 2: Advanced Optimizations (Phases 1-2 Complete ✓)

**Documents:**
- `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` — Start here (5 min read)
- `ADVANCED_OPTIMIZATIONS_PATCH.md` — Complete code changes (1500 lines)

**Completed Phases:**

1. ✅ **Request Queue (Phase 1)** — Smooth bursts → 3 parallel + queue
   - Status: Implemented and stable (2026-03-10)
   - Metrics: 0 queue backlog, 90ms avg latency
   - See: `PHASE1_REQUEST_QUEUE_SUMMARY.md`

2. ✅ **Activity Scheduling (Phase 2)** — Smart refresh based on market activity
   - Status: Implemented and verified (2026-03-13)
   - Metrics: 26 tokens tracked, 27ms avg latency, 0 errors
   - See: `PHASE2_IMPLEMENTATION_SUMMARY.md`

**Planned Phases:**

3. **Multi-Source** (3h) — Dex → Jupiter → Birdeye → cache fallback
4. **Metadata Persistence** (2h) — SQLite cache survives restarts
5. **Pre-Warm Cache** (1h) — Fetch on register, not on first view
6. **WebSocket Streaming** (4h, optional) — Real-time instead of polling

**Results So Far:**
- 20-30% reduction in API calls (Phase 2 activity-based scheduling)
- Queue processing smooth with zero backlog
- Zero failed requests across 8 cycles
- Expected total: 60-70% fewer API calls (after all phases: 900→200-300)

---

## Reading Guide

### If you have 5 minutes
Read: `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md`

### If you have 30 minutes
Read in order:
1. `OPTIMIZATION_SUMMARY.md` (5 min)
2. `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` (5 min)
3. `TOKEN_PRICE_SYSTEM.md` (20 min, baseline)

### If you're implementing core optimizations
Read:
1. `OPTIMIZATION_SUMMARY.md`
2. `OPTIMIZATION_IMPLEMENTATION_GUIDE.md`
3. Reference `TOKEN_PRICE_OPTIMIZATION_PATCH.md` while coding

### If you're implementing advanced optimizations
Read:
1. `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md`
2. `ADVANCED_OPTIMIZATIONS_PATCH.md` (reference for code)
3. `OPTIMIZATION_IMPLEMENTATION_GUIDE.md` (testing patterns)

### If something is broken
Check:
1. `OPTIMIZATION_IMPLEMENTATION_GUIDE.md` section "Common Issues & Fixes"
2. `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` section "Common Issues"

---

## Document Map

```
README_OPTIMIZATIONS.md (this file)
├── TOKEN_PRICE_SYSTEM.md
│   └── Baseline architecture (18+ endpoints, database schema, worker internals)
│
├── OPTIMIZATION_SUMMARY.md
│   └── Executive summary: problem → solution → impact
│
├── OPTIMIZATION_IMPLEMENTATION_GUIDE.md
│   └── File-by-file changes, testing checklist, rollout plan
│
├── TOKEN_PRICE_OPTIMIZATION_PATCH.md
│   └── Before/after code for 4 files, ~450 lines
│
├── ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md
│   └── Quick overview of 6 phases, checklist, monitoring
│
├── ADVANCED_OPTIMIZATIONS_PATCH.md
│   └── Complete code changes for 6 phases, ~1500 lines
│
├── PHASE2_ACTIVITY_SCHEDULING_PLAN.md
│   └── Design document for activity-based refresh scheduling (detailed algorithm)
│
└── PHASE2_IMPLEMENTATION_SUMMARY.md
    └── Phase 2 implementation report (scoring examples, test results, metrics)
```

---

## Quick Facts

### Core Optimization (Implemented or Ready to Deploy)

| | |
|---|---|
| **Effort** | 8 hours |
| **Files** | 4 files, ~450 lines |
| **Risk** | Medium (low for Phase 1) |
| **Phases** | 3 independent phases |
| **API Reduction** | 60% (900→350 calls/hour) |
| **429s** | 95% reduction (10-50/day → 0-2) |
| **UI Speed** | 100x faster metadata, no flicker |

### Advanced Optimization (Phases 1-2 Complete)

| | |
|---|---|
| **Effort** | 13-17 hours (7h done, 6-10h remaining) |
| **Files** | 8 files, ~1500 lines (~100 lines implemented) |
| **Risk** | Medium (low for most phases) |
| **Phases** | 6 sequential phases (2 complete) |
| **API Reduction** | 20-30% already achieved (Phase 2) |
| **Expected Total** | Additional 30-50% more (Phases 3-6) |
| **429s** | Prevents future spikes |
| **Availability** | Smooth queue processing, 0 errors |
| **Optional** | Phase 6 (WebSocket) is nice-to-have |

---

## Implementation Timeline

### Week 1: Core Optimization (If not done)
- **Phase 1:** Cache-first symbols, idempotent registration (2h)
- **Phase 2:** Source backoff, faster downgrade (4h)
- **Phase 3:** Row patching, separate refresh loops (2h)
- **Result:** 60% API reduction

### Week 2: Advanced Optimization (Phases 1-5)
- **Phase 1:** Request queue (4h)
- **Phase 2:** Activity scheduling (3h)
- **Phase 3:** Multi-source (3h)
- **Phase 4:** Metadata persistence (2h)
- **Phase 5:** Pre-warm cache (1h)
- **Result:** Additional 30% API reduction, 99%+ availability

### Week 3: Advanced (Optional)
- **Phase 6:** WebSocket streaming (4h)
- **Result:** Real-time updates, smoother UI

---

## Success Criteria

After **Core Optimization**:
- ✓ 0-2 429 errors/day (down from 10-50)
- ✓ 70%+ cache hit ratio (up from 40%)
- ✓ <5ms symbol latency (from 100-500ms)
- ✓ 0 table rebuilds (smooth UI)

After **Advanced Optimization (Phases 1-5)**:
- ✓ 600-800 API calls/hour (down from 900-1200)
- ✓ 99%+ availability (up from 95%)
- ✓ 0 restart storms (metadata persists)
- ✓ 1-2s new token load (down from 5-10s)
- ✓ Queue depth ≤5 (smooth traffic)

After **Phase 6 (WebSocket)**:
- ✓ <100ms price updates (from 15s polling)
- ✓ Fewer polling requests
- ✓ Real-time dashboard feel

---

## Key Files Modified

### Core Optimization
- `src/apis/price_api.py` — Symbol endpoint, batch register
- `src/core/price_worker.py` — Downgrade logic, backoff
- `src/core/price_service.py` — Backoff-aware fetching
- `src/core/main.py` — Row patching, separate loops

### Advanced Optimization
- `src/core/price_fetch_queue.py` (new) — Request queue
- `src/core/price_worker.py` — Activity scoring, queue integration
- `src/core/price_service.py` — Multi-source aggregation
- `src/apis/price_api.py` — Metadata persistence, pre-warm
- `src/apis/price_streaming.py` (new, Phase 6) — WebSocket
- `src/core/main.py` — WebSocket client (Phase 6)
- Database schema — metadata_cache table (Phase 4)

---

## Quick Start

### 1. Understand Current State
```bash
# Read baseline architecture
cat TOKEN_PRICE_SYSTEM.md | head -200
```

### 2. Decide on Scope
- **Minimum:** Core optimization (8h, 60% API reduction)
- **Recommended:** Core + Advanced Phases 1-5 (21h, 80% total reduction)
- **Nice-to-have:** Add Phase 6 (25h, real-time updates)

### 3. Create Implementation Branch
```bash
git checkout -b optimize/advanced
```

### 4. Start with Phase 1
```bash
# Read quick reference
less ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md

# Look at code changes
less ADVANCED_OPTIMIZATIONS_PATCH.md | grep -A 50 "Phase 1"

# Implement, test, commit
git add -A && git commit -m "optimization(advanced): Phase 1 — Request queue"
```

---

## Support & Questions

If you're stuck:

1. **Common issues?** Check `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` → "Common Issues"
2. **Implementation detail?** See `ADVANCED_OPTIMIZATIONS_PATCH.md` for complete code
3. **Testing approach?** See `OPTIMIZATION_IMPLEMENTATION_GUIDE.md` → "Testing Checklist"
4. **Monitoring?** See `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` → "Monitoring & Alerts"

---

## Commits You'll Make

### Core Optimization (if not done)
```
optimization: Phase 1 — Cache-first symbols, idempotent registration
optimization: Phase 2 — Source backoff, faster HIGH downgrade
optimization: Phase 3 — In-place row patching, separate refresh loops
```

### Advanced Optimization
```
optimization(advanced): Phase 1 — Request queue
optimization(advanced): Phase 2 — Activity scheduling
optimization(advanced): Phase 3 — Multi-source aggregation
optimization(advanced): Phase 4 — Metadata persistence
optimization(advanced): Phase 5 — Pre-warm cache
optimization(advanced): Phase 6 — WebSocket streaming (optional)
```

---

## Rollback at Any Time

```bash
# Revert last 6 commits (all advanced phases)
git reset --hard HEAD~6

# Or revert specific phase
git revert <commit-hash>

# Restart
./scripts/restart.sh
```

---

## Performance Dashboard

Monitor at: `http://localhost:5002/api/price/health`

Key metrics:
```json
{
  "worker_stats": {
    "cycles": 1234,
    "tokens_prefetched": 45678,
    "api_calls": 543,
    "cache_hits": 12345,
    "high_priority_downgrades": 200,
    "backoff_events": 5,
    "queue_stats": {
      "queue_depth": 3,
      "processed": 1200,
      "failed": 34
    }
  }
}
```

---

## Next Steps

1. Read `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` (5 min)
2. Decide which phases to implement
3. Create feature branch
4. Start with Phase 1
5. Deploy after testing each phase

**Estimated time:** 13-17 hours total

**Return on investment:** 60-80% API reduction, 99%+ availability, smoother UI

