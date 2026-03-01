# RPC Metrics Dashboard - Quick Start (5 Minutes)

Get the dashboard running in 5 minutes.

---

## Step 1: Initialize (30 seconds)

In your main application entry point (e.g., `pumpfun_curve_listener.py` or `main.py`):

```python
from rpc_metrics_recorder import initialize_recorder

# At startup (once):
initialize_recorder(plan_monthly_credits=50_000_000)
```

---

## Step 2: Record One RPC Call (1 minute)

Add to any RPC wrapper function:

```python
import time
from rpc_metrics_recorder import record_request

# Before your RPC call:
start_time = time.time()

# Make the call:
response = requests.get(url)

# After the call:
latency_ms = (time.time() - start_time) * 1000
record_request(
    section="funder_incoming",      # Change to your section
    provider="helius_enhanced",       # helius_rpc, helius_enhanced, etc.
    method="helius_enhanced_addresses_transactions",  # Your RPC method
    status_code=response.status_code,
    latency_ms=latency_ms,
    mode="realtime",                 # or "background"
)
```

---

## Step 3: Start the Dashboard (1 minute)

```bash
# In a terminal:
python rpc_metrics_api.py

# Or with uvicorn:
uvicorn rpc_metrics_api:app --host 0.0.0.0 --port 8001
```

---

## Step 4: View Dashboard (30 seconds)

Open browser:
- **Dashboard**: http://localhost:8001/dashboard
- **API JSON**: http://localhost:8001/metrics/rpc

---

## Step 5: Make RPC Calls (remaining time)

Make some API calls to your system. Watch the metrics update live on the dashboard!

---

## What You'll See

**Dashboard shows:**
- Total credits today
- Burn rate (credits/min)
- Per-section breakdown (credits, requests, errors, latency)
- Top methods consuming credits
- Active alerts (if any)

---

## Integration Checklist

- [ ] Added `initialize_recorder()` at startup
- [ ] Wrapped first RPC call with `record_request()`
- [ ] Started metrics API server
- [ ] Viewed dashboard in browser
- [ ] Made some API calls (dashboard updates in real-time)

**Done!** You now have production-grade credit monitoring.

---

## Next: Add More Coverage

To monitor all RPC calls, add `record_request()` to:

1. **funder_helius_extractor.py** - `get_transactions_helius()` (highest value)
2. **realtime_creator_funding_extractor.py** - All Helius API calls
3. **funder_incoming_extractor.py** - `get_transactions_helius()` + batch calls
4. **pumpfun_curve_listener.py** - WebSocket streaming (use `record_stream_bytes()`)
5. **main.py** - Any Flask endpoint making RPC calls

See [RPC_METRICS_INTEGRATION_GUIDE.md](RPC_METRICS_INTEGRATION_GUIDE.md) for detailed examples.

---

## Customize Configuration

Edit `rpc_metrics_config.py`:

```python
# 1. Set your Helius plan:
PlanConfig.CURRENT_PLAN = "business"  # or "developer", "professional"

# 2. Adjust burn rate alert threshold:
AlertConfig.BURN_RATE_THRESHOLD_PER_MINUTE = 100.0  # Change as needed

# 3. Set admin token for reset endpoint:
MetricsAPIConfig.ADMIN_TOKEN = "your-secret-token"
```

---

## Key Section Tags

Use these when calling `record_request()`:

| Tag | Usage |
|-----|-------|
| `listener` | WebSocket/gRPC streaming |
| `creator_funding` | Extract creator funding |
| `funder_incoming` | Trace funder sources |
| `creator_outgoing_scan` | Background enrichment |
| `ui_api` | Flask API endpoints |
| `background_enrichment` | Batch processing |

---

## Key Method Tags

Use these when calling `record_request()`:

```python
# Standard RPC (archival - 10 credits)
"getTransaction"
"getSignaturesForAddress"

# Helius Enhanced Transactions (PLAN-DEPENDENT)
# ⚠️ IMPORTANT: These are NOT published by Helius as fixed credits.
# Verify actual cost with your Helius billing dashboard or support.
# Configure in rpc_metrics_config.py CREDIT_SCHEDULE after checking your plan.
"helius_enhanced_addresses_transactions"  # Default: "unknown" (0 credits until configured)
"helius_enhanced_transactions_batch"      # Default: "unknown" (0 credits until configured)

# Streaming (3 credits per 0.1MB)
# Use record_stream_bytes() instead
```

---

## Common Patterns

### Pattern 1: Sync RPC Call
```python
import time
from rpc_metrics_recorder import record_request

start = time.time()
response = requests.get(url)
record_request(
    section="funder_incoming",
    provider="helius_enhanced",
    method="helius_enhanced_addresses_transactions",
    status_code=response.status_code,
    latency_ms=(time.time() - start) * 1000,
)
```

### Pattern 2: Async RPC Call
```python
import time
from rpc_metrics_recorder import record_request

start = time.time()
async with session.get(url) as resp:
    status = resp.status
record_request(
    section="creator_funding",
    provider="helius_rpc",
    method="getTransaction",
    status_code=status,
    latency_ms=(time.time() - start) * 1000,
)
```

### Pattern 3: Error Handling
```python
import time
from rpc_metrics_recorder import record_request

start = time.time()
try:
    response = requests.get(url, timeout=15)
except requests.Timeout:
    record_request(
        section="funder_incoming",
        provider="helius_enhanced",
        method="helius_enhanced_addresses_transactions",
        status_code=504,  # Gateway timeout
        latency_ms=(time.time() - start) * 1000,
        error="Request timeout",
    )
```

### Pattern 4: Streaming Data
```python
from rpc_metrics_recorder import record_stream_bytes

# After receiving data:
record_stream_bytes(
    section="listener",
    provider="helius_ws",
    stream_name="enhanced_ws",
    bytes_count=1_000_000,  # 1MB = 30 credits
)
```

---

## API Endpoints

| Endpoint | Purpose | Bandwidth |
|----------|---------|-----------|
| `/metrics/rpc` | Full metrics | ~2KB |
| `/metrics/rpc/summary` | Quick summary | ~300B |
| `/metrics/rpc/sections` | Per-section only | ~1KB |
| `/metrics/rpc/methods` | Top methods only | ~500B |
| `/metrics/rpc/alerts` | Alerts only | ~200B |
| `/dashboard` | HTML UI | ~20KB |

**Dashboard auto-refreshes** from `/metrics/rpc` every 5 seconds.

---

## Troubleshooting

**Q: Dashboard shows 0 credits?**
A: Make sure `record_request()` is being called. Add print statement:
```python
credits = record_request(...)
print(f"Recorded {credits} credits")
```

**Q: API error 403 on reset?**
A: Update admin token:
```
POST /metrics/rpc/reset?admin_token=SECRET_ADMIN_TOKEN
```

**Q: High latency showing?**
A: Verify `time.time()` calls bracket the actual request, not just the response.

**Q: Dashboard not auto-updating?**
A: Check browser console for errors. Verify API responds to:
```bash
curl http://localhost:8001/metrics/rpc
```

---

## Example Output

**Dashboard Summary Cards:**
```
Total Credits Today        45,230
Daily Burn Rate            360.5 credits/min
Monthly Estimate           1,356,900
Monthly Remaining          (Unlimited plan)
Total Requests             1,204
Errors                     12
```

**Per-Section Table:**
```
Section                Credits   Requests  Errors  429s  Avg Latency  P95 Latency
funder_incoming        35,000    350       5       1     245ms        890ms
listener               8,000     8         2       0     125ms        200ms
ui_api                 2,230     846       5       1     150ms        450ms
```

**Top Methods:**
```
Method                                              Credits  Requests  Credits/Req
helius_enhanced_addresses_transactions             35,000    350       100
getTransaction                                     1,200     120       10
getSignaturesForAddress                            1,030     103       10
```

---

## Resources

- **Full Integration Guide**: [RPC_METRICS_INTEGRATION_GUIDE.md](RPC_METRICS_INTEGRATION_GUIDE.md)
- **Configuration Reference**: [rpc_metrics_config.py](rpc_metrics_config.py)
- **Detailed README**: [RPC_METRICS_README.md](RPC_METRICS_README.md)
- **Source Code**: [rpc_metrics_recorder.py](rpc_metrics_recorder.py), [rpc_metrics_api.py](rpc_metrics_api.py)

---

**Done! You're now monitoring Helius credit usage.** 🚀
