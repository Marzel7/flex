# Price Freshness and Update Mechanism

## Answer: Yes, Database Prices Are Automatically Updated

When you run `python main.py`, a background thread called `update_pool_prices()` continuously refreshes token prices from the blockchain and DexScreener API.

---

## Automatic Update Frequency

Prices are updated on a **sliding scale based on token age**:

| Token Age | Update Interval | Rationale |
|-----------|-----------------|-----------|
| 0-5 minutes | Every 30 seconds | New launches need frequent updates |
| 5-30 minutes | Every 2 minutes | Price stabilizing phase |
| 30+ minutes | Every 5 minutes | Mature pools, less volatile |

---

## How The Update System Works

### 1. Background Update Thread
When `python main.py` starts, it launches `update_pool_prices()`:
- Runs continuously in a daemon thread
- Checks for pools needing updates every 10 seconds
- Calculates age from `first_seen` timestamp
- Compares age to `last_price_update` timestamp

### 2. Update Process for Each Pool
For each pool needing an update:

```
1. Fetch LIVE price from blockchain
   └─ Uses: amm_id (pool address)
   └─ Fetches current SOL/token ratio from vault balances

2. Fetch token supply
   └─ Uses: base_mint
   └─ Gets current total supply

3. Calculate market cap
   └─ market_cap = price × total_supply

4. Update database
   └─ Stores new price, supply, market cap
   └─ Records timestamp in last_price_update

5. Fetch from DexScreener API
   └─ Gets external price for comparison
   └─ Stores as dexscreener_price_usd
   └─ Stores as dexscreener_price_native
```

### 3. Database Timestamps

Two key timestamps track update history:

- **`first_seen`**: When the pool was first detected (never changes)
  - Used to calculate token age
  - Example: "2024-01-15 14:32:45"

- **`last_price_update`**: When price was last refreshed (updated constantly)
  - Example: "2024-01-15 14:35:22"
  - Used to determine if pool needs updating

---

## How to Verify Price Freshness

### Option 1: Use `get_price_from_pools.py`
This script now shows price status:

```bash
python get_price_from_pools.py <TOKEN_MINT>
```

Output includes:
```
Current Price (USD): $0.00000061/token
Price Status: Updated 30s ago ✓ (fresh)
```

**Status indicators:**
- `✓ (fresh)` - Updated less than 5 minutes ago
- `~ (ok)` - Updated 5-30 minutes ago
- `~ (moderate)` - Updated 30-60 minutes ago
- `⚠ (stale)` - Updated more than 1 hour ago

### Option 2: Direct Database Query
```bash
python -c "
import sqlite3
conn = sqlite3.connect('pumpswap_tokens.db')
cursor = conn.cursor()
cursor.execute('SELECT base_mint, symbol, last_price_update, first_seen FROM pools LIMIT 5')
for row in cursor.fetchall():
    print(row)
"
```

### Option 3: Monitor main.py Output
When running `python main.py`, watch for:

```
[PRICE UPDATER] === Cycle 123 ===
[PRICE UPDATER] Found 5 pool(s) needing update
[PRICE UPDATER] Pool ages: 45s, 2m, 15m, 1h, 2h
[PRICE UPDATER] [1/5] Pool age: 45.0s, interval: 30s (0-5min)
[PRICE UPDATER] ✓ Updated 5wD5oj...: $0.00000061 (supply: 999,990,000, mcap: $61,099)
[DEXSCREENER] Updated for 5wD5oj...: $0.000000615
```

This shows:
- Pool age in seconds
- Update interval for that pool
- New price fetched
- DexScreener comparison price

---

## Price Data Hierarchy

The script uses prices in this order:

### 1. DEXScreener Prices (Most Accurate)
```python
dexscreener_price_usd      # Example: 0.0001332
dexscreener_price_native   # Example: 0.000000667
```
- Fetched from external API during updates
- Most accurate for comparison
- Updated every cycle

### 2. Calculated from Vault Balances (Fallback)
```python
current_price              # Stored SOL-per-token ratio
# Calculate: current_price * SOL_USD_PRICE
```
- Calculated from blockchain vault data
- Only used if DEXScreener data unavailable
- **Note**: `current_price` may be inverse (tokens per SOL) - use carefully!

---

## Troubleshooting: How Fresh Are Prices?

### Check the Timestamp
```bash
python get_price_from_pools.py <TOKEN_MINT>
```

Look for lines like:
```
Price Status: Updated 2m ago ✓ (fresh)
Last Updated: 2024-01-15 14:35:22
```

### Calculate Age Yourself
```python
from datetime import datetime

last_update = "2024-01-15 14:35:22"  # From database
now = datetime.now()
last_dt = datetime.fromisoformat(last_update)
age_seconds = (now - last_dt).total_seconds()
print(f"Age: {int(age_seconds)} seconds")
```

### Common Ages by Pool Status
- **New launch (0-5 min)**: Updated within 30 seconds ✓
- **Stabilizing (5-30 min)**: Updated within 2 minutes ✓
- **Mature (30+ min)**: Updated within 5 minutes (ok)
- **Idle (hasn't been checked)**: May be hours old ⚠

---

## Live Prices vs. Database Prices

### Database Prices
- Updated every 30s-5min (based on age)
- Reflect the most recent blockchain state
- Cached for fast queries
- Subject to network/RPC delays (~1-3 seconds)

### Live Prices from Vault (New Feature)
Use the enhanced script for fresh calculations:

```bash
python get_price_live_with_balances.py <TOKEN_MINT>
```

This script:
1. Queries database for pool info
2. Shows stored prices (from last update)
3. Shows when prices were last refreshed
4. Explains how to fetch vault balances directly from RPC (if vault addresses available)

---

## Configuration

### Update SOL Price for USD Conversion
Edit line 21 in `get_price_from_pools.py`:

```python
SOL_USD_PRICE = 200  # Change to current SOL market price
```

Current rates:
- ~$200/SOL (default)
- ~$250/SOL (as of late 2024)
- Update to your market's current rate

---

## Understanding Update Intervals

### Why Sliding Scale?
1. **New launches are volatile**
   - Price swings 10%+ per second
   - Need 30s updates to catch changes

2. **Mature pools are stable**
   - Price moves slowly
   - Can use 5min updates (more efficient)

3. **Older pools are sleeping**
   - Liquidity may have dried up
   - Still need periodic checks for sudden activity

### How It's Calculated
```python
# In main.py - get_pools_needing_update()
age_seconds = (now - first_seen_dt).total_seconds()

if age_seconds < 300:        # 0-5 minutes
    update_interval = 30     # Every 30 seconds
elif age_seconds < 1800:     # 5-30 minutes
    update_interval = 120    # Every 2 minutes
else:                        # 30+ minutes
    update_interval = 300    # Every 5 minutes

# Check if update needed
seconds_since_update = (now - last_price_update_dt).total_seconds()
if seconds_since_update >= update_interval:
    needs_update = True
```

---

## Summary: How Fresh Are Your Prices?

✓ **Fresh** = Updated within the last 5 minutes
~ **OK** = Updated within the last 30 minutes
⚠ **Stale** = Updated more than 1 hour ago

**To check:**
```bash
python get_price_from_pools.py <TOKEN_MINT>
# Look at "Price Status:" line
```

**To ensure fresh data:**
1. Run `python main.py` continuously in the background
2. Wait for initial price update (first pool update = 30 seconds for new launches)
3. Prices are automatically refreshed on sliding scale

---

## Related Scripts

- **get_price_from_pools.py** - Query prices + freshness status
- **get_price_live_with_balances.py** - Show stored prices + vault details
- **main.py** - The monitoring system that keeps prices fresh
- **test_pumpswap_listener.py** - Monitor price updates in real-time

---

## Next Steps

1. **Start monitoring**: `python main.py`
2. **Check a token**: `python get_price_from_pools.py <TOKEN_MINT>`
3. **Watch updates**: Look for `[PRICE UPDATER]` logs in main.py output
4. **Verify freshness**: Look at "Price Status:" in the output

