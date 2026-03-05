# RPC Monitoring & Tracking Guide

Guide for monitoring RPC activity when listener starts and tracking which files/processes make RPC calls.

## Quick Start

### Step 1: Start the Listener
```bash
cd /Users/kevinkeaveney/Dev/claude/flex
python pumpfun_curve_listener.py
```

You should see:
```
[LISTENER] ✅ Initialized WebSocket listener
[LISTENER] 🔄 Starting creator outgoing transfer extractor...
```

### Step 2: Monitor RPC Activity (in another terminal)

**Option A: Real-time Python Monitor (RECOMMENDED)**
```bash
cd /Users/kevinkeaveney/Dev/claude/flex
python monitor_rpc_live.py
```

Output example:
```
🚀 RPC Metrics Monitor started
📁 Logs saved to: /Users/kevinkeaveney/Dev/claude/flex/rpc_monitor_logs
⏱️  Checking every 10 seconds

[ITERATION 1] 2026-03-03 12:00:15

Total Calls: 145
Total Credits: 1450
Source Files: 2
Unique Methods: 2

========================================================
METHOD                                   SOURCE                         CALLS    CREDITS
========================================================
getSignaturesForAddress                  creator_outgoing_extractor     125      1250
helius_enhanced_transactions_batch       creator_outgoing_extractor      20      2000
```

Run for specific duration (30 minutes):
```bash
python monitor_rpc_live.py 30
```

Check every 5 seconds instead of 10:
```bash
python monitor_rpc_live.py 30 5
```

**Option B: One-Shot Quick Check (Bash)**
```bash
./check_rpc_activity.sh
```

Output example:
```
=== RPC Activity Report - 2026-03-03 12:05:30 ===

📊 OVERALL SUMMARY
metric|value
Total RPC Calls|456
Total Credits|4560
Unique Methods|2
Unique Sources|2

📈 CREDITS BY METHOD (Last 1 hour)
method|calls|total_credits
getSignaturesForAddress|425|4250
helius_enhanced_transactions_batch|31|3100

🔍 CREDITS BY SOURCE (Last 1 hour)
source|calls|total_credits
creator_outgoing_extractor|425|4250
unknown|31|3100

✅ Report saved to: rpc_monitor_logs/activity_20260303_120530.log
```

Run repeatedly every 5 seconds:
```bash
watch -n 5 "./check_rpc_activity.sh"
```

## Understanding the Output

### Source Files
- **webhook_handler**: Real-time creator outgoing transfer monitoring via Helius webhook (replaced background scanning)
- **main**: Manual API calls from `/api/scan-creator` endpoint
- **unknown**: RPC calls not properly attributed (code issue)

### Methods
- **getSignaturesForAddress**: 10 credits each - fetch transaction signatures for an account
- **helius_enhanced_transactions_batch**: 100 credits each - parse transaction details in batch

### Note: Real-Time Webhook Monitoring
Creator outgoing transfers are now monitored in **real-time via Helius webhook** instead of periodic background scanning. This provides:
- **Real-time detection** of creator transfers (immediate vs 12-hour delay)
- **Lower RPC costs** (webhook-based instead of continuous polling)
- **Always-on monitoring** without manual toggle configuration

## Database Monitoring

### Direct SQL Queries

**All-time summary by source:**
```bash
sqlite3 flex_complete_database.db "
SELECT
  source_file,
  method,
  COUNT(*) as calls,
  SUM(credits) as total_credits
FROM rpc_metrics
GROUP BY source_file, method
ORDER BY source_file, total_credits DESC;
"
```

**Last hour by method:**
```bash
sqlite3 flex_complete_database.db "
SELECT
  method,
  COUNT(*) as calls,
  SUM(credits) as total_credits
FROM rpc_metrics
WHERE recorded_at > datetime('now', '-1 hour')
GROUP BY method
ORDER BY total_credits DESC;
"
```

**Real-time activity (last 30 seconds):**
```bash
sqlite3 flex_complete_database.db "
SELECT
  strftime('%H:%M:%S', recorded_at) as time,
  method,
  COUNT(*) as calls,
  SUM(credits) as credits
FROM rpc_metrics
WHERE recorded_at > datetime('now', '-30 seconds')
GROUP BY time, method
ORDER BY recorded_at DESC;
"
```

**All calls in a time range:**
```bash
sqlite3 flex_complete_database.db "
SELECT
  recorded_at,
  method,
  source_file,
  credits
FROM rpc_metrics
WHERE recorded_at > datetime('2026-03-03 12:00:00')
  AND recorded_at < datetime('2026-03-03 12:05:00')
ORDER BY recorded_at;
"
```

## Output Files

All monitoring data saved to `rpc_monitor_logs/`:

- `rpc_snapshot_YYYYMMDD_HHMMSS.json` - Machine-readable metrics snapshot
- `rpc_snapshot_YYYYMMDD_HHMMSS.txt` - Human-readable summary
- `rpc_final_YYYYMMDD_HHMMSS.json` - Final snapshot when monitor stops
- `activity_YYYYMMDD_HHMMSS.log` - One-shot activity report

Example file structure:
```
rpc_monitor_logs/
├── rpc_snapshot_20260303_120015.json
├── rpc_snapshot_20260303_120015.txt
├── rpc_snapshot_20260303_120025.json
├── rpc_snapshot_20260303_120025.txt
├── activity_20260303_120530.log
└── rpc_final_20260303_120545.json
```

## Analyzing Results

### Look for these patterns:

**1. Steady background job activity:**
```
Every 1-2 seconds: getSignaturesForAddress + helius_enhanced_transactions_batch
Source: creator_outgoing_extractor
Indicates: Normal background scan operation
```

**2. Spikes from manual API calls:**
```
Sudden burst of helius_enhanced_transactions_batch
Source: main or unknown
Indicates: Someone clicked /api/scan-creator button
```

**3. Token launch activity:**
```
New sources appearing (not creator_outgoing_extractor)
Methods: getTransaction, getSignatures
Indicates: New tokens detected by listener (listen_to_launches=true)
```

### Compare snapshots to see changes:
```bash
# Diff JSON snapshots
diff rpc_monitor_logs/rpc_snapshot_20260303_120015.json rpc_monitor_logs/rpc_snapshot_20260303_120545.json

# Show which files were created/modified during monitoring
ls -lt rpc_monitor_logs/ | head -20
```

## Key Configuration Toggles

### listen_to_launches (Default: OFF)
```bash
curl -X POST http://localhost:5000/api/listener-settings/listen_to_launches/true
curl -X POST http://localhost:5000/api/listener-settings/listen_to_launches/false
```

## Troubleshooting

**Monitor shows 0 calls:**
- Listener not running? Check: `ps aux | grep pumpfun_curve_listener`
- No RPC activity yet? Wait a moment and check again
- Check database: `sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM rpc_metrics;"`

**`unknown` source_file appearing:**
- Some code path not properly recording source_file parameter
- Most common: API endpoints that import functions from other modules
- Check recent changes to identify which endpoint is making calls

**Metrics differ from Helius:**
- Local metrics count ALL attempts (including retries, failed calls)
- Helius bills only successful calls (HTTP 200)
- Compare using `status_code=200` filter:
  ```bash
  sqlite3 flex_complete_database.db "SELECT SUM(credits) FROM rpc_metrics WHERE status_code=200;"
  ```

**Database locked:**
- Close other database connections
- Monitor script will retry automatically
- Force timeout: `sqlite3 -cmd ".timeout 5000" ...`

## Integration with Dashboard

The `/rpc-metrics` dashboard page also tracks this data:
- Shows live credits from Helius API comparison
- Updates every 2 minutes
- Shows credits consumed by module
- Compares with Helius actual charges

Monitor scripts provide MORE detailed real-time view by:
- Checking every 10 seconds (vs dashboard's 2-minute snapshots)
- Showing full method-level breakdown
- Recording historical snapshots for analysis
- Working without browser interface

## Next Steps

After listener starts, you should:
1. Let monitor run for 30-60 minutes to capture initial scan
2. Check saved snapshots in `rpc_monitor_logs/`
3. Verify RPC calls match expected patterns
4. Compare with Helius actual charges
5. Turn toggles on/off and observe impact on RPC activity
