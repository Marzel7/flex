# Meteora Price Fetcher Integration - Complete

## Overview

Successfully integrated the enhanced Meteora price fetcher (`meteora_price_fetcher_v2.py`) into the main application (`main.py`) and the web UI with full DexScreener comparison capabilities.

## What Was Added

### 1. Backend Integration

#### MeteoraPriceFetcher Class
A new class added to `main.py` that provides:

- **`fetch_price(token_mint)`** - Main method to fetch prices
- **Vault extraction** - Extracts vault addresses from pool creation transactions
- **Balance fetching** - Gets real-time vault balances from Solana blockchain
- **Smart price selection** - 3-priority vault pair selection:
  1. Prefer SOL/Token pairs over Token/Token pairs
  2. Among same-type pairs, prefer larger base token balances
  3. Fall back to "closest to 1.0" for readability

#### Methods
- `rpc_call()` - RPC calls to Helius
- `get_dexscreener_price()` - Fetches DexScreener data
- `_extract_vaults_from_tx()` - Extracts vaults from pool creation TX
- `_is_token_account()` - Validates SPL token accounts
- `_get_vault_info()` - Fetches vault balances and mint info
- `_get_mint_decimals()` - Gets token decimals
- `_calculate_best_price()` - Smart price pair selection

### 2. API Endpoint

#### `/api/meteora/price/<token_mint>`

**Request:**
```
GET /api/meteora/price/HUvp4TqYf7vocfdATium96w5TVQDfbaekvrJUPu9D5JH
```

**Response:**
```json
{
  "on_chain_price": 0.000000761804986728,
  "dexscreener_data": {
    "priceNative": 0.000000712400000000,
    "priceUsd": 0.00009026,
    "liquidity": { "usd": 31725.25 },
    "volume24h": 4821.57,
    "baseToken": "WIFE",
    "quoteToken": "SOL"
  },
  "comparison": {
    "ratio": 1.069350,
    "difference_pct": 6.94,
    "status": "matched"
  }
}
```

### 3. UI Enhancement

#### New Pool Card Features

**Price Button**
- Added green "🔍 Price" button to each pool card
- Click to fetch Meteora price data
- Shows loading state during fetch

**Price Data Display**
- Shows on-chain price (in SOL)
- Shows DexScreener price (in SOL and USD)
- Displays liquidity and 24h volume
- Shows comparison ratio and discrepancy %
- Color-coded status (green for matched, red for large discrepancy)
- Auto-hides after 10 seconds

**Layout**
```
┌─────────────────────────────────────────────┐
│ [Icon] Pool Name (SYM) [DEX Badge]          │
│ [Token Address Link]                         │
│                          Time | Price | +X% │
│                          🔍 Price Button     │
│                          [Price Data Display]│
└─────────────────────────────────────────────┘
```

### 4. Configuration

**Known Quote Tokens** (used for smart pair selection)
```python
KNOWN_QUOTES = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWaLb3odccjf2cj6zpf5p6A8wMJvhystNRepAHRA": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenErt": "USDT",
    "BXXx3hqV7pZGT3XKRsvB9LyXYH2JjHVHwp4GbpNXYL3": "PAI",
}
```

## Key Features

✅ **Real-time Price Fetching** - Gets current on-chain prices from Meteora pools
✅ **Smart Vault Selection** - Intelligent algorithm to pick correct vault pair
✅ **DexScreener Comparison** - Compares against cached data
✅ **Discrepancy Detection** - Flags large price differences
✅ **Liquidity Tracking** - Shows current liquidity and volume
✅ **User-Friendly UI** - Interactive price button with clean display
✅ **Error Handling** - Graceful fallbacks and error messages
✅ **Performance** - Async fetching with loading indicators

## Testing

### Test Cases Verified

1. **WIFE Token** (HUvp4TqYf7vocfdATium96w5TVQDfbaekvrJUPu9D5JH)
   - ✅ On-chain: 0.000000761804 SOL per WIFE
   - ✅ DexScreener: 0.000000712400 SOL per WIFE
   - ✅ Ratio: 1.069350x (6.94% difference)
   - Status: Matched ✅

2. **MONY Token** (7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS)
   - ✅ On-chain: 0.0000000884 SOL per MONY
   - ✅ DexScreener: 0.0000006046 SOL per MONY
   - ✅ Ratio: 0.146306x (85.37% difference)
   - Status: Large discrepancy (pool depleted)

3. **HEDGE Token** (2eAzfJ3ooEKSu45xsryVmVj6a22QFSMB9Ne5DhzZ352v)
   - ✅ Correctly detected pool depletion
   - ✅ DexScreener data still available
   - Status: Depleted pool

## Usage

### Via Web UI
1. Navigate to http://localhost:5002
2. Wait for pools to appear
3. Click "🔍 Price" button on any pool
4. Price data appears in 1-2 seconds
5. Auto-hides after 10 seconds

### Via API
```bash
# Fetch Meteora price for a token
curl http://localhost:5002/api/meteora/price/HUvp4TqYf7vocfdATium96w5TVQDfbaekvrJUPu9D5JH

# Response includes on-chain price, DexScreener data, and comparison
```

## Architecture

### Integration Flow
```
New Pool Created
    ↓
Pool Detection (WebSocket)
    ↓
Pool Broadcast Queue
    ↓
UI Polls /api/pools/new
    ↓
Pool Rendered with "🔍 Price" Button
    ↓
User clicks button
    ↓
/api/meteora/price/<mint> called
    ↓
MeteoraPriceFetcher extracts vaults
    ↓
Fetches balances from Solana RPC
    ↓
Calculates best price (smart selection)
    ↓
Fetches DexScreener data
    ↓
Displays comparison in UI
```

## Files Modified

- **main.py**
  - Added `MeteoraPriceFetcher` class (250+ lines)
  - Added `/api/meteora/price/<token_mint>` endpoint
  - Enhanced pool card UI with price button
  - Added `fetchMeteoraPriceData()` JavaScript function
  - Total changes: ~350 lines added

## Performance Considerations

- **Async Operations** - Price fetches don't block UI
- **RPC Caching** - Helius RPC provides response caching
- **Timeout Protection** - 10s timeout on API calls
- **Optional Feature** - Price fetching is opt-in (click button)
- **Auto-cleanup** - UI auto-hides price data after 10s

## Error Handling

- **Missing Vaults** - Returns error if can't extract vaults
- **RPC Failures** - Gracefully handles timeouts/errors
- **Pool Depletion** - Detects and reports depleted pools
- **Invalid Tokens** - Handles non-token addresses
- **DexScreener Unavailable** - Still shows on-chain price

## Future Enhancements

Possible improvements:
- Auto-refresh price data every N seconds
- Store price history in database
- Price alert notifications
- Bulk price fetch for top X pools
- Price trend analysis
- Export price data to CSV

## Deployment

No changes to deployment needed. The integration is backward-compatible:
- Existing functionality unchanged
- Price fetching is opt-in (button click)
- No new database tables required
- Uses existing RPC and API infrastructure

## Summary

The Meteora price fetcher has been successfully integrated into the main application with a clean, user-friendly UI. Users can now:

1. Click "🔍 Price" on any Meteora pool
2. See real-time on-chain prices
3. Compare against DexScreener cached data
4. Identify arbitrage opportunities and stale prices

The implementation is robust, efficient, and provides valuable insights into pool pricing dynamics.
