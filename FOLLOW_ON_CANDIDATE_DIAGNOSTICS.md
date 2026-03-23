# Follow-On Candidate-Level Diagnostics

## Purpose

Previous logs showed "follow-on exhausted, 0 pools found" but didn't explain **why** — was it:
- No candidates extracted from TXs?
- Candidates extracted but validation rejected them?
- Wrong anchor/window/pattern?

These new logs answer that question at the candidate level.

## New Diagnostic Checkpoints

### 1. Anchor Scanning Start
```
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve, scanning up to 10
```
**What it shows:** RPC found 20 TXs for this anchor, we'll check up to 10 of them

### 2. Per-TX Extraction Result
```
[FOLLOW_ON_DISCOVERY] TX abc123... (offset=3) anchor=bonding_curve: 5 candidate(s) extracted
```
or
```
[FOLLOW_ON_DISCOVERY] TX abc123... (offset=3) anchor=bonding_curve: 0 candidates extracted
```
**What it shows:** Did this TX have any pool-like accounts? (0 = no pool program calls in this TX)

### 3. Per-Candidate Validation
```
[FOLLOW_ON_DISCOVERY] ✓ Candidate xyz789... from anchor=bonding_curve (offset=3), validating via RPC...
```
**What it shows:** We extracted a candidate and are checking if it's a valid pool

### 4. Validation Results

**Success:**
```
[FOLLOW_ON_DISCOVERY] ✅ Found valid pool xyz789... via anchor=bonding_curve at offset=3
```

**Rejected (not on-chain):**
```
[FOLLOW_ON_DISCOVERY] ❌ Candidate xyz789... anchor=bonding_curve: Account not found on-chain
```
→ The address was mentioned in the TX but doesn't exist as an on-chain account

**Rejected (wrong owner):**
```
[FOLLOW_ON_DISCOVERY] ❌ Rejected xyz789... anchor=bonding_curve: owner=11111111... NOT a pool program
```
→ Account exists but owner is System Program, not a pool program (pAMM/Raydium/etc)

### 5. Exhaustion Summary
```
[FOLLOW_ON_DISCOVERY] ⏹️ EXHAUSTED: Scanned 12 TXs, 0 valid pools found (7 RPC calls used of 15)
```
**What it shows:** Checked 12 TXs, used 7 of 15 RPC calls, found nothing

## Interpretation Guide

### Case 1: 0 candidates extracted from any TX
```
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] TX abc123... offset=1: 0 candidates extracted
[FOLLOW_ON_DISCOVERY] TX def456... offset=2: 0 candidates extracted
...
[FOLLOW_ON_DISCOVERY] ⏹️ EXHAUSTED: Scanned 12 TXs, 0 valid pools found
```
**Root cause:** No TXs contained pool program calls. Either:
- Pool creation uses a different pattern than expected
- Bonding curve anchor is wrong (not the owner of pool creation TX)
- Pool was created before migration (not in "follow-on" TXs)

### Case 2: Candidates extracted but all rejected (not on-chain)
```
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] TX abc123... offset=1: 2 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] ✓ Candidate xyz789... from anchor=bonding_curve (offset=1), validating...
[FOLLOW_ON_DISCOVERY] ❌ Candidate xyz789...: Account not found on-chain
[FOLLOW_ON_DISCOVERY] ✓ Candidate 123abc... from anchor=bonding_curve (offset=1), validating...
[FOLLOW_ON_DISCOVERY] ❌ Candidate 123abc...: Account not found on-chain
```
**Root cause:** TX mentioned these accounts but they're not real on-chain accounts (may be:
- Derived addresses that aren't stored accounts
- PDAs that aren't initialized yet
- Addresses from logs (not account keys)

### Case 3: Candidates extracted and validation passes (but wrong owner)
```
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] TX abc123... offset=1: 3 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] ✓ Candidate xyz789..., validating...
[FOLLOW_ON_DISCOVERY] ❌ Rejected xyz789... owner=11111111... NOT a pool program
[FOLLOW_ON_DISCOVERY] ✓ Candidate 123abc..., validating...
[FOLLOW_ON_DISCOVERY] ❌ Rejected 123abc... owner=pAMMBay6... but is same as bonding_curve (not a pool)
```
**Root cause:** Extracted valid on-chain accounts, but they're not pools (owned by System, Token program, etc)

### Case 4: Pool found ✅
```
[FOLLOW_ON_DISCOVERY] Found 20 signatures for bonding_curve
[FOLLOW_ON_DISCOVERY] TX abc123... offset=1: 2 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] ✓ Candidate xyz789..., validating...
[FOLLOW_ON_DISCOVERY] ✅ Found valid pool xyz789... via anchor=bonding_curve at offset=1
```
**Result:** SUCCESS — pool discovered

## When to Read These

Run listener, trigger token migration, then:
```bash
tail -200 listener.log | grep "\[FOLLOW_ON_DISCOVERY\]"
```

Look for patterns in the logs above to identify which case you're in, then see "Root cause" for next debugging step.

## Files Modified

- `src/core/post_migration_pool_discovery.py` — Enhanced logging at candidate extraction/validation points
- Key function: `discover_follow_on_pools()` (lines 484+)

## Example Full Flow

```
🔴 [FOLLOW_ON_CHECK] follow_on_max_txs=10 tx_data=True cached_count=0
[FOLLOW_ON_DISCOVERY] Starting search for GKJvx2ko... curve=9sy31onQpE... creator=CnT2wMRr...
[FOLLOW_ON_DISCOVERY] Found 15 signatures for bonding_curve, scanning up to 10
[FOLLOW_ON_DISCOVERY] TX 2Lp1vBbu... (offset=0) anchor=bonding_curve: 0 candidates extracted
[FOLLOW_ON_DISCOVERY] TX 3Xp2vBbu... (offset=1) anchor=bonding_curve: 1 candidate(s) extracted
[FOLLOW_ON_DISCOVERY] ✓ Candidate 9jkxLmno... from anchor=bonding_curve (offset=1), validating...
[FOLLOW_ON_DISCOVERY] ❌ Candidate 9jkxLmno... anchor=bonding_curve: Account not found on-chain
[FOLLOW_ON_DISCOVERY] TX 4Yp3vBbu... (offset=2) anchor=bonding_curve: 0 candidates extracted
...
[FOLLOW_ON_DISCOVERY] ⏹️ EXHAUSTED: Scanned 10 TXs, 0 valid pools found (9 RPC calls used of 15)
```

This tells you: "Bonding curve anchor found TXs, some had candidates, but none were valid on-chain pools"

Next step: Try creator anchor, or re-examine the pool creation pattern.
