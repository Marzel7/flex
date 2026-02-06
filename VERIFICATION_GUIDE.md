# Verification Guide: Three Critical Fixes

## Quick Start

After deploying these fixes, use this guide to verify they're working correctly.

---

## Fix #1: Fast-Path Bug

### What to Look For

**When querying the mint for CREATE signature (should use fast-path):**
```
[CREATOR] 🚀 Fast path: Already have CREATE tx signature, skipping pagination
```

**When querying the bonding curve for earliest activity (should NOT use fast-path):**
```
[CREATOR] Page 1: 1000 sigs from bonding_curve_pda (api.mainnet-beta...)
[CREATOR] Page 2: 1000 sigs from bonding_curve_pda (api.mainnet-beta...)
...
[CREATOR] ✅ Reached true end of history (bonding_curve_pda) from api.mainnet-beta...
```

### Success Criteria

- ✅ Log shows pagination when querying `bonding_curve_pda`
- ✅ Log shows "Fast path" only when NOT querying `bonding_curve_pda`
- ✅ `earliest_curve_sig` value differs from `create_sig` (at least sometimes)

### Failure Indicators

- ❌ Always shows "Fast path" regardless of what's being queried
- ❌ Never shows pagination for `bonding_curve_pda`
- ❌ `earliest_curve_sig` always matches `create_sig`

---

## Fix #2: Owner Program Filtering

### What to Look For

**Successful bonding curve extraction with owner verification:**
```
[CREATOR] Found System.createAccount creating: BondsWKhXQ... (owner=6EF8rrecthR5Dkzon...)
[CREATOR] ✓ Owner program matches PUMPFUN_BONDING_CURVE_PROGRAM!
```

**Failed extraction with wrong owner (correctly rejected):**
```
[CREATOR] Found System.createAccount creating: ATAyT4f... (owner=TokenkegQfeZyiNwAJbN...)
[CREATOR] ✗ Owner program TokenkegQfeZyiNwAJbN... != 6EF8rrecthR5Dkzon...
```

**Fallback to heuristic (deterministic approach failed):**
```
[CREATOR] ⚠ No System.createAccount with bonding curve owner found, falling back to heuristic
[CREATOR] → Selected bonding curve (heuristic): BondsWKhXQ...
```

### Success Criteria

- ✅ Log shows owner program when finding System.createAccount
- ✅ Owner matches `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` for CREATE transactions
- ✅ Wrong owners are explicitly rejected
- ✅ Correct bonding curve is extracted

### Failure Indicators

- ❌ No owner program check visible in logs
- ❌ Accepts accounts with owner != `6EF8...`
- ❌ Returns ATA addresses (TokenkegQfez...) as bonding curve
- ❌ Wrong bonding curves extracted

---

## Fix #3: Field Name Mismatch

### What to Look For

**Correct field names in provenance:**
```
[CREATOR] create_sig=2vMbMs9g7Mv...
[CREATOR] earliest_curve_sig=4cNhUZX6pAK...
```

**And in API responses:**
```json
{
  "creator_provenance": {
    "create_sig": "2vMbMs9g7Mv...",
    "earliest_curve_sig": "4cNhUZX6pAK...",
    "is_pumpfun_create": true
  }
}
```

### Success Criteria

- ✅ Log shows both `create_sig` and `earliest_curve_sig`
- ✅ API response includes both fields in `creator_provenance`
- ✅ Both fields are non-null when token processed successfully
- ✅ No errors about missing field names

### Failure Indicators

- ❌ Log shows `earliest_sig` (old field name)
- ❌ API response missing one or both signature fields
- ❌ KeyError in logs about missing fields
- ❌ Only one signature visible

---

## Complete Verification Test

### Step 1: Start the Listener

```bash
python3 pumpfun_curve_listener.py
```

Wait for a few tokens to be detected.

### Step 2: Check Log Output

Look for all three patterns in the log:

1. **Fast-path test:**
```bash
grep "🚀 Fast path" listener.log
```
Expected: Should see this when appropriate

```bash
grep "bonding_curve_pda.*api.mainnet-beta" listener.log | head -3
```
Expected: Should see pagination for bonding_curve_pda queries

2. **Owner filtering test:**
```bash
grep "Owner program" listener.log | head -5
```
Expected: Should see owner program checks

```bash
grep "PUMPFUN_BONDING_CURVE_PROGRAM!" listener.log
```
Expected: Should see successful owner matches

3. **Field naming test:**
```bash
grep "create_sig=" listener.log | head -3
grep "earliest_curve_sig=" listener.log | head -3
```
Expected: Both fields should appear

### Step 3: Test API

```bash
# Get a token from the database
MINT=$(sqlite3 pumpswap_tokens.db "SELECT mint FROM token_analysis LIMIT 1;" | tr -d '\n')

# Query the API
curl http://localhost:5002/api/token-metrics/$MINT | jq '.creator_provenance'
```

Expected output:
```json
{
  "pumpfun_creator": "6Qzc...",
  "pumpfun_status": "confirmed",
  "bonding_curve_pda": "Bonds...",
  "create_sig": "2vMbMs...",
  "earliest_curve_sig": "4cNhUZ...",
  "is_pumpfun_create": true,
  "reached_end": true,
  "mint_in_accounts": true,
  "pumpfun_program_found": true
}
```

### Step 4: Verify Signature Separation

Check if `create_sig` and `earliest_curve_sig` can be different:

```bash
sqlite3 pumpswap_tokens.db << EOF
SELECT
  mint,
  json_extract(metadata, '$.create_sig') as create_sig,
  json_extract(metadata, '$.earliest_curve_sig') as earliest_curve_sig
FROM token_analysis
WHERE json_extract(metadata, '$.is_pumpfun_create') = 1
LIMIT 5;
EOF
```

Look at the results:
- ✅ Both fields populated: Good
- ✅ Some rows have `create_sig != earliest_curve_sig`: Excellent (shows fix #1 working)
- ✅ All rows have `create_sig == earliest_curve_sig`: Also fine (means CREATE was the earliest activity)

---

## Debugging If Something's Wrong

### Symptom: Fast-path not being used

**Log shows:**
```
[CREATOR] Page 1: 1000 sigs from token_mint
[CREATOR] Page 2: 1000 sigs from token_mint
```

**Should be:**
```
[CREATOR] 🚀 Fast path: Already have CREATE tx signature
```

**Check:**
```python
# In get_true_earliest_signature():
if bonding_curve_pda is None and self._create_tx_signature:  # ← Both conditions?
    return self._create_tx_signature, True, "cached"
```

### Symptom: Owner filtering not happening

**Log shows:**
```
[CREATOR] Found System.createAccount creating: ... (no owner shown)
```

**Should show:**
```
[CREATOR] Found System.createAccount creating: ... (owner=6EF8...)
```

**Check:**
```python
# In _extract_bonding_curve_from_tx():
# After finding created_account, verify:
owner_program = self._decode_system_create_owner_program(sys_ix)
if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:  # ← Check present?
    return created_account
```

### Symptom: Field names wrong

**Log shows error:**
```
KeyError: 'earliest_sig'
```

**Check:**
```python
# In get_summary_async():
create_sig = provenance.get('create_sig')  # ← New name?
earliest_curve_sig = provenance.get('earliest_curve_sig')  # ← New name?
```

---

## Success Indicators Checklist

- ✅ Fast-path optimization works (shows pagination only when querying bonding_curve_pda)
- ✅ Owner filtering active (logs show owner program verification)
- ✅ Both signatures tracked (logs show create_sig and earliest_curve_sig)
- ✅ API responses complete (creator_provenance includes all fields)
- ✅ Signatures can differ (create_sig != earliest_curve_sig sometimes)
- ✅ Creator assigned from CREATE only (never from earliest_curve_sig)
- ✅ No errors in logs about missing fields

---

## Still Seeing Incorrect Signatures?

If you still see signatures like `4cNhUZ...` appearing as the **create_sig**:

1. Check that bonding curve extraction picked the right account:
   ```
   [CREATOR] ✓ Owner program matches PUMPFUN_BONDING_CURVE_PROGRAM!
   ```

2. Check that fast-path is being skipped for bonding_curve_pda:
   ```
   [CREATOR] Page 1: ... from bonding_curve_pda
   ```

3. Check that both signatures are logged correctly:
   ```
   [CREATOR] create_sig=...
   [CREATOR] earliest_curve_sig=...
   ```

If any of these are missing, revisit the implementation of the three fixes.

---

**Date:** 2026-02-06
**Status:** Verification Guide Complete
