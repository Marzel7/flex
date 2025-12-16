# Meteora Price Fetcher V2 - DexScreener Comparison

## Summary

The enhanced `meteora_price_fetcher_v2.py` script now compares on-chain spot prices against DexScreener cached data, helping identify when pools have experienced significant liquidity changes.

## Key Features

### 1. On-Chain Price Calculation
- Extracts vault addresses from pool creation transactions
- Fetches actual SOL and token balances
- Calculates spot price as: `SOL_balance / Token_balance`
- Handles decimals correctly (SOL = 9, tokens = 6-8 typically)

### 2. DexScreener Comparison
- Queries DexScreener API for cached price data
- Returns both native (SOL) and USD prices
- Includes liquidity and 24h volume information
- Shows which tokens pair in the pool

### 3. Price Discrepancy Analysis
- Calculates ratio: `On-Chain / DexScreener`
- Shows percentage difference
- **Logs both SOL and USD prices** for easy comparison
- Flags large discrepancies (>10%) with explanation
- Helps identify:
  - Stale DexScreener data
  - Significant liquidity changes
  - Arbitrage opportunities

### 4. Smart Vault Pair Selection
- **Priority 1**: Prefers SOL/Token pairs (known quotes) over Token/Token pairs
- **Priority 2**: Among same-type pairs, selects the pair with larger base token balance (better liquidity)
- **Fallback**: Uses "closest to 1.0" heuristic only when balances are within 10%
- Ensures most liquid and accurate price is selected from multiple vault combinations

### 5. Pool Depletion Detection
- Flags pools where smallest vault < 0.00001 SOL
- Flags pools with >100x imbalance between vaults
- Returns meaningful error messages instead of meaningless prices

## Usage

```bash
# Use token mint (best for DexScreener lookup)
python meteora_price_fetcher_v2.py 7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS

# Verbose output with vault details
python meteora_price_fetcher_v2.py 7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS -v

# Multiple pools
python meteora_price_fetcher_v2.py TOKEN1 TOKEN2 TOKEN3
```

## Example Output

```
Pool: 7ZLUrUkVM9F1e46HjrZPXrPdsJ7pnbxLJRxubhKUghbS
On-chain price (spot):  0.000000088456691304

DexScreener data:
  Native price (SOL):    0.000000604600000000
  USD price:             0.00007625
  Base token:            MONY
  Quote token:           SOL
  Liquidity USD:         $1.33
  24h Volume:            $5,307.31

Price Comparison:
  On-chain / DexScreener ratio: 0.146306x
  Difference:                   85.37%
  ⚠️  Large discrepancy detected!
     This may indicate:
     - DexScreener has cached/stale data
     - Pool liquidity has changed significantly
     - Pool is being arbitraged
```

## Understanding Price Discrepancies

### Example: MONY Pool

**On-Chain (Current):**
- SOL: 0.00135 (99.88% withdrawn)
- MONY: 15,306
- Price: 0.0000000884 SOL per MONY

**DexScreener (Cached):**
- Shows: 0.0000006046 SOL per MONY
- This represents state when SOL balance was ~1.17 SOL
- 6.8x difference indicates massive liquidity removal

**Explanation:**
The pool originally had ~1.17 SOL, then 99.88% of SOL was withdrawn. The on-chain price is correct for the current state, but DexScreener still shows the old price because it caches data.

## Technical Details

### Price Calculation Formula
```
price_native = quote_balance / base_balance
price_usd = price_native * sol_usd_price
```

### Depletion Detection Thresholds
- Smallest vault < 0.00001 → Depleted
- Imbalance > 100x → Depleted
- This prevents returning meaningless prices

### DexScreener API
- Endpoint: `https://api.dexscreener.com/latest/dex/tokens/{token_mint}`
- Returns multiple trading pairs for a token
- Primary pair has highest volume/liquidity
- Data may be cached and stale

## Files

- **meteora_price_fetcher_v2.py** - Enhanced script with DexScreener comparison
- **meteora_price_fetcher.py** - Original implementation
- **PRICE_FETCHER_NOTES.md** - Pool state explanations
- **PRICE_FETCHER_SUMMARY.md** - This document

## Next Steps

1. **Monitor pool states** - Track when liquidity is added/removed
2. **Compare prices** - Use on-chain prices for accurate arbitrage detection
3. **Identify opportunities** - Find pools with large discrepancies
4. **Update DexScreener** - Help identify stale cached data
