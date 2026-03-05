# Helius CLI-Based Usage Snapshots

**Status**: ✅ RECOMMENDED APPROACH
**Date**: 2026-03-02

---

## Overview

Captures Helius account usage periodically using the official Helius CLI tool.

**Advantages**:
- ✅ Official tool - always accurate
- ✅ No web scraping required
- ✅ Works with wallet/keypair authentication
- ✅ Easy to schedule with cron/systemd
- ✅ Historical snapshots in SQLite
- ✅ Combines with your RPC instrumentation for full reconciliation

---

## Quick Start

### 1. Install Helius CLI

```bash
npm install -g helius-cli
```

### 2. Authenticate Once

Use your Solana keypair (default location or custom):

```bash
# Default keypair location
helius login --keypair ~/.config/solana/id.json --json

# Or custom keypair
helius login --keypair /path/to/your/keypair.json --json
```

This authenticates your local CLI with your Helius account.

### 3. Test It Works

```bash
helius usage --json
```

Expected output:
```json
{
  "creditsRemaining": 975318,
  "creditsUsed": 24682,
  "creditsUsedMonth": 24682,
  "projectId": "your-project-id"
}
```

### 4. Capture Once

```bash
cd /path/to/flex
python helius_cli_monitor.py
```

Output:
```
[HELIUS] 📊 Capturing usage via CLI...
[HELIUS] ✅ Got usage data from CLI
================================================================================
[HELIUS] 📊 ACCOUNT USAGE (from CLI)
================================================================================
Credits Remaining:      975,318
Credits Used:            24,682
Credits Used Month:      24,682
Project ID:             your-project-id
Captured At:            2026-03-02T10:30:45.123456
================================================================================
[HELIUS] 💾 Recorded snapshot in database
[HELIUS] ✅ Done
```

### 5. Schedule Periodic Captures

#### Option A: Cron (every 5 minutes)

```bash
*/5 * * * * cd /path/to/flex && python helius_cli_monitor.py >> /var/log/helius_cli.log 2>&1
```

#### Option B: Every 15 minutes

```bash
*/15 * * * * cd /path/to/flex && python helius_cli_monitor.py >> /var/log/helius_cli.log 2>&1
```

#### Option C: Systemd timer (recommended for servers)

Create `/etc/systemd/system/helius-capture.service`:

```ini
[Unit]
Description=Helius Usage Snapshot Capture
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/flex
ExecStart=/usr/bin/python3 /path/to/flex/helius_cli_monitor.py
StandardOutput=journal
StandardError=journal
```

Create `/etc/systemd/system/helius-capture.timer`:

```ini
[Unit]
Description=Run Helius capture every 5 minutes
Requires=helius-capture.service

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now helius-capture.timer
```

Check status:
```bash
sudo systemctl status helius-capture.timer
sudo journalctl -u helius-capture.service -f
```

---

## API Endpoints

### Capture a Snapshot

```bash
curl -X POST http://localhost:8001/metrics/helius/capture
```

Response:
```json
{
  "status": "success",
  "message": "Helius usage snapshot captured",
  "snapshot": {
    "credits_remaining": 975318,
    "credits_used": 24682,
    "credits_used_month": 24682,
    "timestamp": "2026-03-02T10:30:45.123456"
  }
}
```

### Get Recent Snapshots

```bash
curl http://localhost:8001/metrics/helius/snapshots?limit=20
```

Response:
```json
{
  "status": "success",
  "count": 20,
  "snapshots": [
    {
      "timestamp": "2026-03-02T10:30:45.123456",
      "credits_remaining": 975318,
      "credits_used": 24682,
      "credits_used_month": 24682
    },
    ...
  ]
}
```

---

## Database Schema

### helius_usage_snapshots table

```sql
CREATE TABLE helius_usage_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  credits_remaining INTEGER,
  credits_used INTEGER,
  credits_used_month INTEGER,
  project_id TEXT,
  raw_json TEXT,
  captured_at TIMESTAMP
);
```

**Columns**:
- `id` - Unique snapshot ID
- `timestamp` - When inserted into DB
- `credits_remaining` - Current remaining credits
- `credits_used` - Credits used today (or period)
- `credits_used_month` - Credits used this month
- `project_id` - Your Helius project ID
- `raw_json` - Full JSON response from CLI
- `captured_at` - Timestamp from CLI

---

## Data Reconciliation

Your system now has **two data sources**:

### 1. RPC Instrumentation (Your Code)
- **Source**: `rpc_metrics_recorder.py`
- **What it tracks**: Credits consumed by YOUR RPC calls
- **Updates**: Real-time as RPC calls happen
- **Example**: "My scan used 5,000 credits today"

### 2. Helius CLI Snapshots (Actual Account)
- **Source**: `helius_cli_monitor.py`
- **What it tracks**: Actual account balance from Helius
- **Updates**: Every 5-15 minutes (configurable)
- **Example**: "Account has 975,318 credits remaining"

### Reconciliation

Compare the two:

```bash
# Get your instrumented metrics
curl http://localhost:8001/metrics/rpc/summary | jq '.summary.credits_instrumented_today'
# Output: 24682

# Get Helius account snapshot
curl http://localhost:8001/metrics/helius/snapshots?limit=1 | jq '.snapshots[0].credits_used'
# Output: 24682

# Perfect match = all your RPC calls are accounted for
# Discrepancy = uninstrumented calls or delays in reporting
```

---

## Troubleshooting

### Error: "Not logged in"

The CLI hasn't been authenticated yet.

**Fix**:
```bash
helius login --keypair ~/.config/solana/id.json --json
```

### Error: "No random values implementation could be found"

Node.js version issue (needs >= 18, ideally >= 20).

**Check**:
```bash
node --version
```

**Fix** (if needed):
```bash
npm install -g n
n lts  # Install latest LTS
```

### Error: "Command not found: helius"

CLI not installed.

**Fix**:
```bash
npm install -g helius-cli
which helius  # Verify it's in PATH
```

### Usage command returns empty or error

Your Helius account may not be associated with the keypair used.

**Fix**:
1. Verify you logged in with the right keypair
2. Check your Helius account in browser to confirm it exists
3. Try logging out and back in:
   ```bash
   helius logout  # (if command exists)
   helius login --keypair ~/.config/solana/id.json --json
   ```

### Cron job not running

Check cron logs:
```bash
log stream --predicate 'eventMessage contains[cd] "helius"'
# On Linux: grep CRON /var/log/syslog | grep helius
```

Verify the path is absolute:
```bash
# ❌ WRONG
* * * * * cd ~/flex && python helius_cli_monitor.py

# ✅ CORRECT
* * * * * cd /Users/kevinkeaveney/Dev/claude/flex && python helius_cli_monitor.py
```

---

## Usage Patterns

### Daily Reconciliation Report

```python
from helius_cli_monitor import get_snapshot_history
from rpc_metrics_recorder import get_recorder

def daily_report():
    # Get Helius snapshots for today
    snapshots = get_snapshot_history(limit=100)

    # Get our instrumented metrics
    recorder = get_recorder()

    print(f"Today's Usage Summary:")
    print(f"  Helius reports: {snapshots[0]['credits_used']} credits used")
    print(f"  We instrumented: {recorder._daily_credits} credits")
    print(f"  Discrepancy: {snapshots[0]['credits_used'] - recorder._daily_credits}")
```

### Budget Alert

```python
def check_budget():
    snapshot = get_latest_snapshot()
    remaining = snapshot['credits_remaining']

    if remaining < 50000:
        print(f"⚠️ BUDGET ALERT: Only {remaining:,} credits remaining!")
    elif remaining < 100000:
        print(f"⚠️ LOW BUDGET: {remaining:,} credits remaining")
```

### Usage Trend

```python
def usage_trend():
    snapshots = get_snapshot_history(limit=12)  # Last 12 snapshots

    for snap in snapshots:
        ts = snap['timestamp']
        used = snap['credits_used']
        remaining = snap['credits_remaining']
        print(f"{ts}: Used {used:,} | Remaining {remaining:,}")
```

---

## Files

### Core Files
- `helius_cli_monitor.py` - Main CLI monitor + database functions
- `rpc_metrics_api.py` - API endpoints for captures + snapshots

### Database
- `flex_complete_database.db` - SQLite with helius_usage_snapshots table

### Cron/Systemd
- `/var/log/helius_cli.log` - Capture logs (if using cron)
- `/etc/systemd/system/helius-capture.*` - Systemd timer (if using systemd)

---

## Summary

✅ **Setup**: `helius login` (one-time, ~1 minute)
✅ **Monitoring**: `python helius_cli_monitor.py` (on-demand or scheduled)
✅ **Storage**: SQLite helius_usage_snapshots table (automatic)
✅ **API**: POST /metrics/helius/capture, GET /metrics/helius/snapshots
✅ **Reconciliation**: Compare CLI snapshots vs RPC instrumentation
✅ **Scheduling**: Cron or systemd timer (every 5-15 minutes recommended)

**Production Ready**: YES

The system now has complete visibility into both where credits are going (your RPC calls) and what Helius is billing (account snapshots).

---

## Next Steps

1. ✅ Install helius-cli: `npm install -g helius-cli`
2. ✅ Authenticate: `helius login --keypair ~/.config/solana/id.json --json`
3. ✅ Test: `helius usage --json`
4. ✅ Capture once: `python helius_cli_monitor.py`
5. ✅ Schedule with cron or systemd timer
6. ✅ Monitor via API endpoints

That's it!
