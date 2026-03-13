# Token Price Service — Quick Start Guide

**Status**: ✅ Ready to Use  
**Date**: March 12, 2026

---

## 30-Second Overview

The Token Price Service provides reliable, cached token prices with fallback sources.

**Three ways to get prices:**

1. **Single Token**: `GET /api/price/{mint}`
2. **Multiple Tokens**: `POST /api/price/batch` (up to 100)
3. **History**: `GET /api/price/{mint}/history?hours=24`

---

## Starting Point

The service is already integrated into the FLEX dashboard.

### Server is Running
```bash
# Server runs on http://localhost:5002
python3 -m src.core.main
```

### API is Ready
```bash
# All price endpoints available immediately
curl http://localhost:5002/api/price/health
```

---

## Basic Usage

### Get Current Price

```bash
# Get price for USDC token
curl "http://localhost:5002/api/price/EPjFWaLb3odcccccccccccccccccccccccccccccccccc"
```

Response:
```json
{
  "mint": "EPjFWaLb3odcccccccccccccccccccccccccccccccccc",
  "price_usd": 0.9995,
  "price_sol": 0.00555,
  "liquidity_usd": 18200000,
  "volume_24h": 450000000,
  "market_cap": 29000000000,
  "source": "dexscreener",
  "freshness": "live",
  "is_stale": false
}
```

### Get Multiple Prices

```bash
curl -X POST http://localhost:5002/api/price/batch \
  -H "Content-Type: application/json" \
  -d '{
    "mints": [
      "EPjFWaLb3odcccccccccccccccccccccccccccccccccc",
      "So11111111111111111111111111111111111111112",
      "TokenkegQfeZyiNwAJsyFbPVwwQW8Spritualityy"
    ],
    "cache_type": "hot"
  }'
```

---

## Python Usage

### Simple Example

```python
from src.core.price_service import get_price_service

# Get service instance
service = get_price_service()

# Get single price
price = service.get_token_price_sync('EPjFWaLb3odcccccccccccccccccccccccccccccccccc')
print(f"Price: ${price.price_usd}")
print(f"Source: {price.source}")
print(f"Live: {not price.is_stale}")
```

### Batch Prices

```python
mints = [
    'EPjFWaLb3odcccccccccccccccccccccccccccccccccc',
    'So11111111111111111111111111111111111111112'
]

prices = service.get_token_prices_sync(mints)

for mint, price in prices.items():
    print(f"{mint[:8]}... → ${price.price_usd}")
```

### Price History

```python
# Get last 24 hours of prices
history = service.get_price_history('EPjFWaLb3odcccccccccccccccccccccccccccccccccc', hours=24)

for snapshot in history[-10:]:  # Last 10 snapshots
    print(f"${snapshot['price_usd']} ({snapshot['captured_at']})")
```

---

## Cache Types

The service supports three cache durations:

| Type | TTL | Use |
|------|-----|-----|
| `hot` | 10s | Dashboard (real-time) |
| `org` | 30s | Organization pages |
| `history` | 5m | Charts, historical data |

**Example:**
```bash
# Use longer cache for org page (don't refresh as often)
curl "http://localhost:5002/api/price/EPjFWaLb3odcccccccccccccccccccccccccccccccccc?cache_type=org"
```

---

## Dashboard Integration Example

### Display Token Price Card

```html
<div id="token-price"></div>

<script>
async function displayTokenPrice(mint) {
  const response = await fetch(`/api/price/${mint}`);
  const price = await response.json();
  
  document.getElementById('token-price').innerHTML = `
    <div class="price-card">
      <div class="price-main">
        <span class="label">Price</span>
        <span class="value">$${price.price_usd.toFixed(8)}</span>
      </div>
      <div class="price-metrics">
        <span>Liquidity: $${(price.liquidity_usd/1000).toFixed(1)}k</span>
        <span>24h Vol: $${(price.volume_24h/1000).toFixed(1)}k</span>
      </div>
      <div class="price-footer">
        <span class="source">${price.source}</span>
        <span class="freshness ${price.is_stale ? 'stale' : 'live'}">
          ${price.is_stale ? '⚠️ Stale' : '✅ Live'}
        </span>
      </div>
    </div>
  `;
}

displayTokenPrice('EPjFWaLb3odcccccccccccccccccccccccccccccccccc');
</script>
```

### Display Price Chart

```html
<canvas id="priceChart"></canvas>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
async function displayPriceChart(mint, hours = 24) {
  const response = await fetch(`/api/price/${mint}/history?hours=${hours}`);
  const data = await response.json();
  
  const timestamps = data.snapshots.map(s => new Date(s.captured_at * 1000));
  const prices = data.snapshots.map(s => s.price_usd);
  
  new Chart(document.getElementById('priceChart'), {
    type: 'line',
    data: {
      labels: timestamps,
      datasets: [{
        label: 'Price (USD)',
        data: prices,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        tension: 0.4,
        fill: true
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true }
      }
    }
  });
}

displayPriceChart('EPjFWaLb3odcccccccccccccccccccccccccccccccccc', 24);
</script>
```

---

## Health Monitoring

### Check Service Status

```bash
curl http://localhost:5002/api/price/health
```

Response:
```json
{
  "status": "healthy",
  "cache_size": 23,
  "timestamp": 1710086400
}
```

---

## Common Patterns

### Refresh Price Every 10 Seconds

```javascript
setInterval(async () => {
  const response = await fetch(`/api/price/${mint}?cache_type=hot`);
  const price = await response.json();
  updatePriceDisplay(price);
}, 10000);
```

### Load Prices for Multiple Tokens

```javascript
async function loadTokenPrices(mints) {
  const response = await fetch('/api/price/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mints, cache_type: 'hot' })
  });
  return await response.json();
}

const prices = await loadTokenPrices(['mint1', 'mint2', 'mint3']);
prices.forEach((mint, price) => {
  console.log(`${mint}: $${price.price_usd}`);
});
```

### Handle Stale Prices

```javascript
async function getPriceWithFallback(mint) {
  const response = await fetch(`/api/price/${mint}`);
  const price = await response.json();
  
  if (price.is_stale) {
    console.warn(`⚠️ Price is stale (${price.source})`);
  }
  
  if (price.source === 'unavailable') {
    console.error(`❌ No price available`);
    return null;
  }
  
  return price;
}
```

---

## Endpoints Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/price/<mint>` | Get current price |
| POST | `/api/price/batch` | Get multiple prices |
| GET | `/api/price/<mint>/history` | Get price history |
| GET | `/api/price/health` | Health check |

---

## Error Handling

### Network Error
If API is unreachable, service falls back to cached/stale prices.

### Timeout
If price fetch takes >5 seconds, service tries next source.

### No Price Available
If all sources fail, returns `source: 'unavailable'` with `is_stale: true`.

---

## Performance Tips

1. **Use Batch Endpoint**: Fetch 10+ prices at once (faster)
2. **Appropriate TTLs**: Use longer TTLs for less critical data
3. **Cache Strategically**: Check local cache before requesting
4. **Monitor Health**: Keep eye on service with health endpoint

---

## Troubleshooting

### No Prices Returned
- Check if Dexscreener/Jupiter APIs are up
- Verify mint address is correct
- Try `/api/price/health` to confirm service is running

### All Prices Stale
- Service is falling back to cached data
- External APIs may be down
- Check API status pages (Dexscreener, Jupiter)

### Performance Issues
- Check database size (many old snapshots)
- Clear old snapshots: `service.clear_old_snapshots(days=30)`
- Reduce request frequency (use longer TTLs)

---

## Next Steps

1. **Add to Dashboard**: Integrate price cards into organization pages
2. **Add Charts**: Display price history with sparklines
3. **Real-time Updates**: WebSocket prices for hot tokens
4. **Additional Sources**: Add CoinGecko, MagicEden, etc.
5. **SOL Price**: Fetch SOL/USD dynamically instead of assuming $180

---

**Ready to use!** Start by getting a price:

```bash
curl http://localhost:5002/api/price/health
```

Then integrate into your dashboard.

---

**Date**: March 12, 2026  
**Status**: ✅ PRODUCTION READY

