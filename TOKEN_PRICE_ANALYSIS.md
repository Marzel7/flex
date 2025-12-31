# Token Price Analysis Guide

## Reference Token
**Mint Address**: `fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7`

---

## How to Determine Token Price

### Method 1: Use the Price Test Script (Recommended)
```bash
python test_token_price.py
```

This script:
1. Fetches token metadata from Jupiter API
2. Queries Jupiter Swap API for price via USDC conversion
3. Checks Pump.fun API for bonding curve data
4. Checks Birdeye API for alternative pricing
5. Compares with locally monitored tokens in database

**Requirements**: Network access to external APIs

---

## Price Determination Methods

### A. Jupiter Swap API (Most Reliable)
Uses swap quote to determine price:
```
Price (USD) = Output Amount (USDC) / Input Amount (Token)
```

**Endpoint**: `https://quote-api.jup.ag/v6/quote`

**Parameters**:
- `inputMint`: Token address to price
- `outputMint`: USDC mint (EPjFWaLb3odcwvLgJgWC1DjwzewMsNuqfZhyvtAPwqf)
- `amount`: Input amount in smallest units (1,000,000 = 1 token if 6 decimals)
- `slippageBps`: Slippage tolerance (50 = 0.5%)

**Response**:
```json
{
  "outAmount": "1234567",  // USDC amount (6 decimals)
  "inputAmount": "1000000",
  "priceImpactPct": "0.01",
  "routePlan": [...]
}
```

### B. Pump.fun API
For tokens still on Pump.fun bonding curve:

**Endpoint**: `https://api.pump.fun/get_token`

**Parameters**:
- `token`: Token mint address

**Response**:
```json
{
  "price": 0.000001,
  "market_cap": 1000000,
  "volume_24h": 500000,
  "bonding_curve_progress": 85.5
}
```

### C. Birdeye API
Community-run price aggregator:

**Endpoint**: `https://api.birdeye.so/defi/token_price`

**Parameters**:
- `address`: Token mint

**Response**:
```json
{
  "data": {
    "value": 0.000001,
    "updateUnixTime": 1234567890
  }
}
```

### D. PumpSwap Local Database
If token has graduated to PumpSwap, price is in local database:

**Query**:
```sql
SELECT symbol, price_usd, price_sol, total_supply, initial_price
FROM pools
WHERE base_mint = 'fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7'
```

**How we calculate it**:
```
Price (SOL per token) = Token Balance / SOL Balance
Price (USD) = Price (SOL) × SOL/USD Rate
```

---

## Token Lifecycle and Price Availability

```
┌─────────────────────────────────────────────────────────────────┐
│                   TOKEN LIFECYCLE                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. LAUNCH (Creator on Pump.fun)                               │
│     ├─ Token created on bonding curve                          │
│     ├─ Price: Available via Pump.fun API                       │
│     └─ Status: No PumpSwap pool yet                            │
│                                                                 │
│  2. BONDING CURVE (Community trading)                          │
│     ├─ Token traded on bonding curve                           │
│     ├─ Price: Available via Pump.fun API                       │
│     └─ Progress tracked (0% → 100%)                            │
│                                                                 │
│  3. GRADUATION (Curve complete → PumpSwap)                     │
│     ├─ Automatic migration to PumpSwap AMM                     │
│     ├─ Pool created in PumpSwap program                        │
│     ├─ Price: Transaction post-balances extracted              │
│     └─ Detected by our WebSocket listener                      │
│                                                                 │
│  4. PUMPSWAP (Active trading)                                  │
│     ├─ Token traded on PumpSwap AMM                            │
│     ├─ Price: Multiple sources (Jupiter, Birdeye, etc)         │
│     ├─ Continuous price updates (30s-5min intervals)           │
│     └─ Stored in local database                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## For Reference Token: fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7

### Status Determination

To check this token's current status:

1. **Check Pump.fun** (if still on bonding curve):
   ```bash
   curl "https://api.pump.fun/get_token?token=fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7"
   ```

2. **Check local database** (if graduated to PumpSwap):
   ```bash
   python test_token_price.py  # Uses script above
   ```

3. **Check DEX Screener** (if on PumpSwap):
   - URL: https://dexscreener.com/solana/fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7

---

## PumpSwap Price Extraction (Our System)

When the token graduates to PumpSwap, our system:

1. **Detects Migration**
   - WebSocket receives pool creation from PumpSwap program (pAMMBay6...)
   - Transaction signature extracted
   - Token mint identified as `base_mint`

2. **Fetches Transaction**
   - RPC call: `getTransaction(signature)`
   - Extracts `meta.postTokenBalances`

3. **Calculates Price**
   ```python
   # Find token balance matching token mint
   token_balance = find_balance(postTokenBalances, base_mint)

   # Find SOL balance
   sol_balance = find_balance(postTokenBalances, "So11111111111111111111111111111111111111112")

   # Calculate price
   price_sol_per_token = token_balance / sol_balance
   price_usd = price_sol_per_token * sol_price_usd
   ```

4. **Stores in Database**
   - Initial price recorded in `pools` table
   - Continuous updates tracked in `pool_history` table

---

## Example Price Data

### Sample Monitored Tokens (from database):

To see current prices of tokens we're monitoring:
```bash
sqlite3 pumpswap_tokens.db "SELECT symbol, price_usd, price_sol FROM pools LIMIT 10;"
```

---

## Script Usage

### Run Token Price Analysis
```bash
python test_token_price.py
```

### Output Sections

1. **TOKEN METADATA**
   - Name, Symbol, Decimals
   - Source: Jupiter API

2. **PRICE SOURCES**
   - Jupiter (swap quote)
   - Pump.fun (bonding curve)
   - Birdeye (alternative)

3. **PRICE ANALYSIS RESULTS**
   - Primary price (most reliable source)
   - Price comparison (if multiple sources)
   - Possible reasons if not found

4. **LOCAL DATABASE**
   - Sample tokens for reference
   - Current prices of monitored tokens

---

## Troubleshooting

### "Token metadata not found"
- Token doesn't exist yet on Solana
- Token is not indexed by Jupiter
- Check token address spelling

### "Could not determine price"
- Token not on any major DEX yet
- Token is too new (< 1 hour)
- Token may have no liquidity
- External APIs not accessible

### "Local database not available"
- `main.py` not running
- Database not created yet
- Run `python main.py` to start collecting data

---

## Reference Information

### PumpSwap Program Address
```
pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
```

### USDC Mint (for price quotes)
```
EPjFWaLb3odcwvLgJgWC1DjwzewMsNuqfZhyvtAPwqf
```

### SOL Mint
```
So11111111111111111111111111111111111111112
```

### Standard Token Decimals
- Most tokens: 6 decimals
- Some: 8 or 9 decimals
- Token-specific: Check via Jupiter API

---

## Related Files

- `test_token_price.py` - Main price analysis script
- `main.py` - PumpSwap monitoring system
- `pumpswap_tokens.db` - Local price database
- `PUMPSWAP_ONLY_MONITORING.md` - Monitoring details

