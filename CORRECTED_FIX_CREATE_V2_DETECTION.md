# Corrected Fix: Pump.Fun create_v2 Detection

## The Correction

Your original observation was right, but the proposed fix was too broad.

### ❌ Original (Too Broad)
```python
has_system_program_instruction  # ANY System instruction
```

**Problem**: SWAP/SELL transactions also have System Program instructions (e.g., Jito tips).
- Your "wrong" tx: Has System Program.transfer (Jito tip) → Would still pass!

### ✅ Corrected (Specific)
```python
has_pumpfun_create_instruction  # Specifically create_v2, not buy/sell
```

**Why this works**:
- CREATE transactions have: `Pump.fun.create_v2`
- SELL transactions have: `Pump.fun.sell` (different instruction)
- Your wrong tx has `.sell` → Will be rejected ✅
- Your correct tx has `.create_v2` → Will be accepted ✅

---

## Implementation: Two Approaches

### Approach A: Check for create_v2 by Decoded Name (EASIEST)

If using Helius or jsonParsed format that includes instruction type:

```python
def _has_pumpfun_create_instruction(self, tx: dict) -> bool:
    """
    Check if transaction contains Pump.fun CREATE (create_v2) instruction.

    Returns True only if a create_v2 instruction is found, not sell/buy.
    """
    message = (tx.get("transaction") or {}).get("message") or {}
    instructions = message.get("instructions") or []
    inner_instructions = tx.get("meta", {}).get("innerInstructions") or []

    all_instructions = list(instructions)
    for inner in inner_instructions:
        all_instructions.extend(inner.get("instructions") or [])

    pumpfun_ids = {"pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
                   "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"}

    for instr in all_instructions:
        # Get program ID
        program_id = instr.get("programId")
        if not program_id and "programIdIndex" in instr:
            account_keys = message.get("accountKeys") or []
            idx = instr.get("programIdIndex")
            if isinstance(idx, int) and 0 <= idx < len(account_keys):
                acct = account_keys[idx]
                program_id = acct if isinstance(acct, str) else acct.get("pubkey")

        if program_id not in pumpfun_ids:
            continue

        # ✅ Check for create instruction (jsonParsed format)
        if "parsed" in instr:
            parsed_type = instr.get("parsed", {}).get("type", "").lower()
            if "create" in parsed_type:
                print(f"[CREATOR] ✓ Found Pump.fun create instruction (type: {parsed_type})", flush=True)
                return True

        # ✅ Alternative: Check instruction data (raw format)
        # Pump.fun create_v2 discriminator: first 8 bytes of instruction data
        # This is an Anchor discriminator
        if "data" in instr:
            data = instr.get("data")
            if isinstance(data, str):
                # Discriminator for create_v2 in Anchor
                # We'd need to compare, but easier to use parsed format above
                pass

    return False
```

**Usage in validation**:
```python
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found'] and
    self._has_pumpfun_create_instruction(tx)
)
```

---

### Approach B: Detect System.createAccount (Not Transfer)

More robust for when instruction types aren't available:

```python
def _has_system_create_account_instruction(self, tx: dict) -> bool:
    """
    Check for System Program createAccount instruction.

    Distinguishes between:
    - createAccount / createAccountWithSeed (account creation) ✅
    - transfer (not account creation, e.g., Jito tips) ❌
    """
    message = (tx.get("transaction") or {}).get("message") or {}
    account_keys = message.get("accountKeys") or []
    instructions = message.get("instructions") or []
    inner_instructions = tx.get("meta", {}).get("innerInstructions") or []

    all_instructions = list(instructions)
    for inner in inner_instructions:
        all_instructions.extend(inner.get("instructions") or [])

    system_program = "11111111111111111111111111111111"

    for instr in all_instructions:
        # Get program ID
        program_id = instr.get("programId")
        if not program_id and "programIdIndex" in instr:
            idx = instr.get("programIdIndex")
            if isinstance(idx, int) and 0 <= idx < len(account_keys):
                acct = account_keys[idx]
                program_id = acct if isinstance(acct, str) else acct.get("pubkey")

        if program_id != system_program:
            continue

        # Check instruction type
        if "parsed" in instr:
            parsed_type = instr.get("parsed", {}).get("type", "").lower()
            # createAccount, createAccountWithSeed, allocate, assign (account creation)
            # NOT transfer (that's for payments/tips)
            if parsed_type in {"createaccount", "createaccountwithseed", "allocate", "assign"}:
                print(f"[CREATOR] ✓ Found System Program account creation (type: {parsed_type})", flush=True)
                return True

    return False
```

**Usage in validation**:
```python
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found'] and
    self._has_system_create_account_instruction(tx)  # NOT any System instruction
)
```

---

### Approach C: Combined (Recommended for Robustness)

Use both checks with OR logic (if either is found, it's likely a CREATE):

```python
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found'] and
    (
        self._has_pumpfun_create_instruction(tx) or
        self._has_system_create_account_instruction(tx)
    )
)
```

This catches:
- ✅ Transactions with explicit create_v2 instruction names
- ✅ Transactions with System createAccount for bonding curve
- ❌ Rejects SWAP with only System transfer (Jito tip)
- ❌ Rejects SELL with only System transfer

---

## Why This Is Better Than Original Proposal

### Original Proposal
```python
has_system_program_instruction  # ❌ WRONG
```

**Test Results**:
- Wrong tx (SELL): Has System.transfer → STILL PASSES ❌
- Correct tx (CREATE): Has System.createAccount → PASSES ✅

**Result**: Doesn't fix the problem!

### Corrected Fix
```python
has_pumpfun_create_instruction  # ✅ RIGHT
# OR
has_system_create_account_instruction  # ✅ RIGHT (excludes transfer)
```

**Test Results**:
- Wrong tx (SELL): No create_v2, only System.transfer → REJECTED ✅
- Correct tx (CREATE): Has create_v2 OR System.createAccount → ACCEPTED ✅

**Result**: Actually fixes the problem! ✅

---

## Implementation Steps

### Step 1: Add Both Helper Methods

Add these two methods to `PostMigrationAnalyzer` class (before `_validate_pumpfun_create_tx`):

```python
def _has_pumpfun_create_instruction(self, tx: dict) -> bool:
    """Check for Pump.fun create_v2 instruction by parsed type."""
    # [See code above]

def _has_system_create_account_instruction(self, tx: dict) -> bool:
    """Check for System Program account creation (not transfer)."""
    # [See code above]
```

### Step 2: Update Validation Logic

Find line 742-745 in `_validate_pumpfun_create_tx`:

**OLD**:
```python
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found']
)
```

**NEW**:
```python
# A valid Pump.fun CREATE must have ALL THREE conditions:
# 1. Mint in accounts (ensures this is the mint's creation)
# 2. Pump.fun program found (ensures it's a Pump.Fun tx)
# 3. Pump.fun create_v2 instruction OR System account creation (not just any instruction)
result['is_pumpfun_create'] = (
    result['mint_in_accounts'] and
    result['pumpfun_program_found'] and
    (
        self._has_pumpfun_create_instruction(tx) or
        self._has_system_create_account_instruction(tx)
    )
)
```

### Step 3: Update Bonding Curve Extraction

In `_extract_bonding_curve_from_tx()`, prioritize `create_v2` instructions:

**Current behavior** (line 1219):
```python
for ix_idx, ix in enumerate(all_ix):
    # Find ANY Pump.Fun instruction
    if program_id not in PUMPFUN_PROGRAM_IDS:
        continue
```

**Better behavior**:
```python
# Priority 1: Find Pump.Fun CREATE instruction (create_v2)
create_instruction = None
for ix_idx, ix in enumerate(all_ix):
    program_id = ix.get("programId")
    # [resolve programIdIndex]

    if program_id not in PUMPFUN_PROGRAM_IDS:
        continue

    # Check if this is create_v2
    if "parsed" in ix:
        if "create" in ix.get("parsed", {}).get("type", "").lower():
            create_instruction = ix
            break

# Priority 2: Fall back to first Pump.Fun instruction
if not create_instruction:
    for ix in all_ix:
        if [get program_id and check if Pump.Fun]:
            create_instruction = ix
            break

# Extract bonding curve from chosen instruction
if create_instruction:
    # [existing extraction logic]
```

---

## Testing the Fix

### Before Implementation
```bash
python3 diagnostic_create_signature_issue.py
```

**Output**:
```
❌ BOTH SIGNATURES PASS THE VALIDATION!
   wrong_sig passes: True
   correct_sig passes: True
```

### After Implementation
```bash
python3 diagnostic_create_signature_issue.py
```

**Expected Output**:
```
✅ ONLY CORRECT SIGNATURE PASSES!
   wrong_sig passes: False (rejected - has sell, not create_v2)
   correct_sig passes: True (accepted - has create_v2)
```

---

## Key Differences: System.transfer vs System.createAccount

### System.transfer (Jito Tip in SELL)
```
Program: System Program (11111111...)
Parsed Type: "transfer"
Effect: Sends SOL to Jito (not creating account)
```

### System.createAccount (Bonding Curve Creation)
```
Program: System Program (11111111...)
Parsed Type: "createAccount" or "createAccountWithSeed"
Effect: Creates bonding curve PDA (new account)
```

---

## Data from Your Examples

### Wrong Transaction (SELL)
```
Pump.fun.sell instruction
System.transfer (Jito tip) ← Not account creation!
Token.transferChecked
```

### Correct Transaction (CREATE)
```
Pump.fun.create_v2 instruction ← ✅ Detected!
Pump.fun.extend_account
System.createAccount ← ✅ Also detected!
ATA.createIdempotent
Pump.fun.buy
```

**With corrected fix**:
- Wrong: rejected (no create_v2, no System.createAccount)
- Correct: accepted (has create_v2)

---

## Summary

| Aspect | Original Proposal | Corrected Fix |
|--------|-------------------|---------------|
| **Check** | Any System instruction | Pump.fun create_v2 OR System.createAccount |
| **Your wrong tx** | PASSES ❌ | REJECTED ✅ |
| **Your right tx** | PASSES ✅ | ACCEPTED ✅ |
| **Specificity** | Too broad | Precise |
| **Robustness** | Low | High |

**Status**: Ready to implement with your guidance
**Recommendation**: Use Approach C (combined) for best robustness
**Effort**: 2-3 hours implementation + testing
