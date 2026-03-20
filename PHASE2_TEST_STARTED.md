# Phase 2 Test - Started

**Date:** 2026-03-20
**Time:** 13:04 UTC
**Status:** ✅ TESTING IN PROGRESS

---

## Test Setup

### Worker Deployment ✅
- **Process:** Python3 src/core/main.py
- **PID:** 99278
- **CPU Usage:** 0.3%
- **Memory:** 48 MB
- **Uptime:** Running
- **Code:** Phase 2 critical-path protection (commit 9e5e039)

### Phase 2 Infrastructure ✅
- Critical window tracking initialized
- RPC quota semaphores ready (8 discovery, 2 background)
- Background job queue processor running
- Tier-based retry strategy deployed

### Log File ✅
- **Path:** `/Users/kevinkeaveney/Dev/claude/flex/worker_phase2_test.log`
- **Status:** Actively writing
- **Size:** ~400 lines
- **Format:** Standard listener logs + Phase 2 events

---

## What to Expect

### When First Token Migrates

```
[EVENT] 🚀 MIGRATION DETECTED: (token_mint)
[STATE] Token ... → pending

[DISCOVERY_T1] attempt=1/12 elapsed=0.5s tier=TX_ONLY critical_window=ACTIVE
[DISCOVERY_TX] attempt=1 candidates_tested=N rejections=...

[DISCOVERY_T2] attempt=2/12 elapsed=1.0s tier=TX_ONLY critical_window=ACTIVE

[DISCOVERY_T3] attempt=3/12 elapsed=1.5s tier=TX_ONLY critical_window=ACTIVE
[DISCOVERY_SUCCESS] attempt=3 elapsed=1.8s strategy=tx_parsing
[STATE] Token ... → resolved (in 1.8s)

[BACKGROUND] 📤 Queueing background tasks (deferred after critical window)
[DISCOVERY_METRICS] tx_attempts=3 rpc_attempts=0 candidates=N rejections={...}
```

### Performance Expectation
- **Target:** 8-12 seconds median (Phase 1 benefit)
- **Phase 2 with RPC:** Could be 3-8 seconds
- **Worst case:** 50-60 seconds (full Tier 3 retry)

---

## How to Monitor

### Quick Watch (Recommended)
```bash
tail -f /Users/kevinkeaveney/Dev/claude/flex/worker_phase2_test.log | \
  grep -E "DISCOVERY|MIGRATION|BACKGROUND|resolved"
```

### Color-Coded Watch
```bash
/tmp/watch_phase2_test.sh
```

### Simple Tail
```bash
tail -f /Users/kevinkeaveney/Dev/claude/flex/worker_phase2_test.log
```

---

## Test Goals

### Minimum (First Token)
- [ ] Migration detected in logs
- [ ] Retry attempts shown with tier labels
- [ ] Rejection reasons visible
- [ ] Success or failure outcome logged
- [ ] Metrics complete

### Verification (5-10 Tokens)
- [ ] Latency averaging 8-12s (Phase 1 working)
- [ ] TX parsing dominates (70%+)
- [ ] Some RPC fallback (20-30%)
- [ ] Critical window tracking visible (ACTIVE → EXPIRED)
- [ ] Background jobs queued (not immediate)

### Phase 2 Specific (10+ Tokens)
- [ ] Tier 1 retries (TX-only) most common success path
- [ ] Tier 2-3 fallback for remaining tokens
- [ ] Rejection patterns identified
- [ ] No RPC timeouts or contention
- [ ] Metrics reveal clear strategy distribution

---

## Quick Test Commands

### Check worker is running
```bash
ps aux | grep "python.*main\.py" | grep -v grep
```

### Watch discovery events
```bash
tail -f worker_phase2_test.log | grep DISCOVERY
```

### Count Phase 2 events so far
```bash
grep -c "DISCOVERY" worker_phase2_test.log
```

### Get latest events
```bash
tail -50 worker_phase2_test.log | grep -E "DISCOVERY|MIGRATION"
```

### Extract latencies (after tokens resolve)
```bash
grep "→ resolved" worker_phase2_test.log | \
  sed 's/.*in \([0-9.]*\)s.*/\1/' | \
  awk '{sum+=$1; print $1 "s"} END {print "\nAverage: " sum/NR "s (n=" NR ")"}'
```

### See strategy distribution
```bash
grep "DISCOVERY_SUCCESS" worker_phase2_test.log | \
  grep -o "tx_parsing\|rpc_discovery" | sort | uniq -c
```

---

## Test Duration

| Time | Expected | What to Check |
|------|----------|---------------|
| T+5m | Initial logs | Worker initialization complete |
| T+15m | First token? | Phase 2 event logging works |
| T+30m | 3-5 tokens | Latency averaging, strategy distribution |
| T+60m | 10+ tokens | Phase 2 infrastructure fully validated |

---

## Success Indicators

### Phase 2 IS Working If:
✅ Retries shown with tiers (TX_ONLY → TX_PLUS_LIGHT_RPC → TX_PLUS_FULL_RPC)
✅ Rejection reasons logged (not generic "no accounts passed")
✅ Most tokens resolve via TX parsing (70%+)
✅ Some use RPC fallback (20-30%)
✅ Latency 8-12s (Phase 1 target) or 3-8s (Phase 2 with RPC)
✅ Background jobs queued & deferred
✅ Metrics complete at each discovery end

### Phase 2 NOT Working If:
❌ No retry logs
❌ All tokens timeout (60s+)
❌ Crashes or exceptions
❌ Old rejection format (no reason codes)
❌ Background jobs causing RPC failures

---

## If Issues Arise

### No tokens appearing
1. Check webhook integration: `grep "WEBHOOK" worker_phase2_test.log`
2. Check RPC connectivity: `grep "RPC\|post_rpc" worker_phase2_test.log`
3. Wait longer - real token launches can be sparse

### Phase 2 logs missing
1. Verify retry logic called: `grep "resolving" worker_phase2_test.log`
2. Check syntax: `python3 -m py_compile src/core/pumpfun_curve_listener.py`
3. Restart worker if needed

### High latency (>20s)
1. Expected if Tier 2-3 fallback needed
2. Check rejection reasons to identify bottleneck
3. RPC quota isolation may be protecting discovery (good)

### Crashes
1. Check error: `grep -i "error\|traceback" worker_phase2_test.log`
2. Review Phase 2 implementation: commit 9e5e039
3. Can rollback to Phase 1 if needed

---

## Next Steps

### After Testing Success
1. **Verify Phase 2 is working** - logs show correct tier strategy, rejection reasons, metrics
2. **Review performance** - latency, strategy distribution, bottleneck identification
3. **Decide on production** - if test passes, deploy to production
4. **Monitor production** - collect 100+ tokens for Phase 3 planning

### If Issues Found
1. **Investigate** - check logs, verify Phase 2 code
2. **Fix if needed** - modify code, commit, restart test
3. **Re-test** - repeat monitoring with fix
4. **Deploy when confident** - move to production

---

## File Locations

| File | Purpose |
|------|---------|
| `worker_phase2_test.log` | Live test log (watching) |
| `PHASE2_IMPLEMENTATION_COMPLETE.md` | What was implemented |
| `PHASE2_TEST_DASHBOARD.md` | Test guide & success criteria |
| `/tmp/watch_phase2_test.sh` | Color-coded log watcher |
| `src/core/pumpfun_curve_listener.py` | Phase 2 code (commit 9e5e039) |

---

## Summary

✅ **Phase 2 is deployed and running**
✅ **Worker is healthy and monitoring for migrations**
✅ **Log monitoring is ready**
✅ **Test is waiting for first real token migration**

**Next:** Real token migrations will trigger Phase 2 discovery with:
- Critical-path protection (RPC quota isolation)
- Tier-based retry strategy (TX → light RPC → full RPC)
- Structured rejection logging
- Complete metrics reporting

**Status: ACTIVELY MONITORING** 🔍

