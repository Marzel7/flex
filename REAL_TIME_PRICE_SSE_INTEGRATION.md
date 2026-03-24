# Real-Time Price Streaming via SSE - Implementation Guide

**Status:** ✅ **BACKEND COMPLETE - Ready for Frontend Integration**

---

## What's Implemented

### Backend Components ✅

1. **Price Stream (Pub/Sub)**
   - File: `src/core/price_stream.py`
   - Class: `PriceStream`
   - Handles subscriptions + broadcasting
   - Thread-safe with proper locking

2. **SSE Endpoint**
   - File: `src/core/main.py` (~line 20180)
   - Route: `GET /api/price-stream`
   - Streaming Server-Sent Events
   - Proper headers + error handling

3. **Price Worker Broadcast**
   - File: `src/core/price_worker.py` (~line 1710)
   - Integrates in `_on_price_fetched()`
   - Broadcasts every price update
   - Non-blocking async call

---

## Architecture Flow

```
Price Worker computes price
  ↓
Updates database
  ↓
Calls: price_stream.broadcast(event)
  ↓
All connected browsers receive update
  ↓
Browser DOM updates instantly
  ↓
User sees live prices
```

---

## Frontend Integration (4 Steps)

### Step 1: Add EventSource Connection

Add this to `templates/flex_dashboard_v2.html` in the main JavaScript section:

```javascript
// Connect to real-time price stream
function initPriceStream() {
    const eventSource = new EventSource('/api/price-stream');

    eventSource.onmessage = function(event) {
        try {
            const update = JSON.parse(event.data);

            if (update.type === 'price_update') {
                updateTokenPrice(update);
            }
        } catch (error) {
            console.error('[PRICE_STREAM] Parse error:', error);
        }
    };

    eventSource.onerror = function(error) {
        console.error('[PRICE_STREAM] Connection error:', error);
        // Browser will auto-reconnect
        // eventSource.close() if you want to stop
    };

    // Clean up on page unload
    window.addEventListener('beforeunload', () => {
        eventSource.close();
        console.log('[PRICE_STREAM] Connection closed');
    });

    return eventSource;
}

// Call on page load
document.addEventListener('DOMContentLoaded', () => {
    initPriceStream();
});
```

### Step 2: Update Token Row in DOM

```javascript
function updateTokenPrice(update) {
    const {
        mint,
        price_usd,
        market_cap,
        source,
        updated_at
    } = update;

    // Find the token row (adjust selector based on your HTML)
    const row = document.querySelector(`[data-mint="${mint}"]`);

    if (row) {
        // Update price display
        const priceElem = row.querySelector('.token-price');
        if (priceElem) {
            const oldPrice = parseFloat(priceElem.textContent) || 0;
            const newPrice = parseFloat(price_usd) || 0;

            priceElem.textContent = newPrice.toFixed(8);

            // Visual feedback: highlight if price changed
            if (oldPrice !== newPrice) {
                if (newPrice > oldPrice) {
                    priceElem.classList.add('price-up');
                    // Remove highlight after 1s
                    setTimeout(() => priceElem.classList.remove('price-up'), 1000);
                } else if (newPrice < oldPrice) {
                    priceElem.classList.add('price-down');
                    setTimeout(() => priceElem.classList.remove('price-down'), 1000);
                }
            }
        }

        // Update market cap
        const mcElem = row.querySelector('.market-cap');
        if (mcElem && market_cap) {
            mcElem.textContent = formatMarketCap(market_cap);
        }

        // Update source indicator
        const sourceElem = row.querySelector('.price-source');
        if (sourceElem) {
            sourceElem.textContent = source === 'pool' ? '🔗 Pool' : '📊 DexScreener';
            sourceElem.className = 'price-source ' + source;
        }

        // Update timestamp
        const timeElem = row.querySelector('.updated-at');
        if (timeElem) {
            const date = new Date(updated_at * 1000);
            timeElem.textContent = date.toLocaleTimeString();
        }

        console.log(`[PRICE_STREAM] Updated ${mint.slice(0, 8)}... → $${newPrice.toFixed(8)}`);
    }
}

// Helper: format market cap
function formatMarketCap(mc) {
    if (mc >= 1e9) return `$${(mc / 1e9).toFixed(2)}B`;
    if (mc >= 1e6) return `$${(mc / 1e6).toFixed(2)}M`;
    if (mc >= 1e3) return `$${(mc / 1e3).toFixed(2)}K`;
    return `$${mc.toFixed(0)}`;
}
```

### Step 3: Add CSS for Visual Feedback

```css
/* Price update highlighting */
.token-price {
    transition: background-color 0.3s ease;
}

.price-up {
    background-color: rgba(34, 197, 94, 0.2);  /* Green */
    color: #22c55e;
    font-weight: 600;
}

.price-down {
    background-color: rgba(239, 68, 68, 0.2);  /* Red */
    color: #ef4444;
    font-weight: 600;
}

/* Source indicator */
.price-source {
    font-size: 0.85em;
    padding: 0.25em 0.5em;
    border-radius: 4px;
}

.price-source.pool {
    background-color: rgba(59, 130, 246, 0.1);  /* Blue for on-chain */
    color: #3b82f6;
}

.price-source.dexscreener {
    background-color: rgba(107, 114, 128, 0.1);  /* Gray for fallback */
    color: #6b7280;
}
```

### Step 4: Test the Connection

Add debug logging to your dashboard:

```javascript
// Add a status indicator
function addStreamStatusIndicator() {
    const indicator = document.createElement('div');
    indicator.id = 'stream-status';
    indicator.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        padding: 10px 15px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        background: #4ade80;
        color: white;
        z-index: 9999;
        display: flex;
        align-items: center;
        gap: 8px;
    `;
    indicator.innerHTML = '● Connected to price stream';
    document.body.appendChild(indicator);

    // Update on disconnect
    window.addEventListener('offline', () => {
        indicator.style.background = '#ef4444';
        indicator.innerHTML = '● Connection lost';
    });

    window.addEventListener('online', () => {
        indicator.style.background = '#4ade80';
        indicator.innerHTML = '● Reconnected';
    });
}

// Call on page load
document.addEventListener('DOMContentLoaded', () => {
    initPriceStream();
    addStreamStatusIndicator();
});
```

---

## Event Format

Events received from `/api/price-stream`:

```json
{
    "type": "price_update",
    "mint": "4UPUXWLeyuvm2cKbVVtfnRFmCs19tRFixwoeZnK3pump",
    "price_usd": 0.00003909,
    "price_sol": 0.00000186,
    "market_cap": 39090,
    "liquidity_usd": 15000,
    "source": "pool",
    "updated_at": 1774286512,
    "broadcast_id": 12345
}
```

---

## How It Works

### Connection Lifecycle

```
1. Browser connects:
   const es = new EventSource('/api/price-stream')

2. Server adds queue to subscribers list

3. Price worker emits event:
   await price_stream.broadcast({...})

4. Event sent to all subscribers:
   yield f"data: {json.dumps(event)}\n\n"

5. Browser receives onmessage event

6. JavaScript updates DOM

7. User sees live price instantly
```

### No Polling

- ✅ Browser connects once
- ✅ Server pushes updates
- ✅ Zero request overhead
- ✅ Real-time (no delay)

### Scalability

- Single SSE connection per browser
- Server handles multiple connections
- Efficient queue-based delivery
- Auto-cleanup on disconnect

---

## Testing

### Server-Side

Check if broadcasts are working:

```bash
# Monitor listener logs
tail -f listener.log | grep PRICE_STREAM

# Should see:
# [PRICE_STREAM] New browser client connected
# [PRICE_STREAM] Broadcast #1: 4UPUXWLeyuvm2c... price=$0.00003909 to 1 subscribers
```

### Client-Side

Open browser console and watch:

```javascript
// In browser console:
fetch('/api/price-stream').then(r => r.body.getReader())
    .then(reader => {
        const decoder = new TextDecoder();
        reader.read().then(function process({done, value}) {
            if (!done) {
                console.log(decoder.decode(value));
                return reader.read().then(process);
            }
        });
    });

// Should see events coming through:
// data: {"type":"price_update","mint":"...","price_usd":0.00003909,...}
```

---

## Browser Support

| Browser | Support | Notes |
|---------|---------|-------|
| Chrome | ✅ | Full support |
| Firefox | ✅ | Full support |
| Safari | ✅ | Full support |
| Edge | ✅ | Full support |
| IE11 | ❌ | Not supported (use fallback) |

For IE11 support, add fallback polling:

```javascript
function initPriceStreamWithFallback() {
    // Try SSE first
    if (typeof EventSource !== 'undefined') {
        return initPriceStream();
    }

    // Fallback to polling
    console.log('[PRICE_STREAM] SSE not supported, using polling');
    setInterval(() => {
        fetch('/api/price/latest')
            .then(r => r.json())
            .then(data => {
                Object.entries(data).forEach(([mint, price]) => {
                    updateTokenPrice({
                        mint,
                        price_usd: price.price_usd,
                        source: price.source
                    });
                });
            });
    }, 5000);  // Poll every 5 seconds
}
```

---

## Performance Metrics

### Expected Results

- **Latency:** <100ms from price update to browser
- **Bandwidth:** ~1KB per price update
- **CPU:** Minimal (event-driven, not polling)
- **Scalability:** ~1000 concurrent browsers per server

### Monitoring

Track in listener logs:

```
[PRICE_STREAM] Broadcast #1234: 4UPUXWLeyuvm2c... price=$0.00003909 to 3 subscribers
```

Count subscribers:
- 0 = no browsers connected
- N = N browsers watching prices

---

## Troubleshooting

### No Updates Received

1. Check server is running: `curl http://localhost:5002/api/price-stream`
   - Should return `HTTP 200` with streaming headers
2. Check listener logs: `tail -f listener.log | grep PRICE_STREAM`
3. Verify no errors: `grep ERROR listener.log | tail -10`

### Connection Drops

SSE auto-reconnects. If persistent:
1. Check server logs for exceptions
2. Verify network connectivity
3. Check browser console for CORS issues

### High CPU Usage

If server CPU high:
1. Verify not broadcasting too frequently
2. Check subscriber count: `subscribers` in price_stream.py
3. Monitor queue sizes

---

## Production Checklist

- [ ] SSE endpoint tested with curl
- [ ] Frontend JavaScript integrated
- [ ] CSS for price highlighting added
- [ ] Status indicator visible
- [ ] Console logs clean (no errors)
- [ ] Multiple price updates received live
- [ ] Page refresh/reconnect working
- [ ] Mobile browser tested

---

## Summary

### What You Have Now

✅ **Backend:** Complete
- Price stream pub/sub system
- SSE endpoint ready
- Broadcast on every price update
- Thread-safe, scalable design

🔄 **Frontend:** Ready for integration
- 4 simple steps to add
- No dependencies
- Vanilla JavaScript
- Works on all modern browsers

### Result

Real-time price updates flowing from your on-chain pool pricing engine directly to browser with zero polling. Users see live prices as they update.

---

**Next:** Integrate the 4 frontend steps into your dashboard template.

