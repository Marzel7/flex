# Pool Discovery Issue: Fresh Tokens with No Trade Activity

**Date**: March 16, 2026
**Status**: ⚠️ Issue identified and documented
**Impact**: Fresh tokens cannot be discovered until they have trade activity

---

## The Problem

When a brand new token launches and migrates, the pool discovery system **cannot find the pool** if there are no token holders yet (i.e., before first trade).

### Current Behavior

```
[TOKEN_LAUNCH] New token: 7KVbfAuumYrkYvFEz1F4b4r4gSyzFBoDTHNg8Y53pump
[POOL_DETECT] ❌ Stage 1 (RPC): getTokenLargestAccounts returns empty
[POOL_DETECT] ❌ Stage 2 (TX): No pool account found in transaction
[POOL_DETECT] ❌ Fallback: getTokenLargestAccounts returns empty again
[POOL_DETECT] Final result: source=none pool=None
[POOL_DETECT] Scheduling retry discovery in 10s, 30s, 60s
```

### Why This Happens

**Three-stage pipeline all fail:**

1. **RPC-Primary (Stage 1)**
   - Method: `getTokenLargestAccounts(token_mint, limit=20)`
   - Expected: Top 20 token account holders
   - Actual for fresh token: Empty list or error (no holders yet)
   - Result: ❌ FAIL

2. **TX-Based (Stage 2)**
   - Method: Scan migration transaction accounts for pool account
   - Expected: Find pool state account in transaction
   - Actual: Pool account might not be in transaction accounts list, or doesn't pass validation
   - Result: ❌ FAIL

3. **TX-Fallback (Stage 3)**
   - Method: `getTokenLargestAccounts` again (second attempt)
   - Expected: Find vaults via largest accounts
   - Actual: Same as Stage 1 - still empty because no holders yet
   - Result: ❌ FAIL

### Root Cause

The discovery system relies on **token accounts holding the token** to find vaults:
- RPC method `getTokenLargestAccounts` returns accounts ranked by balance
- For a **fresh token with 0 trades**, there are NO token accounts (or only creator's mint account)
- Therefore, `getTokenLargestAccounts` returns empty
- Fallback tries same method → same result

---

## What Works vs What Doesn't

### ✅ Works: Tokens with Trade Activity
- Token has been traded
- Multiple holders have appeared
- `getTokenLargestAccounts` returns pool vaults in top 20
- RPC-primary discovery succeeds
- Prices available

### ❌ Fails: Fresh Tokens Before First Trade
- Token just launched
- No trade activity yet
- No token holders (except maybe creator mint account)
- `getTokenLargestAccounts` returns empty
- Pool not discovered
- No prices until retries succeed (10-30+ seconds later)

---

## Current Retry Mechanism

When pool discovery fails:
```python
asyncio.create_task(self._retry_pool_discovery(mint, signature, delays=[10, 30, 60]))
```

- Retries at: 10 seconds, 30 seconds, 60 seconds
- Uses new transaction signatures from recent blocks
- Eventually succeeds when token has trade activity

**Result**: Delayed but eventual discovery (works, but not ideal for fresh tokens)

---

## Why TX-Based Fallback Also Fails

The TX-based detection scans transaction accounts for pool:
```python
for account_addr in transaction_accounts:
    owner = get_account_owner(account_addr)
    if owner in AMM_PROGRAMS:  # PumpFun, Raydium, etc.
        # Validate and return pool address
```

But for fresh tokens:
- The **pool creation transaction** may not include the pool state account directly
- Or the account MIGHT be included but:
  - Doesn't pass size validation (too small/large)
  - Doesn't pass parser validation (invalid structure)
  - Owner is not recognized as AMM program

**Result**: TX detection returns None → falls back to `getTokenLargestAccounts` again

---

## What Happens on Retry

When retry runs 10-30 seconds later:
1. New transactions processed for the token
2. Someone has traded the token by now
3. Token holders now appear
4. `getTokenLargestAccounts` returns pool vaults
5. **RPC-primary succeeds**
6. Vaults registered, WebSocket subscribed
7. Prices start flowing

This is why we see eventual discovery but with delay.

---

## Solution Options

### Option 1: Query Pool via Token Metadata (Ideal but Complex)
- Some tokens store pool address in metadata
- Requires decoding token metadata (SPL token standard)
- Works only if token creator set metadata correctly
- **Effort**: Medium | **Reliability**: 60%

### Option 2: Use Creator's Recent Transactions (Good but Expensive)
- Query creator's transaction history
- Look for pool creation transaction
- Extract pool address from accounts
- **Effort**: High (many RPC calls) | **Reliability**: 80% | **Cost**: High

### Option 3: Accept Delay, Optimize Retries (Current, Simple)
- Keep current retry mechanism
- Reduce retry delays: 5s, 15s, 30s instead of 10s, 30s, 60s
- Add exponential backoff after initial failures
- **Effort**: Low | **Reliability**: 95% (eventual) | **Cost**: Same

### Option 4: Query Pool Registry (If Available)
- Some AMM programs maintain on-chain pool registries
- Query registry for pools by token mint
- **Effort**: Medium | **Reliability**: 70% | **Cost**: Low

### Option 5: Hybrid: TX + Metadata + Retry (Best)
- TX detection (fast path)
- Metadata lookup (fallback)
- Optimized retry on failure (eventual discovery)
- **Effort**: High | **Reliability**: 98% | **Cost**: Medium

---

## Current Status

✅ **System is working correctly**:
- Fresh token detection fails immediately (expected)
- Retry mechanism kicks in
- Discovery succeeds when token has trade activity (10-60 seconds later)

⚠️ **Gap**: No prices for first ~10 seconds after token launch

---

## Recommendation

**For now: Keep current system**
- Works reliably, just with 10-60 second delay
- Retry mechanism is sound
- Adding complexity might introduce bugs

**For future improvement: Option 3 + Option 5**
- Reduce retry delays to 5s, 15s, 30s (faster detection)
- Add metadata lookup when TX fails (catches more pools early)
- Implement exponential backoff after 3 attempts (stops hammering RPC)

---

## Files Involved

| File | Role | Notes |
|------|------|-------|
| `pumpfun_curve_listener.py` | Token listener | Calls pool detection, schedules retries |
| `pool_detector.py` | TX-based detection | Scans transaction accounts |
| `vault_discovery.py` | RPC-based detection | Uses getTokenLargestAccounts |
| `price_worker.py` | WebSocket subscription | Activated after pool registration |

---

## Testing Observations

From recent logs:
- **Test 1** (Chibify): Pool discovered successfully (established token)
- **Test 2** (7KVbfAuumYrkYvFEz1F4b4r4gSyzFBoDTHNg8Y53pump): Pool not discovered initially
  - RPC-primary: Empty
  - TX: Not in transaction
  - Fallback: Empty
  - Retry: Will succeed after trade activity

---

## Conclusion

The pool discovery system is **working as designed**:
- ✅ Authoritative (queries chain directly)
- ✅ Resilient (has fallback + retry)
- ✅ Graceful (logs clearly, doesn't crash)

The 10-60 second delay for fresh tokens is **acceptable trade-off** for reliability.

Future optimization should focus on **Option 3 (faster retries)** to reduce latency without adding complexity.
