# Reconciliation System Integration Guide

## Overview
The reconciliation system compares **Helius CLI project-level credit totals** (ground truth) with **FLEX internal per-request metrics**, detecting drift and marking breaks (restarts/resets).

## Files
- `reconciliation_schema.py` - SQLite schema setup
- `reconciliation_collectors.py` - Snapshot collectors (Helius CLI + FLEX API)
- `reconciliation_engine.py` - Delta-based reconciliation logic
- `reconciliation_reporter.py` - CLI reporting
- `reconciliation_api.py` - FastAPI endpoints
- `reconciliation_main.py` - Orchestrator + CLI interface

## Step 1: One-Time Setup

```bash
# Initialize schema
python reconciliation_main.py --init
```

This creates three tables:
- `helius_usage_snapshots` - CLI ground truth
- `internal_usage_snapshots` - FLEX metrics
- `usage_reconciliation` - Reconciliation results

## Step 2: Automated Collection (Cron)

Add to crontab (every 5 minutes):

```bash
*/5 * * * * cd /path/to/flex && python reconciliation_main.py
```

This:
1. Collects Helius CLI snapshot via `helius usage --json`
2. Collects FLEX internal metrics via `GET /metrics/rpc`
3. Runs delta-based reconciliation
4. Stores results

**Requires:**
- Helius CLI installed: `helius`
- `HELIUS_WALLET_KEYPAIR` env var set (same as `helius_cli_monitor.py`)
- FastAPI server running on port 8001 (or customize `--api-url`)

## Step 3: View Results

### Latest reconciliation:
```bash
python reconciliation_main.py --latest
```

Output:
```
================================================================================
LATEST RECONCILIATION
================================================================================
Timestamp:        2025-03-02T14:30:00Z
Window:           300s
CLI Delta:        500 credits
Internal Delta:   505 credits
Difference:       -5 credits
Diff %:           -1.00%
Break:            NO
Status:           clean
================================================================================
```

### Daily report:
```bash
python reconciliation_main.py --daily 2025-03-02
```

Shows all intervals for the day with aggregated totals.

### Health check (7-day summary):
```bash
python reconciliation_main.py --health
```

Output:
```
================================================================================
HEALTH CHECK - Last 7 Days (UTC)
================================================================================
Status:           HEALTHY
Total Intervals:  100
Clean:            98
Breaks:           1
Avg Diff %:       0.50%
Max Diff %:       2.25%
Clean:            98
Minor Drift:      1
Significant Drift:0
================================================================================
```

## Step 4: FastAPI Endpoints (Optional)

Integrate into `rpc_metrics_api.py`:

```python
from reconciliation_api import router as reconciliation_router

app = FastAPI()
# ... existing app setup ...
app.include_router(reconciliation_router, prefix="/reconciliation", tags=["reconciliation"])
```

Then access via HTTP:

```bash
# Collect snapshots
POST /reconciliation/collect

# Run reconciliation
POST /reconciliation/reconcile

# Get latest result
GET /reconciliation/latest

# Get daily report
GET /reconciliation/daily?date=2025-03-02

# Get health check
GET /reconciliation/health
```

## Understanding Results

### Status Values

| Status | Meaning |
|--------|---------|
| `clean` | Diff % within ±1–2%, reconciliation is good |
| `minor_drift` | Diff % within ±2–5%, acceptable drift |
| `significant_drift` | Diff % > ±5%, investigate |
| `cli_reset` | CLI total decreased (new billing cycle) |
| `internal_reset` | Internal total decreased (restart/reset) |
| `large_gap` | Large time gap between samples |

### Delta Calculation

```
cli_delta = helius_total(t) - helius_total(t-1)
internal_delta = flex_credits(t) - flex_credits(t-1)
diff_pct = (cli_delta - internal_delta) / max(cli_delta, 1) * 100
```

If `diff_pct` is negative, FLEX is undercounting; if positive, overcounting (rare).

### Health Levels

| Level | Criteria |
|-------|----------|
| `HEALTHY` | Avg diff % ≤ 2%, breaks < 10% of intervals |
| `WARNING` | Avg diff % > 2%, but no significant drift |
| `DEGRADED` | > 5% of intervals show significant drift |
| `UNSTABLE` | > 10% of intervals marked as breaks |

## Known Drift Sources

### 1. Streaming / Webhooks
If Helius includes streaming bytes converted to credits, FLEX may undercount. Check:
- `helius usage --json` → `creditsUsage.webhookUsage`
- FLEX metrics → track internally or document as expected drift

### 2. Uninstrumented Endpoints
If some services bypass the recorder, FLEX undercounts. Inspect:
- `/metrics/rpc/source-files` → identify missing files/processes
- Update recorder instrumentation

### 3. Retry Storms (429 rate limits)
Helius may count all attempts; FLEX may track only successful retries. Result:
- Consistent CLI-positive drift during storms
- Mark intervals with high 429 counts and note in analysis

### 4. Billing Cycle Changes
When Helius billing resets, CLI total drops. Reconciliation detects:
- `is_break=1` and `notes='cli_reset'`
- Delta computation skipped for that interval

## Troubleshooting

### No snapshots collected
- Check `HELIUS_WALLET_KEYPAIR` is set in `.env`
- Verify Helius CLI installed: `helius --version`
- Check FastAPI running: `curl http://localhost:8001/health`

### High drift (> 5%)
1. Check `/metrics/rpc/source-files` for missing instrumentation
2. Check `/metrics/rpc` for high `rate_limits_429` (retry storms)
3. Compare Helius billing categories: are you tracking webhooks, streaming?
4. Check for recent FLEX restarts (internal reset marked as break)

### Missing data
- Run `python reconciliation_main.py --init` to create schema
- Ensure cron job is running: `crontab -l`
- Check logs: `tail -f /var/log/syslog | grep reconciliation` (or equivalent)

## Integration with Listener

To capture reconciliation snapshots when the listener detects new tokens:

```python
# In pumpfun_curve_listener.py, after funding extraction
from reconciliation_main import main as reconciliation_main

# In background task:
try:
    print("[RECONCILIATION] Running snapshot collection...")
    import sys
    sys.argv = ["reconciliation_main.py", "--collect", "--reconcile"]
    reconciliation_main()
except Exception as e:
    print(f"[RECONCILIATION] Error: {e}")
```

Or simpler, just ensure cron runs every 5 minutes independently.

## API Integration (rpc_metrics_api.py)

Add to dashboard to show reconciliation status:

```javascript
// Fetch health in renderDashboard()
const health = await fetch('/reconciliation/health').then(r => r.json());

// Display in dashboard
html += `<div class="card">
    <h3>Reconciliation Health</h3>
    <div class="value" style="color: ${health.health === 'HEALTHY' ? '#10b981' : '#f59e0b'}">
        ${health.health}
    </div>
    <div class="unit">Avg diff: ${(health.details?.avg_diff_pct || 0).toFixed(2)}%</div>
</div>`;
```

## Success Criteria

After 1–2 days of automated collection:

✅ Per-interval diff_pct within ±1–2% (non-break intervals)
✅ Daily aggregated diff within ±1–2%
✅ No unexplained significant drifts
✅ Health status stays HEALTHY or WARNING (not DEGRADED/UNSTABLE)

If criteria not met, investigate drift sources above.

## Next Steps

1. Run `python reconciliation_main.py --init`
2. Add to crontab: `*/5 * * * * cd /path/to/flex && python reconciliation_main.py`
3. Wait 1 hour, then: `python reconciliation_main.py --latest`
4. Check health: `python reconciliation_main.py --health`
5. If high drift, run diagnostic: `python reconciliation_main.py --daily <date>`
