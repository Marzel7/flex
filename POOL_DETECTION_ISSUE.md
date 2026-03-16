# Pool Detection Issue — Summary

## The Problem

Pool detector finds PumpSwap program-owned accounts but accepts helper/config PDAs instead of actual pool state accounts.

### Symptom
All tokens get identical vault addresses:
```
Token A: base=EZGLemQL2H2oCUDk... quote=9AQ5oouQjPDAaPn5...
Token B: base=EZGLemQL2H2oCUDk... quote=9AQ5oouQjPDAaPn5...
Token C: base=EZGLemQL2H2oCUDk... quote=9AQ5oouQjPDAaPn5...
```

### Root Cause
Parser validation only checks size (>=296 bytes), not account type. Helper PDAs pass this check.

---

## What We Did

### 1. Added Extraction Hardening (Complete)
- Size validation: 165 bytes for SPL token accounts
- Vault existence checks
- Mint match validation
- Result: **Prevents bad data from entering database ✅**

### 2. Improved Parser (In Progress)
- Added discriminator check: Raydium AMM pools have specific byte pattern
- Added vault padding check: Detects garbage data (all 0s or 1s)
- Result: **Rejects helper PDAs, but now no pool found ❓**

---

## Current Status

### Test Results
```
Before parser improvement:
  Detection: Found pool ✅
  Extraction: Rejected as invalid ✅
  Database: Prevented from registering ✅

After parser improvement:
  Detection: Found 4 candidates, rejected all ✅
  Extraction: N/A
  Database: Clean (no bad pools) ✅
```

### Issue Now
Parser correctly rejects helper PDAs, but the **actual pool is not in the migration transaction**.

---

## Why This Happens

The Raydium pool might be:
1. Created in a **separate transaction** (after migration)
2. Created by a **different contract** (migrator program)
3. Stored at a **different account** (not directly visible in migration tx)

---

## Solution Needed

Pool discovery must:
1. **Search beyond migration transaction** (check subsequent txs)
2. **Verify real pool state** (use discriminator check)
3. **Find accounts where vault tokens match launched token**

Or: Look for pools in Raydium program state by mint address.

---

## Files Modified
- `src/core/pool_parser_dispatcher.py`: Added discriminator + vault validation
- `src/core/pool_discovery.py`: Added size validation + logging

## Status
✅ Hardening prevents bad data
✅ Parser rejects wrong account types
❌ Need to find where actual pool is stored

Next: Improve fallback pool discovery mechanism.
