# Price Lookup Guide

## Quick Usage

Get token price from local pool database (in USD):

```bash
python get_price_from_pools.py <TOKEN_MINT>
```

### Examples

```bash
# Check reference token
python get_price_from_pools.py fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7

# Check LIT token
python get_price_from_pools.py 47bXryb6KGkF4kTGmveUAzFfigHSSzRkZi3ibtjhUbJY

# Show all PumpSwap tokens
python get_price_from_pools.py
```

---

## Price Data from Pool Vaults

Prices are extracted from **on-chain pool vault balances** when tokens migrate from Pump.fun to PumpSwap:

**Formula**:
```
Price (SOL/token) = SOL Balance in Vault / Token Balance in Vault
Price (USD/token) = Price (SOL) × SOL Price (USD)
```

---

## Current PumpSwap Tokens (with prices)

| Symbol | Price (USD) | Market Cap | Supply |
|--------|-------------|-----------|---------|
| LIT | $1,425,363.46 | $11,254.13B | 1,000.00M |
| N/A (5wD5...) | $33,100,701,451.43 | $261,590,406.37B | 999.99M |
| N/A (DjxJ...) | $172,672.64 | $1,368.12B | 999.99M |
| 365/365, Codex, Money, FILECOin | N/A (no price data) | N/A | N/A |

---

## How Prices Are Calculated

1. **WebSocket detects** pool creation from PumpSwap program
2. **Transaction fetched** via RPC with post-balance metadata
3. **Vault balances extracted**:
   - Token balance (matching token mint)
   - SOL balance (native SOL)
4. **Price calculated**: Token Balance / SOL Balance
5. **Converted to USD**: SOL Price × 200 (default, can be updated)
6. **Stored in database**: `current_price` and `market_cap` columns

---

## Reference Token Status

**Token**: `fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7`

**Status**: ✗ Not detected yet

**When it migrates to PumpSwap**, it will:
1. Appear in the database
2. Have price calculated from vault balances
3. Be queryable via this script
4. Show USD price automatically

---

## Script Features

### Price Display
- Shows price in both **SOL** and **USD**
- Calculates **price change** percentage
- Formats large numbers (B, M, K notation)

### Market Data
- Total supply
- Market cap
- Liquidity
- 24h volume

### Pool Information
- AMM address
- PumpSwap flag
- Detection timestamp
- Last update time

---

## Database Schema

Prices stored in `pumpswap_tokens.db` with columns:

| Column | Purpose |
|--------|---------|
| `current_price` | Price in SOL/token |
| `pumpswap_initial_price` | Initial price when pool was created |
| `market_cap` | Market cap in USD |
| `total_supply` | Token supply |
| `sol_usd_price` | SOL/USD rate (for conversion) |

---

## Updating SOL Price

Edit line 21 in `get_price_from_pools.py`:

```python
SOL_USD_PRICE = 200  # Change to current SOL price in USD
```

Current rates (approximate):
- $200/SOL (default)
- Update to current market price for accurate USD conversion

---

## For Your Reference Token

To check price when it's available:

```bash
python get_price_from_pools.py fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7
```

The script will:
1. Search the database
2. Extract vault balance prices (SOL/token)
3. Convert to USD
4. Show complete market data
5. Display price history if available

Once detected by the monitoring system (`python main.py`), prices will be updated continuously.

---

## Troubleshooting

### "Token NOT IN DATABASE"
- Token hasn't migrated to PumpSwap yet
- Monitoring system isn't running (`python main.py`)
- Token mint address might be incorrect

### "N/A" prices shown
- Token data incomplete in database
- No vault balance data available yet
- Token may have been added to database but price not calculated

### "Cannot connect to database"
- Database file (`pumpswap_tokens.db`) doesn't exist
- Start monitoring: `python main.py`
- Wait for tokens to be detected

---

## Related Commands

```bash
# Start monitoring system
python main.py

# Run tests
python test_pumpswap_detection.py
python test_pumpswap_phase2.py

# Check listener
python test_pumpswap_listener.py
```

