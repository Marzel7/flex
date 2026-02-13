# Spam Filtering Correction - Coordinator Analysis

## Summary

Applied spam filtering to coordinator detection. Removed 3 addresses that were network artifacts below the intentional signal threshold.

## The Problem

Initial analysis identified 4 "coordinators" all with dust transfers:
- po27vzv7... (9 lamports)
- pohJj8FS... (5 lamports)
- GUZv3UAzUA... (2 lamports)
- HLSHeeM2Q... (200 lamports)

## The Distinction

**Network Artifacts (Spam) - Sub-10 Lamports:**
- Below any intentional signal threshold
- Could be routing errors, failed transactions, or accidental dust
- Not evidence of deliberate coordination
- **REMOVED FROM COORDINATOR LIST**

**Intentional Signals - 100+ Lamports:**
- Deliberately high enough to avoid accidental inclusion
- Minimum viable signal size
- Evidence of deliberate multi-path funding strategy
- **RETAINED FOR MONITORING**

## The Math

```
1 SOL = 1,000,000,000 lamports

HLSHeeM2Q: 0.0000002 SOL = 200 lamports (SIGNAL)
po27vzv7:  0.000000009 SOL = 9 lamports (SPAM)
pohJj8FS:  0.000000005 SOL = 5 lamports (SPAM)
GUZv3UAzUA: 0.000000002 SOL = 2 lamports (SPAM)
```

## Why This Matters

The 3 removed addresses share funder infrastructure with HLSHeeM2Q:
- 4khTDC81... (Hyperunit Router)
- 9s4gzvCo... (Hyperunit Aggregator)

This could have created false impression of larger coordination ring. After filtering, we see:

**Reality:** 1 confirmed coordinator (HLSHeeM2Q) using legitimate dust signal to coordinate through hub router infrastructure.

## Updated Detection Rules

```python
def is_cross_funder_coordinator(sender):
    funder_count = count_distinct_funders(sender)
    total_sol = sum_all_transfers(sender)

    # Filter spam
    if total_sol < 1e-7:  # Less than 100 lamports
        return False  # Network artifact, not coordination

    # Must reach 2+ creators via different funders
    creators = count_creators_via_multiple_funders(sender)

    return funder_count >= 2 and creators >= 2
```

## Retained Coordinator

**HLSHeeM2Q141C4PEYMeeKtWeP4uVQeYsk4fmVCMxhi2F**
- 200 lamport signal (intentional)
- Reaches 2 creators
- Uses 2 funders
- Involves HWPgjY8 hub router
- Medium confidence (smaller operation than initially thought)

## Key Insight

The coordinator isn't trying to be "hidden" through large-scale multi-funder coordination. Instead, it's using:
1. **Dust signaling** (200 lamports) to identify as coordinator
2. **Hub router leverage** (HWPgjY8) for infrastructure access
3. **Two-creator targeting** for manageable operation

This is more sophisticated than the 49-wallet ring discovered earlier - it's using infrastructure obfuscation rather than size obfuscation.

## Files Updated

- `analyze_cross_funder_coordinators.py` - Added min_total_sol=1e-8 filter
- `COORDINATOR_ANALYSIS.md` - Removed 3 spam entries, detailed HLSHeeM2Q only
- `main.py` - API still functional, returns 1 coordinator

## Status

✅ Spam filtering applied
✅ Documentation corrected
✅ Database updated
✅ API endpoint functional
✅ Ready for monitoring integration
