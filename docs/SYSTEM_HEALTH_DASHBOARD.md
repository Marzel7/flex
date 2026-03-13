# System Health Dashboard

**Date:** March 13, 2026
**Status:** ✅ LIVE
**Route:** `GET /system-health`

---

## Overview

The System Health Dashboard provides real-time visibility into the Flex price service, WebSocket pool subscriptions, and multi-pool aggregation. It replaces the need for manual `curl` commands to check system health.

---

## Access

**Dashboard URL:** `http://localhost:5002/system-health`

**Navigation:** Sidebar → Operations → System Health

---

## Dashboard Sections

### 1. Overall Service Status (Top)

Shows three live indicators:

- **Service** — `HEALTHY` (green) or error state (red)
- **Worker** — `RUNNING` (green) or `STOPPED` (red)
- **WebSocket** — `CONNECTED` (green) or `DISCONNECTED` (red)

Each has a pulsing status dot that animates when unhealthy.

---

### 2. WebSocket Pool Stats

Real-time monitoring of pool subscriptions:

| Metric | Description |
|--------|-------------|
| **Connected** | WebSocket connection state |
| **Subscriptions** | Total account subscriptions (2 per pool) |
| **Events Received** | Total events since service started |
| **Events Decoded** | Successfully decoded balance updates |
| **Deduplicated** | Duplicate events filtered by slot |
| **Reconnects** | Total reconnect attempts |
| **Status** | `HEALTHY` or `⚠ STALE` (>2 min no events) |
| **Last Event** | Timestamp of most recent update |

**What to watch:**
- ✅ Connected = `true`
- ✅ Events increasing during trading
- ⚠️ Reconnects > 5/hour = potential issues
- 🔴 `STALE` status = system using RPC fallback

---

### 3. Pool Pricing

Metrics for on-chain pool price fetching:

| Metric | Description |
|--------|-------------|
| **Pools Registered** | Count of registered pool accounts |
| **Prices Cached** | Distinct mints with prices in cache |
| **Last Cycle Fetched** | Prices computed in last 10s cycle |
| **Success Rate** | % of price fetch attempts successful |
| **Attempted** | Total pool fetch attempts |
| **Success** | Successful price computations |
| **Failed** | Price computation failures |

**What to watch:**
- ✅ Success rate > 90%
- ✅ Prices cached = pools registered
- ⚠️ Success rate 70-90% = some failures
- 🔴 Success rate < 70% = degraded

---

### 4. Aggregation Status (NEW!)

Multi-pool price aggregation configuration:

| Metric | Description |
|--------|-------------|
| **Multi-Pool** | Feature enabled/disabled |
| **Active Pools** | Count of distinct pool accounts being tracked |
| **Strategy** | Liquidity-Weighted Selection (highest liquidity pool wins) |
| **Price Source** | Annotation format: `pool(N)` where N = pool count |

**What to watch:**
- ✅ `ENABLED` = system supports multiple pools per token
- ✅ Active Pools = subscriptions / 2 (each pool has 2 accounts)
- 📊 When N > 1 in price source, aggregation is working

---

### 5. Worker Status

Price worker thread metrics:

| Metric | Description |
|--------|-------------|
| **Cycles Completed** | Total 10s refresh cycles executed |
| **Errors** | Exception count |
| **Error Rate** | % of cycles with errors |
| **Last Run** | Time since last cycle completed |

**What to watch:**
- ✅ Errors = 0
- ✅ Error rate < 2%
- ✅ Last run < 100ms (worker responsive)
- ⚠️ Error rate 2-5% = occasional issues
- 🔴 Error rate > 5% = systemic problem

---

### 6. Cache Performance

Token price cache hit rates:

| Metric | Description |
|--------|-------------|
| **Cache Size** | Distinct mints with cached prices |
| **Cache Hits** | Successful cache lookups |
| **Hit Rate** | % of price requests served from cache |

**What to watch:**
- ✅ Hit rate > 80%
- ⚠️ Hit rate 50-80% = moderate cache misses
- 🔴 Hit rate < 50% = poor cache effectiveness

---

### 7. Queue Status

Price fetch request queue metrics:

| Metric | Description |
|--------|-------------|
| **Queue Depth** | Pending requests in queue |
| **Processed** | Total requests handled |
| **Failed** | Request failures |
| **Avg Latency** | Average request processing time |

**What to watch:**
- ✅ Queue depth < 100
- ✅ Avg latency < 50ms
- ⚠️ Queue depth 100-500 = backlog building
- 🔴 Queue depth > 1000 = severe congestion

---

### 8. Price Source Health (Table)

Detailed breakdown of all price sources:

| Source | Attempted | Success | Failed | Rate |
|--------|-----------|---------|--------|------|
| **POOL** | Attempts via on-chain pools | Successes | Failures | Success % |
| **DEXSCREENER** | Attempts via DEX API | Successes | Failures | Success % |
| **JUPITER** | Attempts via Jupiter API | Successes | Failures | Success % |
| **BIRDEYE** | Attempts via Birdeye API | Successes | Failures | Success % |
| **STALE_FALLBACK** | Served from stale cache | N/A | N/A | N/A |
| **UNAVAILABLE** | Failed all sources | N/A | N/A | N/A |

**What to watch:**
- ✅ POOL success rate > 80% (primary source)
- ✅ At least one fallback > 80% (Dexscreener, Jupiter, or Birdeye)
- ⚠️ STALE_FALLBACK > 0 = some requests using old data
- 🔴 UNAVAILABLE > 0 = unable to fetch price

---

### 9. Circuit Breaker Status (Grid)

Fault tolerance mechanism for fallback sources:

| Source | Status | Meaning |
|--------|--------|---------|
| **POOL** | ACTIVE | On-chain pool pricing enabled |
| **DEXSCREENER** | ACTIVE/DISABLED | API available or in cooldown |
| **JUPITER** | ACTIVE/DISABLED | API available or in cooldown |
| **BIRDEYE** | ACTIVE/DISABLED | API available or in cooldown |

**Disabled** = Source experienced failures, temporarily disabled with cooldown timer.

**What to watch:**
- ✅ At least 2 sources ACTIVE
- ⚠️ 1 source DISABLED = reduced redundancy
- 🔴 All sources DISABLED = single point of failure

---

### 10. Rolling Window Stats (5-Minute)

Success/failure rates over the last 5 minutes:

| Source | Attempts | Success Rate | Failure Rate |
|--------|----------|--------------|--------------|
| **POOL** | Requests in window | % successful | % failed |
| **DEXSCREENER** | Requests in window | % successful | % failed |
| **JUPITER** | Requests in window | % successful | % failed |
| **BIRDEYE** | Requests in window | % successful | % failed |

**What to watch:**
- ✅ Success rate > 90% across sources
- ⚠️ Success rate 70-90% = temporary degradation
- 🔴 Success rate < 70% = ongoing issues

---

## Features

### Auto-Refresh

Dashboard automatically refreshes every **10 seconds** to show live updates.

### Manual Refresh

Click the **"Refresh"** button to force an immediate update.

**Refresh** button shows loading spinner during fetch.

### Status Indicators

- **Green** — Healthy, all checks pass
- **Yellow** — Warning, needs attention
- **Red** — Critical, immediate action needed

### Progress Bars

Visual representation of percentages (success rate, cache hit rate, error rate).

---

## Troubleshooting

### Problem: "Unable to fetch health data"

**Cause:** `/api/price/health` endpoint not responding
**Fix:**
1. Check service is running: `ps aux | grep python`
2. Check logs: `tail logs/dev_intelligence.log`
3. Restart: `./scripts/restart.sh`

### Problem: WebSocket shows "STALE"

**Cause:** No pool events for >2 minutes
**Fix:**
1. Check Helius API key is valid
2. Verify pools are registered: `curl http://localhost:5002/api/price/health | jq '.pool_stats.pools_registered'`
3. Confirm trading is happening on registered pools
4. Check if network is down

### Problem: Circuit breaker shows "DISABLED"

**Cause:** Source API failed repeatedly, auto-disabled for cooldown period
**Fix:**
1. Wait for cooldown to expire (displayed on dashboard)
2. Or manually reset via logs/monitoring
3. Check external API status

### Problem: Queue depth keeps growing

**Cause:** Worker can't keep up with requests
**Fix:**
1. Check worker errors in "Worker Status" section
2. Monitor queue latency (should be <50ms)
3. Reduce token count if too many tokens registered
4. Restart service if errors accumulate

---

## Integration with Multi-Pool Aggregation

The dashboard displays **Aggregation Status** showing:
- Whether multi-pool feature is enabled
- Count of active pools being tracked
- Aggregation strategy (liquidity-weighted selection)
- Price source format showing pool count

**Example:** If a token price shows `source: "pool(2)"`, it means:
- Two pools are registered for that token
- System computed prices from both pools
- Selected highest-liquidity pool as final price

---

## Monitoring Best Practices

### Daily Check (Morning)

```bash
Visit: http://localhost:5002/system-health

Verify:
✓ All three status indicators are GREEN
✓ WebSocket is CONNECTED
✓ Success rates > 90%
✓ Queue depth < 100
✓ No circuit breakers DISABLED
```

### During High-Volume Trading

```bash
Monitor:
- Events Received (should be increasing)
- Queue Depth (should stay < 500)
- Success Rate (should stay > 80%)
- Latency (should stay < 100ms)

If issues appear, check:
- RPC rate limits (Helius quota)
- Network connectivity
- Pool liquidity (high activity = more events)
```

### Weekly Review

Check rolling window stats and circuit breaker history to identify patterns:
- Any sources disabled frequently?
- Success rates trending down?
- Queue building during certain times?

---

## API Reference

### Underlying Endpoint

All dashboard data comes from: `GET /api/price/health`

```bash
curl -s http://localhost:5002/api/price/health | jq '.'
```

**Response structure:**
```json
{
  "status": "healthy",
  "worker_running": true,
  "cache_size": 29,
  "pool_stats": {
    "pools_registered": 10,
    "pool_prices_cached": 8,
    "pool_prices_fetched_last_cycle": 8,
    "ws": {
      "connected": true,
      "subscriptions": 20,
      "events_received": 1250,
      "events_decoded": 1238,
      "events_deduplicated": 12,
      "reconnects": 0,
      "last_event_at": 1710350000,
      "is_stale": false,
      "multi_pool_enabled": true
    }
  },
  "worker_stats": { ... },
  "rolling_window_stats": { ... }
}
```

---

## Related Documentation

- [Multi-Pool Aggregation](MULTI_POOL_AGGREGATION_COMPLETE.md) — How price aggregation works
- [WebSocket Implementation](WEBSOCKET_IMPLEMENTATION_COMPLETE.md) — WebSocket pool subscriptions
- [Price Service](PRICE_SERVICE_SUMMARY.md) — Overall price fetching system
- [Operational Procedures](WEBSOCKET_OPS_CARD.md) — 30-second on-call runbook

---

## Status

✅ **LIVE & OPERATIONAL**

Dashboard is ready for use in production. All metrics update in real-time from the `/api/price/health` endpoint.

Last Updated: March 13, 2026
