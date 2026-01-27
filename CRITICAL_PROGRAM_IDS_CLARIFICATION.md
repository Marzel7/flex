# Critical Clarification: Pump.fun Program IDs vs Accounts

## Status: ✅ CORRECTED AND IMPLEMENTED

**Date**: 2026-01-27
**Commit**: c77bb82 - "Fix: Critical correction to Pump.fun program ID constants"

---

## The Critical Mistake

**What We Had Wrong**:
```python
PUMPFUN_PROGRAM_IDS = {
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # WRONG - This is NOT a program ID!
}
```

**What This Actually Is**:
- `39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg` is a **Pump.fun Migration Account** (an address)
- Used in "graduation" or "migration" flows when tokens migrate to PumpSwap
- It appears in `transaction.message.accountKeys` (account list), NOT as `instruction.programId`

**Why This Matters**:
- Programs (instruction.programId): What operations are being invoked
- Accounts (accountKeys): What addresses are involved in the operation
- Confusing them breaks validation entirely

---

## What We Changed To

### Correct: Split Programs from Accounts

```python
# PROGRAMS: These appear in instruction.programId or resolved via programIdIndex
PUMPFUN_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # Swap/AMM program
PUMPFUN_BONDING_CURVE_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"  # Bonding curve program

PUMPFUN_PROGRAM_IDS = {
    PUMPFUN_AMM_PROGRAM,
    PUMPFUN_BONDING_CURVE_PROGRAM,
}

# ACCOUNTS: These are addresses in transaction.message.accountKeys
PUMPFUN_MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
```

**Source**: Solscan Pump.fun documentation

---

## Understanding the Transaction Structure

### Transaction Format
```json
{
  "transaction": {
    "message": {
      "accountKeys": [
        "..."  ← ACCOUNTS (addresses involved)
        "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  ← PUMP.FUN MIGRATION ACCOUNT
        "..."
      ],
      "instructions": [
        {
          "programId": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  ← PROGRAM (what's being called)
          "accounts": [0, 1, 2, ...],  ← Indexes into accountKeys
          "data": "..."
        },
        {
          "programId": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  ← PROGRAM (bonding curve)
          "accounts": [0, 2, 3, ...],
          "data": "..."
        }
      ]
    }
  }
}
```

### Key Distinction
- **PROGRAMS** (instruction.programId): The smart contract being invoked
- **ACCOUNTS** (accountKeys): The addresses that the program operates on

---

## Pump.fun Programs Explained

### 1. **AMM Program** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`)
- **Purpose**: Swap/AMM operations
- **Appears in**: Swap transactions
- **Activity**: Trading, liquidity operations
- **Where**: `instruction.programId`

### 2. **Bonding Curve Program** (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`)
- **Purpose**: Bonding curve operations (CREATE, initial launch)
- **Appears in**: Token creation transactions
- **Activity**: Bonding curve initialization, initial launches
- **Where**: `instruction.programId`

### 3. **Migration Account** (`39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg`)
- **Purpose**: Marker for migration/graduation flows
- **Appears in**: When tokens graduate from Pump.fun
- **Activity**: Graduation to full Solana tokens
- **Where**: `accountKeys` (NOT programId)

---

## Impact on Creator Extraction

### Before (Wrong)
```python
PUMPFUN_PROGRAM_IDS = {"39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"}

# Validation in _validate_pumpfun_create_tx():
for instr in all_instructions:
    program_id = instr.get("programId")
    if program_id in PUMPFUN_PROGRAM_IDS:  # ← NEVER matches!
        result['pumpfun_program_found'] = True
```

**Result**: Never finds Pump.fun CREATE transactions because we're looking for an account ID in the program ID field.

### After (Correct)
```python
PUMPFUN_PROGRAM_IDS = {
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
}

# Validation in _validate_pumpfun_create_tx():
for instr in all_instructions:
    program_id = instr.get("programId")
    if not program_id and "programIdIndex" in instr:
        idx = instr.get("programIdIndex")
        if 0 <= idx < len(account_pubkeys):
            program_id = account_pubkeys[idx]

    if program_id in PUMPFUN_PROGRAM_IDS:  # ← NOW matches!
        result['pumpfun_program_found'] = True
```

**Result**: Correctly identifies Pump.fun instructions and validates CREATEs.

---

## Additional Fix: Mint Address Sanitization

### The Issue
Users might pass mint addresses with "pump" suffix from URL slugs:
```
62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump  ← Invalid base58 (trailing "pump")
```

### The Fix
```python
def __init__(self, token_mint: str, ...):
    # Strip "pump" suffix if copied from URL/slug
    if token_mint.endswith("pump"):
        token_mint = token_mint[:-4]

    self.token_mint = token_mint  # Now valid base58
```

**Example**:
```
Input:  62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump
Output: 62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYj  ← Valid for RPC calls
```

---

## Summary

✅ **Critical Bug Fixed**: Wrong program ID constant replaced with correct Pump.fun program IDs
✅ **Clarification Complete**: Programs vs Accounts distinction now clear
✅ **Validation Ready**: _validate_pumpfun_create_tx() now uses correct program IDs
✅ **Mint Sanitization**: URL slug handling added

**Status**: Ready for production use with correct program IDs

**Expected Outcome**: Creator extraction will now reliably identify and validate Pump.fun CREATE transactions

---

**Last Updated**: 2026-01-27
**Source**: Solscan Pump.fun Documentation
**Ready for**: Integration testing and deployment
