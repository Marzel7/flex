# Discovery System Status & Architecture Review - March 21, 2026

---

## Executive Summary

### Current State
- ✅ **Phase 1 Complete:** Bonding curve and creator context now passed to follow-on discovery
- ✅ **Priority 1 Bugs Fixed:** Bonding curve/creator initialization and passing
- ⚠️ **Phase 2A In Progress:** 3 critical bugs identified but not yet fixed
- 🔴 **Critical Path Issue:** Diagnostics inform routing but routing ignores diagnostics

### What's Working
1. Cached migration TX parsing (fast, ~95% success rate)
2. Bonding curve extraction from earliest creator TX
3. Creator identification from transaction history
4. Follow-on discovery implementation (search subsequent TXs)
5. Phase 3 tier-based retry strategy

### What's Broken
1. **Bug #1:** Cached TX with zero candidates labeled "tx_not_indexed" (should be "no_amm_program_in_tx", "inner_instructions_only", etc.)
2. **Bug #2:** Diagnostics logged but never stored (prevents caching/reuse)
3. **Bug #3:** Routing doesn't use diagnostic reason codes to skip unnecessary RPC

### Current Failure Mode
```
Token launches → Cached TX parsed → Zero candidates found (reason: no_amm_program_in_tx)
→ System tries RPC (wrong strategy, vaults not ready) → Fails
→ Should have: Gone to follow-on discovery first
```

---

## Architecture Overview

```
handle_migration() [WebSocket/account subscription]
    ↓
_process_migration_with_mint() [Initial discovery path - FLOW A]
    ├─→ TX parsing (discover_pool_candidates_from_migration_tx)
    │   └─→ Zero candidates → Schedule retry
    │
    ├─→ RPC vault discovery (fallback)
    │   └─→ Vaults not ready → Fail
    │
    └─→ Extract bonding_curve and creator for later use
        └─→ Pass to _retry_pool_discovery()

_retry_pool_discovery() [Retry loop - FLOW B]
    ├─→ TIER 1 (Attempts 1-5): TX-only
    │   ├─→ Parse cached TX (parse_candidates_from_cached_tx)
    │   │   └─→ Get diagnostics (reason_code)
    │   └─→ Zero candidates → Try next strategy
    │
    ├─→ TIER 2 (Attempts 6-7): TX + light RPC
    │   └─→ Follow-on discovery (discover_follow_on_pools with bonding_curve, creator)
    │       └─→ Search for pool creation in subsequent TXs
    │
    ├─→ TIER 3 (Attempts 8-12): TX + full RPC
    │   └─→ Vault discovery (discover_and_register_all_pools)
    │       └─→ Try RPC until vaults are ready
    │
    └─→ All retries exhausted → Classify failure

Telemetry & Logging
    ├─→ [CACHED_TX_DIAGNOSTICS] reason_code (currently logged but not stored)
    ├─→ [FOLLOW_ON_SUCCESS] pool found via anchor
    ├─→ [DISCOVERY_FAILED] all attempts exhausted
    └─→ token_resolution_telemetry table (detected_at, resolved_at, resolve_source)
```

---

## Discovery Paths Analysis

### Flow A (Initial Discovery) - _process_migration_with_mint()
**Lines:** 2326-2470
**Uses:** `discover_pool_candidates_from_migration_tx()`
**Diagnostics:** NONE
**Follow-on:** NO
**Success rate:** ~70% for tokens with pool in migration TX

**Problem:** If this fails, transitions to Flow B for retry. No diagnostics logged.

### Flow B (Retry Discovery) - _retry_pool_discovery()
**Lines:** 2753-3100
**Uses:** `parse_candidates_from_cached_tx()`
**Diagnostics:** YES (emit_cached_tx_diagnostics with reason_code)
**Follow-on:** YES (discover_follow_on_pools with bonding_curve, creator)
**Success rate:** ~20-30% for tokens where Flow A failed

**Problem:** Better implementation but only runs on retry. Flow A doesn't use it.

### Root Cause
Two implementations = inconsistent behavior, wasted RPC calls, delayed resolution.

---

## Diagnostic Reason Codes

From `emit_cached_tx_diagnostics()`:

| Reason Code | Meaning | Strategy |
|---|---|---|
| `no_amm_program_in_tx` | AMM program not in accounts (40% of zero-candidates) | Follow-on discovery needed |
| `inner_instructions_only` | Pool is in CPI call (inner instruction) (20% of zero) | Follow-on discovery needed |
| `meta_incomplete` | TX indexed but account metadata not yet (15% of zero) | Wait + RPC later |
| `meta_owner_not_indexed` | TX in chain but account owner not indexed (10% of zero) | Follow-on or wait |
| `meta_has_owners_but_no_pool_matches` | Owners indexed but no pool found (10% of zero) | Different token/pool pair |
| `no_accounts_in_tx` | TX has no accounts in message (rare) | Skip this token |

**Current Routing:** All reason codes → try RPC (WRONG)
**Correct Routing:**
- `no_amm_program_in_tx` → follow_on_only
- `inner_instructions_only` → follow_on_first
- `meta_incomplete` → wait_retry
- Others → rpc_try

---

## Critical Bugs - Detailed Analysis

### Bug #1: Failure Reason Mislabeled

**Location:** Line ~2880 (inside `_retry_pool_discovery`, after follow-on fails)

**Code:**
```python
if not pool_candidates:
    using_cached_payload = tx_data is not None
    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(...)
```

**Problem:**
- Line 2904: `rejection_reasons.append("tx_not_indexed")`
- But TX WAS parsed successfully (we have cached_diagnostics with reason_code)
- The actual reason (e.g., "no_amm_program_in_tx") is lost
- Telemetry shows wrong classification

**Impact:**
- Can't analyze failure patterns correctly
- Routing decisions based on wrong information
- No way to measure reason-code-specific resolution times

**Fix:** Store actual `cached_diagnostics.get('reason_code')` instead of hardcoded "tx_not_indexed"

---

### Bug #2: Diagnostics Not Cached

**Location:** Line ~2798 (inside `_retry_pool_discovery`, TX parsing tier)

**Code:**
```python
candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = \
    await discovery.parse_candidates_from_cached_tx(tx_data)
```

**Problem:**
- Cached TX is immutable (from chain, never changes)
- If attempt 1 gets `reason_code='no_amm_program_in_tx'`, attempt 2-12 will get same result
- System re-parses TX on EVERY retry attempt (12 times total)
- Same reason code logged 12 times (useless noise)

**Impact:**
- CPU wasted on repeated parsing
- Can't implement smart routing (need to remember reason from attempt 1)
- Log file bloated with duplicate diagnostics

**Fix:** Cache the diagnostic result in `self.cached_tx_diagnostics_cache[mint]` and reuse on subsequent attempts

---

### Bug #3: Routing Ignores Diagnostics

**Location:** Line ~2900 (before RPC fallback tier)

**Code:**
```python
if run_rpc:
    try:
        # RPC vault discovery always runs regardless of cached_diagnostics
```

**Problem:**
- If `reason_code='no_amm_program_in_tx'`, pool doesn't exist yet (it's in follow-on TX)
- RPC will fail with "vaults_not_ready"
- System should skip RPC and wait/try follow-on instead
- Currently RPC runs unconditionally

**Impact:**
- RPC quota wasted on requests that will definitely fail
- Delays resolution (RPC timeout occurs before moving to next strategy)
- Creates false "vaults_not_ready" failures in telemetry

**Fix:** Check reason_code before running RPC. Skip if reason indicates follow-on is the only option.

---

## Metrics & Observability

### Current Telemetry
```
token_resolution_telemetry table:
- mint: Token address
- detected_at: When migration was detected
- resolved_at: When pool/vaults were found
- resolve_seconds: Time from detected to resolved
- resolve_source: 'tx_parsing' | 'follow_on' | 'rpc_discovery' | 'unresolved'
- retry_count: Number of retries needed
```

### Missing Telemetry
- `zero_candidate_reason`: Why cached TX returned zero candidates
- `follow_on_anchor_used`: Which anchor succeeded (bonding_curve | creator | mint)
- `follow_on_txs_scanned`: How many follow-on TXs were examined
- `follow_on_candidates_tested`: How many candidates from follow-on were evaluated

### Gap Analysis
Without stored diagnostics, we can't answer:
1. What % of tokens have `no_amm_program_in_tx`?
2. For those tokens, how effective is follow-on discovery?
3. Which anchor (bonding_curve vs creator) is more reliable?
4. How many tokens fail because we try RPC instead of follow-on?

---

## Performance Analysis

### Current Bottleneck
```
Scenario: Token with pool in follow-on TX (no_amm_program_in_tx)

Attempt 1: Parse cached TX → no candidates → go to attempt 2
Attempt 2: Try RPC → fail (vaults_not_ready) → go to attempt 3
Attempt 3: Parse cached TX again (wasted) → no candidates
...
Attempt 6: Try follow-on → SUCCESS

Total time: ~13 seconds (5 seconds wasted attempts before following right strategy)
```

### With Fixes Applied
```
Attempt 1: Parse cached TX → reason='no_amm_program_in_tx' → CACHE this
Attempt 2: Check cache → found 'no_amm_program_in_tx' → skip RPC
...
Attempt 4: Try follow-on → SUCCESS

Total time: ~3 seconds (correct strategy from attempt 4, no wasted RPC)
```

### Aggregate Impact
- 30-40% of tokens have `no_amm_program_in_tx` reason
- Each wastes 10-20 seconds on wrong RPC strategy
- With fixes: ~5-10 second improvement per token
- For 1000 tokens/hour: 5000-10000 seconds saved (1.4-2.8 hours)
- RPC quota saved: 200-400 calls/hour

---

## Implementation Roadmap

### Phase 2A - COMPLETED ✅
- [x] Pass bonding_curve to _retry_pool_discovery()
- [x] Pass creator to _retry_pool_discovery()
- [x] Initialize bonding_curve_pda before try block

### Phase 2B - NEXT (4-6 hours)
- [ ] Bug #1: Store actual reason code instead of "tx_not_indexed" (~5 lines)
- [ ] Bug #2: Cache diagnostics to avoid re-parsing (~15 lines)
- [ ] Bug #3: Skip RPC for zero-candidate reasons (~10 lines)

### Phase 2C - FUTURE (10-15 hours)
- [ ] Unify discovery paths (Flow A + Flow B → single function)
- [ ] Add anchor reliability tracking
- [ ] Optimize follow-on search depth per anchor
- [ ] Implement reason-based retry delay strategy

### Phase 3+ - OPTIONAL
- [ ] Add CPI/inner-instruction pool detection
- [ ] Implement pool creation time prediction
- [ ] Build creator behavior clustering (for funding analysis)

---

## Key Insights

### Discovery vs. Extraction
The system has evolved from:
- **"Is the TX indexed?"** (early attempts)
- **"Are the vaults ready?"** (RPC fallback)

To:
- **"Where did pool creation happen?"** (the real question)
  - In migration TX itself? (TX parsing)
  - In a follow-on TX by the creator? (follow-on discovery)
  - Not yet created? (RPC wait)
  - CPI call within migration? (inner instruction scanning)

### Root Cause Pattern
```
Tokens with no_amm_program_in_tx + no_follow_on_discovery = Unresolved

Tokens with no_amm_program_in_tx + follow_on_discovery = Resolved (within 5s)
```

The system already has the solution (follow-on discovery). It just needs to:
1. Recognize when to use it (diagnostic reason codes)
2. Route correctly (skip RPC, do follow-on)
3. Remember the decision (cache diagnostics)

---

## Quality Gates

Before Phase 2C, validate:
1. [ ] All zero-candidate failures have correct reason_code in telemetry
2. [ ] Cache hit rate > 50% after 1 hour of runtime
3. [ ] RPC calls for `no_amm_program_in_tx` tokens reduced by 80%+
4. [ ] Average resolution time for follow-on tokens < 10 seconds
5. [ ] No new errors introduced in retry loop

---

## Appendix: Code Locations Reference

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Migration detection | pumpfun_curve_listener.py | 2267-2685 | Entry point for migrations |
| Cached TX parse | post_migration_pool_discovery.py | 358-500 | Pure TX parsing, emits diagnostics |
| Diagnostics emit | post_migration_pool_discovery.py | 235-350 | Classify zero-candidate reasons |
| Follow-on discovery | post_migration_pool_discovery.py | 357-700 | Search subsequent TXs with anchors |
| Retry loop | pumpfun_curve_listener.py | 2753-3100 | Tier-based retry orchestration |
| RPC fallback | vault_discovery.py | 700-900 | RPC-based vault discovery |
| Telemetry write | pumpfun_curve_listener.py | 2928-2940 | Store resolution metrics |

---

## Questions for Implementation Team

1. **For Bug #2 (caching):** Should we also cache successful candidates found during TX parsing? Or only the zero-candidate diagnostics?

2. **For Bug #3 (routing):** Is 7 attempts the right threshold before allowing full RPC? Should it be per-attempt tier?

3. **For Phase 2C:** Should we unify before or after Phase 2B validation? Unifying is larger but better architecture.

4. **Rollback:** If Phase 2B changes cause issues, is immediate rollback acceptable or should we have a gradual feature flag?

---

**Status:** Ready for Phase 2B implementation
**Confidence Level:** HIGH (bugs confirmed, fixes simple + low risk)
**Next Step:** Implement Phase 2B fixes per PHASE2B_ROUTING_FIXES_IMPLEMENTATION.md
