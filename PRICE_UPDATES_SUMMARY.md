# Price Updates and Freshness - Complete Summary

## What You Asked
**"Do the values in the database get updated? How do we know the price is up to date?"**

## The Answer

### Yes, Database Values Are Automatically Updated

When you run `python main.py`, it automatically:
1. ✓ Detects new token pools via WebSocket
2. ✓ Calculates prices from vault balances
3. ✓ Updates prices continuously on a sliding scale:
   - **0-5 minutes old**: Every 30 seconds
   - **5-30 minutes old**: Every 2 minutes
   - **30+ minutes old**: Every 5 minutes
4. ✓ Fetches prices from DexScreener for comparison
5. ✓ Stores all data in SQLite with timestamps

---

## How to Know Prices Are Fresh

### Method 1: Use Enhanced Script (Easiest)
```bash
python get_price_from_pools.py <TOKEN_MINT>
```

Output now shows:
```
Current Price (USD): $0.00000061/token
Price Status: Updated 30s ago ✓ (fresh)
```

**Status Meanings:**
- `Updated Xs ago ✓ (fresh)` = Less than 5 minutes old
- `Updated Xm ago ~ (ok)` = 5-30 minutes old
- `Updated Xm ago ~ (moderate)` = 30-60 minutes old
- `Updated Xh ago ⚠ (stale)` = More than 1 hour old

### Method 2: Monitor main.py Output
While `python main.py` runs, watch for:

```
[PRICE UPDATER] === Cycle 123 ===
[PRICE UPDATER] Found 5 pool(s) needing update
[PRICE UPDATER] Pool ages: 45s, 2m, 15m, 1h, 2h
[PRICE UPDATER] [1/5] Pool age: 45.0s, interval: 30s (0-5min)
[PRICE UPDATER] ✓ Updated fdry5i5...: $0.00000061 (supply: 1,000,000,000, mcap: $61,000)
[DEXSCREENER] Updated for fdry5i5...: $0.000000615
```

This shows prices being fetched and updated in real-time.

### Method 3: Check Timestamps Directly
```bash
python -c "
import sqlite3
from datetime import datetime
conn = sqlite3.connect('pumpswap_tokens.db')
cursor = conn.cursor()
cursor.execute('SELECT symbol, last_price_update, first_seen FROM pools LIMIT 5')
for symbol, last_update, first_seen in cursor.fetchall():
    if last_update:
        age = (datetime.now() - datetime.fromisoformat(last_update)).total_seconds()
        print(f'{symbol}: Updated {int(age)}s ago')
    else:
        print(f'{symbol}: Never updated')
"
```

---

## What Gets Updated and When

### The Update Mechanism (in main.py)

**Location**: `main.py` lines 2357-2452 in `update_pool_prices()` function

**Process**:
1. Every 10 seconds, system checks which pools need updating
2. For each pool:
   - Calculates age from `first_seen` timestamp
   - Compares `last_price_update` to determine if due for refresh
   - Fetches new price from blockchain
   - Fetches token supply
   - Calculates market cap = price × supply
   - Updates database with new values
   - Records update time in `last_price_update`
   - Also fetches from DexScreener API for comparison

### Database Timestamps

**`first_seen`**
- When pool was first detected
- Never changes
- Used to calculate pool age

**`last_price_update`**
- When price was last refreshed
- Updated every time a new price is fetched
- Used to determine if pool needs another update

### Example Update Cycle

```
Time: 14:32:00 - New pool detected
  first_seen = 2024-01-15 14:32:00
  last_price_update = NULL (not yet updated)

Time: 14:32:30 - First update
  last_price_update = 2024-01-15 14:32:30
  age = 30 seconds
  next update in 30 seconds (due at 14:33:00)

Time: 14:33:00 - Second update
  last_price_update = 2024-01-15 14:33:00
  age = 60 seconds
  next update in 30 seconds (due at 14:33:30)

Time: 14:35:00 - Pool now 3 minutes old
  age = 180 seconds (still in 0-5 min range)
  Updates every 30 seconds (no change)

Time: 14:37:00 - Pool now 5 minutes old
  age = 300 seconds (moving to 5-30 min range)
  Updates now every 2 minutes instead of 30 seconds
```

---

## Enhanced Scripts Created

### 1. Updated: `get_price_from_pools.py`
**What it does now:**
- Shows price freshness status
- Displays when prices were last updated
- Shows SOL balance (liquidity) for each pool
- Better formatting with column alignment

**Usage:**
```bash
python get_price_from_pools.py <TOKEN_MINT>
python get_price_from_pools.py  # Shows all PumpSwap tokens
```

**New Features:**
- `Price Status:` line shows age and freshness indicator
- `last_price_update` timestamp displayed
- SOL Balance column shows vault liquidity
- Color-coded status (✓, ~, ⚠)

### 2. New: `get_price_live_with_balances.py`
**What it does:**
- Queries database for stored prices
- Shows when prices were last updated
- Explains how to fetch LIVE vault balances from RPC
- Shows comparison between stored and live prices

**Usage:**
```bash
python get_price_live_with_balances.py <TOKEN_MINT>
```

**Features:**
- Shows database prices with freshness
- Shows SOL balance (liquidity) from stored data
- Explains vault account structure
- Ready for live balance fetching (when vault addresses available)

### 3. New: `PRICE_FRESHNESS_GUIDE.md`
Complete guide covering:
- How automatic updates work
- How to verify freshness
- Timestamp interpretation
- Update frequency rationale
- Troubleshooting

---

## Key Implementation Details

### From main.py - get_pools_needing_update()

```python
def get_pools_needing_update(self) -> List[Dict]:
    """Get pools that need price updates based on age and last update time"""
    # Get all pools with their timestamps
    cursor.execute('''
        SELECT amm_id, base_mint, first_seen, last_price_update, ...
        FROM pools
        WHERE base_mint IS NOT NULL
        ORDER BY first_seen DESC
    ''')

    results = []
    for row in cursor.fetchall():
        amm_id, base_mint, first_seen, last_update, ... = row

        # Calculate age
        first_seen_dt = datetime.fromisoformat(first_seen)
        age_seconds = (now - first_seen_dt).total_seconds()

        # Determine update interval based on age
        if age_seconds < 300:          # 0-5 minutes: update every 30 seconds
            update_interval = 30
        elif age_seconds < 1800:       # 5-30 minutes: update every 2 minutes
            update_interval = 120
        else:                          # 30+ minutes: update every 5 minutes
            update_interval = 300

        # Check if needs update
        if last_update is None:
            needs_update = True
        else:
            last_update_dt = datetime.fromisoformat(last_update)
            seconds_since_update = (now - last_update_dt).total_seconds()
            needs_update = seconds_since_update >= update_interval

        if needs_update:
            results.append({
                'amm_id': amm_id,
                'base_mint': base_mint,
                'age_seconds': age_seconds,
                'signature': signature
            })
    return results
```

### From main.py - update_pool_prices()

```python
def update_pool_prices(self):
    """Background thread that updates pool prices on a sliding scale"""
    while self.is_running:
        # Get pools needing updates
        pools_to_update = self.db.get_pools_needing_update()

        for pool_info in pools_to_update:
            # Fetch new price from blockchain
            price_result = self.fetch_pool_price(amm_id, base_mint, signature, dex)

            if price_result:
                # Update database with new price
                self.db.update_pool_supply_and_price(amm_id, total_supply, current_price)

            # Also fetch from DexScreener for comparison
            dex_data = self.get_dexscreener_price(base_mint)
            if dex_data:
                self.db.update_dexscreener_price(amm_id, dex_data['priceUsd'], dex_data.get('priceNative'))

        # Wait 10 seconds before checking again
        time.sleep(10)
```

---

## What's Stored in the Database

### Columns Used for Price Updates

| Column | Purpose | Updated By |
|--------|---------|-----------|
| `first_seen` | Pool detection time | WebSocket listener |
| `last_price_update` | Last time price was refreshed | Price updater |
| `current_price` | SOL per token (or inverse) | Price updater |
| `dexscreener_price_usd` | USD price from external API | Price updater |
| `dexscreener_price_native` | SOL price from external API | Price updater |
| `total_supply` | Token supply | Price updater |
| `market_cap` | Calculated value | Price updater |
| `liquidity` | SOL vault balance | Price updater |
| `last_dexscreener_update` | When DexScreener price was updated | Price updater |

---

## Data Flow Summary

```
1. python main.py starts
   ↓
2. WebSocket listens for pool creation events
   ↓
3. Pool detected → Added to database
   first_seen = now
   last_price_update = NULL
   ↓
4. Background price update thread runs every 10 seconds
   ├─ Finds pools needing update
   ├─ Fetches current prices from blockchain
   ├─ Fetches from DexScreener API
   ├─ Updates database
   └─ Records timestamp in last_price_update
   ↓
5. Users query database
   ├─ Get latest prices
   ├─ See last_price_update to know freshness
   └─ Can calculate age from timestamp
```

---

## Summary Answer to Original Question

### "Do the values in the database get updated?"
**Yes.** Every 30 seconds to 5 minutes (depending on pool age), the background thread fetches new prices and updates the database.

### "How do we know the price is up to date?"
1. **Check the timestamp**: `last_price_update` shows exactly when price was last refreshed
2. **Use the status indicator**: Run `get_price_from_pools.py` to see "Updated 30s ago ✓"
3. **Monitor the logs**: Watch `[PRICE UPDATER]` lines in `python main.py` output
4. **Know the schedule**:
   - Fresh pools (0-5 min): Updated every 30 seconds
   - Medium pools (5-30 min): Updated every 2 minutes
   - Old pools (30+ min): Updated every 5 minutes

---

## Testing

### Test the Enhanced Script
```bash
# Show a specific token with freshness info
python get_price_from_pools.py 47bXryb6KGkF4kTGmveUAzFfigHSSzRkZi3ibtjhUbJY

# Show all available PumpSwap tokens
python get_price_from_pools.py

# Monitor price updates in real-time
python main.py
# Watch for [PRICE UPDATER] lines
```

### Verify Updates Are Happening
```bash
# Terminal 1: Start the monitor
python main.py

# Terminal 2: Check prices
sleep 5 && python get_price_from_pools.py
sleep 35 && python get_price_from_pools.py
# Compare timestamps - should be different!
```

---

## Next Steps

1. **Start the monitor** to begin automatic price updates:
   ```bash
   python main.py
   ```

2. **Check a token** to see freshness status:
   ```bash
   python get_price_from_pools.py <TOKEN_MINT>
   ```

3. **Verify updates** by checking timestamps:
   - Wait 30-60 seconds
   - Run the script again
   - Compare `Price Status:` timestamps

4. **For live balances** (advanced):
   ```bash
   python get_price_live_with_balances.py <TOKEN_MINT>
   ```

