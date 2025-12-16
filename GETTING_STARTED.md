# Getting Started - Meteora Price Fetcher Integration

## Quick Start

### 1. Start the Application
```bash
cd /Users/kevinkeaveney/Dev/claude/flex
python main.py
```

The application will start:
- WebSocket listener for Solana pool events
- Flask web server on http://localhost:5002
- Price update background thread

### 2. Open the Web UI
Navigate to http://localhost:5002 in your browser

### 3. Monitor Pools
- New Meteora pools appear automatically
- Each pool shows: Name, Symbol, Creation Time, Current Price, Price Change %
- Pool images load automatically (if available)

### 4. Fetch Meteora Prices
- Click the green "🔍 Price" button on any pool
- Wait 1-2 seconds for data to fetch
- View results:
  - 💹 On-chain price (real-time from Solana)
  - 📊 DexScreener price (cached from DexScreener API)
  - 📈 Comparison (ratio, % difference, status)
  - Liquidity and 24h volume

## What Each Component Does

### `main.py`
The main application containing:
- **RaydiumDatabase** - SQLite database for pool data
- **RaydiumMonitor** - WebSocket listener for pool creation events
- **MeteoraPriceFetcher** - Price fetching and DexScreener comparison
- **Flask Web Server** - REST API and HTML UI

### `meteora_price_fetcher_v2.py`
Standalone script for testing Meteora prices:
```bash
# Test a single token
python meteora_price_fetcher_v2.py HUvp4TqYf7vocfdATium96w5TVQDfbaekvrJUPu9D5JH

# Verbose output with vault details
python meteora_price_fetcher_v2.py HUvp4TqYf7vocfdATium96w5TVQDfbaekvrJUPu9D5JH -v

# Compare multiple tokens
python meteora_price_fetcher_v2.py TOKEN1 TOKEN2 TOKEN3
```

## API Reference

### Get Pools
```bash
GET /api/pools/new
```
Returns recently detected pools

### Get Meteora Price
```bash
GET /api/meteora/price/<token_mint>
```

Example:
```bash
curl http://localhost:5002/api/meteora/price/HUvp4TqYf7vocfdATium96w5TVQDfbaekvrJUPu9D5JH
```

Response:
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

## Understanding the Results

### Price Comparison Status

**✅ Matched** (< 10% difference)
- On-chain and DexScreener prices are similar
- Price data is fresh and accurate
- Green color indicator

**⚠️ Large Discrepancy** (> 10% difference)
- On-chain price differs significantly from DexScreener
- Could indicate:
  - Stale DexScreener cache
  - Pool liquidity recently changed
  - Arbitrage opportunity
  - Pool may be depleted
- Red color indicator

### Ratio Interpretation

- **Ratio = 1.0x** - Prices match perfectly
- **Ratio > 1.0x** - On-chain price is higher than DexScreener
- **Ratio < 1.0x** - On-chain price is lower than DexScreener

Example:
- On-chain: 0.000000761804 SOL/WIFE
- DexScreener: 0.000000712400 SOL/WIFE
- Ratio: 1.069350x (on-chain is 6.93% higher)

## Smart Price Selection Algorithm

The fetcher uses a 3-tier priority system to select the best vault pair:

### Tier 1: Quote Token Type
Prefers pairs with known quote tokens (SOL, USDC, USDT, etc.)
- SOL/Token pair ← ✅ Preferred
- Token/Token pair ← Less preferred

### Tier 2: Base Token Balance
Among same-type pairs, prefers larger balances
- Larger token vault → Better liquidity → More accurate price
- Small vault (e.g., 1M tokens) vs Large vault (e.g., 100M tokens)

### Tier 3: Proximity to 1.0
Falls back to prices closest to 1.0 for readability
- Price 0.99 ← Preferred (close to 1.0)
- Price 0.0001 ← Less preferred (far from 1.0)
- Price 1000 ← Less preferred (far from 1.0)

## Troubleshooting

### "Could not fetch price"
- Pool may have been depleted
- Token may not be indexed on Solana RPC yet
- Try again in a few seconds

### Large price discrepancy shown
- Check if pool is active or depleted
- DexScreener data may be cached/old
- On-chain price is more current

### No DexScreener data
- Token may be too new (not yet on DexScreener)
- Only on-chain price will be shown

### Price button not responding
- Check browser console for errors (F12)
- Verify API endpoint is working: `/api/meteora/price/TOKEN`
- Try refreshing the page

## Database

Pools are stored in `raydium_pools.db` SQLite database with:
- Pool metadata (name, symbol, image)
- Creation and update timestamps
- Price history (creation_price, current_price)
- DEX source (Raydium, Meteora, etc.)

## Files Reference

```
/Users/kevinkeaveney/Dev/claude/flex/
├── main.py                          # Main application
├── meteora_price_fetcher_v2.py      # Standalone price fetcher
├── meteora_price_fetcher.py         # Original price fetcher (reference)
├── run_and_test.sh                  # Auto-test script
├── CLAUDE.md                        # Project architecture
├── PRICE_FETCHER_SUMMARY.md         # Price fetcher features
├── PRICE_FETCHER_NOTES.md           # Pool state explanations
├── INTEGRATION_COMPLETE.md          # Integration documentation
└── GETTING_STARTED.md               # This file
```

## Performance Tips

1. **Batch Fetches** - Click "🔍 Price" on multiple pools to see comparison data
2. **Real-time Updates** - Prices are fetched fresh each time (not cached in UI)
3. **Background Refresh** - Pool prices update in background automatically
4. **UI Responsive** - Price fetches are non-blocking async operations

## Common Use Cases

### Find Arbitrage Opportunities
1. Monitor pools as they're created
2. Click "🔍 Price" to fetch prices
3. Look for large discrepancies (> 20%)
4. Compare against other DEX prices

### Track Pool Depletion
1. Check if price discrepancy grows over time
2. Large discrepancy + decreasing volume = pool draining
3. Use this to identify rug pulls early

### Validate Pool Prices
1. On-chain price is the source of truth
2. Compare against DexScreener to identify cache lag
3. Can indicate arbitrage opportunities

## Next Steps

- Monitor live pools and track price changes
- Identify interesting opportunities
- Export price history for analysis
- Set up price alerts (future feature)

## Support

For issues or questions:
1. Check error messages in browser console (F12)
2. Review logs in terminal where `python main.py` is running
3. Check `INTEGRATION_COMPLETE.md` for detailed technical documentation
4. Review `PRICE_FETCHER_SUMMARY.md` for price fetcher specifics
