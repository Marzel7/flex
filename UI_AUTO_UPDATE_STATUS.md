# UI Auto-Update Status - March 24, 2026

**Question:** Does the UI auto-refresh/auto-update with new prices?

**Answer:** ❌ **No, the UI does NOT auto-refresh**

---

## Current State

### Frontend (HTML/JavaScript)

**File:** `templates/flex_dashboard_v2.html`

**Behavior:**
- Uses `fetch()` API to load data on initial page load
- API calls are manual/event-based (user clicks buttons)
- **No `setInterval()` or auto-refresh logic**
- **No WebSocket connections to browser**
- Prices are fetched once on page load, then static until refresh

### API Backend (Flask)

**File:** `src/core/main.py`

**Status:**
- ✅ Price API endpoints exist (`/api/price/...`)
- ✅ API returns fresh on-chain prices from database
- ✅ Database is continuously updated by price worker
- ❌ UI does not poll these endpoints

### Data Flow

```
Current (Manual Refresh):
┌─────────────────┐
│ Browser loads   │
│ flex_dashboard  │
└────────┬────────┘
         │
         ↓
    [Single fetch from /api/... endpoints]
         │
         ↓
┌─────────────────┐
│ Display prices  │
│ on page         │ ← Static until user refreshes F5
└─────────────────┘

Expected (Auto-Refresh - Not Implemented):
┌─────────────────┐
│ Browser loads   │
│ flex_dashboard  │
└────────┬────────┘
         │
         ↓
    [setInterval every N seconds]
         │
         ↓
    [Fetch from /api/... endpoints]
         │
         ↓
    [Update DOM with new data]
         │
         ↓
┌─────────────────┐
│ Display prices  │
│ updating live   │ ← Updated every N seconds
└─────────────────┘
```

---

## What You Need to Do for Auto-Update

### Option 1: Simple JavaScript Auto-Refresh (Easiest)

Add to `flex_dashboard_v2.html` in the script section:

```javascript
// Auto-refresh prices every 10 seconds
const AUTO_REFRESH_INTERVAL = 10 * 1000;  // 10 seconds

setInterval(async () => {
    try {
        // Reload price data from API
        const response = await fetch(`${API_BASE}/price`);
        const data = await response.json();

        // Update DOM with new data
        updatePricesDisplay(data);

        console.log('[UI] Prices refreshed at', new Date().toLocaleTimeString());
    } catch (error) {
        console.error('[UI] Auto-refresh error:', error);
    }
}, AUTO_REFRESH_INTERVAL);
```

**Pros:**
- Simple, no dependencies
- Works with existing API
- Easy to configure refresh rate

**Cons:**
- Uses polling (less efficient than WebSocket)
- Slight delay between price change and display
- Server traffic increases

---

### Option 2: WebSocket Real-Time Updates (Better)

Add WebSocket endpoint to Flask:

```python
# src/core/main.py
from flask_socketio import SocketIO, emit

socketio = SocketIO(app)

@socketio.on('subscribe_prices')
def subscribe_prices(data):
    """Client subscribes to live price updates"""
    mints = data.get('mints', [])
    emit('subscribed', {'mints': mints})

# In price_worker.py, emit price updates:
socketio.emit('price_update', {
    'mint': mint,
    'price_usd': price,
    'updated_at': time.time()
})
```

**Pros:**
- Real-time (instant updates)
- Lower latency
- More efficient (push vs pull)

**Cons:**
- More complex to implement
- Requires Flask-SocketIO
- More server resources

---

### Option 3: Server-Sent Events (SSE) (Middle Ground)

```python
@app.route('/api/price/stream')
def price_stream():
    """Stream price updates using Server-Sent Events"""
    def generate():
        while True:
            price = get_latest_price()
            yield f"data: {json.dumps(price)}\n\n"
            time.sleep(1)

    return Response(generate(), mimetype='text/event-stream')
```

**Pros:**
- Real-time updates
- Simpler than WebSocket
- Lower latency than polling

**Cons:**
- Still server push (not all clients support it)
- Moderate complexity

---

## Current Production State

### What's Working

✅ **Backend/Database:**
- Price worker continuously updates prices every WebSocket message
- 25,882 snapshots in last 60 minutes
- On-chain prices being computed and stored

✅ **API:**
- Endpoints ready: `/api/price/...`
- Fresh prices available on request
- Low latency (<100ms)

❌ **Frontend:**
- No auto-refresh mechanism
- User must hit F5 to see new prices
- Shows stale prices until manual refresh

---

## Recommendation

### For Quick Win (Recommended)

Add **Option 1** (JavaScript setInterval) to `templates/flex_dashboard_v2.html`:

```javascript
// In the main script block, after page load:
setInterval(() => {
    fetch('/api/price/latest')
        .then(r => r.json())
        .then(data => {
            // Update each token price on page
            Object.entries(data).forEach(([mint, price]) => {
                const elem = document.querySelector(`[data-mint="${mint}"] .price`);
                if (elem) elem.textContent = price.toFixed(8);
            });
        })
        .catch(e => console.error('Price refresh failed:', e));
}, 5000);  // Every 5 seconds
```

**Time to implement:** 5 minutes
**Impact:** Prices update automatically every 5 seconds

### For Production Quality

Implement **Option 2** (WebSocket) or **Option 3** (SSE):
- Real-time updates
- Better performance
- More scalable
- Industry standard

**Time to implement:** 30-60 minutes

---

## Summary

| Aspect | Current | With Auto-Refresh |
|--------|---------|-------------------|
| **Price freshness** | Manual F5 refresh | Live every N seconds |
| **User experience** | Static display | Dynamic updates |
| **Data source** | Same (on-chain pool) | Same (on-chain pool) |
| **Complexity** | None added | Low (5 min) to High (1 hour) |
| **Server load** | 0 extra requests | ~12 per minute (5s interval) |

**Next Step:** Choose implementation option and integrate into dashboard template.

