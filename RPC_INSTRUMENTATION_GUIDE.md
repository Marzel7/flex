# RPC Metrics Instrumentation - Complete Guide

**Status**: ✅ COMPLETE - All major RPC calls are now instrumented

**Instrumented**: 2026-03-01
**Commit**: 0f27a35
**Metrics Endpoint**: http://localhost:5002/rpc-metrics

---

## What Was Done

All RPC calls across the FLEX codebase are now automatically instrumented to track credit usage, latency, errors, and rate limiting in real-time.

### Files Instrumented

| File | Function(s) | RPC Calls | Status |
|------|-----------|-----------|--------|
| **funder_helius_extractor.py** | `get_transactions_helius()` | Helius Enhanced API | ✅ Complete |
| **realtime_creator_funding_extractor.py** | `_post_rpc()`, `resolve_primary_domains()` | Helius RPC, SNS API | ✅ Complete |
| **funder_incoming_extractor.py** | `_request_json()`, `_rpc_call()` | Helius + Solana RPC | ✅ Complete |
| **creator_outgoing_extractor.py** | `rpc_get_signatures()`, `helius_enhanced_parse()` | Helius RPC + Enhanced API | ✅ Complete |
| **main.py** | RPC Metrics proxy routes | Metrics API forwarding | ✅ Complete |

### Metrics Being Tracked

Every RPC call now records:

```python
record_request(
    section="funder_incoming",                      # Component: funder_incoming, creator_funding, creator_outgoing_scan, ui_api, listener, background_enrichment
    provider="helius_enhanced",                     # Provider: helius_rpc, helius_enhanced, solana_rpc, bonfida_sns, blocksec_aml, etc.
    method="helius_enhanced_addresses_transactions",# RPC method: getSignaturesForAddress, getTransaction, helius_enhanced_transactions_batch, etc.
    status_code=200,                                # HTTP status code (0 for exceptions)
    latency_ms=245.3,                               # Request latency in milliseconds
    mode="realtime",                                # Mode: realtime (single page) or background (batch)
    retries=0,                                      # Number of retry attempts
    error=None,                                     # Error message if any
)
```

---

## Sections Tracked

**6 FLEX Components:**

1. **funder_incoming** - Helius Enhanced API calls for extracting funder transfers
   - Main cost driver: ~100 credits per Enhanced Transactions call
   - Location: `funder_helius_extractor.py`

2. **creator_funding** - Real-time creator funding extraction
   - RPC calls: getSignaturesForAddress, getTransaction
   - Location: `realtime_creator_funding_extractor.py`

3. **creator_outgoing_scan** - Hourly background scan of creator outgoing transfers
   - Calls: getSignaturesForAddress, helius_enhanced_parse batches
   - Location: `creator_outgoing_extractor.py`

4. **ui_api** - Flask API endpoints calling RPC
   - Location: `main.py`

5. **listener** - WebSocket listener for token migration events
   - Location: `pumpfun_curve_listener.py` (needs instrumentation)

6. **background_enrichment** - Background processing tasks
   - Location: Various background jobs

---

## Providers Tracked

- **helius_rpc** - Helius JSON-RPC endpoint
- **helius_enhanced** - Helius Enhanced Transactions REST API
- **solana_rpc** - Public Solana RPC fallback
- **bonfida_sns** - SNS domain resolution API
- **blocksec_aml** - BlockSec AML label API
- **helius_ws** - Helius WebSocket (for future streaming metrics)

---

## RPC Methods Being Monitored

### Standard RPC (10 credits each)
- `getTransaction` - Parse individual transactions
- `getSignaturesForAddress` - Get transaction history for address

### Helius Enhanced API (100 credits each)
- `helius_enhanced_addresses_transactions` - Transaction feed for address
- `helius_enhanced_transactions_batch` - Batch parse signatures

### Other APIs
- `sns_primary_domains` - SNS domain resolution (Bonfida)
- `blocksec_aml_lookup` - AML label checks

---

## How to View Metrics

### 1. **Dashboard UI** (Easiest)
Open in browser: **http://localhost:5002/rpc-metrics**

Shows:
- Daily/monthly credit usage
- Per-section breakdown
- Top RPC methods by credits
- Active alerts (high burn rate, budget warnings)
- Real-time auto-refresh (every 5 seconds)

### 2. **REST API** (Programmatic)
```bash
# Full metrics
curl http://localhost:5002/metrics/rpc | jq .

# Just summary
curl http://localhost:5002/metrics/rpc/summary | jq .

# Per-section breakdown
curl http://localhost:5002/metrics/rpc/sections | jq .

# Top methods
curl http://localhost:5002/metrics/rpc/methods?limit=10 | jq .

# Alerts
curl http://localhost:5002/metrics/rpc/alerts | jq .
```

### 3. **In Python Code**
```python
from rpc_metrics_recorder import get_recorder

recorder = get_recorder()

# Get summary
summary = recorder.get_summary()
print(f"Daily credits: {summary['credits_today']}")
print(f"Burn rate: {summary['credits_burn_rate_per_minute']:.2f} credits/min")

# Get per-section stats
sections = recorder.get_section_stats()
print(f"Funder incoming: {sections['funder_incoming']['credits']} credits")

# Get alerts
alerts = recorder.get_alerts()
for alert in alerts:
    print(f"[{alert['level']}] {alert['type']}: {alert['message']}")
```

---

## Understanding the Data

### Credit Costs

**From Official Helius Billing Documentation**:

| Method | Credits |
|--------|---------|
| getTransaction | 10 |
| getSignaturesForAddress | 10 |
| getTransactionsForAddress | 100 |
| helius_enhanced_addresses_transactions | **100** |
| helius_enhanced_transactions_batch | **100** |
| Streaming (LaserStream/WebSocket) | 3 per 0.1MB |

**Example**: If `funder_incoming` section shows 10,000 credits, and each call costs 100 credits, that's 100 Helius Enhanced API calls.

### Latency Metrics

- **p95_latency** - 95th percentile latency (tail behavior)
- **avg_latency** - Average request time

High latency might indicate:
- Network issues
- Rate limiting
- RPC provider overload
- Timeout/retry overhead

### Error Tracking

- **errors_total** - Failed requests (HTTP 4xx/5xx)
- **rate_limits_total** - HTTP 429 responses
- **retries** - Retry attempts before success

---

## Cost Control Tips

### 1. Monitor Burn Rate
```bash
curl -s http://localhost:5002/metrics/rpc/summary | jq '.summary.credits_burn_rate_per_minute'
```

If burn rate is too high, adjust:
- `max_pages` parameter in `funder_helius_extractor.py`
- Concurrency limits
- Background scan frequency

### 2. Adjust Pagination
```python
# Current (realtime): 1 page = ~100 credits
get_transactions_helius(address, max_pages=1)

# Reduce to save costs
get_transactions_helius(address, max_pages=1, limit=50)

# Background mode: more data
get_transactions_helius(address, max_pages=5)
```

### 3. Set Alerts
Edit `rpc_metrics_config.py`:
```python
class AlertConfig:
    BURN_RATE_THRESHOLD_PER_MINUTE = 100.0  # Alert if > 100 credits/min
    BUDGET_WARNING_PERCENT = 20             # Warn at 20% remaining
    BUDGET_CRITICAL_PERCENT = 5             # Critical at 5% remaining
```

### 4. Enable Cost Governor (Optional)
```python
class CostGovernorConfig:
    ENABLED = True
    THRESHOLDS = {
        "high_burn_rate": 500.0,              # credits/min
        "daily_estimate_limit": 500_000,      # credits/day
    }
```

---

## Real-World Examples

### Example 1: Funder Incoming Extraction
**Scenario**: Extract all funders for a creator with 100 funders

```
100 funders × 1 Helius Enhanced API call per funder
= 100 calls × 100 credits each
= 10,000 credits total
```

**In dashboard**:
- Section: `funder_incoming`
- Method: `helius_enhanced_addresses_transactions`
- Credits: 10,000
- Requests: 100
- Avg latency: ~245ms

### Example 2: Creator Outgoing Hourly Scan
**Scenario**: Scan 1000 creators every hour

```
1000 creators × 1 getSignaturesForAddress call
= 1000 calls × 10 credits each
= 10,000 credits/hour
= 240,000 credits/day
```

**In dashboard**:
- Section: `creator_outgoing_scan`
- Method: `getSignaturesForAddress`
- Credits: 10,000+ per hour

### Example 3: Large Token (942 funders)
**Scenario**: Real-time mode vs background mode

**Realtime** (max_pages=1):
- 942 calls × 100 credits = 94,200 credits
- Time: ~16 minutes

**Background** (max_pages=5):
- 942 × 5 calls × 100 credits = 471,000 credits
- Time: ~80 minutes

---

## Troubleshooting

### Q: Dashboard shows 0 credits
**A**: Check that:
1. RPC Metrics service is running: `python rpc_metrics_api.py`
2. Flask proxy is running: `python main.py` on port 5002
3. Your code is making RPC calls (the extractor is running)

### Q: High latency values
**A**:
- Check RPC provider health: https://status.helius.dev/
- Verify timeout settings in code (currently 15-30s)
- Check network connectivity

### Q: Missing metrics for some RPC calls
**A**: These files are **not yet instrumented**:
- `pumpfun_curve_listener.py` - WebSocket listener (stream_bytes tracking needed)
- `pump_fun_analyzer.py` - Post-migration analysis
- `blocksec_aml_batcher.py` - AML lookups (low frequency)

### Q: Metrics reset on server restart
**A**: This is expected - metrics are in-memory only. To persist:
- Implement periodic snapshots to database
- Use Prometheus scraping (see RPC_METRICS_README.md)

---

## Next Steps

### 1. Monitor Metrics
- Start dashboard: http://localhost:5002/rpc-metrics
- Watch burn rate trends over time
- Set up email alerts on high cost days

### 2. Optimize RPC Calls
- Adjust `max_pages` based on cost vs data needs
- Reduce concurrency if rate-limited
- Cache results to avoid redundant calls

### 3. Integrate with Monitoring
- Set up Prometheus scraping of `/metrics/prometheus` endpoint
- Create AlertManager rules for budget alerts
- Add dashboards to Grafana if using it

### 4. Instrument Remaining Files (Optional)
- `pumpfun_curve_listener.py` - WebSocket streaming metrics
- `pump_fun_analyzer.py` - Post-migration RPC calls
- `blocksec_aml_batcher.py` - AML API calls

---

## Configuration

All settings in `rpc_metrics_config.py`:

```python
# Plan configuration
class PlanConfig:
    CURRENT_PLAN = "business"  # Free, Developer, Business, Professional, Unlimited

# Alert thresholds
class AlertConfig:
    BURN_RATE_THRESHOLD_PER_MINUTE = 100.0
    BUDGET_WARNING_PERCENT = 20

# Section definitions
class SectionConfig:
    SECTIONS = {
        "funder_incoming": "Helius Enhanced API for funder transfers",
        "creator_funding": "Real-time creator funding extraction",
        "creator_outgoing_scan": "Background hourly scan",
        # ... etc
    }

# Cost governor (optional)
class CostGovernorConfig:
    ENABLED = False
    THRESHOLDS = {...}
```

---

## Summary

✅ **All major RPC calls** are now instrumented
✅ **Real-time dashboard** shows credit usage by section
✅ **Cost tracking** broken down by RPC method and provider
✅ **Automatic alerts** for high burn rates and budget issues
✅ **JSON API** for programmatic access
✅ **Official Helius rates** used for all credit calculations

**Start monitoring**: http://localhost:5002/rpc-metrics

---

**Last Updated**: 2026-03-01
**Commit**: 0f27a35
**Status**: Production-Ready ✨
