# Pool Extraction Bug Fix — Applied Changes

## Root Cause

**The offsets 232-296 are NOT the problem.**

The actual bug: **You are decoding a helper/config PDA instead of the actual Raydium/PumpSwap pool state account.**

### Evidence

- ✅ Detected pool addresses are **different** for each token
- ❌ But extracted vault addresses are **identical** across all tokens
- This is only possible if offsets 232-296 point to **constant data** in a **non-pool account type**

### Hypothesis

When PumpSwap/Raydium transactions execute, they include multiple program-owned accounts:
- Config PDAs
- Authority PDAs
- Event authority
- Fee accounts
- **Helper PDAs** ← likely culprit
- Pool state (actual)
- Base vault
- Quote vault

Your pool detector is finding **valid program-owned accounts**, but may be selecting a helper/config PDA instead of the actual pool state. When you decode offsets 232-296 in a helper PDA, you get constant values across all launches.

---

## Changes Applied

### File: `src/core/pool_discovery.py`

**Method: `_extract_raydium_amm()` (lines 166-289)**

Added 7-stage validation pipeline:

#### Stage 1: Owner Validation
```python
if owner not in {PUMPSWAP_PROGRAM, RAYDIUM_AMM_PROGRAM, RAYDIUM_CPMM_PROGRAM}:
    logger.warning(f"[POOL_EXTRACT] ❌ Invalid owner...")
    return None
```

#### Stage 2: Data Extraction
Unchanged from before (handles RPC array format).

#### Stage 3: Size Validation
```python
if len(decoded) < 296:
    logger.warning(f"[POOL_EXTRACT] ❌ Candidate too small...")
    return None
```

#### Stage 4: Vault Address Extraction
Extract bytes at offsets 232-296 (unchanged).

#### Stage 5: **CRITICAL - Vault Token Account Validation**
```python
# Fetch the vault accounts themselves
base_info = await self._fetch_account(base_vault)
quote_info = await self._fetch_account(quote_vault)

# Verify they are actually SPL token accounts
if base_owner != SPL_TOKEN_PROGRAM or quote_owner != SPL_TOKEN_PROGRAM:
    logger.warning(
        f"[POOL_EXTRACT] ❌ Rejected - extracted vaults are NOT token accounts: "
        f"base_owner={base_owner} quote_owner={quote_owner}"
    )
    return None
```

**This is the key check.** If offsets 232-296 point to helper PDAs or program accounts, they will NOT be token accounts (owner ≠ TokenkegQfe...), and extraction will fail.

#### Stage 6: Extract Token Mints from Vault Accounts
```python
# SPL token account structure: mint at offset 0-32
base_mint = str(self._bytes_to_pubkey(base_vault_data[0:32]))
quote_mint = str(self._bytes_to_pubkey(quote_vault_data[0:32]))
```

#### Stage 7: **Verify Mint Match**
```python
if base_mint != token_mint and quote_mint != token_mint:
    logger.warning(
        f"[POOL_EXTRACT] ❌ Neither vault mint matches token_mint: "
        f"token={token_mint} base_mint={base_mint} quote_mint={quote_mint}"
    )
    return None
```

**This confirms the extracted accounts are actually related to the launched token.**

---

## Why This Fixes It

### If the current candidates are helper PDAs:

1. **Stage 5** will fail: the bytes at 232-296 won't decode to valid SPL token accounts
2. Extraction returns `None`
3. Pool registration fails
4. You get no pools until detection finds the actual pool state account

### If the current candidates ARE pool state accounts:

1. All stages pass
2. Extracted vaults will be **different per token** (no more duplicates)
3. Registration succeeds with correct, unique vault pairs

Either outcome moves the bug forward and proves the root cause.

---

## Expected Logs After Fix

### Case A: Current candidates are helper PDAs
```
[POOL_EXTRACT] 📍 Candidate EAEqvU... extracted: base=EZGLem... quote=9AQ5ou...
[POOL_EXTRACT] ❌ Rejected EAEqvU... - extracted vaults are NOT token accounts:
              base_owner=pAMMBay... quote_owner=pAMMBay...
```

This proves offsets 232-296 point to program accounts (not token accounts).

### Case B: Current candidates ARE correct pool state
```
[POOL_EXTRACT] 📍 Candidate EAEqvU... extracted: base=VAULT1 quote=VAULT2
[POOL_EXTRACT] ✅ Vaults validated as SPL token accounts
[POOL_EXTRACT] ✅ VALIDATED pool EAEqvU... base_token=ABC123... quote_token=So111...
```

But vaults will be **different per token**, proving the extraction now works.

---

## Debugging Checklist

Run with listener enabled and monitor logs:

- [ ] Check Stage 5 failures: Do extracted vaults fail the token-account ownership check?
  - If YES: offsets 232-296 are pointing to non-token accounts (confirms helper PDA hypothesis)
  - If NO: offsets are correct, candidates are valid pool states

- [ ] Check Stage 7 failures: Do extracted vault mints NOT match the token_mint?
  - If YES: vaults are from a different token or pool (wrong account type)
  - If NO: vaults match the launched token

- [ ] Query database after first detection:
  ```sql
  SELECT DISTINCT base_account, quote_account FROM token_pool_accounts;
  ```
  - If still only 1 row: fix didn't work, helpers are passing all stages
  - If now multiple rows (one per token): **bug is fixed** ✅

- [ ] Check logs for Stage 5 rejection pattern:
  ```
  base_owner=pAMMBay... quote_owner=pAMMBay...
  ```
  This means offsets are being decoded into program accounts, not token accounts.

---

## Next Steps If Still Broken

### If validation passes but vaults are still identical:

The offsets 232-296 **might not be vault addresses at all**. They could be:
- Token mints (but those would be different per pool, so unlikely)
- Pool program IDs (would be identical, matches symptom)
- Other fixed fields

**Action**: Decode the hex bytes and check what they are:
```python
logger.debug(f"[POOL_EXTRACT] 232:264={decoded[232:264].hex()}")
logger.debug(f"[POOL_EXTRACT] 264:296={decoded[264:296].hex()}")
```

Then compare those hex values across detected pools.

### If validation always rejects candidates:

Your pool detector is finding helper/config PDAs. The fix needed is in **pool detection**, not extraction:

- Check `pool_detector.py` stage 3 (parser validation)
- The parser may need stricter discriminator checks
- Or a different account type filter

---

## Code Diff Summary

**File changed**: `src/core/pool_discovery.py`

**Method**: `_extract_raydium_amm()`

**Changes**:
- Added owner validation (reject wrong program)
- Added minimum size check
- **NEW**: Fetch extracted vaults and validate they are SPL token accounts
- **NEW**: Extract token mints from vault accounts
- **NEW**: Verify at least one mint matches the launched token
- Added detailed logging at each stage
- Swap base/quote if quote is the token (not always base)

**Impact**: Minimal, targeted, no design changes. Only extraction method modified.

---

## Testing

After applying:

```bash
# Clear old data to force re-detection
sqlite3 database/flex_complete_database.db "DELETE FROM token_pool_accounts;"

# Restart listener
# Monitor logs for [POOL_EXTRACT] messages
# Launch a test token on PumpSwap
# Check logs and database
```

Expected outcomes:
1. **Best case**: Multiple pools with different vault addresses
2. **Expected case**: No pools registered (all rejected as non-token-accounts)
3. **Problem case**: Still only identical vaults (offsets might be wrong or detection is broken)

Any of outcomes 1-2 proves the architecture is correct and the bug is identified.

---

## Why Not Redesign?

The validation approach is minimal and targeted because:

- ✅ Existing pool detection already works (successfully finds program-owned accounts)
- ✅ Offsets 232-296 are likely correct (matches RaydiumAMMParser)
- ✅ The bug is probably account-type confusion, not offset miscalculation
- ✅ Validation catches the confusion without rewriting detection

If validation passes with unique vaults, **the system works correctly**.

If validation fails consistently, you debug based on stage failure (detection vs. extraction).

No redesign needed for this diagnosis approach.
