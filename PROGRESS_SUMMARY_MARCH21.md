# Progress Summary: March 21, 2026

## What Is Now Working ✅

### Retry System
- **[RETRY_START]** fires correctly with all context
- Retry task created and executed
- Delays firing as expected (0.5s, 1s, 1.5s, 2s, ...)

### Data Propagation
- **Creator extraction** working (pulled from earliest CREATE tx)
- **Bonding curve** extraction working (derived from pool authority)
- **TX data** present and passed through retry chain
- **TX enrichment** functional (reconstructs 38-39 accounts from accountKeys + loadedAddresses)

### Follow-On Discovery Trigger
- **Attempt 1:** TX-only parsing (no follow-on, correct)
- **Attempt 2+:** Follow-on enabled with `follow_on_max_txs=10` (now working at attempt 2, not 4)
- **[FOLLOW_ON_CHECK]** logs show trigger conditions met
- **Discovery runs** from attempt 2 onward as intended

## What Is Still Failing ❌

### Candidate Extraction / Validation
Every token tested so far ends with:
```
[FOLLOW_ON_EXHAUSTED] Scanned 11–12 TXs, 0 valid pools found
```

This means:
- ❌ No pool address found yet for any test token
- ❌ Failure is INSIDE follow-on search logic (after trigger, not before)
- ❌ Either: no candidates extracted, or extracted candidates fail validation

## Known State of This Token

From the logs:
```
[CACHED_TX_DIAGNOSTICS] reason=no_amm_program_in_tx accounts=39 writable=0 amm_present=False inner_ix=3
```

**What this tells us:**
- ✅ Pool is NOT in the migration TX itself (correct—it's created after)
- ✅ Follow-on discovery is the right strategy
- ❌ But follow-on still isn't finding it

## Critical Inconsistency (Still Present)

Attempt 1:
```
[FOLLOW_ON_CHECK] follow_on_max_txs=0
```

Attempts 2+:
```
[FOLLOW_ON_CHECK] follow_on_max_txs=10
```

**Why this happens:** Tier 1 (attempts 1-3) was originally `attempt <= 5` but we lowered threshold to `attempt >= 2`. So attempt 1 still sees `follow_on_max_txs=0` (correct, it's Tier 1), then attempt 2 switches to 10.

This is actually correct behavior, not a bug—but the logs make it look inconsistent.

## Narrowed-Down Problem Space

At this point, the failure is ONE of these:

### Case 1: Pool Creation TX Outside Window
- Follow-on scans 11-12 TXs from bonding_curve/creator anchors
- The TX where pool was created is TX #13+
- **Symptom:** [FOLLOW_ON_DISCOVERY] finds signatures but 0 candidates extracted

### Case 2: Pool Not Created Via Anchor
- Pool was created in a TX that doesn't reference bonding_curve or creator
- **Symptom:** [FOLLOW_ON_DISCOVERY] finds 0 signatures for both anchors
- **Fix needed:** Try mint address as anchor, or extend window

### Case 3: Candidates Extracted But Validation Fails
- Follow-on finds candidate addresses
- But validation rejects them (not on-chain, or wrong owner)
- **Symptom:** [FOLLOW_ON_DISCOVERY] shows "0 candidates extracted" OR "❌ Rejected xyz... NOT a pool program"
- **Fix needed:** Relax validation or fix candidate extraction

### Case 4: Extraction Logic Broken for This Token
- Candidate extraction doesn't work for this token's TX structure
- **Symptom:** [FOLLOW_ON_DISCOVERY] finds signatures but extracts 0 candidates from all of them
- **Fix needed:** Debug _extract_pool_candidates_from_tx() logic

## What the New Logs Will Show

With the latest code, you'll see:

```
[FOLLOW_ON_DISCOVERY] Found 15 signatures for bonding_curve, scanning up to 10
[FOLLOW_ON_DISCOVERY] TX abc123... (offset=1) anchor=bonding_curve: 3 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] ✓ Candidate xyz789..., validating...
[FOLLOW_ON_DISCOVERY] ❌ Candidate xyz789...: Account not found on-chain
```

This tells you EXACTLY:
- ✅ Signatures found
- ✅ Candidates extracted
- ❌ Validation failed (why: not on-chain)

## Next Action

Restart listener and wait for next token. The new logs will immediately tell you which of the 4 cases above is happening, and that tells you the exact fix needed.

## Files Involved

- **Trigger/Orchestration:** `src/core/pumpfun_curve_listener.py` (lines 2757-3100)
- **Follow-On Discovery:** `src/core/post_migration_pool_discovery.py` (lines 484-800)
- **Program IDs:** `src/core/pool_detector.py` (lines 97-121)
- **TX Enrichment:** `src/core/pumpfun_curve_listener.py` (lines 2810-2850)

## Progress Metrics

| Component | Status |
|-----------|--------|
| Retry scheduling | ✅ Working |
| Creator extraction | ✅ Working |
| TX enrichment | ✅ Working |
| Follow-on trigger | ✅ Working (at attempt 2+) |
| Candidate extraction | ❓ Unknown (no detailed logs yet) |
| Candidate validation | ❓ Unknown (no detailed logs yet) |
| Pool discovery | ❌ 0/5 tokens found |

The framework is solid. The remaining issue is purely technical—within follow-on's candidate extraction/validation logic.
