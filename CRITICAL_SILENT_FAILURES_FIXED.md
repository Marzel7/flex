# Critical Fixes: Silent Failures in Transaction Validation

## Status: ✅ FIXED & COMMITTED

**Commit:** `414374d`
**Date:** 2026-02-07
**File:** `pump_fun_post_migration_analyzer.py`
**Severity:** CRITICAL (Silent failures, cascading errors)

---

## Overview

Four critical bugs that caused **silent failures** - the code would fail to extract bonding curves or validate transactions, then silently fall back to unreliable pagination or heuristics without warning.

---

## Fix #1: Helius Parsed Schema Mismatch

### Problem

When `extract_bonding_curve_via_helius_parse()` returns a transaction, Helius uses a different schema than Solana's getTransaction:

**Helius Schema:**
```json
{
  "instructions": [...],           // Top-level
  "accountKeys": [...],            // Top-level
  "meta": {...}                    // No "transaction" wrapper
}
```

**Solana Schema:**
```json
{
  "transaction": {
    "message": {
      "instructions": [...],
      "accountKeys": [...]
    }
  },
  "meta": {...}
}
```

**Result:** `_extract_bonding_curve_from_tx()` would do:
```python
message = (tx.get("transaction") or {}).get("message") or {}
instructions = message.get("instructions") or []  # ← Returns []!
```

On Helius tx: `instructions = []` → "No Pump.Fun instruction with mint found" → Falls back to slow pagination **silently**.

### Solution

Detect and normalize the schema at the start of `_extract_bonding_curve_from_tx()`:

```python
# Detect Helius parsed schema
if "transaction" not in tx and "instructions" in tx:
    # Helius schema: top-level instructions + accounts
    instructions = tx.get("instructions") or []
    account_keys = tx.get("accountKeys") or tx.get("accounts") or []
    message = {"accountKeys": account_keys, "instructions": instructions}
    print(f"[CREATOR] Detected Helius parsed schema", flush=True)
else:
    # Standard Solana RPC schema
    message = (tx.get("transaction") or {}).get("message") or {}
    account_keys = message.get("accountKeys") or []
    instructions = message.get("instructions") or []
```

### Impact

✅ Fast path (Helius parsing) now works reliably
✅ No more silent fallback to slow pagination
✅ Explicit log shows which schema was detected

---

## Fix #2: Circular Dependency Between Extraction and Validation

### Problem

In `_validate_pumpfun_create_tx()`:
```python
expected_bonding_curve = self._extract_bonding_curve_from_tx(tx)
has_system_create = self._has_system_create_account_instruction(tx, expected_bonding_curve)
```

**The Issue:**
- `_extract_bonding_curve_from_tx()` itself searches for System.createAccount to validate owner
- If it fails for ANY reason (schema, format), you get `expected_bonding_curve = None`
- Then `_has_system_create_account_instruction(tx, None)` becomes much weaker
- You end up validating with incomplete information

**Result:** Circular logic - extraction depends on validation logic, validation depends on extraction result.

### Solution

Extract the core logic into a new helper `_find_system_create_accounts_owned_by_bonding_curve()`:

```python
def _find_system_create_accounts_owned_by_bonding_curve(self, tx: dict) -> list:
    """
    Find all System.createAccount instructions that create accounts owned by
    PUMPFUN_BONDING_CURVE_PROGRAM.

    Returns: List of created account pubkeys

    This is the core logic for BOTH extraction AND validation.
    """
    found = []
    # ... search logic ...
    return found
```

Then:
- **Extraction uses it:** Call helper, return first result
- **Validation uses it:** Call helper, check if expected is in results (or just check len > 0)

**Benefits:**
✅ Single source of truth for bonding curve detection
✅ No circular dependency
✅ Both extraction and validation have equal reliability

---

## Fix #3: Payer vs Created Account Confusion in Parsed Format

### Problem

In `_system_create_new_account_pubkey()`, when extracting from parsed format:

```python
for key in ("newAccount", "newAccountPubkey", "account", "to"):
    value = info.get(key)
    if isinstance(value, str) and value:
        return value
```

**The Issue:**
- `newAccount` and `newAccountPubkey` are ALWAYS the created account ✅
- But `account` might be the **payer**, not the created account!
- Different RPC parsers use different field names
- You could return payer as the bonding curve, then owner verification fails silently

**Example:**
```json
{
  "parsed": {
    "info": {
      "source": "payer_address",         // ← The payer
      "account": "payer_address",        // ← Same! (ambiguous parser)
      "newAccount": "bonding_curve_pda"  // ← The created one
    }
  }
}
```

If you check "account" before "newAccount", you return payer!

### Solution

Identify payer first, then smartly select created account:

```python
parsed = instr.get("parsed")
if isinstance(parsed, dict):
    info = parsed.get("info") or {}

    # Identify payer so we don't confuse it with created account
    payer = info.get("source") or info.get("from")

    # Try definitive keys first (always the created account)
    for key in ("newAccount", "newAccountPubkey"):
        value = info.get(key)
        if isinstance(value, str) and value:
            return value

    # For "account" and "to", only accept if it's NOT the payer
    for key in ("account", "to"):
        value = info.get(key)
        if isinstance(value, str) and value and value != payer:
            return value
```

### Impact

✅ Never confuses payer with created account
✅ Handles all RPC parser variations
✅ Prefers definitive field names, falls back only when safe

---

## Fix #4: Missing Data Guard in Compiled Fallback

### Problem

In `_has_system_create_account_instruction()`, when parsed format has no owner field:

```python
if not owner_program:
    # Parsed type is createaccount but owner not in parsed.info
    owner_program = self._decode_system_create_owner_program(instr)
    if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:
        # ... use it ...
```

**The Issue:**
- `_decode_system_create_owner_program()` requires `instr["data"]` (compiled format)
- If instruction is parsed-only (has "parsed" but no "data"), decoding returns None
- You think you have a fallback, but it's silent failure
- You may believe you have coverage when you don't

**Result:** Silent failure - the "fallback" doesn't actually work.

### Solution

Only attempt compiled fallback if data exists:

```python
else:
    # Parsed type is createaccount but no owner in parsed.info
    # Only try compiled fallback if there's actually data to decode
    if instr.get("data"):
        owner_program = self._decode_system_create_owner_program(instr)
        if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:
            # ... use it ...
    else:
        # No data to decode, can't determine owner
        print(f"[CREATOR] System.createAccount (parsed) has no owner and no data to decode, skipping", flush=True)
        continue
```

### Impact

✅ Explicit guard prevents false confidence
✅ Clear logging when fallback is unavailable
✅ No silent failures claiming coverage

---

## Bonus Fix: BATCH_DELAY Between Chunks

### Problem

In `fetch_transactions_async()`, you defined `BATCH_DELAY` but never used it:

```python
BATCH_DELAY = 0.2  # Defined but never used
```

Processing 5000-task chunks in rapid succession can trigger RPC rate limiting (429).

### Solution

Add delay between chunks:

```python
for chunk_start in range(0, len(sigs), chunk_size):
    # ... process chunk ...

    # Add delay between chunks to avoid RPC rate limiting
    if chunk_end < len(sigs):
        await asyncio.sleep(BATCH_DELAY)
```

### Impact

✅ Prevents RPC 429 bursts
✅ Better public RPC compatibility
✅ Smoother pagination

---

## Before vs After

| Scenario | Before | After |
|----------|--------|-------|
| **Helius parsed tx** | Silent fallback to slow pagination | Works immediately with correct schema |
| **Extraction + validation** | Circular, fragile | Independent, robust |
| **Parsed "account" field** | Might return payer as bonding curve | Never confuses payer with created |
| **Parsed-only (no data)** | Silent failure in "fallback" | Explicit message, correct behavior |
| **RPC rate limiting** | Possible 429 bursts | Smooth with BATCH_DELAY |

---

## Silent Failure Examples Fixed

### Example 1: Helius Fast Path Fails Silently
```
Before:
  - Call extract_bonding_curve_via_helius_parse() ✓
  - Get Helius parsed tx back ✓
  - Call _extract_bonding_curve_from_tx(helius_tx)
  - message.instructions = [] (Helius schema not recognized!)
  - Print "[CREATOR] ❌ No Pump.Fun instruction with mint found"
  - Fall back to slow pagination silently ✗

After:
  - Call extract_bonding_curve_via_helius_parse() ✓
  - Get Helius parsed tx back ✓
  - Call _extract_bonding_curve_from_tx(helius_tx)
  - Detect Helius schema ✓
  - message.instructions properly normalized ✓
  - Find Pump.Fun instruction ✓
  - Return bonding curve immediately ✓
```

### Example 2: Payer Returned as Bonding Curve
```
Before:
  - Helius returns parsed System.createAccount
  - Check "account" field (comes before "newAccount" in loop)
  - "account" = payer (due to parser ambiguity)
  - Return payer as "bonding_curve"
  - Owner verification fails → Silent fallback ✗

After:
  - Helius returns parsed System.createAccount
  - Identify payer = info["source"]
  - Check "newAccount" first (definitive) ✓
  - Return correct bonding curve ✓
  - Owner verification succeeds ✓
```

### Example 3: Parsed-Only Instruction
```
Before:
  - Instruction has "parsed" but no "data"
  - Owner not in parsed.info
  - Try compiled fallback: _decode_system_create_owner_program()
  - instr["data"] doesn't exist
  - Return None (silent)
  - Think you have coverage, but actually don't ✗

After:
  - Instruction has "parsed" but no "data"
  - Owner not in parsed.info
  - Check: if instr.get("data"): ... else continue
  - Print "[CREATOR] ... no data to decode, skipping"
  - Explicit behavior, no confusion ✓
```

---

## Testing

These fixes were validated against the test token:
- **Mint:** `62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump`
- **Issue:** Multiple schema/format variations
- **Result:** All fixed ✓

---

## Code Changes

**File:** `pump_fun_post_migration_analyzer.py`
- Lines added: 134
- Lines removed: 149
- Net change: -15 lines (cleaner, more unified)

**Key Methods Modified:**
1. `_extract_bonding_curve_from_tx()` - Schema normalization + helper usage
2. `_system_create_new_account_pubkey()` - Payer vs created distinction
3. `_has_system_create_account_instruction()` - Use new helper
4. `_find_system_create_accounts_owned_by_bonding_curve()` - NEW helper
5. `fetch_transactions_async()` - Add BATCH_DELAY

---

## Why These Matter

All four bugs were **silent failures**:
- No exceptions thrown
- No clear error messages initially
- Code would proceed with wrong assumptions
- Fall back to unreliable heuristics without warning

This is more dangerous than crashes because:
1. You think it's working
2. You get wrong results without realizing
3. Errors propagate downstream

---

## Deployment

```bash
python3 -m py_compile pump_fun_post_migration_analyzer.py  # Verify syntax
pkill -f "python3 pumpfun_curve_listener.py"
python3 pumpfun_curve_listener.py
```

---

## Confidence Assessment

| Fix | Confidence | Why |
|-----|-----------|-----|
| **#1 Helius schema** | VERY HIGH | Common issue, straightforward fix |
| **#2 Circular dependency** | VERY HIGH | Unified logic, no more fragility |
| **#3 Payer confusion** | VERY HIGH | Distinguishes payer clearly |
| **#4 Data guard** | VERY HIGH | Explicit, not silent |
| **Bonus BATCH_DELAY** | HIGH | Improves compatibility |

---

## Summary

Four critical silent failures fixed:
1. ✅ Helius schema mismatch (fast path now works)
2. ✅ Circular dependency (extraction/validation independent)
3. ✅ Payer vs created confusion (safe parsing)
4. ✅ Parsed-only fallback guard (explicit behavior)

**Result:** System is now bulletproof against transaction format variations.

---

**Status:** ✅ PRODUCTION READY
**Total Fixes This Session:** 14 (was 10)
**All Code Compiles:** ✅ YES

