# Phase 2 Test Dashboard

**Test Start:** 2026-03-20 13:04 UTC
**Worker:** Running with Phase 2 code
**Log File:** `worker_phase2_test.log`

---

## Status

### Worker Health ✅
- Process: Running (PID visible in ps output)
- PYTHONPATH: Set correctly
- Syntax: Verified before startup
- Initialization: Complete (Flask, WebSocket, price worker ready)

### Phase 2 Infrastructure ✅
- Critical window tracking: Ready
- RPC quota semaphores: Ready (8 discovery, 2 background)
- Background job queue: Ready
- Tier-based retry logic: Ready

---

## What We're Waiting For

**Real token migrations** from the Solana blockchain. The listener is connected to:
- RPC endpoint: Helius/mainnet
- Webhook system: Listening for PumpFun migrations
- Pool discovery: Primed and ready

When a new token launches with a migration event, Phase 2 will:
1. Detect migration → log `[EVENT] 🚀 MIGRATION DETECTED:`
2. Start critical window → log `[STATE] Token ... → pending`
3. Begin retries → log `[DISCOVERY_T1]`, `[DISCOVERY_T2]`, etc.
4. Log rejection reasons → `[DISCOVERY_TX]`, `[DISCOVERY_RPC]`
5. Report outcome → `[DISCOVERY_SUCCESS]` or `[DISCOVERY_FAILED]`
6. Queue background jobs → `[BACKGROUND] 📤 Queueing...`
7. Final metrics → `[DISCOVERY_METRICS]`

---

## What to Expect

### Per Token Discovery (T=0 to T=60s)

```
T=0.0s   [EVENT] 🚀 MIGRATION DETECTED: 9cjT...
         [STATE] Token 9cjT... → pending

T=0.5s   [DISCOVERY_T1] attempt=1/12 tier=TX_ONLY critical_window=ACTIVE
         [DISCOVERY_TX] attempt=1 candidates_tested=N rejections=...

T=1.0s   [DISCOVERY_T2] attempt=2/12 tier=TX_ONLY critical_window=ACTIVE
         [DISCOVERY_TX] attempt=2 candidates_tested=N rejections=...

T=2.0s   [DISCOVERY_T3] attempt=3/12 tier=TX_ONLY critical_window=ACTIVE

T=4.0s   [DISCOVERY_SUCCESS] attempt=3 elapsed=4.0s
         [STATE] Token 9cjT... → resolved (in 4.0s)
         [DISCOVERY_METRICS] tx_attempts=3 rpc_attempts=0 ...
         [BACKGROUND] 📤 Queueing background tasks (deferred)

T=45.0s  [BACKGROUND] Starting background funding and clustering...
         [FUNDING] Creating... [FUNDER_EXTRACTION] ...
```

### Key Log Patterns to Watch For

**Tier 1 Success (most common):**
```
[DISCOVERY_T3] tier=TX_ONLY
[DISCOVERY_SUCCESS] strategy=tx_parsing elapsed=2.1s
```

**Tier 2 Light RPC:**
```
[DISCOVERY_T6] tier=TX_PLUS_LIGHT_RPC
[DISCOVERY_RPC] strategy=light_rpc
[DISCOVERY_SUCCESS] strategy=rpc_discovery elapsed=15.2s
```

**Tier 3 Full RPC:**
```
[DISCOVERY_T8] tier=TX_PLUS_FULL_RPC
[DISCOVERY_RPC] strategy=full_rpc
[DISCOVERY_SUCCESS] strategy=rpc_discovery elapsed=35.5s
```

**Rejection Reasons:**
- `tx_not_indexed` - TX not yet in Solana ledger (early retries)
- `owner_mismatch` - Pool has wrong owner
- `registration_failed` - Valid pool, registration rejected
- `check_error` - RPC call failed
- `vaults_not_ready` - RPC vaults not available yet

---

## How to Monitor

### Option 1: Simple grep tail (recommended)
```bash
tail -f worker_phase2_test.log | grep -E "\[MIGRATION\]|\[DISCOVERY|\[BACKGROUND\]"
```

### Option 2: Watch full events
```bash
tail -f worker_phase2_test.log | grep -E "EVENT.*MIGRATION|DISCOVERY|resolved|BACKGROUND"
```

### Option 3: Get raw logs
```bash
tail -100 worker_phase2_test.log
```

---

## Test Goals

### Minimum Validation
- [ ] First token migration captured in logs
- [ ] `[DISCOVERY_T*]` retry logs appear
- [ ] Rejection reasons logged
- [ ] `[DISCOVERY_SUCCESS]` or `[DISCOVERY_FAILED]` outcome logged
- [ ] Metrics shown at end

### Performance Verification (after 5+ tokens)
- [ ] Most tokens resolve <15s (Phase 1 benefit)
- [ ] TX parsing dominates (70%+ success rate)
- [ ] Background jobs queued (not immediate)
- [ ] No critical errors or crashes

### Phase 2 Infrastructure Verification (after 10+ tokens)
- [ ] Critical window tracking working (ACTIVE → EXPIRED)
- [ ] RPC quota isolation working (no timeouts, background throttled)
- [ ] Tier strategy followed (Tier 1 tried first, Tier 2-3 as fallback)
- [ ] Rejection codes present and varied
- [ ] Metrics complete and accurate

---

## Expected Timing

**For first token migration:**
- Elapsed detection → resolution: **2-8 seconds** (Tier 1 typical)
- Time to first `[DISCOVERY_T1]` log: **~0.5 seconds**
- Time to metrics output: **~0.1 seconds** after resolution

**For subsequent tokens:**
- Each should follow similar pattern
- Some may take 12-50s (Tier 2-3 fallback)
- Rare outliers >60s (timeout)

---

## If Something Goes Wrong

### No migrations appearing
- **Check:** Is webhook receiving events? Check recent deployment/config
- **Verify:** `tail -f worker_phase2_test.log | grep "WEBHOOK"`
- **Wait:** Real token launches may be sparse

### Phase 2 logs not appearing
- **Check:** Is retry pool discovery being called?
- **Look for:** `[STATE] Token ... → resolving` (triggers retries)
- **Verify:** `grep "\[STATE\]" worker_phase2_test.log`

### Crashes or exceptions
- **Check:** Full error in logs with `[ERROR]` or `Traceback`
- **Verify:** Syntax (already done, but always possible)
- **Rollback:** If needed, can revert to Phase 1 code

### High latency
- **Expected:** 8-12s from Phase 1, potentially 3-8s with Phase 2
- **If >20s:** May indicate RPC quota contention (working as designed, protecting discovery)
- **If consistent timeouts:** Check RPC connectivity

---

## Collecting Test Data

### After first token:
```bash
# Extract resolve time
grep "→ resolved" worker_phase2_test.log | head -1
# Example: [STATE] Token 9cjT... → resolved (in 4.2s)
```

### After 5+ tokens:
```bash
# Calculate average latency
grep "→ resolved" worker_phase2_test.log | \
  sed 's/.*in \([0-9.]*\)s.*/\1/' | \
  awk '{sum+=$1; count++} END {print "Avg: " sum/count "s (n=" count ")"}'
# Expected: 8-12s average
```

### After 10+ tokens:
```bash
# See strategy distribution
grep "DISCOVERY_SUCCESS" worker_phase2_test.log | grep -o "tx_parsing\|rpc_discovery" | \
  sort | uniq -c | awk '{print $2 ": " $1}'
# Expected: tx_parsing ~70-80%, rpc_discovery ~20-30%
```

### Final metrics (any time):
```bash
# See discovery metrics
grep "DISCOVERY_METRICS" worker_phase2_test.log | tail -5
# Shows: tx_attempts, rpc_attempts, rejections breakdown
```

---

## Test Timeline

| Time | Event | Action |
|------|-------|--------|
| T+0 | Worker starts | Monitor logs begin |
| T+5m | First token? | Start collecting data |
| T+20m | 5+ tokens? | Verify latency improving |
| T+40m | 10+ tokens? | Analyze strategy distribution |
| T+60m | Test decision | Evaluate Phase 2 success |

---

## Success Criteria

### Phase 2 is working if:
1. ✅ Retries shown with tier labels (TX_ONLY → light/full RPC)
2. ✅ Rejection reasons logged (not just "no accounts passed validation")
3. ✅ Most tokens resolved via TX parsing (70%+)
4. ✅ Some tokens fall back to RPC (20-30%)
5. ✅ Latency is 8-12s for Phase 1 benefit (target 3-8s with Phase 2 RPC fallback)
6. ✅ Background jobs queued, not immediate (see [BACKGROUND] logs)
7. ✅ Metrics complete at end of each discovery

### Phase 2 is NOT working if:
- ❌ No retry logs appear (discovery not happening)
- ❌ All tokens timeout (60s+)
- ❌ Crashes or exceptions in logs
- ❌ Rejection logging missing (old format)
- ❌ Background jobs causing RPC contention (see RPC timeouts)

---

## Notes

- **Real data is better than simulation** - Current setup waits for actual token launches
- **May need to wait** - Token launches can be sparse; even 1-2 tokens gives good signal
- **Phase 2 infrastructure is silent when not needed** - Only logs when discovering
- **Background jobs deferred** - Won't see activity until after critical window expires (45s)

---

**Test Status: READY** - Waiting for first token migration from blockchain

