# Invalid Vaults Cleanup - March 24, 2026

## Problem Identified

Tokens discovered **before** `FVNediAcMzQ69RsnYLnijFRqT7tC7u1yYmiYsx3Gpump` had **invalid vault addresses** that were discovered using broken offset-based extraction logic.

These tokens:
- ✅ Exist in database
- ❌ Have wrong vault addresses (from fixed byte offsets, not dynamic discovery)
- ❌ Can't validate or price correctly

## Solution Applied

**Deleted 158 invalid vault records** from `token_pool_accounts` table that were created before FVNediAc's timestamp (1774288501).

### Database Changes

```sql
DELETE FROM token_pool_accounts
WHERE created_at < 1774288501
AND vault_validation_status IN ('validated', 'pending');
-- Deleted: 158 records
```

### Result

**Before**: 167 pools (158 invalid + 9 valid)
**After**: 9 pools (all validated with correct dynamic discovery)

### Tokens Preserved

All tokens remain in `token_analysis` table:
- ✅ 54+ tokens still available
- ✅ Can rediscover pools if they trade again
- ✅ New trades will use correct dynamic vault discovery

## Impact on System

### Price Updates
- ❌ Old vaults won't subscribe to WebSocket (they're deleted)
- ✅ New vaults (if tokens trade) will use correct discovery
- ✅ FVNediAc... and newer tokens have valid pools
- ✅ These will now use WebSocket prices (source: "pool")

### WebSocket Subscriptions
- Removed 158 invalid subscriptions
- Keeping 9 correct subscriptions
- Price worker will only compute from valid pool reserves

## Next Steps

1. Monitor new token discoveries - they will use corrected vault extraction
2. If old tokens trade again, they'll get new valid pools from `_extract_raydium_amm()`
3. WebSocket pricing should now work for the 9 valid pools
4. Test with `TEST_LIVE_PRICES.html` to verify pool-sourced prices

## Verification Commands

```bash
# Check current pool status
sqlite3 database/flex_complete_database.db \
  "SELECT COUNT(*), vault_validation_status FROM token_pool_accounts GROUP BY vault_validation_status;"

# Check which tokens have valid pools
sqlite3 database/flex_complete_database.db \
  "SELECT DISTINCT mint FROM token_pool_accounts ORDER BY created_at DESC LIMIT 10;"
```
