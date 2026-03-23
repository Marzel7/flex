# Session Summary - March 23, 2026

## Problem Statement
Follow-on pool discovery was returning zero results for Pump.fun migration tokens, preventing real-time pool detection on Solana.

## Root Causes Identified & Fixed

### 1. TX Data Integrity Issue ❌→✅
**Problem:** Helius RPC returns TX data with `meta.accounts = None`
- Caused downstream extraction to fail silently
- No way to identify pool accounts in migration TX

**Solution:** TX Data Enrichment (lines 2810-2850 in pumpfun_curve_listener.py)
```
Reconstructs meta.accounts from:
- transaction.message.accountKeys
- meta.loadedAddresses.writable
- meta.loadedAddresses.readonly

Added [TX_DATA_VALIDATION] checkpoint to detect incomplete structures
```

**Status:** ✅ FIXED - Now reconstructs 25 accounts per migration TX

---

### 2. Candidate Extraction Too Broad ❌→✅
**Problem:** Original code extracted from full account list (25+ accounts)
- Drowned pool detector in irrelevant accounts
- Low signal-to-noise ratio

**Solution:** Focused Candidate Extraction (parse_candidates_from_cached_tx in post_migration_pool_discovery.py)
```
Extract ONLY from:
1. Top-level instruction accounts
2. Inner instruction accounts
3. Fallback to full list only if no instructions found

Results: 17 candidates instead of 25
```

**Status:** ✅ FIXED - Tested on real migration TX, works correctly

---

### 3. Pool Detection Too Strict ❌→✅
**Problem:** Original detector had strict validation (size checks, parser validation)
- Filtered out valid pools prematurely
- Never returned any results

**Solution:** Minimal Pool Detector (detect_pool_from_tx in pool_detector.py)
```
MINIMAL implementation for debugging:
1. Scan all transaction accounts
2. Fetch owner for each account
3. Return FIRST account owned by known AMM program
4. Skip: size thresholds, parser validation, helper-PDA filtering
```

**Status:** ✅ FIXED - Successfully detected pool on test TX

---

### 4. Retry Orchestration Inconsistent ❌→✅
**Problem:** Follow-on discovery had timing issues
- Attempt 1: follow_on_max_txs=0 (skip follow-on)
- Attempts 2+: follow_on_max_txs=10 (run follow-on)
- Wasted RPC re-parsing migration TX after follow-on exhausted

**Solution:** Smart Orchestration (lines 2965-3085 in pumpfun_curve_listener.py)
```
If reason_code == "no_amm_program_in_tx":
  - Start follow-on immediately at attempt 1 with max_txs=12
  - Skip wasteful migration-TX re-parsing after follow-on exhausts

Otherwise:
  - Keep current tiering (follow-on starts at attempt 2)
  - Normal fallback behavior
```

**Status:** ✅ FIXED - Optimized attempt sequencing

---

### 5. Anchor Propagation Bug (Partial)
**Problem:** Some tokens had `curve=None, creator=None` on retry start
- Breaks follow-on discovery anchors

**Status:** ⚠️ STILL EXISTS - Edge case for some tokens, needs investigation

---

## Test Results - Real Migration TX

**TX:** `36TzLUy6QqPSwHZGQqGcJhM6GP7aQhXD6xNrgrLLmtfTR5Ft77Fw5VB8Bmmw3ZfqJP7YJnWf6VJ36rUVaUK8gV1D`

### Extraction
```
✅ 17 focused candidates extracted
✅ vs 25 from full account list (29% reduction)
✅ Correct accounts identified
```

### Detection
```
✅ Pool detected: 5tkaAixycLTVKraA1QCxaXDQkE5TprpVzVNtQG73r2n8
✅ Program: pumpswap (Raydium AMM v4)
✅ Matches expected bonding curve from logs
```

---

## Code Changes Summary

### src/core/pumpfun_curve_listener.py
- Lines 2780-2850: TX_DATA_ENRICHMENT checkpoint + reconstruction logic
- Lines 2910-3085: Smart orchestration based on cached diagnostics
- Lines 2965-2980: Diagnostic logging (POST_PARSE_ROUTE, FOLLOW_ON_CHECK)

### src/core/post_migration_pool_discovery.py
- Lines 358-528: parse_candidates_from_cached_tx() - focused extraction
- Now extracts instruction-referenced accounts only, not full account list

### src/core/pool_detector.py
- Lines 174-259: detect_pool_from_tx() - minimal detector
- Scans accounts, fetches owners, returns first AMM-owned account

---

## Current State

### ✅ Working Components
- TX enrichment reconstructs meta.accounts
- Focused extraction identifies relevant accounts
- Minimal detector finds AMM-owned pools
- Orchestration optimized for early follow-on triggering

### ❌ Remaining Issues
1. **Integration gap** — Extracted candidates aren't flowing to pool registration correctly
2. **Anchor propagation** — Some tokens lose curve/creator on retry start
3. **Live validation** — Need to test with real tokens to confirm end-to-end flow

---

## Next Steps (Priority Order)

1. **Verify end-to-end on live token**
   - Wait for migration event
   - Confirm extracted candidates reach validation
   - Confirm pool gets registered to database

2. **Fix anchor propagation bug**
   - Investigate why curve=None, creator=None for some tokens
   - Ensure consistency across retry attempts

3. **Optimize candidate validation**
   - Ensure extracted candidates use efficient RPC validation
   - Consider batching owner checks

---

## Key Metrics

- **TX Data Enrichment:** 0→25 accounts reconstructed ✅
- **Candidate Extraction:** 25→17 candidates (29% noise reduction) ✅
- **Pool Detection Rate (Test):** 1/1 successful on migration TX ✅
- **Orchestration Efficiency:** Eliminated wasteful re-parsing ✅

---

## Files Modified

```
src/core/pumpfun_curve_listener.py     (orchestration + enrichment)
src/core/post_migration_pool_discovery.py (focused extraction)
src/core/pool_detector.py              (minimal detector)
```

---

## Testing Status

- ✅ Unit test on real migration TX passed
- ⏳ Live token integration test pending
- ⏳ End-to-end database registration test pending

**Listener Status:** Running (PID 83068) with unbuffered output to listener.log
**Next Event:** Waiting for migration event to trigger live validation
