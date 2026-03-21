# Follow-On Discovery Diagnostic Setup

## Status: ✅ READY FOR VALIDATION

All critical fixes have been deployed with comprehensive diagnostic logging.

### Code Deployed

**Commits:**
- `93d947d` - Phase 3 critical fixes (search direction, RPC budget per-anchor, time window)
- `fc24b49` - Diagnostic logging in post_migration_pool_discovery.py
- `98d255f` - Trigger condition logging in pumpfun_curve_listener.py

### What to Look For

When a NEW TOKEN launches with migration detected:

#### Stage 1: Migration Detection
```
Look for: [MIGRATION_DETECTED] or migration processing logs
```

#### Stage 2: Follow-On Trigger Check
```
Look for: [FOLLOW_ON_CHECK] mint=... follow_on_max_txs=? tx_data=? cached_count=?

Expected values (should trigger follow-on):
- follow_on_max_txs > 0 ✅ (should be 20)
- tx_data = True ✅ (migration TX should be cached)
- cached_count = 0 ✅ (zero candidates from initial parse)

If any is False → follow-on won't run, investigate that path
```

#### Stage 3: Follow-On Starting
```
Look for: [FOLLOW_ON_DISCOVERY] Starting search for ...mint=... curve=... creator=... window=30s

This log should appear if trigger conditions are met.
If not → check conditions from Stage 2
```

#### Stage 4: Anchor Scanning
```
Look for: [FOLLOW_ON_DISCOVERY] Scanning anchor=bonding_curve (...)
          [FOLLOW_ON_DISCOVERY] Scanning anchor=creator (...)
          [FOLLOW_ON_DISCOVERY] Scanning anchor=mint (...)

These show which anchors are being searched.
If only bonding_curve and RPC budget exhausted → creator never searched (bug #6 not fixed)
```

#### Stage 5: Signature Fetching
```
Look for: [FOLLOW_ON_DISCOVERY] Found X signatures for anchor=...

X should be > 0 for at least one anchor.
If all are 0 → either no TXs or anchor addresses wrong
```

#### Stage 6: Time Window Filtering
```
Look for: [FOLLOW_ON_DISCOVERY] Skipped ...sig... time_diff=?s (outside 30s window)

This shows TXs being filtered by time.
If too many "Skipped" → might need to increase time window
```

#### Stage 7: Candidate Extraction
```
Look for: [FOLLOW_ON_DISCOVERY] Found candidate ...address... from anchor=... at offset=?

Offset should be 1, 2, 3, etc (shows which TX in sequence)
If none → extraction not finding candidates in TXs
```

#### Stage 8: Candidate Validation
```
Look for: [FOLLOW_ON_DISCOVERY] Candidate ...address... owner=... anchor=...

Shows owner being checked.
If owner not in known programs → will be rejected next
```

#### Stage 9: Valid Pool Found (SUCCESS)
```
Look for: [FOLLOW_ON_DISCOVERY] ✅ Found valid pool ...address... via anchor=...

This is the SUCCESS case!
Pool should be registered and resolution_source should be 'follow_on'
```

#### Stage 10: No Pool Found (EXHAUSTED)
```
Look for: [FOLLOW_ON_DISCOVERY] No pool found after scanning ... TXs (...  RPC calls)

Shows why search failed (time window, owner mismatch, extraction issues, etc)
Falls through to RPC fallback
```

### Monitoring Commands

```bash
# Real-time monitoring (shows all diagnostic logs)
./monitor_follow_on.sh

# Status check (database metrics)
./check_follow_on_status.sh

# Watch specific stage
tail -f listener.log | grep "FOLLOW_ON_CHECK"
tail -f listener.log | grep "Starting search"
tail -f listener.log | grep "Found candidate"
tail -f listener.log | grep "Found valid pool"
```

### Quick Diagnostic Checklist

When follow-on doesn't find a pool:

1. **Is follow-on being triggered?**
   ```bash
   grep "FOLLOW_ON_CHECK" listener.log | head -1
   ```
   - If none: trigger condition not met (check follow_on_max_txs, tx_data, cached_count)

2. **Are anchors being searched?**
   ```bash
   grep "Scanning anchor" listener.log | tail -5
   ```
   - If none: follow-on code not running
   - If only bonding_curve: creator not reached (RPC budget issue)

3. **Are signatures being fetched?**
   ```bash
   grep "Found.*signatures for" listener.log | tail -5
   ```
   - If 0 signatures: anchor addresses might be wrong

4. **Are candidates being extracted?**
   ```bash
   grep "Found candidate" listener.log | tail -5
   ```
   - If none: extraction not working (owner mismatch issue)

5. **Are candidates being validated?**
   ```bash
   grep "Candidate.*owner=" listener.log | tail -10
   ```
   - Shows which owners are found
   - If all rejected: owner list incomplete or wrong

6. **Are TXs being filtered by time?**
   ```bash
   grep "Skipped.*time_diff" listener.log | wc -l
   ```
   - If many: time window might be too strict
   - Check actual time_diff values

### Expected Timeline

When token launches with pool in +1 or +2 TX after migration:

```
T+0.0s:    Migration detected
T+0.5s:    Follow-on search triggered
T+2.0s:    Bonding curve signatures fetched (1-2 RPC calls)
T+3.0s:    Pool found in +1 TX, validated
T+3.5s:    ✅ Follow-on discovery SUCCESS
           Pool registered with resolve_source='follow_on'
           Resolution time: 3.5 seconds
```

### Expected Success Rate

After fixes, expect:
- **Tokens with no_amm_program_in_tx:** 20-30% success via follow-on
- **Average resolution time:** 3-5 seconds (vs 15+ via RPC)
- **Creator anchor usage:** When bonding_curve fails, creator searched
- **RPC quota savings:** 50-70% reduction for follow-on tokens

### System is Ready

Listener is running with all:
- ✅ Critical code fixes
- ✅ Diagnostic logging at all decision points
- ✅ Monitoring scripts configured
- ✅ Database tracking enabled

**Next Step:** Wait for new token migrations and monitor diagnostics
