# Pool Discovery Fix Summary

## Problem
Pools were being discovered by the listener but not registered to the database, so they didn't appear in the UI.

Example:
- Listener found mint: `nWufycny4kAzXMygydJ3eEpdPAxcyBgrEHzethbpump`
- Pool discovered: `DNN7GQ3btxAvWwRd...`
- Status: Pool extraction failed → Not registered → Not in UI

## Root Cause
Pool extraction was failing because:
1. **PumpFun V1 pools not recognized**: The discovered pool likely had owner `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` (PumpFun V1), which was not in `AMMPrograms.ALL`
2. **Pool extraction failed**: Even if recognized, the extraction logic didn't handle PumpFun V1 pools (they use a different data structure than Raydium)

## Fixes Applied

### 1. Added PumpFun V1 to AMMPrograms (src/core/pool_detector.py)
```python
# Before: PumpFun V1 not in ALL set
ALL = {PUMPSWAP, RAYDIUM_AMM, RAYDIUM_CLMM, ORCA_WHIRLPOOL, METEORA_DLMM, SOLEND}

# After: PumpFun V1 added
PUMPFUN_V1 = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
ALL = {PUMPSWAP, PUMPFUN_V1, RAYDIUM_AMM, RAYDIUM_CLMM, ORCA_WHIRLPOOL, METEORA_DLMM, SOLEND}
```

**Effect**: Pools with PumpFun V1 ownership now pass the validation check in the listener.

### 2. Added PumpFun V1 extraction logic (src/core/pool_discovery.py)
```python
# Route PumpFun V1 pools to dedicated extractor
if owner == PUMPFUN_V1_PROGRAM:
    return await self._extract_pumpfun_v1(pool_data, pool_address, token_mint)

# New method: _extract_pumpfun_v1()
# Attempts to extract using Raydium-like layout, then tries alternative offsets
# Falls back gracefully if structure doesn't match
```

**Effect**: PumpFun V1 pools can now be registered (if extraction succeeds).

### 3. Improved migration TX extraction (src/core/post_migration_pool_discovery.py)
```python
# Before: Returned first pool-sized account
# After: Returns largest pool-sized account (more likely to be real pool, not helper)

# Also added size filtering:
# - Minimum 296 bytes (valid pool state size)
# - Skips helper/config accounts (~150 bytes)
```

**Effect**: When multiple pools exist in migration TX, returns the actual pool state (not a helper account with garbage data).

### 4. Added null pubkey filtering (src/core/pool_discovery.py)
```python
# Before: Would attempt to decode null pubkeys
# After: Rejects pubkeys that are all zeros or all ones
if data == b'\x00' * 32 or data == b'\xff' * 32:
    return None
```

**Effect**: Prevents wasting RPC calls on placeholder pubkey addresses.

## What Works Now
✅ PumpSwap pools: Full extraction
✅ Raydium pools: Full extraction
✅ Raydium CPMM pools: Full extraction
✅ Orca Whirlpool pools: Full extraction
✅ PumpFun V1 pools: Recognized + attempted extraction (structure TBD)

## What's Still Needed
❌ PumpFun V1 extraction: Vault addresses don't appear to be at Raydium offsets
   - Need to reverse-engineer actual PumpFun V1 pool data structure
   - Currently tries alternative offsets (8-40, 64-96) but needs real structure docs

## Testing
1. Run listener and wait for a token launch
2. Check listener logs for `[POOL_DISCOVER_FALLBACK]` messages
3. Verify pool is registered: `SELECT * FROM token_pool_accounts WHERE mint = ?`
4. Verify UI displays the token

## Files Modified
- `src/core/pool_detector.py` - Added PumpFun V1 to AMMPrograms
- `src/core/pool_discovery.py` - Added PumpFun V1 extraction + null pubkey filtering
- `src/core/post_migration_pool_discovery.py` - Improved migration TX extraction

## Commits
- cb83523: "feat: Add PumpFun V1 pool support to discovery pipeline"

## Next Steps
1. Enable listener: `UPDATE listener_settings SET setting_value = 'true' WHERE setting_key = 'listen_to_launches'`
2. Monitor for token launches and verify pools are registered
3. If PumpFun V1 extraction still fails, need to document actual pool structure
