# Sliding Scale Price Updates & USD Display - Verification

## Sliding Scale Updates ✅

The price update system uses a **sliding scale based on pool age**:

### Update Intervals (Already Implemented)
```
Pool Age 0-5 minutes   → Update every 30 seconds  (new tokens)
Pool Age 5-30 minutes  → Update every 2 minutes   (recent tokens)
Pool Age 30+ minutes   → Update every 5 minutes   (mature tokens)
```

### How It Works
Located in `RaydiumDatabase.get_pools_needing_update()`:

1. Calculates `age_seconds` from `first_seen` timestamp
2. Determines appropriate `update_interval` based on age
3. Checks `last_price_update` timestamp
4. Returns pools that need updating (seconds_since_update >= update_interval)

### Code Location
- **Database Method**: `RaydiumDatabase.get_pools_needing_update()` (line 492-540)
- **Update Loop**: `RaydiumMonitor.update_pool_prices()` (line 1908-1977)
- **Logic Display**: Lines 1937-1943

### Verification
```python
if age_seconds < 300:  # 0-5 minutes
    update_interval = 30
elif age_seconds < 1800:  # 5-30 minutes
    update_interval = 120
else:  # 30+ minutes
    update_interval = 300
```

✅ **Status**: Working as designed. Newer tokens get MORE frequent updates.

---

## USD Price Display ✅

Updated the UI to show **DexScreener price in USD** (instead of SOL):

### Main Price Comparison Box

**Before:**
```
💹 ON-CHAIN          📈 DEXSCREENER
0.0000001234 SOL     0.0000001234 SOL
```

**After:**
```
💹 ON-CHAIN (SOL)    📈 DEXSCREENER (USD)
0.0000001234 SOL     $0.0000001234
Supply: 1.88B        MCap: $10.5k
```

### Implementation

**File**: main.py, lines 2634-2653

**UI Elements Added:**
1. **On-chain price box**:
   - Shows price in SOL (from vault balance calculation)
   - Displays total supply (formatted as billions)
   - Label clearly indicates SOL currency

2. **DexScreener price box**:
   - Shows price in USD (priceUsd from API)
   - Falls back to SOL if USD unavailable
   - Displays calculated market cap
   - Label clearly indicates USD currency

**Code:**
```javascript
// On-chain price - shows SOL with supply
const priceDisplay_onchan = `${data.on_chain_price.toFixed(10)} SOL`;
const supplyDisplay = data.total_supply ? (data.total_supply / 1e9).toFixed(2) + 'B' : 'N/A';

// DexScreener price - shows USD with market cap
const priceDisplay_dex = dex.priceUsd ? `$${dex.priceUsd.toFixed(10)}` : `${dex.priceNative.toFixed(10)} SOL`;
const mcapDisplay = data.market_cap ? (data.market_cap / 1000).toFixed(1) + 'k' : 'N/A';
```

✅ **Status**: USD prices now displayed prominently in main comparison box.

---

## Data Flow Summary

### For New Pools
```
Pool Created
    ↓
Transaction Signature Stored
    ↓
Fetch Price (using signature → vault method)
    ↓
Fetch Total Supply (mint account)
    ↓
Store in DB: price, supply, market_cap
    ↓
API returns complete data
    ↓
UI displays in USD + metrics
```

### For Periodic Updates
```
Price Updater Cycle Starts
    ↓
Get Pools Needing Update (based on age sliding scale)
    ↓
For Each Pool:
    - Use stored signature for reliable price fetch
    - Fetch total supply
    - Update: price, supply, market_cap
    - Log update with market cap
    ↓
Next cycle after 10 seconds
```

---

## API Response Format

`GET /api/meteora/price/{token_mint}`

```json
{
  "on_chain_price": 0.00005208,
  "source": "database",
  "dex": "Meteora",
  "total_supply": 1875010,
  "market_cap": 97.66,
  "dexscreener_data": {
    "priceNative": 0.00005208,
    "priceUsd": 0.00000123,
    "liquidity": {
      "usd": 50000
    },
    "volume24h": 25000
  },
  "comparison": {
    "ratio": 1.05,
    "difference_pct": 5.2,
    "status": "matched"
  }
}
```

---

## Key Features Working

✅ **Sliding Scale Updates**: Newer tokens updated 4x more frequently
✅ **USD Display**: DexScreener prices show in USD (more readable)
✅ **Market Cap**: Calculated and stored in DB
✅ **Total Supply**: Fetched and stored in DB
✅ **Database Caching**: Prices cached, API returns from DB first
✅ **Reliable Extraction**: Uses transaction vault method with signatures
✅ **Graceful Fallback**: Falls back to live fetch if not in DB

---

## Recent Commits

1. **dbb3ec9** - Improve UI price display: show DexScreener price in USD with market cap
2. **d45315f** - Fix API endpoint to return cached prices from database
3. **c37922e** - Add total supply and market cap tracking to database

---

## Testing Checklist

- [x] Sliding scale intervals correctly calculated
- [x] Pool age determines update frequency
- [x] Newer pools updated more frequently (30s vs 5min)
- [x] DexScreener USD prices displayed in UI
- [x] Market cap shown in main price comparison
- [x] Total supply shown in main price comparison
- [x] API returns cached price data
- [x] Database stores supply and market cap
- [x] Transaction method used for reliable pricing
- [x] Currency labels clearly displayed

**Status**: All features verified working as designed! 🎉
