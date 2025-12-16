# Supply and Price Tracking Implementation

## Overview
Enhanced the Raydium/Meteora pool tracker to store and track token supply, price, and market cap data persistently in the database.

## What Was Added

### Database Schema
Added two new columns to the `pools` table:
- **`total_supply`** (REAL): Token total supply from on-chain mint account
- **`market_cap`** (REAL): Calculated as `total_supply * current_price`

Automatic schema migration for existing databases using `ALTER TABLE`.

### New Methods

#### RaydiumMonitor.get_token_total_supply(mint: str) → Optional[float]
Fetches the total supply of a token by:
1. Querying the mint account from RPC (base64 encoded)
2. Decoding and parsing SPL mint account structure
3. Reading supply field at offset 32-39 (8-byte u64)
4. Converting to human-readable amount using token decimals
5. Logging the result

**Solana SPL Mint Account Structure:**
- Offset 0-31: mint_authority (32 bytes)
- Offset 32-39: **supply** (8 bytes, u64) ← This is what we read
- Offset 40-43: decimals (1 byte) + isInitialized (1 byte) + owner

#### RaydiumDatabase.update_pool_supply_and_price(amm_id, total_supply, price) → bool
Updates pool record with:
- `total_supply`: Token total supply
- `current_price`: Current token price
- `market_cap`: Automatically calculated as `supply * price`
- `last_price_update`: Current timestamp

### Integration Points

#### 1. New Pool Detection (Initial Fetch)
When a new pool is detected:
```
[PRICE INIT] Fetching initial price and supply for E2HVtHRT...
[PRICE INIT] ✓ Initial price set: $0.00000001
[PRICE INIT] ✓ Total supply: 1,000,000,000
[PRICE INIT] ✓ Market cap: $10,000.00
```

- Fetches transaction signature from pool data
- Extracts vault addresses and prices using reliable transaction method
- Fetches total supply from mint account
- Stores all data in database in single update

#### 2. Periodic Price Updates
Price updater cycle now:
```
[PRICE UPDATER] ✓ Updated 6Jq1vmBF...: $0.00005208 (supply: 1,875,010, mcap: $97.66)
```

- Retrieves stored signature from database
- Uses signature for reliable vault-based price extraction
- Fetches total supply
- Updates price, supply, and market cap together
- Provides comprehensive logging with market cap info

### How It Works

**Data Flow for New Pools:**
1. WebSocket detects pool creation event
2. Transaction is parsed, pool data extracted
3. **Pool stored to database** (with signature)
4. Initial price fetch triggered:
   - Uses stored signature → calls transaction method
   - Extracts vaults from pool creation TX
   - Fetches vault balances from RPC
   - Calculates price = SOL / token
5. Total supply fetched from mint account
6. Price, supply, market cap stored in database
7. Pool broadcast to UI with all data

**Data Flow for Periodic Updates:**
1. Price updater finds pools needing update
2. Retrieves stored signature from database
3. Uses signature → transaction method (reliable!)
4. Updates price in database
5. Fetches total supply
6. Updates supply and market cap
7. Calculates market cap automatically

## Benefits

✅ **Persistent Storage** - Supply and price data saved indefinitely
✅ **Market Cap Tracking** - Automatically calculated from supply and price
✅ **Reliable Pricing** - Uses transaction method with stored signatures
✅ **Non-Invasive** - Gracefully falls back if supply fetch fails
✅ **Query-Ready** - Data structure enables future analytics and trending
✅ **On-Chain Data** - Uses verified on-chain data, not external APIs

## Data Query Examples

```sql
-- View all pools with supply and market cap
SELECT name, symbol, current_price, total_supply, market_cap
FROM pools
WHERE market_cap IS NOT NULL
ORDER BY market_cap DESC;

-- Find pools by market cap range
SELECT name, market_cap
FROM pools
WHERE market_cap > 10000 AND market_cap < 1000000;

-- Track price changes
SELECT name, creation_price, current_price,
       ((current_price - creation_price) / creation_price * 100) as change_pct
FROM pools
WHERE creation_price IS NOT NULL;
```

## Technical Details

### RPC Data Parsing
- SPL Token Mint accounts follow standard structure
- Account data received base64-encoded from RPC
- Must decode before binary parsing
- Supply is stored as unsigned 64-bit integer at specific offset

### Precision Handling
- Decimals retrieved per token (typically 6-18)
- Human-readable amounts calculated by dividing by 10^decimals
- Prices displayed with 18 decimal places for precision
- Market cap calculated before rounding for accuracy

### Error Handling
- If supply fetch fails → price still updates (graceful fallback)
- If price fetch fails → supply not updated (consistency)
- All errors logged with `[TOKEN SUPPLY]` and `[PRICE UPDATER]` prefixes

## Testing

Current implementation has been tested with:
- Fresh database initialization
- New pool detection and parsing
- Transaction method activation
- Database schema migration
- Periodic price update cycles
- Multiple pools being tracked simultaneously

## Future Enhancements

1. **Supply History Table** - Track supply changes over time
2. **Price History Table** - Store price snapshots for charting
3. **Trending Analysis** - Calculate 24h changes, moving averages
4. **Alerts** - Notify on price/supply anomalies
5. **Export Features** - CSV export, analytics API
6. **Chain-specific Data** - Track burn/mint events
7. **Bulk Operations** - Batch fetch supplies for efficiency

## Database Migration

Existing databases automatically get new columns:
```sql
ALTER TABLE pools ADD COLUMN total_supply REAL;
ALTER TABLE pools ADD COLUMN market_cap REAL;
```

No data loss - existing pools remain intact with NULL values until updated.
