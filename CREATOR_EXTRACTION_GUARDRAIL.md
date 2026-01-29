# Creator Extraction Guardrail - Detailed Reference

**Date**: 2026-01-29
**Status**: ✅ PRODUCTION READY
**Commits**: `aae40a8`, `00c4850`

---

## The Principle

**Crisp Definition:**

✅ **Creator** = fee payer of the Pump.fun CREATE transaction (usually `accountKeys[0]`, must be a signer)

❌ **NOT creator** = fee payer of the earliest bonding curve transaction (that's just "who paid for earliest activity" — could be a bot, router, or program)

---

## The Guardrail

The system now enforces a strict guardrail to prevent false creator attribution:

```python
# GUARDRAIL: Only use fee payer as "creator" if transaction is confirmed as CREATE
# is_pumpfun_create = (mint_in_accounts AND pumpfun_program_found)
if self._create_tx_creator and validation['is_pumpfun_create']:
    creator = self._create_tx_creator  # Assign as creator
    print(f"✓ Creator = CREATE tx fee payer: {creator}")
else:
    creator = None  # Don't assign creator yet
    print(f"⚠ Not assigning as creator (CREATE not confirmed)")
```

### What This Means in Practice

1. **Fee payer is ALWAYS extracted**
   - Even if CREATE is not yet confirmed
   - Stored for diagnostic/logging purposes

2. **Creator is ONLY assigned** when:
   - Transaction is confirmed as Pump.fun CREATE
   - Validation passes: `is_pumpfun_create = (mint_in_accounts AND pumpfun_program_found)`
   - Both conditions required (AND logic, not OR)

3. **Status reflects confidence:**
   - `status='confirmed'`: Creator assigned, reached history end, is_pumpfun_create=True
   - `status='unproven'`: Any validation condition failed or pagination incomplete

4. **No false attribution:**
   - If CREATE not confirmed, `creator=None`
   - Fee payer still extracted (available as `fee_payer` field)
   - Can be used for debugging but not for creator attribution

---

## Validation Conditions

### Two-Part CREATE Validation

```
is_pumpfun_create = (mint_in_accounts AND pumpfun_program_found)
```

Both conditions must be true:

1. **mint_in_accounts**:
   - The token mint appears in transaction account keys
   - Proves this transaction touched the token

2. **pumpfun_program_found**:
   - A Pump.fun program ID appears in transaction instructions
   - Proves this is a Pump.fun operation (not some other token system)

### Only Both Together = CREATE

| Scenario | mint_in_accounts | pumpfun_program | is_pumpfun_create | Creator Assigned? |
|----------|-----------------|-----------------|-------------------|------------------|
| Real CREATE | ✅ True | ✅ True | ✅ True | ✅ Yes |
| Trading on bonding curve | ✅ True | ❌ False | ❌ False | ❌ No |
| Other program using mint | ❌ False | ✅ True | ❌ False | ❌ No |
| Unrelated transaction | ❌ False | ❌ False | ❌ False | ❌ No |

---

## Why This Guardrail Matters

### Problem It Solves

Without the guardrail, the system could label anyone who paid for an activity on a bonding curve as "the creator":

```
Scenario: Trading bot executes swap on bonding curve
- Bot pays gas fee (fee_payer = bot)
- Earliest bonding curve transaction happens to be from bot
- WITHOUT guardrail: Bot gets labeled as "creator" ❌
- WITH guardrail: Activity not confirmed as CREATE, so no creator attribution ✅
```

### Protection Mechanism

1. **CREATE is the only original creation event** - happens exactly once per token
2. **Only fee payer from CREATE** = true creator (verified by signature)
3. **Any other bonding curve activity** = trading/activity (not creation)
4. **Guardrail prevents confusion** between these two categories

---

## Real-World Example

### Test Token: 3bzeQJqDPfQQbxJ6VmtMkXyyQEJrj9HS1SMvHR1Hpump

**Validation Process:**
```
1. Find bonding curve PDA from CREATE tx ✓
2. Query bonding curve account history ✓
3. Validate earliest transaction ✓
   - is_pumpfun_create = True (mint + program both present)
   - reached_end = True (pagination complete)
4. Extract fee payer as creator ✓
   - Creator = fee payer from CREATE tx
5. Return status='confirmed' ✓
   - All conditions met, creator reliably identified
```

**Output:**
```
✅ CONFIRMED CREATOR: AHEmzpd2UR1EAcpkArY8SXaf3EFpDkTxfUDGfodqUDyH
━━ 6 VALIDATION CRITERIA ━━
  ✅ status = 'confirmed'
  ✅ reached_end = True
  ✅ is_pumpfun_create = True
  ✅ pumpfun_program_found = True
  ✅ mint_in_accounts = True
  ✅ earliest_sig exists = True
```

---

## Implementation Details

### Guardrail Location

**File**: `pump_fun_post_migration_analyzer.py`

**Method**: `get_creator_from_earliest_tx()` (lines 1353-1363)

```python
# GUARDRAIL: Only use fee payer as "creator" if transaction is confirmed as CREATE
# is_pumpfun_create = (mint_in_accounts AND pumpfun_program_found)
if self._create_tx_creator and validation['is_pumpfun_create']:
    print(f"[CREATOR] ✓ Creator = CREATE tx fee payer: {creator}", flush=True)
    force_creator = self._create_tx_creator
else:
    force_creator = None
```

### Key Instance Variables

```python
self._create_tx_creator = None        # Fee payer from CREATE tx
self._create_tx_validation = None     # Validation result from CREATE tx
self._create_tx_signature = None      # Signature of CREATE tx
```

### Fee Payer Extraction

**Where**: `extract_bonding_curve_from_creation_tx()` (lines 1048-1067)

```python
# Extract and store the CREATE transaction's fee payer (true creator)
message = earliest_create_tx.get("transaction", {}).get("message", {})
account_keys = message.get("accountKeys", [])

if account_keys:
    first_key = account_keys[0]
    if isinstance(first_key, dict):
        fee_payer = first_key.get("pubkey")
    else:
        fee_payer = str(first_key)

    if fee_payer:
        self._create_tx_creator = fee_payer
```

---

## Fallback Behavior

If CREATE validation is not available:

```python
if self._create_tx_validation:
    # Use stored CREATE validation (preferred)
    validation = self._create_tx_validation
else:
    # Fallback: validate earliest bonding curve tx
    validation = self._validate_pumpfun_create_tx(tx)
```

This maintains backward compatibility while preferring the more reliable CREATE validation.

---

## Testing the Guardrail

To verify the guardrail is working:

```python
import asyncio
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer

async def test():
    analyzer = PostMigrationAnalyzer('TOKEN_MINT_HERE')
    provenance = await analyzer.get_creator_from_earliest_tx()

    # Check that creator only assigned when confirmed
    assert (provenance['creator'] is not None) == provenance['is_pumpfun_create']

    # Check that status reflects validation
    if provenance['is_pumpfun_create'] and provenance['reached_end']:
        assert provenance['status'] == 'confirmed'
    else:
        assert provenance['status'] == 'unproven'

    print("✅ Guardrail working correctly")

asyncio.run(test())
```

---

## Future Implications

This guardrail prevents future bugs related to:

1. **False creator attribution** - Wrong addresses marked as creators
2. **Blocklist false positives** - Innocent accounts blocked incorrectly
3. **Risk scoring errors** - Incorrect reputation propagation
4. **Funding network confusion** - Mixing trader activity with creator funding

---

## Summary

The guardrail is a simple but critical safety mechanism:

**Before**: "Fee payer from earliest bonding curve tx" → Sometimes wrong
**After**: "Fee payer from CREATE tx IF and only IF CREATE is confirmed" → Reliable

This ensures creator attribution is only made with high confidence, preventing false positives in security and risk analysis.

---

**Commits**:
- `aae40a8` - Initial implementation
- `00c4850` - Guardrail enforcement + documentation

**Status**: ✅ Production Ready - Tested on real tokens
