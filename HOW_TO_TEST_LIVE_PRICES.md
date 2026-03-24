# How to Test Live Price Updates

**Status:** ✅ Ready to test
**Server Port:** 5002
**Test URL:** http://localhost:5002/test-prices

---

## Quick Start (2 minutes)

### Step 1: Make sure Flask is running
```bash
# Check if port 5002 is listening
lsof -i :5002

# If not running, start Flask
python -u src/core/main.py
```

### Step 2: Open test dashboard
```
http://localhost:5002/test-prices
```

### Step 3: Click "▶ Start Test"

You should see:
- **Connection Status** → Changes to "CONNECTED" (green)
- **Events Received** → Count increments (1, 2, 3, ...)
- **Price Cards** → Token data appears with:
  - Token mint address (first 8 chars)
  - Current price in USD
  - Price source (pool, dexscreener, etc.)
  - Market cap

---

## What You're Seeing

### Real-Time Data Flow
```
Server (Price Worker) broadcasts every ~10 seconds
  ↓
Browser receives via EventSource
  ↓
Test page updates in real-time
  ↓
Watch prices change as pools update on-chain
```

### Price Card Colors
- **Green border** = Price went UP since last broadcast
- **Red border** = Price went DOWN since last broadcast
- **Gray border** = First time seeing this token

---

## Test Dashboard Features

### Status Panel (Top)
- **Connection Status:** Shows if connected to price stream
- **Events Received:** Total number of price updates
- **Last Event:** When the last price update arrived
- **Unique Tokens:** How many different tokens have prices

### Statistics
- **Price Updates:** Total events received
- **Avg Event Time:** How fast data is processed
- **Updates/Min:** Frequency of price updates
- **Connection Time:** How long you've been connected

### Price Cards Grid
- Shows 12 most recent token updates
- Sorted by most recent first
- Color-coded by price direction

### Live Event Log
- Shows every price update as it arrives
- Formatted as: `[timestamp] [PRICE_UPDATE #N] mint... → $price (source)`
- Auto-scrolls to newest entries

### Controls
- **▶ Start Test:** Begin receiving price updates
- **⏹ Stop Test:** Stop listening for updates
- **🗑 Clear Logs:** Clear the event log
- **💾 Export Data:** Download test results as JSON

---

## Troubleshooting

### Problem: "DISCONNECTED" Status
**Cause:** EventSource can't connect to `/api/price-stream`

**Solution:**
1. Check Flask is running on port 5002
2. Check listener is running (should see logs in listener.log)
3. Check console for CORS errors

### Problem: Events not arriving
**Cause:** No browsers connected to receive broadcasts

**Solution:**
1. Keep test page open and test page running
2. Check listener logs: `tail -f listener.log | grep BROADCAST`
3. Verify price worker is running: `tail -f listener.log | grep PRICE_WORKER`

### Problem: Page loads but no data
**Cause:** Server might not be serving test page correctly

**Solution:**
1. Check file exists: `ls -la TEST_LIVE_PRICES.html`
2. Check Flask logs for errors
3. Try refreshing page (F5)

---

## Verifying Server-Side

### Check Price Worker is Running
```bash
tail -f listener.log | grep "PRICE_WORKER"
```

Expected output every ~10 seconds:
```
[PRICE_WORKER] CYCLE LOOP ENTERED
[PRICE_WORKER] cycle at 1774340238.398477
[PRICE_DEBUG] Built pool_map with 53 pool entries
[PRICE_WORKER] ✅ Bootstrapped 51 mints
```

### Check Broadcasting is Active
```bash
tail -f listener.log | grep "BROADCAST"
```

Expected output when test page is open:
```
[BROADCAST_DEBUG] Have 51 prices, 1 subscribers
[BROADCAST_DEBUG] Broadcasting 6MxLhwC7...
```

### Check EventSource Endpoint
```bash
curl -N http://localhost:5002/api/price-stream
```

Should show JSON events like:
```json
{"type": "price_update", "mint": "6MxLhwC7...", "price_usd": 0.00012345, "source": "pool", ...}
```

---

## What Data Is Being Tested

### Token Information Shown
- **Mint:** Token contract address (first 8 chars shown)
- **Price USD:** Current price in US dollars
- **Source:** Where price came from (pool, dexscreener, etc.)
- **Market Cap:** Token's market capitalization

### Price Sources
- **pool:** On-chain price from WebSocket pool state
- **dexscreener:** DexScreener API fallback
- **cached:** Price from cache (recent fetch)

### Update Frequency
- Price worker computes every ~10 seconds
- Each token gets 1 broadcast per cycle
- If 51 tokens × 10s cycle = ~5 updates/minute per token

---

## Interpreting Results

### Good Results
```
✅ Connection Status: CONNECTED (green)
✅ Events Received: 50+ (increases every 10s)
✅ Unique Tokens: 40-51 (matches listener output)
✅ Price Cards: Show variety of tokens with prices
✅ Log shows [PRICE_UPDATE] events arriving constantly
```

### This Proves
- WebSocket pool state is flowing ✅
- Price worker is computing ✅
- Broadcasting is working ✅
- SSE endpoint is delivering ✅

---

## Advanced Testing

### Export Test Results
1. Run test for a few minutes
2. Click "💾 Export Data"
3. Check JSON file for:
   - Event count
   - Token diversity
   - Timestamps
   - Price sources

### Monitor Performance
- Watch "Avg Event Time" (should stay <10ms)
- Watch "Updates/Min" (should be ~5 per token)
- Check "Connection Time" (should stay stable)

### Stress Test
1. Keep page open for 1+ hour
2. Watch for connection drops
3. Verify no memory leaks
4. Check consistency of price updates

---

## Success Criteria

| Metric | Expected | Status |
|--------|----------|--------|
| Connection Status | CONNECTED | ✅ |
| Events after 10s | 1+ | ✅ |
| Unique tokens | 40-51 | ✅ |
| Price sources | pool, dexscreener | ✅ |
| Update frequency | ~5/min per token | ✅ |
| Log entries | [PRICE_UPDATE] | ✅ |

---

## Next Steps

If test works:
1. ✅ Live prices verified operational
2. ✅ Real-time updates flowing
3. ✅ Browser integration complete

If test has issues:
1. Check listener logs for errors
2. Verify Flask is running on 5002
3. Check network tab for EventSource status
4. Review console for JavaScript errors

---

## Files Related

- **TEST_LIVE_PRICES.html** - Test dashboard
- **src/core/main.py** - Flask server with /test-prices route
- **src/core/price_worker.py** - Broadcasts prices
- **src/core/price_stream.py** - SSE pub/sub system
- **listener.log** - Server logs

---

**Ready to test? Open http://localhost:5002/test-prices and click "Start Test"!**
