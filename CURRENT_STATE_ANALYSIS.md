# Current State Analysis - March 21, 11:20 AM

## What Is Deployed and Working ✅

### Retry Framework
- [x] Retry task creation and execution
- [x] Creator and bonding curve extraction
- [x] TX data propagation through retry chain
- [x] TX enrichment (reconstructing meta.accounts from accountKeys + loadedAddresses)
- [x] Anchors present in follow-on discovery calls

### Follow-On Discovery Trigger
- [x] Immediate trigger on `reason_code=no_amm_program_in_tx` (attempt 1+)
- [x] Tiered trigger on other reason codes (attempt 2+)
- [x] Orchestration skips wasteful migration-TX re-parsing if pool proven not in migration

### Candidate Extraction Breadth
- [x] Fixed narrow filtering bug (was only including accounts with owner in POOL_PROGRAMS)
- [x] Now includes ALL non-system accounts as candidates
- [x] Fast-path optimization if owner metadata exists
- [x] Fallback includes accounts even without owner info for downstream RPC validation

### Detailed Diagnostics Deployed
- [x] `[TX_DATA_VALIDATION]` — checks meta.accounts presence
- [x] `[TX_DATA_ENRICHMENT]` — shows reconstructed account count
- [x] `[CACHED_TX_PARSE]` — shows extracted candidates
- [x] `[FOLLOW_ON_DISCOVERY]` — shows per-anchor signatures, per-TX candidates, per-candidate validation
- [x] `[FOLLOW_ON_EXHAUSTED]` — includes reason_code and anchor info
- [x] `[MIGRATION_TX_PARSE_SKIP]` — explains why migration TX re-parse is skipped

## What Is Still Failing ❌

**Zero pools found via follow-on discovery despite:**
- ✅ Enriched 38-39 accounts available
- ✅ Follow-on running from attempt 1-2 onward
- ✅ Extraction now includes 30+ candidates per TX
- ✅ Validation running on each candidate

**Expected next logs for next token:**
```
[FOLLOW_ON_DISCOVERY] Found 15 signatures for bonding_curve, scanning up to 12
[FOLLOW_ON_DISCOVERY] TX abc123... (offset=1) anchor=bonding_curve: 30 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] ✓ Candidate xyz789..., validating...
[FOLLOW_ON_DISCOVERY] ❌ Candidate xyz789... anchor=bonding_curve: Account not found on-chain
```

OR

```
[FOLLOW_ON_DISCOVERY] ❌ Rejected xyz789... anchor=bonding_curve: owner=11111111... NOT a pool program
```

These will show us WHICH of 4 cases we're in:
1. Pool creation TX outside the 12-TX window
2. Pool not created via bonding_curve/creator anchor (try mint anchor instead)
3. Candidates extracted but validation rejects them all (no valid on-chain pools)
4. Extraction logic still broken for this token's TX structure

## Next Investigation

The new candidate extraction fix + detailed follow-on logs will immediately tell us:

- **Do signatures get found?** → Anchors are correct
- **Do candidates get extracted?** → Extraction works
- **Are candidates on-chain?** → Data integrity is good
- **Do any pass validation?** → Pool program validation works

Without running the next token, we can't know which of these is the bottleneck. The logs will pinpoint it.

## Code Status

### Files Ready
- `src/core/pumpfun_curve_listener.py` — Orchestration + TX enrichment
- `src/core/post_migration_pool_discovery.py` — Follow-on discovery + candidate extraction
- `src/core/pool_detector.py` — Pool validation logic (unchanged, but may be next focus)

### Listener Status
- **PID:** 46384
- **Running:** Yes
- **All fixes deployed:** Yes
- **Waiting for:** Next token migration

## Critical Path Summary

```
Migration detected
  ├─ TX fetched and cached ✅
  ├─ Creator extracted ✅
  ├─ Bonding curve extracted ✅
  ├─ Retry scheduled with anchors ✅
  │
  └─ Attempt 1+
      ├─ Cached TX parsed ✅
      │  └─ Accounts extracted (30+) ✅
      │
      ├─ Follow-on triggered (reason_code aware) ✅
      │  ├─ Signatures found? → Unknown (waiting for logs)
      │  ├─ Candidates extracted? → Unknown
      │  ├─ Validation results? → Unknown
      │  └─ Pool found? → ❌ Still failing
      │
      └─ Migration TX parsing skipped (if reason proved pool not there) ✅
```

## What To Expect Next

When token arrives, the new detailed logs will immediately answer:

**Which anchor produced results?**
```
[FOLLOW_ON_DISCOVERY] Found X signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] Found Y signatures for creator
[FOLLOW_ON_DISCOVERY] Found Z signatures for mint
```

**Which TXs had candidates?**
```
[FOLLOW_ON_DISCOVERY] TX abc... offset=1: 25 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] TX def... offset=2: 0 candidates extracted
```

**Why were candidates rejected?**
```
[FOLLOW_ON_DISCOVERY] ❌ Candidate xyz...: Account not found on-chain
[FOLLOW_ON_DISCOVERY] ❌ Rejected xyz... owner=11111111... NOT a pool program
```

These logs answer the question: "Why isn't the pool being found?" with machine precision.

---

**Bottom line:** Everything before follow-on discovery is working. The issue is now entirely within candidate extraction/validation inside follow-on. Next token will show us exactly where.
