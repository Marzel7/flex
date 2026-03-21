# Phase 2B: Failure Reason Routing & Caching Implementation Plan

**Status:** Ready for implementation
**Estimated effort:** 4-6 hours for all 3 critical fixes
**Dependencies:** Completed (bonding_curve/creator now passed to follow-on)

---

## Overview

Implement the 3 critical bug fixes that enable smart routing based on diagnostic reason codes:

1. **Fix #1:** Store actual diagnostic reason instead of "tx_not_indexed" (~5 lines)
2. **Fix #2:** Cache diagnostics to avoid repeated parsing (~15 lines)
3. **Fix #3:** Skip RPC for zero-candidate reasons that won't help (~10 lines)

---

## Implementation Sequence

### Step 1: Add diagnostics cache to listener (5 min)

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** `__init__` method, around line 240

**Add:**
```python
# Cache for immutable cached-TX diagnostic results (avoid re-parsing on every retry)
self.cached_tx_diagnostics_cache: Dict[str, Dict] = {}
```

### Step 2: Fix failure reason classification (10 min)

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** Line ~2870 (inside `_retry_pool_discovery`, within TX parsing block)

**Find this block:**
```python
# If no follow-on or follow-on failed, try RPC fetch
if not pool_candidates:
    using_cached_payload = tx_data is not None
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
        mint=mint,
        migration_sig=original_migration_sig,
        tx_data=tx_data  # Pass cached TX to avoid redundant fetch
    )
```

**Replace with:**
```python
# If no follow-on or follow-on failed, try RPC fetch
if not pool_candidates:
    using_cached_payload = tx_data is not None

    # Capture actual diagnostic reason (if available) instead of defaulting to 'tx_not_indexed'
    if cached_candidate_count == 0 and cached_diagnostics:
        actual_zero_reason = cached_diagnostics.get('reason_code', 'unknown')
        rejection_reasons.append(actual_zero_reason)
    else:
        rejection_reasons.append('tx_not_indexed')

    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
        mint=mint,
        migration_sig=original_migration_sig,
        tx_data=tx_data
    )
```

### Step 3: Implement diagnostics caching (15 min)

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** Line ~2798 (inside TX parsing tier, before parse_candidates_from_cached_tx)

**Find this block:**
```python
candidates_from_cached = []
cached_tx_parsed = False
cached_candidate_count = 0

cached_diagnostics = {}
if tx_data is not None:
    # Use cached-only parsing: no RPC, no fallback
    candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = await discovery.parse_candidates_from_cached_tx(tx_data)
```

**Replace with:**
```python
candidates_from_cached = []
cached_tx_parsed = False
cached_candidate_count = 0
cached_diagnostics = {}

if tx_data is not None:
    # Check if we already parsed this cached TX before
    if mint in self.cached_tx_diagnostics_cache:
        # Reuse cached diagnostic result
        cached_diagnostics = self.cached_tx_diagnostics_cache[mint]
        cached_tx_parsed = True
        cached_candidate_count = 0  # We already know it's zero, that's why we cached it
        candidates_from_cached = []
        log_print(
            f"{Colors.DISCOVER}[CACHED_TX_PARSE] Using cached result for {mint[:8]}... reason={cached_diagnostics.get('reason_code', 'unknown')}{Colors.RESET}",
            flush=True
        )
    else:
        # Parse for first time
        candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
            await discovery.parse_candidates_from_cached_tx(tx_data)

        # Store the diagnostic result if zero candidates (immutable cached TX, won't change on retry)
        if cached_candidate_count == 0 and cached_diagnostics:
            self.cached_tx_diagnostics_cache[mint] = cached_diagnostics
            log_print(
                f"{Colors.DISCOVER}[CACHED_TX_PARSE] Cached result for {mint[:8]}... reason={cached_diagnostics.get('reason_code', 'unknown')}{Colors.RESET}",
                flush=True
            )
```

### Step 4: Implement reason-based RPC routing (15 min)

**File:** `src/core/pumpfun_curve_listener.py`
**Location:** Line ~2900 (before `if run_rpc:` block in RPC fallback tier)

**Find:**
```python
                # ===== TIER: RPC FALLBACK =====
                if run_rpc:
                    try:
```

**Add before the `if run_rpc:` line:**
```python
                # Route RPC based on cached TX diagnostic reason
                # Skip RPC for reasons that definitely won't help
                if run_rpc and cached_candidate_count == 0 and cached_diagnostics:
                    reason_code = cached_diagnostics.get('reason_code', 'unknown')

                    if reason_code in ['no_amm_program_in_tx', 'inner_instructions_only']:
                        # Pool creation in follow-on TX, RPC won't help
                        # Skip RPC and rely on follow-on discovery
                        log_print(
                            f"{Colors.DISCOVER}[DISCOVERY_ROUTE] Skipping RPC (reason={reason_code}, needs follow-on){Colors.RESET}",
                            flush=True
                        )
                        run_rpc = False
                    elif reason_code == 'meta_incomplete' and attempt < 7:
                        # Metadata still indexing, RPC will fail
                        # Wait for next retry in a few seconds
                        log_print(
                            f"{Colors.DISCOVER}[DISCOVERY_ROUTE] Skipping RPC (reason={reason_code}, metadata not ready){Colors.RESET}",
                            flush=True
                        )
                        run_rpc = False

```

### Step 5: Verify syntax and test

```bash
# Syntax check
python3 -m py_compile src/core/pumpfun_curve_listener.py

# Check no unintended changes
git diff src/core/pumpfun_curve_listener.py | head -200

# Commit
git add src/core/pumpfun_curve_listener.py
git commit -m "fix: Implement failure reason classification, diagnostics caching, and reason-based routing

Fixes 3 critical bugs in failure handling:

1. Store actual diagnostic reason codes instead of blanket 'tx_not_indexed'
   - no_amm_program_in_tx: pool in follow-on TX
   - inner_instructions_only: pool in CPI
   - meta_incomplete: metadata not indexed yet
   Impact: Enables correct routing strategy selection

2. Cache immutable cached-TX diagnostics across retries
   - Avoid re-parsing static TX on attempts 2-12
   - Reuse diagnostic reason on subsequent retries
   Impact: Saves CPU, faster logging, enables telemetry

3. Implement reason-based RPC routing
   - Skip RPC when reason indicates follow-on is only option
   - Skip RPC when metadata not ready (wait strategy better)
   Impact: RPC quota conservation, faster resolution

Changes:
- Add cached_tx_diagnostics_cache dict to __init__
- Check cache before parsing cached TX
- Store zero-candidate diagnostics after first parse
- Use actual reason code in rejection metrics
- Skip RPC for no_amm_program_in_tx and inner_instructions_only
- Skip RPC for meta_incomplete in early attempts

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

## Rollout Strategy

### Phase 2B-1 (Immediate - 1 hour)
Implement all 3 fixes above in sequence. Verify syntax. No feature flags needed (all fixes are backwards compatible).

### Phase 2B-2 (Validation - 2 hours)
Restart listener, collect telemetry:
- Log grep to verify caching is working
- Database query to confirm reason codes are correct
- Measure improvement in RPC quota usage
- Check follow-on success rate for no_amm_program_in_tx tokens

### Phase 2B-3 (Later - architectural)
Consider Fix #2 from analysis (unify discovery paths) if issues remain.

---

## Testing Checklist

After implementing:

```bash
# 1. Check cache initialization
grep "cached_tx_diagnostics_cache" src/core/pumpfun_curve_listener.py

# 2. Verify all 3 edits were applied
grep -n "cached_tx_diagnostics_cache\[mint\]" src/core/pumpfun_curve_listener.py  # Should show 2+ matches
grep -n "actual_zero_reason\|reason_code" src/core/pumpfun_curve_listener.py | grep -E "rejection_reasons|DISCOVERY_ROUTE"

# 3. Run listener and collect data (15-30 min)
source .env
python3 src/main.py  # Ctrl+C after 30 min

# 4. Verify cache is being used
grep "CACHED_TX_PARSE.*Using cached result" listener.log | wc -l  # Should be > 0 after 30 min

# 5. Verify reason-based routing is working
grep "DISCOVERY_ROUTE.*Skipping RPC" listener.log | wc -l  # Should be > 0

# 6. Confirm no new errors
grep "ERROR\|EXCEPTION" listener.log | tail -20

# 7. Measure RPC savings
before_rpc_calls=$(grep -c "DISCOVERY_RPC\|call_discovery_rpc" listener.log | head -1)
echo "RPC calls made: $before_rpc_calls (should be lower than without reason-based routing)"
```

---

## Expected Impact

**Before fixes:**
- Token with `no_amm_program_in_tx` tries RPC on attempt 2-7 (wastes quota, fails)
- Cached TX re-parsed 12 times (same result, wasted work)
- Failure classified as `tx_not_indexed` (misleading)
- Resolution time: 30-50 seconds (multiple wrong strategies)

**After fixes:**
- Token with `no_amm_program_in_tx` skips RPC, goes to follow-on (attempt 4+)
- Cached TX parsed once, result reused (fast)
- Failure classified correctly for telemetry
- Resolution time: 15-25 seconds (correct strategy, faster)

---

## Rollback Plan

If issues arise, revert is clean (only additions + conditional logic):

```bash
git revert HEAD  # Removes all changes
```

No database migrations needed. No state corruption risk.

---

## Next Steps After This Phase

Once these 3 fixes are validated, consider:
1. Unifying discovery paths (Fix #2 from analysis)
2. Adding anchor reliability tracking
3. Measuring resolution time improvements
4. Optimizing follow-on search depth based on anchor
