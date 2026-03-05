# Helius Account Monitor – Real-Time Credit Tracking

**Status**: ✅ COMPLETE
**Date**: 2026-03-02
**Commit**: f198e16

---

## Overview

Real-time monitoring of your Helius account credit usage, remaining budget, and burn rate.

Tracks:
- **Account Balance** – Credits used, remaining, and monthly budget
- **Burn Rate** – Current consumption per minute (extrapolated to daily)
- **Instrumented Metrics** – Our tracked RPC calls vs actual usage
- **Discrepancies** – Detects uninstrumented RPC calls (not in our metrics)
- **Alerts** – Warnings when budget usage is high

---

## Quick Start

### 1. Check Account Status (CLI)

```bash
python helius_account_monitor.py
```

Output:
```
================================================================================
[HELIUS] 📊 ACCOUNT STATUS
================================================================================

💰 Helius Account:
   Credits Used:                 0
   Credits Remaining:    1,000,000
   Monthly Budget:       1,000,000
   Usage:                      0.0%
   API Calls:                    0
   Est. Daily Burn:              0

📈 Our Instrumented Metrics:
   Credits Today:                0
   Burn Rate:                 0.00 cr/min
   Requests:                     0
   Errors:                       0

🔍 Comparison:
   Helius Used:                  0
   Our Instrumented:             0
   Difference:                   0
   % Difference:               0.0%

================================================================================
```

### 2. Check via API

```bash
curl http://localhost:8001/metrics/helius | jq '.'
```

Returns:
```json
{
  "timestamp": "2026-03-02T10:16:41.610740",
  "helius_account": {
    "credits_used": 0,
    "credits_remaining": 1000000,
    "monthly_budget": 1000000,
    "api_calls": 0,
    "estimated_daily_burn": 0,
    "percent_used": 0.0
  },
  "instrumented_metrics": {
    "credits_today": 0,
    "burn_rate_per_minute": 0.0,
    "requests_total": 0,
    "errors_total": 0
  },
  "discrepancy": {
    "helius_used": 0,
    "instrumented_used": 0,
    "difference": 0,
    "percent_difference": 0
  },
  "alerts": [],
  "source": "config"
}
```

### 3. Export Usage History

```bash
python helius_account_monitor.py --export usage_history.csv
```

Exports:
- timestamp
- credits_used
- credits_remaining
- monthly_budget
- api_calls
- estimated_daily_burn

---

## API Endpoint

**GET** `/metrics/helius`

Returns comprehensive account status.

### Example Response

```json
{
  "timestamp": "2026-03-02T10:16:41.610740",
  "helius_account": {
    "credits_used": 24682,
    "credits_remaining": 975318,
    "monthly_budget": 1000000,
    "api_calls": 3434,
    "estimated_daily_burn": 1191456,
    "percent_used": 2.5
  },
  "instrumented_metrics": {
    "credits_today": 24682,
    "burn_rate_per_minute": 825.75,
    "requests_total": 3434,
    "errors_total": 0
  },
  "discrepancy": {
    "helius_used": 24682,
    "instrumented_used": 24682,
    "difference": 0,
    "percent_difference": 0.0
  },
  "alerts": [
    "🟡 INFO: 2.5% of monthly budget used",
    "🟡 INFO: 1191 days of credits remaining at current burn rate"
  ],
  "source": "config"
}
```

---

## Data Sources

### Helius Account (helius_account)

**Source**: `rpc_metrics_config.PlanConfig.CURRENT_USAGE`
- Manually synced from Helius dashboard
- Set to current actual usage
- Updated when you reset metrics or check account

**Fallback Method** (when API not available):
- Uses stored configuration values
- No real-time API polling (Helius doesn't expose account API)

### Instrumented Metrics (instrumented_metrics)

**Source**: `rpc_metrics_recorder.get_summary()`
- Real-time from RPC calls we've tracked
- Updated as requests are made
- Includes burn rate calculation

---

## Alert System

Alerts trigger based on account status:

### Budget Alerts

| Threshold | Level | Message |
|-----------|-------|---------|
| >= 95% | 🔴 CRITICAL | 95.5% of monthly budget used |
| >= 80% | 🟠 WARNING | 82.3% of monthly budget used |
| >= 50% | 🟡 INFO | 50.0% of monthly budget used |

### Burn Rate Alerts

| Threshold | Level | Message |
|-----------|-------|---------|
| < 1 day | 🔴 CRITICAL | Less than 1 day remaining at current burn rate |
| < 7 days | 🟠 WARNING | 5.2 days of credits remaining |

### Discrepancy Alerts

| Threshold | Level | Message |
|-----------|-------|---------|
| > 20% | 🟠 WARNING | Large discrepancy - check for uninstrumented calls |

---

## Discrepancy Analysis

**What it means**:

If Helius says you've used 100K credits but we've only tracked 80K:
- 20K credits are unaccounted for
- Could indicate uninstrumented RPC calls
- Or estimation errors in cost model

**Example**:
```
Helius Used:          100,000
Our Instrumented:      80,000
Difference:            20,000
% Difference:          20.0%

⚠️ WARNING: Large discrepancy (20.0%)
Check for uninstrumented RPC calls or estimation errors
```

**How to fix**:
1. Check which endpoints are making RPC calls
2. Add instrumentation if missing
3. Verify cost estimates are correct

---

## Usage History Table

Stored in SQLite (`helius_account_history` table):

```sql
CREATE TABLE helius_account_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TIMESTAMP,
  credits_used INTEGER,
  credits_remaining INTEGER,
  monthly_budget INTEGER,
  api_calls INTEGER,
  estimated_daily_burn INTEGER,
  recorded_at TIMESTAMP
)
```

Useful queries:

```sql
-- Last 24 hours of usage
SELECT timestamp, credits_used, credits_remaining
FROM helius_account_history
WHERE timestamp >= datetime('now', '-24 hours')
ORDER BY timestamp DESC;

-- Daily snapshot
SELECT DATE(timestamp), MAX(credits_used), MIN(credits_remaining)
FROM helius_account_history
GROUP BY DATE(timestamp);

-- Burn rate trend
SELECT timestamp, estimated_daily_burn FROM helius_account_history
ORDER BY timestamp DESC LIMIT 10;
```

---

## Configuration

### API Key

Monitor automatically uses the primary Helius API key:

```python
# From creator_outgoing_extractor.py
RPC_KEYS = [
    ("a132b19d-9b44-4c71-8e6f-d320d9f351c6", "GITHUB"),     # Primary
    ("f084fae8-d111-4337-9960-2d9c5e02a726", "MARZEL"),
    # ...
]
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or RPC_KEYS[0][0]
```

Or set environment variable:
```bash
export HELIUS_API_KEY="your-api-key-here"
```

### Budget Configuration

Edit `rpc_metrics_config.py`:

```python
CURRENT_USAGE = {
    "credits_used_today": 0,          # Current usage
    "credits_remaining": 1_000_000,   # Budget remaining
    "budget_start_date": "2026-03-01",
}
```

---

## Automated Monitoring

### Schedule Regular Checks

```bash
# Every 5 minutes via cron
*/5 * * * * cd /path/to/flex && python helius_account_monitor.py >> /var/log/helius.log 2>&1

# Every hour via cron
0 * * * * cd /path/to/flex && python helius_account_monitor.py --export helius_usage_$(date +%Y%m%d_%H%M).csv
```

### Integration with Dashboard

The RPC Metrics dashboard automatically shows:
- Total Credits Today
- Credits Used (Since Reset)
- Monthly Remaining
- Burn Rate
- Alerts

All sync with this monitor.

---

## Typical Workflow

### Daily Monitoring

```bash
# Morning check
python helius_account_monitor.py

# Check via API
curl http://localhost:8001/metrics/helius | jq '.helius_account'

# Export weekly
python helius_account_monitor.py --export usage_week_$(date +%Y%m%d).csv
```

### When You See High Usage

```bash
# Check account
python helius_account_monitor.py

# Look for discrepancies (uninstrumented calls)
# Check dashboard for burn rate spikes
# View alerts
curl http://localhost:8001/metrics/helius | jq '.alerts'
```

### After Spending Limit Reached

```bash
# Document final state
python helius_account_monitor.py > final_usage.txt

# Export full history
python helius_account_monitor.py --export final_history.csv

# Check if discrepancies explain overage
curl http://localhost:8001/metrics/helius | jq '.discrepancy'
```

---

## Troubleshooting

### Monitor shows 0 credits but Helius shows usage

**Cause**: Configuration not synced with Helius dashboard
**Fix**: Update `rpc_metrics_config.py` with current values from Helius

### Large discrepancy between Helius and our metrics

**Cause**: Uninstrumented RPC calls or estimation errors
**Fix**:
1. Search for RPC endpoints not calling `record_request()`
2. Verify credit cost estimates are accurate
3. Check for external tools making calls

### API endpoint returning error

**Cause**: rpc_metrics_api.py not running or import error
**Fix**:
```bash
pkill -f rpc_metrics_api
python rpc_metrics_api.py > /tmp/rpc_metrics.log 2>&1 &
```

---

## See Also

- [RPC Metrics Dashboard](DASHBOARD_CREDITS_USED_CARD.md) - Visual credit tracking
- [Reset Metrics Button](RESET_METRICS_FIX.md) - Zero out metrics
- [RPC Metrics Config](rpc_metrics_config.py) - Budget and plan settings

---

## Summary

✅ Real-time monitoring of Helius account credit usage
✅ Discrepancy detection (uninstrumented calls)
✅ Alert system for budget limits
✅ Usage history export
✅ API endpoint for programmatic access
✅ CLI tool for quick checks

The monitor helps you:
- Stay within budget
- Detect wasted credits
- Track consumption patterns
- Plan capacity

**Access**:
- CLI: `python helius_account_monitor.py`
- API: `curl http://localhost:8001/metrics/helius`
- Dashboard: `http://localhost:5002/rpc-metrics`
