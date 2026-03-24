# Quick Start: Live Price Updates

**Backend:** ✅ Ready
**Frontend:** Add 4 code blocks
**Time:** 5 minutes

---

## 1. Verify Backend Works

```bash
# Check SSE endpoint responds
curl -I http://localhost:5002/api/price-stream

# Expected: HTTP/1.1 200 OK + text/event-stream header
```

---

## 2. Add JavaScript to Dashboard

Edit: `templates/flex_dashboard_v2.html`

Find the main script tag, add:

```javascript
// ============================================================
// REAL-TIME PRICE STREAMING
// ============================================================

function initPriceStream() {
    const es = new EventSource('/api/price-stream');

    es.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'price_update') {
            // Find row with mint
            const row = document.querySelector(`[data-mint="${data.mint}"]`);
            if (row) {
                // Update price
                const priceCell = row.querySelector('[class*="price"]');
                if (priceCell) {
                    priceCell.textContent = data.price_usd.toFixed(8);
                }

                // Flash green/red
                row.style.backgroundColor = data.price_usd > 0 ? '#10b98120' : 'transparent';
                setTimeout(() => { row.style.backgroundColor = 'transparent'; }, 500);
            }
        }
    };

    es.onerror = () => console.log('Price stream reconnecting...');

    console.log('✅ Connected to live price stream');
}

// Start on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPriceStream);
} else {
    initPriceStream();
}
```

---

## 3. Test

1. Open browser: `http://localhost:5002`
2. Open DevTools (`F12`)
3. Watch Console for: `✅ Connected to live price stream`
4. Watch prices update without refresh

---

## 4. Done!

Prices now update live as your pool pricing engine computes them.

---

## Event Format

```json
{
  "type": "price_update",
  "mint": "...",
  "price_usd": 0.00003909,
  "market_cap": 39090,
  "source": "pool",
  "updated_at": 1774286512
}
```

---

## How It Works

```
Pool balance changes
    ↓
Price computed
    ↓
Broadcast to all browsers
    ↓
JavaScript receives event
    ↓
DOM updates
    ↓
User sees live price
```

---

## Files Modified

- ✅ `src/core/price_stream.py` (new)
- ✅ `src/core/main.py` (SSE endpoint added)
- ✅ `src/core/price_worker.py` (broadcast call added)
- 📝 `templates/flex_dashboard_v2.html` (JavaScript added by you)

---

## Full Documentation

See:
- `REAL_TIME_PRICE_SSE_INTEGRATION.md` - Complete integration guide
- `SSE_DEPLOYMENT_CHECKLIST.md` - Testing & troubleshooting

