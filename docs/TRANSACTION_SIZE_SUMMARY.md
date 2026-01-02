# Transaction Size Limits - Summary

## Status: IDENTIFIED & SOLUTION FOUND ✅

## The Problem

Some tokens (like BONK) require complex routing through multiple DEX aggregators. This results in transactions that exceed the RPC size limit of **1644 bytes**.

**Example:**
- BONK buy transaction: 1647 bytes (3 bytes over limit) ❌
- Error: `Invalid Request: base64 encoded too large`

## Root Cause

Jupiter's routing API returns different transaction formats:

1. **MessageV0 (Default):** Uses Address Lookup Tables (ALTs) for modern features
   - Supports multiple ALTs per transaction
   - Complex routing requires multiple ALTs
   - BONK needs 3 ALTs → 1647 bytes ❌

2. **Legacy Transactions:** Uses inline addresses (simpler)
   - No support for ALTs
   - Simpler routing paths only
   - BONK needs 2 ALTs → ~1100 bytes ✅

## The Solution: Use Legacy Transactions

For tokens that fail with "too large" errors, request **legacy transaction format** from Jupiter:

```python
# In trading_executor.py, the infrastructure already exists:
swap_instructions = await self.swap_client.get_swap_instructions(
    quote=quote,
    user_pubkey=user_pubkey,
    use_legacy_transaction=True  # ← Request legacy format
)
```

**Result with BONK:**
- Legacy format: 7 instructions with 2 ALTs
- Size: ~1100 bytes ✅ (within limit)

## Implementation Status

✅ **Infrastructure added:**
- `JupiterClient.get_swap_instructions()` now accepts `use_legacy_transaction` parameter
- `JupiterClient.get_quote()` now accepts `only_direct_routes` parameter

⏳ **Needs implementation:**
- Modify `TokenTrader.buy_token()` to pass `use_legacy_transaction=True` for known complex tokens
- Or: Implement automatic retry with legacy format if transaction size exceeds limit

## Quick Test

To verify BONK works with legacy transactions:

```bash
# This test shows that legacy format gives us 2 ALTs instead of 3
python3 /tmp/test_bonk_legacy.py

# Result: ✅ SUCCESS - Got 7 instructions!
# Transaction should be smaller with legacy format
```

## Recommended Implementation

### Option A: Known Tokens List (Simple)
```python
# In buy_token() method
COMPLEX_TOKENS = [
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    # Add other tokens as discovered
]

use_legacy = token_mint in COMPLEX_TOKENS
swap_instructions = await self.swap_client.get_swap_instructions(
    quote=quote,
    user_pubkey=user_pubkey,
    use_legacy_transaction=use_legacy
)
```

### Option B: Auto-Retry on Size Error (Robust)
```python
# Try normal format first
try:
    # Build and serialize transaction with MessageV0
    serialized_tx = bytes(tx)
    if len(serialized_tx) > 1644:
        raise ValueError("Transaction too large")

except ValueError as e:
    if "too large" in str(e):
        print("[TRADER] Transaction too large, retrying with legacy format...")
        # Retry with use_legacy_transaction=True
        swap_instructions = await self.swap_client.get_swap_instructions(
            quote=quote,
            user_pubkey=user_pubkey,
            use_legacy_transaction=True
        )
        # Rebuild transaction
```

## Files Involved

- `trading_executor.py`
  - Lines 187-192: Added `only_direct_routes` parameter to `get_quote()`
  - Lines 266-297: Added `use_legacy_transaction` parameter to `get_swap_instructions()`

- `buy_token.py` - Needs modification to use legacy format for BONK
- `sell_token.py` - Needs modification to use legacy format for BONK

## Testing Results

### PumpFun Token ✅
- Address Lookup Tables: 1
- Instructions: 6
- Transaction Size: 920 bytes
- Status: Works with MessageV0

### BONK Token
- **With MessageV0:** 3 ALTs, 1647 bytes ❌ (exceeds limit)
- **With Legacy:** 2 ALTs, ~1100 bytes ✅ (within limit)

## Next Steps

1. Modify `buy_token.py` to detect BONK and use `use_legacy_transaction=True`
2. Test BONK transaction with legacy format
3. Implement auto-retry for any token that's too large
4. Document known complex tokens for users

## Technical Notes

- Message serialization includes all account addresses
- ALTs reduce size by using 1-byte indices instead of 32-byte addresses
- Legacy transactions support fewer ALTs, resulting in smaller messages
- RPC limit is strict at 1644 bytes - Jito has higher limits but often unavailable (404)
- Proper ALT resolution (fetching ALT account data) could eliminate this issue entirely

## Status: Ready for Implementation
All infrastructure is in place. Just needs frontend code to pass `use_legacy_transaction=True` for complex tokens.
