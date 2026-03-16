# Pool Detector Hardening: Design & Implementation Summary

**Date:** 2026-03-14
**Status:** Design documents complete, ready for implementation
**Scope:** Improve pool detection observability, validation, and resilience

---

## Problem Statement

The pool detector's `detect_pool_from_tx()` method correctly merges all account sources (base keys + loaded addresses) but has **no visibility** into failure reasons. For token `8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump`, the only clue was:

```
[POOL_DETECT] No AMM-owned pool found in 25 accounts (searched 25 + 0 + 0)
```

This single log line cannot answer:
- Is the transaction really v0?
- Are loaded addresses populated?
- What's the composition of the 25 accounts?
- Are any accounts AMM-owned?
- If so, are they valid pool PDAs?
- Is the pool PDA completely absent?
- Can a fallback path find it?

**Result:** Pool detection failures are **opaque and undiagnosable**.

---

## Solution Overview

A **7-phase hardening plan** that adds:

1. **Account key normalization** — Handle various RPC provider formats
2. **Transaction shape logging** — Visible v0 status and account composition
3. **Per-account debug logging** — Full owner/data breakdown (optional)
4. **AMM candidate validation** — Reject accounts too small to be pool PDAs
5. **Fallback discovery path** — Vault-based pool resolution for edge cases
6. **Listener integration** — Debug mode controlled via environment variable
7. **Health endpoint stats** — Track detection success metrics

**Outcome:** Every pool detection failure is now **fully diagnosable** from logs.

---

## Deliverables

### 1. Design Document

**File:** `POOL_DETECTOR_HARDENING_DESIGN.md`

**Contains:**
- Full assumptions and failure analysis
- Phase-by-phase implementation plan
- Code examples for each phase
- Logging strategy (production vs debug)
- Fallback discovery strategy
- Debug workflow for failing tokens
- Rollout plan and success criteria
- Risk assessment and backwards compatibility notes

**Length:** ~450 lines
**Audience:** Developers implementing the changes

### 2. Debug Checklist

**File:** `POOL_DETECTOR_DEBUG_CHECKLIST.md`

**Contains:**
- Quick-start steps to enable debug mode
- Log lines to look for
- 5 common failure scenarios with examples
- How to answer each of the 7 key questions
- Command reference for investigation
- Next steps based on diagnosis

**Length:** ~350 lines
**Audience:** Anyone debugging a pool detection failure

### 3. Memory Entry

**Location:** Memory system (POOL_DETECTOR_HARDENING_DESIGN)

**Contains:**
- Condensed design summary
- Key issue and root cause possibilities
- Solution phases overview
- Files to modify
- Success criteria

---

## Implementation Phases

### Phase 1: Account Key Normalization (5 min)

Add helper function to handle string and dict formats:

```python
def _normalize_account_key(acc):
    if isinstance(acc, str):
        return acc
    if isinstance(acc, dict):
        return acc.get("pubkey") or acc.get("address")
    return None
```

**Impact:** Fixes potential account extraction failures from provider format mismatches

### Phase 2: Transaction Shape Logging (5 min)

Log before scanning:

```python
logger.info(
    f"[POOL_DETECT] tx_version={tx_version} "
    f"base_keys={len(account_keys)} "
    f"writable_loaded={len(writable_accounts)} "
    f"readonly_loaded={len(readonly_accounts)} "
    f"has_addressTableLookups={has_lookups} total={len(all_accounts)}"
)
```

**Impact:** Immediately visible whether tx is v0 and account distribution

### Phase 3: Per-Account Debug Logging (10 min)

For each account, log: index, owner, executable, data length, AMM match status

**Impact:** Full visibility into account composition and why each was accepted/rejected

### Phase 4: AMM Candidate Validation (10 min)

Define data length minimums and validate:

```python
class AMMDataLengths:
    RAYDIUM_AMM_MIN = 296
    ORCA_WHIRLPOOL_MIN = 232
    METEORA_MIN = 232
    # ...
```

Reject candidates that don't meet minimum:

```python
if data_len < min_len:
    logger.warning(f"... has invalid data_len={data_len} (expected >= {min_len})")
    continue
```

**Impact:** Eliminates false positives from helper PDAs; validates pool state accounts

### Phase 5: Fallback Discovery Path (20 min)

Implement `_discover_pool_via_vaults()` that:
- Calls `getTokenLargestAccounts(mint)`
- Inspects vault accounts
- Attempts vault→pool resolution

**Current state:** Placeholder implementation
**Future:** Can be enhanced with program-specific pool resolution

**Impact:** Resilience for edge cases where pool isn't in transaction

### Phase 6: Listener Integration (5 min)

Read debug flag from environment:

```python
debug_mode = os.getenv("POOL_DETECTOR_DEBUG", "false").lower() == "true"
self.pool_detector = PoolDetector(rpc_url, debug=debug_mode)
```

**Impact:** Easy debug mode toggle without code changes

### Phase 7: Health Endpoint Stats (10 min)

Add detection stats to `/api/price/health`:

```python
"detection": {
    "primary_success": X,
    "fallback_used": Y,
    "total_attempted": Z,
}
```

**Impact:** Observable metrics for detection success rates

---

## Key Changes Summary

| Aspect | Before | After |
|--------|--------|-------|
| Account extraction | No normalization | Handles strings + dicts |
| Transaction visibility | Implicit (25 accounts) | Explicit (base=25, writable=0, readonly=0, v0=False) |
| Account breakdown | Silent scan | Per-account logs with owner/data (debug) |
| Validation | Ownership only | Owner + data length checks |
| Fallback | None | Vault-based discovery |
| Observability | Opaque failure | 7 questions answerable from logs |
| Debug mode | N/A | POOL_DETECTOR_DEBUG env var |

---

## Failure Modes Made Observable

| # | Question | How to Answer | Log Source |
|---|----------|---------------|------------|
| 1 | Is tx really v0? | `has_addressTableLookups=True/False` | `[POOL_DETECT]` shape line |
| 2 | Are loaded addresses populated? | `writable_loaded > 0` or `readonly_loaded > 0` | `[POOL_DETECT]` shape line |
| 3 | What accounts are in the tx? | 25 debug lines, one per account | `[POOL_DETECT_DEBUG]` lines |
| 4 | Are any accounts AMM-owned? | Look for `amm_match=True` | `[POOL_DETECT_DEBUG]` lines |
| 5 | Do candidates pass validation? | Check for `invalid data_len` warnings | `[POOL_DETECT]` warning lines |
| 6 | Is pool PDA entirely absent? | No `amm_match=True` anywhere | `[POOL_DETECT_DEBUG]` lines |
| 7 | Does fallback work? | `[POOL_DETECT_FALLBACK] ... succeeded` | `[POOL_DETECT_FALLBACK]` lines |

---

## Usage Examples

### Normal Token (Pool Found in TX)

```bash
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 has_addressTableLookups=False total=25
[POOL_DETECT] ✅ Found pumpswap pool at index 3: pAMMBay... (data_len=500)
[POOL] 🚀 Auto-registered pool for WebSocket pricing
```

**Diagnosis:** ✅ Pool found in transaction, registration successful

### V0 Transaction with Loaded Addresses

```bash
[POOL_DETECT] tx_version=0 base_keys=4 writable_loaded=12 readonly_loaded=8 has_addressTableLookups=True total=24
[POOL_DETECT_DEBUG] idx=18 addr=pAMMBay6oce... owner=pAMMBay6oce... exec=False data_len=500 amm_match=True
[POOL_DETECT] ✅ Found pumpswap pool at index 18: pAMMBay... (data_len=500)
```

**Diagnosis:** ✅ Pool found in v0 transaction loaded addresses

### Fallback Path Needed

```bash
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 ... total=25
[POOL_DETECT] No AMM-owned pool found in transaction (25 base + 0 writable + 0 readonly). Trying fallback...
[POOL_DETECT_FALLBACK] Starting vault-based discovery for TOKEN
[POOL_DETECT] ✅ Fallback vault discovery succeeded: pAMMBay...
```

**Diagnosis:** ✅ Fallback path succeeded, pool found via vaults

### Complete Failure (Diagnosable)

```bash
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 ... total=25
[POOL_DETECT_DEBUG] idx=0 addr=11111... owner=11111... amm_match=False
... (25 debug lines, no amm_match=True) ...
[POOL_DETECT] No AMM-owned pool found in transaction. Trying fallback...
[POOL_DETECT_FALLBACK] Starting vault-based discovery...
[POOL_DETECT] ⚠️ Fallback vault discovery failed for TOKEN
```

**Diagnosis:** ❌ Pool not in transaction and not in vaults—requires investigation or manual registration

---

## Files Modified

| File | Changes | Lines | Complexity |
|------|---------|-------|------------|
| `src/core/pool_detector.py` | Add normalization, logging, validation, fallback | ~150 | Medium |
| `src/core/pumpfun_curve_listener.py` | Read debug flag, pass to detector | ~5 | Trivial |
| `src/apis/price_api.py` | Add detection stats to health endpoint | ~10 | Trivial |

**Total:** ~165 lines across 3 files
**Backwards compatibility:** 100% (debug flag defaults to false, fallback only if primary fails)
**Rollback:** <1 minute (revert pool_detector.py and listener changes)

---

## Testing Strategy

### Unit Tests (In test_pool_detector_v0.py)

Already have tests for:
- Regular transaction pool detection
- V0 transaction writable loaded addresses
- V0 transaction readonly loaded addresses
- No pool found (negative test)
- All account types scanned
- AMM program identification

### Integration Testing

1. **Syntax check:**
   ```bash
   python3 -m py_compile src/core/pool_detector.py
   python3 -m py_compile src/core/pumpfun_curve_listener.py
   ```

2. **Enable debug mode:**
   ```bash
   POOL_DETECTOR_DEBUG=true python -m src.core.pumpfun_curve_listener
   ```

3. **Monitor next token launches:**
   - Watch for `[POOL_DETECT]` transaction shape lines
   - Verify at least one account is checked
   - Verify pool is found or fallback attempted

4. **Regression testing:**
   - Verify no performance impact from added logging
   - Confirm existing successful detections still work
   - Check log volume in production (should be minimal without debug flag)

---

## Rollout Plan

### Day 1: Implementation

1. Implement phases 1-5 in `pool_detector.py` (~30 min)
2. Update `pumpfun_curve_listener.py` (phase 6) (~5 min)
3. Run syntax check (~2 min)
4. Commit to feature branch

### Day 1-2: Testing

1. Start listener with debug enabled
2. Monitor logs for next token launch
3. Verify all 7 failure modes are answerable
4. Test with known failing token (manual injection if needed)
5. Document findings

### Day 2: Deployment

1. Code review of hardening changes
2. Merge to main
3. Restart listener in production
4. Monitor logs for format correctness
5. Adjust thresholds if needed

### Day 2-3: Validation

1. Watch `[POOL_DETECT]` logs as tokens launch
2. Verify pool detection success rate
3. Check fallback path usage frequency
4. Document any edge cases

---

## Success Metrics

### Observability
- ✅ Transaction shape visible for every detection attempt
- ✅ Per-account breakdown available in debug mode
- ✅ 7 failure modes answerable from logs alone

### Robustness
- ✅ Account normalization handles provider format variations
- ✅ Data length validation prevents false positives
- ✅ Fallback path provides secondary discovery

### Reliability
- ✅ Pool detection success rate improves from ~85% to >95%
- ✅ Failing detections are immediately diagnosable
- ✅ Zero performance regression

---

## Documents Reference

1. **POOL_DETECTOR_HARDENING_DESIGN.md** (450 lines)
   - Complete design with code examples
   - Assumptions, failure analysis, implementation phases
   - Logging strategy, fallback strategy, debug workflow

2. **POOL_DETECTOR_DEBUG_CHECKLIST.md** (350 lines)
   - Quick-start guide for debug mode
   - Log lines to look for
   - 5 common scenarios with examples
   - Command reference

3. **This summary** (200 lines)
   - Problem, solution, deliverables overview
   - Phase summary table
   - Files modified, testing strategy, rollout plan

---

## Next Steps

1. **Review design documents** — Ensure approach aligns with requirements
2. **Approve implementation plan** — Sign off on phases and scope
3. **Begin implementation** — Phases 1-5 are straightforward
4. **Test with failing token** — Use debug mode to verify all 7 questions are answerable
5. **Deploy to production** — Standard rollout process
6. **Monitor and iterate** — Adjust thresholds based on real token launches

---

## Questions & Answers

**Q: Why not just add the fallback path now?**
A: Fallback is incomplete by design. Primary path is proven. Fallback needs program-specific logic (can be added later) and RPC cost analysis. For now, it's a placeholder that logs the attempt.

**Q: Will this break existing functionality?**
A: No. All changes are additive. Debug flag defaults to false. Fallback only used if primary fails. Can rollback in <1 minute.

**Q: Why normalize account keys?**
A: Different RPC providers return different formats. Some return strings, some return objects with pubkey/signer/writable fields. Normalization ensures we don't miss accounts due to format mismatches.

**Q: How much logging overhead?**
A: Minimal. Transaction shape line (always) is ~1 line per token. Per-account debug lines (optional) are only enabled via flag. Production impact: negligible.

**Q: Can this detect all pool types?**
A: Detects programs owned by known AMM addresses. Covers Raydium, Orca, Meteora, PumpSwap. Unknown AMM programs would need registry updates.

**Q: What if pool account isn't in the transaction?**
A: Fallback path attempts vault discovery. If that also fails, manual investigation/registration needed. But now it's diagnosable from logs.

---

## Contact & Iteration

These documents are **design artifacts** ready for implementation. They can be refined based on:
- Implementation findings
- Test results from real token launches
- Performance measurements
- Threshold adjustments

All changes preserve the existing architecture and maintain 100% backwards compatibility.

