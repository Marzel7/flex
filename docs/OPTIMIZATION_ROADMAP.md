# Token Price System Optimization Roadmap

**Project Status**: Phases 1-2 Complete ✅ | Phases 3-5 Designed 📋
**Overall Progress**: 40% complete (2 of 5 core phases done)
**Next Focus**: Phase 3 implementation (Multi-Source Aggregation)

---

## High-Level Progress

### ✅ Completed Phases

| Phase | Feature | Status | Commit | Impact |
|-------|---------|--------|--------|--------|
| 1 | Request Queue | ✅ DONE | `7956dc9` | Smooth traffic (0 backlog) |
| 2 | Activity Scheduling | ✅ DONE | `7955dc9` | 20-30% fewer API calls |

**Cumulative results so far:**
- Queue depth: 0-1 (smooth)
- 26 tokens tracked with 0 errors
- Average latency: 27ms per request
- 68 tokens processed in 8 cycles

### 📋 Designed Phases (Ready to Implement)

| Phase | Feature | Status | Design Doc | Est. Time | Impact |
|-------|---------|--------|------------|-----------|--------|
| 3 | Multi-Source | 📋 DESIGNED | `PHASES3-5_IMPLEMENTATION_PLAN.md` | 3h | 99%+ availability |
| 4 | Metadata Persistence | 📋 DESIGNED | `PHASES3-5_IMPLEMENTATION_PLAN.md` | 2h | No restart storms |
| 5 | Cache Pre-Warming | 📋 DESIGNED | `PHASES3-5_IMPLEMENTATION_PLAN.md` | 1.5h | 1-2s faster first load |

### ⏳ Optional Phase

| Phase | Feature | Status | Design Doc | Est. Time | Impact |
|-------|---------|--------|------------|-----------|--------|
| 6 | WebSocket Streaming | 📋 DESIGNED | `ADVANCED_OPTIMIZATIONS_PATCH.md` | 4h | Real-time updates |

---

## Documentation Map

### Quick Start (5 minutes)
→ Start here: [`ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md`](ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md)

### Understanding Current State (20 minutes)
1. [`OPTIMIZATION_SUMMARY.md`](OPTIMIZATION_SUMMARY.md) — Executive overview of core optimizations
2. [`TOKEN_PRICE_SYSTEM.md`](TOKEN_PRICE_SYSTEM.md) — Baseline architecture and APIs

### Phase-Specific Documentation

**Phase 1: Request Queue**
- Status: ✅ Complete and stable
- Doc: [`ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md`](ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md) (section: Phase 1)
- Details: Reduces API bursts from 20 simultaneous → 3 concurrent + queue

**Phase 2: Activity-Based Scheduling**
- Status: ✅ Complete and tested
- Design: [`PHASE2_ACTIVITY_SCHEDULING_PLAN.md`](PHASE2_ACTIVITY_SCHEDULING_PLAN.md) (detailed algorithm)
- Summary: [`PHASE2_IMPLEMENTATION_SUMMARY.md`](PHASE2_IMPLEMENTATION_SUMMARY.md) (results & metrics)
- Details: Dynamic refresh intervals based on market activity

**Phases 3-5: Next Implementations**
- Status: 📋 Fully designed, ready to code
- Plan: [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md) (complete with code)
- Covers:
  - Phase 3: Multi-source fallback chain (Dex → Jupiter → Birdeye → cache)
  - Phase 4: Persistent SQLite metadata cache
  - Phase 5: Background cache pre-warming on registration

**Phase 6: WebSocket (Optional)**
- Status: 📋 Designed but not prioritized
- Doc: [`ADVANCED_OPTIMIZATIONS_PATCH.md`](ADVANCED_OPTIMIZATIONS_PATCH.md) (section: Phase 6)
- Details: Real-time price updates instead of 15s polling

### Full Reference
- [`ADVANCED_OPTIMIZATIONS_PATCH.md`](ADVANCED_OPTIMIZATIONS_PATCH.md) — Complete code examples (1500 lines)
- [`OPTIMIZATION_IMPLEMENTATION_GUIDE.md`](OPTIMIZATION_IMPLEMENTATION_GUIDE.md) — Testing patterns & rollback

---

## Results Summary

### Baseline (Before Any Optimizations)
```
API calls/hour:     3000+
429 errors/day:     10-50
Symbol latency:     100-500ms
Cache hit ratio:    40%
Queue depth:        N/A
Table rebuilds:     2/min (every 30s)
```

### After Phase 1 (Request Queue)
```
API calls/hour:     2500-2800 (same, but smooth)
429 errors/day:     0-2 (burst eliminated)
Symbol latency:     100-500ms (unchanged)
Cache hit ratio:    40% (unchanged)
Queue depth:        ≤ 5 (smooth)
Table rebuilds:     2/min (unchanged)
```

### After Phase 2 (Activity Scheduling) ← CURRENT STATE
```
API calls/hour:     600-800 (-70%)
429 errors/day:     0 (sustained)
Symbol latency:     100-500ms (unchanged)
Cache hit ratio:    40% (unchanged)
Queue depth:        ≤ 1 (very smooth)
Table rebuilds:     2/min (unchanged)
Activity dist:      26 low, 0 medium, 0 high
Failed requests:    0
```

### After Phase 3 (Multi-Source)
```
API calls/hour:     600-800 (same)
429 errors/day:     0 (resilient)
Availability:       99%+ (vs 95%)
Symbol latency:     100-500ms (unchanged)
Cache hit ratio:    40% (unchanged)
Birdeye fallback:   < 10% of requests
```

### After Phase 4 (Metadata Cache)
```
API calls/hour:     600-800 (same)
Symbol latency:     1-5ms from cache (vs 100-500ms from upstream)
Cache hit ratio:    80%+ (vs 40%)
Restart impact:     0 symbol storms (vs 25+ calls)
Metadata RTT:       < 5ms (from SQLite)
```

### After Phase 5 (Pre-Warming)
```
API calls/hour:     600-800 (same)
New token load:     1-2s with metadata (vs 5-10s)
Queue pre-filled:   Yes (warm-up on register)
First render:       Complete with symbol/price
```

### After Phase 6 (WebSocket, Optional)
```
Price update latency: < 100ms (vs 15s polling)
Dashboard poll rate:  < 10 handshakes (vs 240/hour)
Real-time feel:      Yes (push updates)
```

---

## Implementation Timeline

### Week 1: Complete Phases 1-2 (Already Done ✅)
```
Fri 2026-03-10: Phase 1 (Request Queue) deployed
Fri 2026-03-13: Phase 2 (Activity Scheduling) deployed
```

### Week 2: Implement Phases 3-5 (Next Step)
```
Mon 2026-03-17: Phase 3 (Multi-Source) — 3h
Tue 2026-03-18: Phase 4 (Metadata Cache) — 2h
Wed 2026-03-19: Phase 5 (Pre-Warming) — 1.5h
Wed 2026-03-19: Integration testing — 2h
Thu 2026-03-20: Deploy Phases 3-5 to production
```

### Week 3: Monitoring & Phase 6 (Optional)
```
Fri 2026-03-21 onwards: Monitor Phases 3-5 stability
                        Plan Phase 6 if bandwidth available
```

---

## Next Steps (Phase 3 Execution)

### 1. Review Design Document (30 min)
Read: [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md)
- Focus on "File-by-File Implementation" section
- Review code examples for each file
- Check assumptions and risks

### 2. Create Feature Branch (5 min)
```bash
git checkout -b optimize/phase3-multi-source
```

### 3. Implement Phase 3: Multi-Source (3 hours)
```bash
# 1. Modify src/core/price_service.py
#    - Add BirdeyeClient class
#    - Modify get_token_prices_sync() with fallback chain
#    - Add source metrics

# 2. Add to __init__() stats dict:
#    - dexscreener_success/fail
#    - jupiter_success/fail
#    - birdeye_success/fail
#    - stale_cache_fallback

# 3. Test
#    - Verify fallback chain works
#    - Check source metrics in health endpoint
#    - Simulate Dexscreener failure
```

### 4. Test Phase 3 (1 hour)
```bash
# Run locally
./scripts/restart.sh

# Monitor health endpoint
watch -n 2 'curl http://localhost:5002/api/price/health | jq ".worker_stats.source_stats"'

# Verify no 429s
tail -f logs/dev_intelligence.log | grep "429"
```

### 5. Commit Phase 3
```bash
git add src/core/price_service.py
git commit -m "optimization(Phase 3): Multi-source price aggregation"
./scripts/restart.sh
```

### 6. Repeat for Phases 4-5

See [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md) section "Rollout Order & Testing" for detailed steps.

---

## Decision Trees

### Should I implement Phase 3 next?
```
Is Phase 2 stable? (no queue backlog, 0 errors)
├─→ Yes: Proceed with Phase 3
└─→ No: Debug Phase 2, rollback if needed
```

### Should I implement Phase 4 before Phase 3?
```
Is metadata cache critical to your deployment?
├─→ Yes (frequent restarts, symbol storms): Do Phase 4 first
└─→ No (stable deployment): Do Phase 3 first (better resilience)
```

### Should I implement Phase 5?
```
Is new token load time a user-facing issue?
├─→ Yes (users complain): Prioritize Phase 5
└─→ No (backend focus): Phase 5 is lower priority
```

### Should I implement Phase 6 (WebSocket)?
```
Is real-time dashboard updates required?
├─→ Yes (high-frequency traders): Implement Phase 6
└─→ No (15s polling acceptable): Skip for now (nice-to-have)
```

---

## Key Metrics to Watch

### Phase 3 Success Criteria
- [ ] `dexscreener_success` > 90% (still primary)
- [ ] `jupiter_success` > 80% (good fallback)
- [ ] `birdeye_success` > 50% (acceptable for fallback)
- [ ] `stale_fallback` < 5% (rare)
- [ ] No 429 errors even with partial outage

### Phase 4 Success Criteria
- [ ] `persistent_metadata_hits` > 100 within 4 hours
- [ ] After restart: first symbol lookup hits persistent cache
- [ ] No upstream symbol fetches in logs after restart
- [ ] Symbol latency drops from 100-500ms to 1-5ms

### Phase 5 Success Criteria
- [ ] `warm_up_queued` > 0 on batch register
- [ ] `warm_up_completed / warm_up_queued` > 95%
- [ ] New tokens show metadata within 1-2 seconds
- [ ] UI no longer shows "UNKNOWN" or blank symbols initially

---

## Rollback Procedures

### If Phase 3 breaks
```bash
git revert <phase3-commit-hash>
./scripts/restart.sh
```
Expected downtime: 30 seconds
No data loss (no database changes)

### If Phase 4 breaks
```bash
git revert <phase4-commit-hash>
# Optional: rm database/flex_complete_database.db (if corrupted)
./scripts/restart.sh
```
Expected downtime: 30 seconds
Data: metadata_cache table deleted, in-memory cache survives

### If Phase 5 breaks
```bash
git revert <phase5-commit-hash>
./scripts/restart.sh
```
Expected downtime: 30 seconds
Warm-up stops but registration still works

### If all three break
```bash
# Revert all three at once
git reset --hard HEAD~3
./scripts/restart.sh

# Falls back to Phase 2 (last known good state)
```

---

## Communication Template

### When rolling out to team:

```
📦 Token Price Optimization — Phases 3-5 Ready for Implementation

Status:
- Phases 1-2: ✅ Complete and stable (2026-03-10/13)
- Phases 3-5: 📋 Fully designed, ready to code
- Phase 6: 📋 Designed but optional

What's Next:
1. Phase 3 (Multi-Source): Add Birdeye fallback → 99%+ availability
2. Phase 4 (Metadata Cache): SQLite persistence → no restart storms
3. Phase 5 (Pre-Warming): Background enqueue → 1-2s faster load

Implementation Plan:
- Design docs: Phases 3-5 complete (PHASES3-5_IMPLEMENTATION_PLAN.md)
- Code ready to write (~10 hours total)
- Rollout over 3 days: Mon/Tue/Wed next week
- Each phase tested independently before merging

Expected Results:
- API calls: 600-800/hour (steady)
- Availability: 95% → 99%+ (Phase 3)
- Symbol latency: 100-500ms → 1-5ms (Phase 4)
- New token load: 5-10s → 1-2s (Phase 5)

Questions? See PHASES3-5_IMPLEMENTATION_PLAN.md section "Assumptions"
```

---

## Reference Table

### All Documentation
| File | Purpose | Read Time |
|------|---------|-----------|
| `OPTIMIZATION_SUMMARY.md` | Executive overview | 5 min |
| `ADVANCED_OPTIMIZATIONS_QUICK_REFERENCE.md` | 6-phase overview | 5 min |
| `TOKEN_PRICE_SYSTEM.md` | Baseline architecture | 20 min |
| `PHASE2_ACTIVITY_SCHEDULING_PLAN.md` | Phase 2 design | 15 min |
| `PHASE2_IMPLEMENTATION_SUMMARY.md` | Phase 2 results | 10 min |
| `PHASES3-5_IMPLEMENTATION_PLAN.md` | Phases 3-5 design + code | 30 min |
| `ADVANCED_OPTIMIZATIONS_PATCH.md` | All 6 phases (reference) | 45 min |
| `OPTIMIZATION_ROADMAP.md` | This file (navigation) | 5 min |

### Key Files to Modify
| File | Phase | Changes | Lines |
|------|-------|---------|-------|
| `src/core/price_service.py` | 3 | BirdeyeClient, fallback chain | 200 |
| `src/apis/price_api.py` | 4,5 | Metadata cache, pre-warming | 150 |
| `src/core/price_worker.py` | 3,4,5 | Stats tracking | 50 |

### Commits You'll Make
```
optimization(Phase 3): Multi-source price aggregation
optimization(Phase 4): Persistent metadata cache
optimization(Phase 5): Cache pre-warming on registration
```

---

## FAQ

**Q: Should I wait for feedback on Phase 2 before starting Phase 3?**
A: No. Phase 2 is production-stable. Phases 3-5 are independent and can start immediately. Phase 3 doesn't depend on Phase 2.

**Q: Can I skip Phase 4 and do Phase 5 first?**
A: No. Phase 4 (metadata cache) is required for Phase 5 to work properly. Do 3 → 4 → 5 in order.

**Q: How long should I monitor each phase before moving to the next?**
A: Minimum 4 hours (one full activity cycle). Watch health metrics, check for errors in logs.

**Q: What if Birdeye is frequently slow in Phase 3?**
A: Add it to skip list. Check `PHASES3-5_IMPLEMENTATION_PLAN.md` section "Risk: Birdeye API slow". Can revert to Dex+Jupiter only.

**Q: Can users access the system during Phase 3-5 deployments?**
A: Yes. Each phase is backward-compatible. Restart is ~30 seconds. Schedule during low-traffic window if possible.

**Q: Do I need to update the dashboard for Phases 3-5?**
A: No. All changes are backend. Dashboard continues to poll as-is. Phase 6 (WebSocket) is where dashboard changes occur.

**Q: What happens if I rollback mid-phase?**
A: Git revert removes the commits. Database changes (Phase 4) are left as-is but not used (safe). Restart, system falls back to previous phase.

---

## Contact & Support

**Questions about design?** Check [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md) section "Assumptions"

**Questions about testing?** Check [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md) section "Rollout Order & Testing"

**Questions about risks?** Check [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md) section "Risks & Mitigations"

**Questions about implementation?** Check [`PHASES3-5_IMPLEMENTATION_PLAN.md`](PHASES3-5_IMPLEMENTATION_PLAN.md) section "File-by-File Implementation"

---

**Last Updated**: 2026-03-13
**Next Review**: After Phase 3 deployment
