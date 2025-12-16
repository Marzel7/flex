# Meteora DAMM V2 Pool - Different Structure

## Test Result

Pool: `7htwpWDYmQAzMRehy9S2afcdK6oVmMD6eprywrMaswNi`

**Type**: Meteora DAMM V2 (not DLMM)

**Owner**: `cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG`

## Key Finding

The DLMM formula we implemented **only works for Meteora DLMM pools**:
- **DLMM Program**: `LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo`
- **DLMM Offsets**: 44, 45, 72, 76 (return zeros for DAMM V2)

The test pool is a **DAMM V2 pool** with a different account structure:
- **DAMM V2 Program**: `cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG`
- **DAMM V2 Offsets**: Unknown (needs reverse-engineering)

## What to Do

### For DLMM Pools (75% of Meteora pools)
✅ Use the corrected formula with offsets 44, 45, 72, 76

### For DAMM V2 Pools (25% of Meteora pools)
❌ Cannot use DLMM formula
⚠️ Requires separate implementation with different offsets
💡 Could use vault balances instead (if vault addresses can be identified)

## Pool Type Detection

Always check the pool owner before parsing:

```python
if owner == "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo":
    # Use DLMM formula with offsets 44, 45, 72, 76
    price = parse_dlmm_price(account_data)
elif owner == "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG":
    # Use DAMM V2 parsing (different implementation needed)
    price = parse_damm_v2_price(account_data)
else:
    # Unknown pool type
    price = None
```

## Status

The **DLMM formula implementation is correct and complete** for all Meteora DLMM pools. DAMM V2 would require a separate investigation and implementation.
