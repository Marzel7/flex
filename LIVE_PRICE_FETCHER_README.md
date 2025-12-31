# Live Price Fetcher - Real-Time Vault Balance Prices

## Overview

The `test_vault_price_template.py` script fetches **LIVE prices directly from PumpSwap pool vaults** instead of using stale database data.

## What It Does

1. **Queries database** for pool AMM addresses and token mints
2. **Derives vault accounts** - Finds the SPL token accounts owned by each pool
3. **Fetches LIVE balances** from blockchain via RPC:
   - Token vault balance (how many tokens in vault)
   - SOL vault balance (how much SOL in vault)
4. **Calculates CURRENT price**: `Price = SOL Balance / Token Balance`
5. **Displays real metrics**:
   - Price in SOL per token
   - Price in USD per token
   - Liquidity (SOL in vault)
   - Market cap (price × total supply)
   - Vault address

## Price Calculation Formula

```
Price (SOL per token) = SOL Vault Balance / Token Vault Balance
Price (USD per token) = Price (SOL) × SOL USD Price ($200)
Market Cap (USD)      = Price (USD) × Total Token Supply
Liquidity (SOL)       = SOL Vault Balance
```

## Vault Discovery Methods

The script uses multiple methods to discover and access vault accounts, making it robust across different pool structures:

1. **Method 1: Direct Account Structure Extraction**
   - Fetches the pool account data from RPC
   - Extracts vault addresses from the LBPair account structure
   - Vault addresses are at fixed byte offsets (168-200 and 200-232)
   - Most reliable for PumpSwap/Pump.fun style pools

2. **Method 2: Token Program Query Fallback**
   - Uses `getProgramAccounts` on the SPL Token Program
   - Filters for token accounts owned by the pool
   - Discovers vault accounts dynamically
   - Works as fallback when Method 1 doesn't find vaults

## Setup

### Option 1: Use Database Fallback (No API Key Needed)
```bash
python test_vault_price_template.py
```
Shows current database prices and market caps (calculated fresh from stored data). Also attempts to fetch SOL balances from RPC if in automatic fallback mode.

### Option 2: Fetch LIVE Prices (Requires API Key)

1. **Get Helius API Key:**
   - Visit: https://www.helius.dev/
   - Sign up for free tier
   - Copy your API key

2. **Set environment variable:**
   ```bash
   export HELIUS_API_KEY="your-api-key-here"
   ```

3. **Run script:**
   ```bash
   python test_vault_price_template.py              # All tokens with LIVE prices
   python test_vault_price_template.py <TOKEN_MINT> # Single token with LIVE price
   ```

## Example Output

### Without API Key (Database Fallback)
```
Symbol          Price (USD)          Total Supply         Market Cap             Token Address
────────────────────────────────────────────────────────────────────────────────────────────────
LIT             $0.00035640          1000.00M             $356.40K               47bXryb6...
Money           $0.00005135          N/A                  N/A                    D2sKNNqE...
```

### With API Key (LIVE Vault Prices)
```
Symbol          Price (SOL)          Price (USD)          Liquidity (SOL)        Market Cap
────────────────────────────────────────────────────────────────────────────────────────────
LIT             $0.000002814         $0.000561            $12,345 SOL            $561K USD
Money           $0.000001245         $0.000249            $5,678 SOL             $249K USD
```

## Key Features

✓ **LIVE data** - Fetches fresh from blockchain, not cached
✓ **No external APIs** - Uses RPC calls directly (only needs Helius key)
✓ **Accurate market cap** - Calculated from LIVE price × supply
✓ **Liquidity tracking** - Shows SOL in vault (real liquidity)
✓ **Vault transparency** - Shows exact vault address being queried
✓ **Fallback mode** - Works without API key using database data

## What's Different from Database Prices?

| Aspect | Database | LIVE Vault |
|--------|----------|-----------|
| **Data Source** | Last stored update (can be stale) | Direct blockchain query (current) |
| **Update Frequency** | Every 30s-5min | Fetched fresh on demand |
| **Accuracy** | Depends on last update | Real-time vault balances |
| **Liquidity** | Stored value (may be 0) | Current SOL in vault |
| **API Requirement** | No | Yes (Helius key) |

## Technical Details

### RPC Methods Used

When API key is provided, script uses:
- `getTokenSupply()` - Get token decimals and supply
- `getProgramAccounts()` - Find vault accounts for a pool
- `getTokenAccountBalance()` - Get token balance in vault
- `getBalance()` - Get SOL balance in vault account

### Vault Account Discovery

The script finds vault accounts by:
1. Querying the SPL Token Program
2. Finding all token accounts with these filters:
   - Size == 165 bytes (SPL token account)
   - Owner == AMM pool address (from database)
   - Mint == token address (from database)

## Output Format

When fetching LIVE prices, you'll see:
```
  Symbol       Fetching metadata... ✓ (9 decimals)
  Symbol       Finding vault accounts... ✓ (2 found)
  Symbol       Fetching vault balances... ✓
  Symbol       Calculating price... ✓
```

## Troubleshooting

### "HELIUS_API_KEY not set"
- This is normal - script falls back to database mode
- To enable LIVE fetching: `export HELIUS_API_KEY="your-key"`

### "no vaults found"
- Pool might not have created vault accounts yet
- Check if pool is fully initialized on-chain
- Try with different pool address

### "Fetching metadata... ✗"
- RPC call failed (may be temporary)
- Check API key is valid
- Try again after a moment

## Files

- `test_vault_price_template.py` - Main script with both modes
- Requires: `pumpswap_tokens.db` (database with pool info)

## Next Steps

1. **Get free Helius API key:** https://www.helius.dev/
2. **Set environment variable:** `export HELIUS_API_KEY="your-key"`
3. **Run with LIVE prices:** `python test_vault_price_template.py`
4. **Monitor vaults:** Run periodically to track price changes

## Real-Time Monitoring

To monitor prices continuously:
```bash
while true; do
    echo "=== $(date) ==="
    python test_vault_price_template.py 47bXryb6KGkF4kTGmveUAzFfigHSSzRkZi3ibtjhUbJY
    sleep 30  # Check every 30 seconds
done
```

## Performance

- **Single token:** ~2-5 seconds (includes RPC calls)
- **All 8 tokens:** ~10-20 seconds (parallel-safe calls)
- **Database fallback:** <1 second (instant display)

