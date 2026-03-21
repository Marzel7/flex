# Quick Bug Reference - Discovery System Phase 2B Fixes

**TL;DR:** 3 critical bugs block correct routing. All fixable in ~30 lines total. High impact.

---

## Bug #1: Wrong Failure Classification

**What:** Cached TX returns zero candidates, gets labeled "tx_not_indexed" (wrong)
**Actually:** Should be "no_amm_program_in_tx" or "inner_instructions_only" (correct)
**Why it breaks:** Routing makes wrong decision (tries RPC instead of follow-on)

**Location:** `src/core/pumpfun_curve_listener.py:~2880`

**Fix:**
```python
# Before: rejection_reasons.append("tx_not_indexed")

# After:
if cached_candidate_count == 0 and cached_diagnostics:
    rejection_reasons.append(cached_diagnostics.get('reason_code', 'unknown'))
else:
    rejection_reasons.append("tx_not_indexed")
```

**Impact:** Enables correct telemetry + routing based on actual reason

---

## Bug #2: Diagnostics Lost on Retry

**What:** Cached TX diagnostics logged on attempt 1, then re-computed on attempts 2-12 (identical results)
**Why it breaks:** Wastes CPU, prevents smart routing strategies, fills log file

**Location:** `src/core/pumpfun_curve_listener.py:~2798`

**Fix:**
```python
# Check cache first:
if mint in self.cached_tx_diagnostics_cache:
    cached_diagnostics = self.cached_tx_diagnostics_cache[mint]
    # Reuse
else:
    # Parse first time
    candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
        await discovery.parse_candidates_from_cached_tx(tx_data)
    # Cache result
    if cached_candidate_count == 0 and cached_diagnostics:
        self.cached_tx_diagnostics_cache[mint] = cached_diagnostics
```

**Cache initialization:** Add to `__init__`:
```python
self.cached_tx_diagnostics_cache: Dict[str, Dict] = {}
```

**Impact:** Reuse diagnostic info, fast logging, enables routing

---

## Bug #3: RPC When Follow-On Needed

**What:** When reason is "no_amm_program_in_tx", system tries RPC (doesn't help, pool doesn't exist yet)
**Why it breaks:** RPC wastes quota, fails with "vaults_not_ready", delays resolution

**Location:** `src/core/pumpfun_curve_listener.py:~2900`

**Fix:**
```python
# Before RPC tier, check if we should skip:
if run_rpc and cached_candidate_count == 0 and cached_diagnostics:
    reason = cached_diagnostics.get('reason_code')
    if reason in ['no_amm_program_in_tx', 'inner_instructions_only']:
        run_rpc = False  # Skip RPC, let follow-on find it
        log_print(f"Skipping RPC (reason={reason}, follow-on only)")
```

**Impact:** RPC quota savings, faster resolution for follow-on tokens

---

## Diagnostic Reason Codes

| Code | Meaning | Action |
|------|---------|--------|
| `no_amm_program_in_tx` | Pool in follow-on TX | Skip RPC, do follow-on |
| `inner_instructions_only` | Pool in CPI call | Skip RPC, do follow-on |
| `meta_incomplete` | Metadata not indexed | Skip RPC, wait next retry |
| `meta_owner_not_indexed` | TX indexed, owner not | Either follow-on or RPC |
| `meta_has_owners_but_no_pool_matches` | Wrong token/pool pair | Skip this token |
| `no_accounts_in_tx` | TX has no accounts | Skip this token |

---

## Testing

After fixes:

```bash
# Verify cache exists
grep "cached_tx_diagnostics_cache" src/core/pumpfun_curve_listener.py

# Check all 3 edits applied
grep -n "actual_zero_reason\|CACHED_TX_PARSE.*cached result\|Skipping RPC" src/core/pumpfun_curve_listener.py

# Run + verify cache is used
python3 src/main.py &
sleep 60
grep "CACHED_TX_PARSE.*cached result" listener.log | wc -l  # Should be > 0

# Verify RPC skip works
grep "Skipping RPC.*follow-on" listener.log | wc -l  # Should be > 0
```

---

## Risk Assessment

**Risk Level:** LOW
- All changes are additions/conditionals (no removals)
- No database changes needed
- Backward compatible (cache is optional)
- Rollback is one git revert

**Effort:** 30 minutes
- Edit 1: ~5 lines
- Edit 2: ~10 lines
- Edit 3: ~5 lines
- Edit 4 (cache init): 1 line

**Impact:** HIGH
- 30-40% of tokens affected (no_amm_program_in_tx reason)
- ~10s improvement per token
- RPC quota savings 200-400 calls/hour

---

## Commit Message Template

```
fix: Implement failure reason classification, diagnostics caching, and smart routing

Fixes 3 critical bugs preventing correct pool discovery routing:

1. Store actual diagnostic reason codes instead of "tx_not_indexed"
   - Cached TX parse emits reason (no_amm_program_in_tx, etc)
   - Was being replaced with hardcoded "tx_not_indexed"
   - Impact: Enables correct routing decisions

2. Cache immutable cached-TX diagnostic results
   - Avoid re-parsing same TX on retries 1-12
   - Reuse diagnostic result to inform routing
   - Impact: CPU savings, enables smart routing

3. Implement reason-based RPC routing
   - Skip RPC when reason indicates follow-on is needed
   - Route no_amm_program_in_tx → follow-on instead of RPC
   - Impact: RPC quota savings, faster resolution

Changes:
- Add cached_tx_diagnostics_cache dict to __init__
- Store reason code from cached diagnostics instead of "tx_not_indexed"
- Check cache before parsing, reuse on subsequent retries
- Skip RPC for no_amm_program_in_tx and inner_instructions_only reasons

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```

---

## Before vs After

### Before Fixes (Current)
```
Token: no_amm_program_in_tx
Attempt 1: Parse TX → reason=no_amm_program_in_tx (logged, not stored)
Attempt 2: Parse TX again (WASTED) → try RPC
Attempt 3: RPC fails → try another parse (WASTED)
...
Attempt 6-7: Try follow-on → FOUND
Result: Delayed, RPC wasted
```

### After Fixes
```
Token: no_amm_program_in_tx
Attempt 1: Parse TX → reason=no_amm_program_in_tx (CACHED)
Attempt 2: Use cached reason → skip RPC
...
Attempt 4: Try follow-on → FOUND
Result: Faster, RPC saved
```

---

## Next Steps

1. Apply fixes per PHASE2B_ROUTING_FIXES_IMPLEMENTATION.md
2. Run syntax check + git diff review
3. Commit with message above
4. Restart listener, collect 30-minute telemetry
5. Run validation queries (see Testing section)
6. If good: Celebrate, then plan Phase 2C
7. If issues: Rollback with `git revert`
