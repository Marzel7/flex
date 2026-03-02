# RPC Metrics Tracking & Source File Monitoring

**Date**: 2026-03-02
**Status**: ✅ Production Ready (with known issues)
**Dashboard**: http://localhost:5002/rpc-metrics
**API**: http://localhost:8001/metrics/rpc

---

## Overview

The FLEX RPC Metrics Dashboard provides real-time Helius credit usage tracking by:
1. **Component Section** - Which logical component is using credits (listener, ui_api, creator_funding, etc.)
2. **Source File/Process** - Which Python file/module initiated the RPC call
3. **RPC Methods** - Which specific RPC methods consumed the most credits
4. **Error Tracking** - HTTP status codes, rate limits (429), and latency monitoring

---

## Current Status (2026-03-02 08:30 UTC)

### Credit Usage
| Metric | Value | Status |
|--------|-------|--------|
| **Helius Dashboard** | 15,880 credits | Official source |
| **FLEX Dashboard** | 15,853 baseline + tracked | Config baseline |
| **Difference** | +27 credits | Uninstrumented endpoints |
| **Monthly Budget** | 1,000,000 credits | Business Plan |
| **Remaining** | 984,120 credits | 98.4% available |

### Active Processes
| Process | PID | Started | Status |
|---------|-----|---------|--------|
| pumpfun_curve_listener | 36736 | 7:36 AM | ✅ Running (old code) |
| rpc_metrics_api | (auto) | Latest | ✅ Running |
| Flask main.py | (auto) | Latest | ✅ Running |
| creator_outgoing_extractor | Task | 12h interval | ⚠️ Background job |

---

## Dashboard Sections

### 1. Summary Cards
Shows high-level metrics:
- **Total Credits Today**: 15,853 (from config) + active metrics
- **Daily Burn Rate**: 0.00 credits/min (when idle)
- **Monthly Estimate**: 0 (from active metrics)
- **Monthly Remaining**: 984,147 credits
- **Total Requests**: 1,017 (from active recordings)
- **Errors**: 923 (923 HTTP 429 rate limit errors)

### 2. By Component Section
Breaks down credit usage by logical component:

| Section | Credits | Requests | Errors | 429s | Avg Latency | P95 Latency |
|---------|---------|----------|--------|------|-------------|-------------|
| listener | 0 | 1 | 0 | 0 | 45.3ms | 45.3ms |
| creator_outgoing_scan | 2,370 | 1,016 | 923 | 923 | 96.24ms | 159.72ms |

**Section Definitions:**
- `listener` - WebSocket listener for token launches (pumpfun_curve_listener)
- `creator_outgoing_scan` - Background scan of creator outgoing transfers (every 12 hours)
- `creator_funding` - Real-time creator funding extraction
- `funder_incoming` - Funder's incoming transfer extraction
- `ui_api` - Flask API endpoints and user-triggered analysis
- `background_enrichment` - Batch processing and historical analysis

### 3. By Source File/Process
**NEW FEATURE** - Shows which Python files/processes are making RPC calls:

| File/Process | Credits | Requests | Errors | 429s | Sections | Avg Latency |
|---|---|---|---|---|---|---|
| unknown | 2,370 | 1,016 | 923 | 923 | creator_outgoing_scan (1016) | 96.24ms |
| pumpfun_curve_listener | 0 | 1 | 0 | 0 | listener (1) | 45.3ms |

**Key Insight**:
- **unknown** source files = old code running (process not restarted since code changes)
- Once restarted with new instrumented code, will show actual module names

### 4. Top RPC Methods by Credits
Shows which RPC methods consumed the most credits:

| Method | Credits | Requests | Credits/Request |
|--------|---------|----------|-----------------|
| getSignaturesForAddress | 10,000+ | 1,000+ | 10 |
| helius_enhanced_transactions_batch | 1,600+ | 16 | 100 |
| getAccountInfo | 0 | 1 | 0 |

---

## Active Alerts

### ⚠️ Warning: High Burn Rate
- **Threshold**: 100.0 credits/min
- **Current**: 303.09 credits/min
- **Cause**: creator_outgoing_extractor making 1,000+ RPC calls
- **Status**: Expected during background scans

### ⚠️ Warning: High Error Rate
- **Threshold**: 5.0% errors
- **Current**: 90.8% errors (923 out of 1,017 requests)
- **Cause**: HTTP 429 Rate Limit responses from Helius
- **Location**: creator_outgoing_scan section
- **Impact**: Requests are failing but being retried

---

## Source File Tracking Feature

### What It Does
Every RPC call now records which Python file/process initiated it:

```python
record_request(
    section="listener",
    provider="helius_rpc",
    method="getAccountInfo",
    status_code=200,
    latency_ms=45.3,
    source_file="pumpfun_curve_listener",  # ← NEW: Tracks which file made the call
    error=None
)
```

### Instrumented Files (With source_file Parameter)
- ✅ pumpfun_curve_listener.py
- ✅ creator_outgoing_extractor.py
- ✅ funder_helius_extractor.py
- ✅ funder_incoming_extractor.py
- ✅ pump_fun_analyzer.py
- ✅ pump_fun_post_migration_analyzer.py
- ✅ realtime_creator_funding_extractor.py

### NOT Yet Instrumented
- ❌ /api/validate-transaction (Flask endpoint)
- ❌ /api/transaction/<signature> (Flask endpoint)
- These endpoints make direct RPC calls without recording metrics

---

## Credit Usage Breakdown

### Helius Dashboard Shows 15,880 Credits
The extra **+27 credits** (from 15,853 baseline) come from:

**Possible Sources:**
1. `/api/validate-transaction` - Makes getTransaction calls (10 cr each) - Not used today
2. `/api/transaction/<signature>` - Makes getTransaction calls (10 cr each) - Not used today
3. LaserStream WebSocket subscription - Streaming costs (3 cr per 0.1MB)
4. Uninstrumented background processes

### Recorded in FLEX Dashboard: 15,853 Credits
Tracked from configuration baseline. The active metrics recorder shows:
- **From creator_outgoing_scan**: ~2,370 credits from 1,016 RPC calls
- **From listener**: Minimal (1 test request)
- **Untracked**: ~27 credits from uninstrumented sources

---

## 923 Rate Limit Errors Explained

### What Happened
The `creator_outgoing_extractor` background scan is running and experiencing heavy rate limiting:

| Metric | Value | Details |
|--------|-------|---------|
| Total Requests | 1,016 | All in creator_outgoing_scan |
| Failed (429) | 923 | Rate limited by Helius |
| Success | 93 | Only 9.2% succeeded |
| Methods | getSignaturesForAddress (1000), helius_enhanced_transactions_batch (16) | |
| Avg Latency | 96.24ms | Higher than normal due to retries |

### Why This Happened
The 12-hour background scan ran and made 1,000+ RPC calls to Helius. The Business plan allows **100 requests/second**, but the scan was likely:
1. Not implementing proper backoff
2. Making requests in bursts too quickly
3. Not respecting rate limit responses

### Impact
- ✅ Calls are still being recorded and tracked
- ✅ Errors are visible in dashboard alerts
- ❌ ~91% of requests failed and needed to be retried
- ❌ Creates inefficient credit usage (retries consume extra credits)

---

## Known Issues & Limitations

### Issue 1: Processes Not Restarted
**Problem**: pumpfun_curve_listener and creator_outgoing_extractor haven't been restarted since code changes.

**Effect**:
- Source file tracking shows "unknown" instead of actual module names
- Old code paths are running without proper instrumentation
- Background scan is happening with potentially outdated code

**Solution**: Restart pumpfun_curve_listener to pick up new instrumented code

### Issue 2: Rate Limiting on Background Scans
**Problem**: creator_outgoing_extractor is too aggressive and hitting Helius rate limits.

**Cause**: Making 1,000+ RPC calls without proper backoff/concurrency limits

**Solutions** (Priority Order):
1. Reduce pagination depth (currently max_pages=5, reduce to 2-3)
2. Add exponential backoff for 429 responses
3. Reduce concurrency from 10 to 3-5 simultaneous requests
4. Increase interval between scans or split into batches

### Issue 3: Uninstrumented Flask Endpoints
**Problem**: Two Flask API endpoints make direct RPC calls without recording:
- `/api/validate-transaction` (POST)
- `/api/transaction/<signature>` (GET)

**Effect**:
- ~27 unaccounted credits in Helius vs FLEX dashboard
- These endpoints don't appear in source file tracking
- User can't see API usage in metrics

**Solution**: Instrument these endpoints to call record_request()

---

## API Endpoints

### Metrics API (Port 8001)
| Endpoint | Purpose | Response |
|----------|---------|----------|
| GET /metrics/rpc | Full metrics with summary, sections, methods, alerts | JSON |
| GET /metrics/rpc/summary | Quick summary only | JSON |
| GET /metrics/rpc/sections | Per-section breakdown | JSON |
| GET /metrics/rpc/methods?limit=10 | Top methods by credits | JSON |
| GET /metrics/rpc/source-files | Per-source-file breakdown | JSON |
| GET /metrics/rpc/alerts | Active alerts | JSON |
| POST /metrics/rpc/record | Record a single RPC metric | JSON |
| POST /metrics/rpc/reset | Reset daily counters (admin only) | JSON |
| GET /dashboard | HTML dashboard | HTML |

### Flask Proxy (Port 5002)
All `/metrics/rpc/*` endpoints are proxied from Flask to FastAPI:
- GET /metrics/rpc
- GET /metrics/rpc/summary
- GET /metrics/rpc/sections
- GET /metrics/rpc/methods
- GET /metrics/rpc/source-files
- GET /metrics/rpc/alerts
- GET /rpc-metrics (dashboard HTML)

---

## Configuration

### File: rpc_metrics_config.py

**Credit Schedule** - Cost of each RPC method:
```python
CREDIT_SCHEDULE = {
    "getAccountInfo": 1,
    "getBalance": 1,
    "getTokenAccountBalance": 1,
    "getTokenAccountsByOwner": 1,
    "getTokenLargestAccounts": 1,
    "getMultipleAccounts": 1,
    "getBlock": 1,
    "getSlot": 1,
    "getTransaction": 10,
    "getSignaturesForAddress": 10,
    "getSignatureStatuses": 1-10,
    "getProgramAccounts": 5,
    "getTransactionsForAddress": 100,  # Helius-only
    "helius_enhanced_addresses_transactions": 100,
    "helius_enhanced_transactions_batch": 100,
    "laserstream_bytes": 3 per 0.1MB,
    "enhanced_ws_bytes": 3 per 0.1MB,
}
```

**Current Usage** - Updated manually from Helius:
```python
CURRENT_USAGE = {
    "credits_used_today": 15_880,
    "credits_remaining": 984_120,
    "budget_start_date": "2026-03-01",
}
```

**Plan Details**:
```python
CURRENT_PLAN = "business"  # 50M monthly credits, 100 req/sec
```

**Alert Thresholds**:
```python
BURN_RATE_THRESHOLD_PER_MINUTE = 100.0        # Alert if > 100 cr/min
BUDGET_WARNING_PERCENT = 20                   # Warn at 20% remaining
BUDGET_CRITICAL_PERCENT = 5                   # Critical at 5% remaining
ERROR_RATE_THRESHOLD_PERCENT = 5.0            # Alert if > 5% errors
RATE_LIMIT_THRESHOLD_PER_5MIN = 10            # Alert if > 10 429s in 5min
```

---

## Recent Changes

### Commit: Add source file/process tracking to RPC metrics dashboard
- Added `source_file` parameter to RequestRecord dataclass
- Updated all record_request() calls to include source_file
- Added get_source_file_stats() method for aggregation
- New /metrics/rpc/source-files API endpoint
- Updated dashboard with "By Source File/Process" section
- All 7 instrumented files include source_file parameter

### Commit: Fix RPC metrics API endpoint
- Changed /metrics/rpc/record to accept JSON body instead of Query params
- Added source_file parameter to record endpoint
- Fixes multi-process metrics collection

### Commit: Add Flask proxy for /metrics/rpc/source-files
- Added missing Flask proxy endpoint
- Dashboard can now fetch source file stats

### Commit: Remove deprecated CreatorWatchManager
- Removed 817 lines of deprecated code
- Replaced with instrumented creator_outgoing_extractor
- Removed +134 untracked RPC calls

---

## Next Steps

### Priority 1: Restart Services
- [ ] Restart pumpfun_curve_listener to pick up new instrumented code
- [ ] Verify source_file shows actual module names instead of "unknown"
- [ ] Monitor dashboard for proper metric recording

### Priority 2: Fix Rate Limiting Issues
- [ ] Reduce creator_outgoing_extractor concurrency (10 → 3-5)
- [ ] Add exponential backoff for 429 responses
- [ ] Reduce max_pages from 5 to 2-3
- [ ] Test: Should see <5% error rate after fix

### Priority 3: Instrument Remaining Endpoints
- [ ] Instrument /api/validate-transaction endpoint
- [ ] Instrument /api/transaction/<signature> endpoint
- [ ] Verify +27 credit discrepancy is resolved

### Priority 4: Monitoring & Alerts
- [ ] Set up continuous monitoring of error rates
- [ ] Configure rate limit alerts before hitting limits
- [ ] Add dashboard notifications for threshold violations

---

## Testing the Dashboard

### View Current Metrics
```bash
curl http://localhost:5002/metrics/rpc | jq '.'
```

### Check Source File Breakdown
```bash
curl http://localhost:5002/metrics/rpc/source-files | jq '.'
```

### Monitor Alerts
```bash
curl http://localhost:5002/metrics/rpc/alerts | jq '.'
```

### View Dashboard
Open browser to: **http://localhost:5002/rpc-metrics**

---

## Key Learnings

1. **Multi-Process Metrics**: Different Python processes need to POST their metrics to the central API since they can't share memory
2. **Rate Limiting is Critical**: The Helius Business plan allows 100 req/sec, but background scans can easily exceed this
3. **Source File Tracking**: Knowing which module made an RPC call is essential for debugging and optimization
4. **Error Visibility**: 90% error rate immediately alerts you to problems that would otherwise be silent
5. **Configuration is Key**: Keeping credits_used_today in sync with Helius allows accurate budgeting

---

## References

- **Helius Billing Docs**: https://www.helius.dev/docs/billing/credits
- **Helius Rate Limits**: https://www.helius.dev/docs/billing/rate-limits
- **RPC Methods API**: https://docs.helius.xyz/
- **Dashboard URL**: http://localhost:5002/rpc-metrics
- **Metrics API**: http://localhost:8001/metrics/rpc

---

**Last Updated**: 2026-03-02 08:35 UTC
**Branch**: rpc
**Status**: ✅ Production Ready with Known Issues
