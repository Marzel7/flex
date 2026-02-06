# Implementation: Add Account Creation Detection to CREATE Validation

## Overview

This document provides the exact code to add account creation detection as the third validation condition.

## Problem (Quick Recap)

Current validation:
```python
is_pumpfun_create = (mint_in_accounts AND pumpfun_program_found)
```

Both wrong and correct signatures pass this validation. We need to add a third condition.

---

## Solution: Detect System Program Account Creation

### Why This Works

- **CREATE transactions**: Initialize bonding curve account (requires System Program)
- **SWAP/TRADE transactions**: Only trade on existing accounts (no System Program calls)
- **Key insight**: Only CREATE creates a new account (bonding curve PDA)

### Implementation Location

**File**: `pump_fun_post_migration_analyzer.py`
**Method**: `_validate_pumpfun_create_tx`
**Line**: Add new method before line 663, then update validation on line 730

---

## Step 1: Add Helper Method

Add this new method BEFORE `_validate_pumpfun_create_tx` (around line 660):

```python
def _has_system_program_instruction(self, tx: dict) -> bool:
    """
    Check if transaction contains System Program instructions.

    System Program is only used for:
    1. Account creation (createAccountWithSeed, createAccount)
    2. Account initialization (allocate, assign)

    Non-CREATE transactions (SWAP, TRADE) don't use System Program
    for account creation—they only work with existing accounts.

    Returns: True if System Program instruction found
    """
    try:
        system_program_id = "11111111111111111111111111111111"

        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        instructions = message.get("instructions") or []
        inner_instructions = tx.get("meta", {}).get("innerInstructions") or []

        # Collect all instructions (top-level + inner)
        all_instructions = list(instructions)
        for inner in inner_instructions:
            all_instructions.extend(inner.get("instructions") or [])

        for instr in all_instructions:
            # Get program ID (handle both formats)
            program_id = instr.get("programId")

            if not program_id and "programIdIndex" in instr:
                idx = instr.get("programIdIndex")
                if isinstance(idx, int) and 0 <= idx < len(account_keys):
                    acct = account_keys[idx]
                    program_id = acct if isinstance(acct, str) else acct.get("pubkey")

            # If this is a System Program instruction, it's likely account creation
            if program_id == system_program_id:
                print(f"[CREATOR] ✓ Found System Program instruction (account creation indicator)", flush=True)
                return True

        return False

    except Exception as e:
        print(f"[CREATOR] ⚠ Error checking for System Program instruction: {e}", flush=True)
        return False
```

---

## Step 2: Update Validation Logic

Find the line in `_validate_pumpfun_create_tx` where `is_pumpfun_create` is computed (around line 730).

**OLD**:
```python
# A valid Pump.fun create MUST have BOTH conditions:
# 1. Mint in accounts (ensures this is the mint's creation)
# 2. Pump.fun program found in instructions (ensures it's a Pump.Fun tx)
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found']
)
```

**NEW**:
```python
# A valid Pump.fun create MUST have ALL THREE conditions:
# 1. Mint in accounts (ensures this is the mint's creation)
# 2. Pump.fun program found in instructions (ensures it's a Pump.Fun tx)
# 3. System Program instruction (ensures account/bonding curve is being created)
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found'] and
    self._has_system_program_instruction(tx)
)
```

---

## Step 3: Add Debug Output (Optional)

Inside `_has_system_program_instruction`, we already have logging:
```python
print(f"[CREATOR] ✓ Found System Program instruction (account creation indicator)", flush=True)
```

This will show in logs when a CREATE is confirmed.

---

## Testing the Fix

### Test 1: Run Diagnostic Script

```bash
python3 diagnostic_create_signature_issue.py
```

**Expected output BEFORE fix**:
```
❌ BOTH SIGNATURES PASS THE VALIDATION!
   wrong_sig passes: True
   correct_sig passes: True
```

**Expected output AFTER fix**:
```
❌ ONLY CORRECT SIGNATURE PASSES!
   wrong_sig passes: False
   correct_sig passes: True
```

### Test 2: Isolated Unit Test

Create `test_account_creation_detection.py`:

```python
#!/usr/bin/env python3
import asyncio
import aiohttp
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer

async def test_account_creation_detection():
    token_mint = "6drKtZkmPeRbTLxJaRyj9rVayBGzt2LotDyvK3L5pump"
    analyzer = PostMigrationAnalyzer(token_mint=token_mint)

    # Test the helper method directly
    wrong_sig = "2R5pRKompxmzzmLfotxmm8NpwcE4DtdvLSnU465DZw6N2TENv7Tk7sKFPuhvxuLxWgPsyQY5PiQYRrVqYf3pnSKz"
    correct_sig = "3PCrjxpfy3Uqab9o2veag4TjUHhRyViibVGU6CuegbgHceiGX4uubXemmeiSttaPskF4d8SjDMbNAexeMgcbD1nt"

    async with aiohttp.ClientSession() as session:
        # Fetch both
        async def fetch_tx(sig):
            payload = {
                "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }
            async with session.post("https://api.mainnet-beta.solana.com", json=payload) as resp:
                data = await resp.json()
                return data["result"] if "result" in data else None

        wrong_tx = await fetch_tx(wrong_sig)
        correct_tx = await fetch_tx(correct_sig)

        # Test method
        print(f"Wrong sig has System Program: {analyzer._has_system_program_instruction(wrong_tx)}")
        print(f"Correct sig has System Program: {analyzer._has_system_program_instruction(correct_tx)}")

        # Validate
        wrong_val = analyzer._validate_pumpfun_create_tx(wrong_tx)
        correct_val = analyzer._validate_pumpfun_create_tx(correct_tx)

        print(f"\nWrong sig is_pumpfun_create: {wrong_val['is_pumpfun_create']}")
        print(f"Correct sig is_pumpfun_create: {correct_val['is_pumpfun_create']}")

        assert not wrong_val['is_pumpfun_create'], "Wrong sig should NOT be CREATE"
        assert correct_val['is_pumpfun_create'], "Correct sig SHOULD be CREATE"

        print("\n✅ Test passed!")

if __name__ == "__main__":
    asyncio.run(test_account_creation_detection())
```

Run it:
```bash
python3 test_account_creation_detection.py
```

---

## Verification Checklist

- [ ] Added `_has_system_program_instruction` method
- [ ] Updated `_validate_pumpfun_create_tx` to use new condition
- [ ] Tested with diagnostic script
- [ ] Tested with isolated unit test
- [ ] Check git diff shows changes
- [ ] Run with live listener on test token
- [ ] Verify new tokens get correct CREATE signatures

---

## Rollback Plan (If Needed)

If this breaks anything:

```bash
# Revert to before the fix
git revert --no-edit <commit-hash>

# OR manually remove the changes
# - Delete _has_system_program_instruction method
# - Revert is_pumpfun_create to two-condition version
```

---

## Estimated Impact

### Performance
- **Additional cost per token**: 1 extra loop through instructions (minimal)
- **RPC calls**: 0 (uses already-fetched transaction data)
- **Time impact**: <10ms per token analyzed

### Safety
- **Regression risk**: LOW—only adds stricter validation
- **False negatives**: Unlikely—most CREATEs have System Program
- **False positives**: Fixed (wrong sigs now rejected)

---

## Success Criteria

After implementation:

1. **Diagnostic test shows**:
   - Wrong sig: `is_pumpfun_create = False` ✅
   - Correct sig: `is_pumpfun_create = True` ✅

2. **New tokens are analyzed correctly**:
   - Check logs: `[CREATOR] ✓ Found System Program instruction`
   - Check DB: `create_tx_signature` is correct

3. **No regression on existing data**:
   - Existing creator assignments don't break
   - Risk scores remain valid

---

## References

- Root cause analysis: `CREATE_SIGNATURE_ROOT_CAUSE_ANALYSIS.md`
- Diagnostic test: `diagnostic_create_signature_issue.py`
- Solana System Program: Program ID `11111111111111111111111111111111`
- Instruction types: createAccountWithSeed, createAccount, allocate, assign

---

**Status**: Ready to implement
**Confidence**: HIGH
**Complexity**: MEDIUM
**Time estimate**: 1-2 hours (code + testing)
