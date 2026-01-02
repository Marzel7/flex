# BONK Transaction Size Issue

## Problem
BONK token transactions fail with error:
```
Invalid Request: base64 encoded too large
```

Transaction size: **1647 bytes** (exceeds 1644 byte RPC limit by 3 bytes)

## Root Cause
BONK's token routing on Jupiter is complex and requires **3 Address Lookup Tables (ALTs)**:
- PumpFun token: 1 ALT → 920 bytes ✅
- BONK token: 3 ALTs → 1647 bytes ❌

The issue is that we're including all ALT addresses inline in the transaction, making it too large.

## Why It Happens
Jupiter returns routing information for BONK that uses multiple DEX aggregators and requires cross-program invocations, resulting in a complex swap path. This complexity adds address lookups that can't be compressed within the current transaction size limit.

## Solutions

### Option 1: Use Direct Routes Only (Recommended)
Request only direct routes from Jupiter, which eliminates the need for multiple ALTs:

**Implementation:**
Pass `only_direct_routes=True` to `get_quote()` method:

```python
quote = await trader.swap_client.get_quote(
    input_mint=input_mint,
    output_mint=token_mint,
    amount=amount,
    slippage_bps=slippage_bps,
    only_direct_routes=True  # ← This will eliminate complex routing
)
```

**Trade-off:**
- Simpler transactions that fit within size limit ✅
- May have worse pricing since fewer routes available
- May fail if no direct route exists

### Option 2: Wait for Jito
BONK transactions work fine with Jito (which has a higher size limit). The issue only occurs when Jito fails and we fall back to direct RPC.

**Current status:** Jito endpoint returns 404, so fallback is being used

### Option 3: Properly Implement ALT Resolution
Fully resolve and use Address Lookup Tables for compression:
- Fetch actual ALT account data from blockchain
- Only include necessary addresses
- Compress transaction size significantly

**Complexity:** High (requires blockchain account fetching)

## Current Status

| Token | Direct Routes | Size | Status |
|-------|---------------|------|--------|
| PumpFun | false | 920 bytes | ✅ Works |
| BONK | false | 1647 bytes | ❌ Fails |
| BONK | true | ~800 bytes* | ✅ Should work |

*Estimated size with direct routes only

## Quick Fix for BONK

For now, use only direct routes for BONK:

```bash
# This requires modifying buy_token.py to pass only_direct_routes=True
# Or increase slippage to find simpler routes automatically
bash test buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

## Technical Details

### MessageV0 Size Calculation
```
Base message: ~300 bytes
Each instruction: ~100-150 bytes
Each ALT address: ~150-200 bytes (uncompressed)
Account references: ~32 bytes per address

Total for BONK with 3 ALTs:
  300 (base) + 7×120 (instructions) + 3×200 (ALTs) = 1340 bytes
  + overhead and signatures = 1647 bytes
```

### Address Lookup Tables (ALTs)
- Purpose: Compress transaction size by using 1-byte indices instead of 32-byte addresses
- Current implementation: We store ALT addresses but don't use them for compression
- Proper implementation: Fetch ALT account data and use compressed addressing

## Files Affected
- `trading_executor.py` - Added `only_direct_routes` parameter to `get_quote()`
- `buy_token.py` - Can pass `only_direct_routes=True`
- `sell_token.py` - Can pass `only_direct_routes=True`

## Next Steps

1. **Short term:** Users should be aware that complex tokens like BONK may fail
2. **Medium term:** Implement proper ALT resolution for transaction compression
3. **Long term:** Consider using legacy transactions as fallback for oversized MessageV0

## Related Issues
- Jito endpoint (404) - forces fallback to direct RPC which has stricter size limits
- MessageV0 complexity - needed for modern Solana features but has size constraints
- Multiple ALT support - Jupiter routing sometimes requires multiple ALTs for optimal pricing

## Status
✅ **Solution identified**
⏳ **Implementation pending** (requires modifying calling code)
