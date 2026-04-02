# Debugging & Fallback Validation — Zero Candidates Issue Fix

**Date**: March 27, 2026
**Commits**: 63e66ea (upstream rejection) + d9bd7db (debugging & fallback)
**Status**: ✅ DEPLOYED

---

## The Problem

New token `7gy1KHvt4sfjbW5qhoE38f7rys9tGokGUHU5z2Wzpump` showed:

```
[VAULT_DISCOVERY] Attempt 1 failed: No accounts passed validation
[VAULT_DISCOVERY] ❌ Vault discovery failed after 1 attempts
[POOL_DETECT] Final discovery result: source=none pool=None
```

**Key difference from previous bug**:
- Previous: Wrong pool selected, then rejected later
- **This**: No candidates survive validation at all

**Root cause**: Unknown why candidates were filtered out (no per-candidate logging)

---

## Solution Part 1: Detailed Per-Candidate Rejection Logging

**Location**: `src/core/pumpfun_curve_listener.py:1501` - `batch_validate_candidates()`

**New logging** shows exactly why each candidate was rejected:

```
[CANDIDATE_REJECTED] addr=5LzwiTW78ctG1eeE... reason=account_not_found
[CANDIDATE_REJECTED] addr=8MuxNquLW5aMUJ3U... reason=wrong_owner owner=pAMMBay6oce...
[CANDIDATE_REJECTED] addr=ADyA8hdefvWN2dbG... reason=shared_account threshold=2
[CANDIDATE_REJECTED] addr=X... reason=shared_check_failed error=connection_timeout
[CANDIDATE_ACCEPTED] addr=4wTV1YmiEkRvAtNt... passed all validation checks
[BATCH_VALIDATE] Result: 1 valid candidates from 5 input
```

**Why this helps**:
- Each check logged separately: exists, owner, shared_account
- Clear reason why each failed
- Can tune thresholds based on what's being rejected
- Difference between "no accounts" and "all accounts rejected"

---

## Solution Part 2: Fallback Validation Mode

**Two-pass validation strategy**:

```
Pass 1 (Strict Mode):
  - threshold=2 for shared account check (aggressive)
  - Reject on any check failure
  - Goal: Find only high-confidence pools
  - Cost: May reject valid candidates with unusual TX shapes

Pass 2 (Loose Mode) - Only if Pass 1 found nothing:
  - threshold=3 for shared account check (relaxed)
  - Never skip shared account checks (ADyA still blocked)
  - Accept candidates if shared-check fails (fail open)
  - Goal: Recovery for weird TX layouts
  - Cost: Slightly lower safety, but better recovery
```

**Code flow**:

```python
# First pass: strict validation
valid = await self.batch_validate_candidates(candidates, strict_mode=True)

# Fallback: if strict mode found nothing, try again with looser validation
if not valid:
    log_print("[RESOLVE_POOL] ⚠️  No valid pools in strict mode, trying looser validation...")
    valid = await self.batch_validate_candidates(candidates, strict_mode=False)
```

**Why this works**:
- Protects against corrupt data (strict mode first)
- Still recovers from unusual but valid TX shapes
- Never accepts shared PDAs like ADyA in either mode
- Natural progression: strict → loose, never loose → strict

---

## Validation Checks (in order)

```
Check 1: Account must exist
  - Rejects: account_not_found
  - Error: RPC returned null for account

Check 2: Owner must be PUMPSWAP_PROGRAM
  - Rejects: wrong_owner owner={other_program}
  - Error: Account owned by non-PumpSwap program

Check 3: Not a shared account
  - Rejects: shared_account threshold={2|3}
  - Error: Account appears in >threshold tokens
  - Behavior:
    - Strict: threshold=2 (reject if 3+ tokens)
    - Loose: threshold=3 (reject if 4+ tokens)

Check 4: Shared account check doesn't error
  - Rejects: shared_check_failed error={...}
  - Behavior:
    - Strict: Reject on check failure
    - Loose: Accept despite check failure (fail open)
```

---

## Example Output

**Token with no valid candidates (all rejected for good reasons)**:
```
[BATCH_VALIDATE] Validating 4 candidates (strict_mode=True)
[CANDIDATE_REJECTED] addr=ADyA8hdefvWN2dbG... reason=shared_account threshold=2
[CANDIDATE_REJECTED] addr=2dF8xmS1BvsvUki4... reason=wrong_owner owner=ComputeBudget...
[CANDIDATE_REJECTED] addr=5FgrigGWubHViK3t... reason=account_not_found
[CANDIDATE_REJECTED] addr=Nt8pYCNsro7sC1wu... reason=account_not_found
[BATCH_VALIDATE] Result: 0 valid candidates from 4 input
[RESOLVE_POOL] ⚠️  No valid pools in strict mode, trying looser validation...
[BATCH_VALIDATE] Validating 4 candidates (strict_mode=False)
[CANDIDATE_REJECTED] addr=ADyA8hdefvWN2dbG... reason=shared_account threshold=3
[CANDIDATE_REJECTED] addr=2dF8xmS1BvsvUki4... reason=wrong_owner owner=ComputeBudget...
[CANDIDATE_ACCEPTED] addr=5FgrigGWubHViK3t... (shared check failed but accepting in retry mode)
[CANDIDATE_ACCEPTED] addr=Nt8pYCNsro7sC1wu... (shared check failed but accepting in retry mode)
[BATCH_VALIDATE] Result: 2 valid candidates from 4 input
```

**Token with valid candidates found immediately**:
```
[BATCH_VALIDATE] Validating 3 candidates (strict_mode=True)
[CANDIDATE_REJECTED] addr=ADyA8hdefvWN2dbG... reason=shared_account threshold=2
[CANDIDATE_ACCEPTED] addr=4wTV1YmiEkRvAtNt... passed all validation checks
[CANDIDATE_ACCEPTED] addr=8MuxNquLW5aMUJ3U... passed all validation checks
[BATCH_VALIDATE] Result: 2 valid candidates from 3 input
[RESOLVE_POOL] Proceeding with 2 valid candidates to selection phase
```

---

## Monitoring

**Success pattern**:
- See `[CANDIDATE_ACCEPTED]` messages
- See `[BATCH_VALIDATE] Result: N valid candidates`
- See `[RESOLVE_POOL] ✅ Selected pool`

**Failure pattern** (needs investigation):
- All candidates rejected in strict mode
- All candidates rejected in loose mode too
- `[RESOLVE_POOL] ❌ No valid pools found even with loose validation`

**Recovery pattern**:
- Zero valid in strict mode
- `[RESOLVE_POOL] ⚠️  No valid pools in strict mode, trying looser validation...`
- Found candidates in loose mode
- `[RESOLVE_POOL] ✅ Selected pool`

---

## Backward Compatibility

✅ **No breaking changes**:
- Default `strict_mode=True` (backward compatible)
- Existing callers work without modification
- Fallback only triggers if strict mode returns nothing
- Can tune thresholds without changing API

---

## Next Steps

1. ✅ Deployed to feat/authority-pda-extraction branch
2. Wait for next token migration (7gy1KHvt... or new)
3. Check logs for per-candidate rejection reasons
4. If fallback is triggering repeatedly, analyze which check is being too aggressive
5. Optional: Merge to main when pattern confirmed

---

## Expected Behavior

For `7gy1KHvt4sfjbW5qhoE38f7rys9tGokGUHU5z2Wzpump` on next discovery attempt:

Should see detailed logs showing:
- Which candidates were found in TX
- Why each was rejected (or accepted)
- Whether strict or loose mode succeeded
- Which pool was ultimately selected

This gives visibility into the exact filtering path, not just the final outcome.
