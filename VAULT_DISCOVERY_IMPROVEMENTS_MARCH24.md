# Vault Discovery Improvements - March 24, 2026

**Status:** ✅ CRITICAL FIXES APPLIED

---

## Issues Fixed

### 1. Quote Token Restriction Too Strict ✅

**Problem:**
```python
quote_candidates = [
    a for a in vault_accounts
    if a["mint"] in (SOL_MINT, USDC_MINT)
]
```

Only accepted SOL/USDC pairs, rejecting valid token/token pairs.

**Solution:**
```python
quote_candidates = [
    a for a in vault_accounts
    if a["mint"] != token_mint
]

def score_quote(acc):
    mint = acc.get("mint", "")
    balance = get_balance(acc)
    if mint == SOL_MINT:
        return (3, balance)  # Highest preference
    if mint == USDC_MINT:
        return (2, balance)  # Medium preference
    return (1, balance)  # Accept any other quote
```

**Impact:**
- ✅ Now accepts token/token pairs
- ✅ Now accepts alternative quotes (USDT, COPE, etc.)
- ✅ Still prefers SOL/USDC by score

---

### 2. Token-2022 Support ✅

**Problem:**
Validation only checked for SPL Token Program, rejecting Token-2022 vaults.

**Solution:**
```python
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
VALID_TOKEN_OWNERS = (SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM)

if base_owner in VALID_TOKEN_OWNERS and quote_owner in VALID_TOKEN_OWNERS:
    vault_status = "validated"
```

**Impact:**
- ✅ Now validates Token-2022 vaults correctly
- ✅ Token `6MxLhwC7u7bq6v7CXURPmT5Fc4ZdZUAMyWnZpdgSpump` now marked as validated

---

### 3. Authority Scan Assumption - Documented ⚠️

**Problem (Known):**
Authority scan assumes:
```python
vault.owner == pool_address
```

But some AMMs use:
- PDA-derived authority
- Delegated authority
- Program-owned vaults

If vaults are not directly owned by pool_address, `getTokenAccountsByOwner(pool_address)` returns empty.

**Current Handling:**
```python
if not vault_accounts:
    logger.warning(
        f"[POOL_EXTRACT] ⚠️ No token accounts found with pool as owner (may use PDA/delegated authority). "
        f"This pool may use advanced authority mechanisms not yet supported."
    )
    return None
```

**Future Fix Needed:**
```python
# Option 1: Derive and scan by PDA authority
# Option 2: Fallback to account scanning with mint + authority field checks
# Option 3: Support for delegated authority patterns
```

**Status:** ✅ Documented, explicitly logged, safe fallback

---

## Code Changes Summary

| File | Change | Lines |
|------|--------|-------|
| `src/core/pool_discovery.py` | Quote selection: accept any non-base token with scoring | +15 |
| `src/core/pool_discovery.py` | Token-2022 support in vault validation | +3 |
| `src/core/pool_discovery.py` | Authority scan limitation documented | +5 |

---

## Database Updates

### Previous Pending Tokens Now Validated

Token with Token-2022 vaults:
- `6MxLhwC7u7bq6v7CXURPmT5Fc4ZdZUAMyWnZpdgSpump`
- Base owner: `TokenzQdBNbLqP5V...` (Token-2022) ✅
- Quote owner: `TokenkegQfeZyiNw...` (SPL Token) ✅
- Status: `pending` → `validated`

---

## Deployment Readiness

### Ready for Production ✅

- ✅ Handles SOL/USDC pairs
- ✅ Handles token/token pairs
- ✅ Handles alternative quotes
- ✅ Handles Token-2022 vaults
- ✅ Known limitation documented (PDA authority)
- ✅ Safe fallback behavior

### Known Limitations Explicitly Documented

1. **PDA Authority:** Pools using PDA-derived authorities won't be discovered via authority scan
2. **Delegated Authority:** Pools with delegated vault authority patterns won't be discovered
3. **Fresh Pools:** Very new pools with zero balances may fail balance-based sorting (handled via max/default 0)

---

## Testing Results

### Authority Scan Tests

✅ Standard authority discovery:
- Pool address owns vaults directly
- RPC returns token account list
- Filtering by mint works
- Both Token Program and Token-2022 accepted

### Quote Selection Tests

✅ Multi-quote scenarios:
- SOL/USDC pairs: Selects highest balance ✅
- Token/token pairs: Accepts any non-base ✅
- Alternative quotes: COPE, COPE, others accepted ✅

---

## Next Steps

### Immediate
1. Restart listener with new fixes
2. Monitor next token launches
3. Verify validated status on discovery

### Short Term
1. Collect pool discovery patterns
2. Identify any PDA-authority pools
3. Prioritize based on volume impact

### Medium Term
1. Implement PDA authority fallback
2. Support delegated authority discovery
3. Optimize authority scan caching

---

## Confidence Level

**High** ✅✅

- Quote selection now future-proof for any token pair
- Token-2022 support critical for growing ecosystem
- Authority scan limitation is known and documented
- Safe fallback behavior prevents bad registrations
- Production-ready for current pool types

---

**Commit Changes Ready:**
- Quote selection flexibility
- Token-2022 vault support
- Authority scan limitation documented

**Waiting For:**
- PDA/delegated authority discovery implementation (future)

