# Helius Live Dashboard Sync – Real Account Usage

**Status**: ✅ NEW FEATURE
**Date**: 2026-03-02

---

## Overview

Automatically fetch **real, live account usage** from your Helius dashboard and sync it with FLEX metrics.

This uses web scraping to read actual credits from your Helius account in real-time.

---

## Quick Start

### 1. Install Requirements

```bash
pip install selenium webdriver-manager
```

### 2. Set Credentials (Option A - Environment Variables)

```bash
export HELIUS_EMAIL="your-email@example.com"
export HELIUS_PASSWORD="your-password"
```

### 3. Fetch Live Usage

```bash
# Automatic (uses env vars)
python helius_account_monitor.py

# Manual (pass credentials)
python helius_dashboard_scraper.py your-email@example.com your-password

# Show browser (debug)
python helius_dashboard_scraper.py your-email@example.com your-password --no-headless
```

### 4. Verify It Works

```bash
# Check what was fetched
python helius_account_monitor.py

# View via API
curl http://localhost:8001/metrics/helius | jq '.helius_account'

# Check updated config
python3 -c "from rpc_metrics_config import PlanConfig; print(PlanConfig.CURRENT_USAGE)"
```

---

## How It Works

**Flow**:

```
helius_account_monitor.py
  ↓
  get_account_usage()
    ↓
    Try Dashboard (if credentials set)
      ↓
      helius_dashboard_scraper.py
        ↓
        Selenium Chrome Driver
          ↓
          1. Navigate to https://dashboard.helius.dev/login
          2. Enter email + password
          3. Wait for dashboard to load
          4. Parse HTML for usage values
          5. Extract: credits_used, credits_remaining, monthly_budget
          6. Return to monitor
    ↓
    Fall back to config if dashboard unavailable
    ↓
    Add metrics (burn rate from our instrumentation)
    ↓
    Return complete usage dict
```

**Data Flow**:

```
Live Dashboard
    ↓
    Web Scraper (Selenium)
    ↓
    helius_account_monitor.py
    ↓
    ├─ Print to console
    ├─ Store in SQLite history
    ├─ Update rpc_metrics_config.py
    └─ API endpoint (/metrics/helius)
        ↓
        Dashboard (/rpc-metrics)
```

---

## File Structure

### helius_dashboard_scraper.py

Standalone tool to scrape Helius dashboard.

**Functions**:
- `scrape_helius_dashboard(email, password, headless=True)` – Fetch live usage
- `parse_dashboard_page(html)` – Parse HTML for credit values
- `record_account_usage(usage)` – Store in SQLite
- `update_config_with_usage(usage)` – Update config file

**Usage**:
```bash
python helius_dashboard_scraper.py email@example.com password123
```

### helius_account_monitor.py

Enhanced to try dashboard first, then fall back to config.

**New function**:
- `get_account_usage_from_dashboard()` – Try scraper first

**Order of operations**:
1. Check if HELIUS_EMAIL + HELIUS_PASSWORD set
2. If yes → try Selenium scraper
3. If scraper works → return live usage + update config
4. If scraper fails → fall back to config (no-op)
5. Add burn rate from metrics recorder
6. Return complete usage dict

---

## Usage Scenarios

### Scenario 1: One-Time Check

```bash
# Just want to check current balance?
python helius_dashboard_scraper.py your-email your-password
```

Output:
```
================================================================================
[HELIUS] 📊 ACCOUNT USAGE (from Dashboard)
================================================================================

Credits Used:           24,682
Credits Remaining:     975,318
Monthly Budget:      1,000,000
API Calls:               3,434
Usage:                    2.5%
Source:             dashboard
Timestamp:          2026-03-02T10:30:45.123456
================================================================================

[HELIUS] ✅ Updated rpc_metrics_config.py
```

### Scenario 2: Automated Sync (Cron)

```bash
# Every 6 hours, sync latest balance
0 */6 * * * export HELIUS_EMAIL="your@email.com" HELIUS_PASSWORD="pass123" && cd /path/to/flex && python helius_account_monitor.py >> /var/log/helius_sync.log 2>&1
```

### Scenario 3: Dashboard Display

Once synced, dashboard shows actual balance:

```
http://localhost:5002/rpc-metrics

Total Credits Today:     24,682  ← From Helius dashboard
Credits Used (Since):        0  ← From our instrumentation
Monthly Remaining:     975,318  ← From Helius dashboard
```

### Scenario 4: API Access

```bash
# Get live status (includes dashboard data if synced)
curl http://localhost:8001/metrics/helius | jq '.'

# Check only account info
curl http://localhost:8001/metrics/helius | jq '.helius_account'
```

---

## Configuration

### Environment Variables

```bash
# Required for dashboard scraping
export HELIUS_EMAIL="your-email@example.com"
export HELIUS_PASSWORD="your-password"

# Optional
export DB_PATH="/path/to/flex_complete_database.db"
```

### .env File (Alternative)

Create `.env` file in project root:

```
HELIUS_EMAIL=your-email@example.com
HELIUS_PASSWORD=your-password
```

Load before running:
```bash
source .env
python helius_account_monitor.py
```

### Secure Storage (Recommended)

Use password manager instead of plain text:

```bash
# macOS Keychain
security find-generic-password -w -s "helius-email" -a "helius"
security find-generic-password -w -s "helius-password" -a "helius"

# Or use pass manager
eval $(pass show helius | grep "^export")
```

---

## Troubleshooting

### Issue: "Selenium not installed"

**Fix**:
```bash
pip install selenium webdriver-manager
```

### Issue: "Chrome driver not found"

**Fix**: `webdriver_manager` will auto-download, but you can also:
```bash
pip install webdriver-manager
```

### Issue: Login fails (Wrong credentials)

**Check**:
- Email is correct (case-sensitive)
- Password is correct
- Account is not locked
- 2FA is not enabled (disable temporarily for scraping)

**Fix**:
```bash
# Test with --show to see browser
python helius_dashboard_scraper.py your-email your-password --show
```

### Issue: Scraper times out

**Possible causes**:
- Network issue
- Helius dashboard slow
- Too many simultaneous scrapes

**Fix**:
- Try again in a few seconds
- Check network connectivity
- Helius may rate-limit scrapers (use sparingly)

### Issue: Parser can't extract values

**Debug**:
```python
# Check what HTML we're seeing
from helius_dashboard_scraper import scrape_helius_dashboard, parse_dashboard_page
import requests

# Get page HTML manually
r = requests.get("https://dashboard.helius.dev/login")
html = r.text

# Try to parse
result = parse_dashboard_page(html)
print(result)
```

---

## Important Notes

### Authentication

⚠️ **Security**: Never hardcode credentials in code. Use environment variables or secure vaults.

```bash
# ❌ DON'T do this
HELIUS_EMAIL="my@email.com" python helius_account_monitor.py

# ✅ DO this
export HELIUS_EMAIL="my@email.com"
export HELIUS_PASSWORD="mypass"
python helius_account_monitor.py
```

### 2FA / Multi-Factor Auth

If your Helius account has 2FA enabled:
1. Temporarily disable 2FA
2. Run scraper
3. Re-enable 2FA

Or manually sync and update config:
```bash
python helius_dashboard_scraper.py  # Will fail with 2FA
# Manually enter credentials in dashboard
# Then manually update rpc_metrics_config.py:
#   CURRENT_USAGE["credits_used_today"] = 24682
#   CURRENT_USAGE["credits_remaining"] = 975318
```

### Rate Limiting

Helius may rate-limit excessive scraping. Use sparingly:
- ✅ Every 6 hours (4x daily)
- ✅ Every 12 hours (2x daily)
- ❌ Every minute (will likely block)

---

## Flow Diagram

```
┌─ HELIUS_EMAIL set?
│  └─ YES: Try dashboard scraper
│     ├─ Login to https://dashboard.helius.dev
│     ├─ Parse HTML
│     ├─ Extract credits (live data)
│     ├─ Update config file
│     └─ Return with source="dashboard"
│
└─ Dashboard unavailable / creds not set
   └─ Use config fallback
      ├─ Read rpc_metrics_config.py
      ├─ Get credits_used_today
      ├─ Get credits_remaining
      └─ Return with source="config"

Both paths:
  ├─ Add burn_rate from metrics recorder
  ├─ Calculate estimated_daily_burn
  └─ Store in SQLite history
```

---

## Real Usage Example

**Without Dashboard Sync**:
```bash
$ python helius_account_monitor.py

[HELIUS] ❌ Config error: ...
Helius Account:
  Credits Used: 0
  Source: config
```

**With Dashboard Sync**:
```bash
$ export HELIUS_EMAIL="your@email.com"
$ export HELIUS_PASSWORD="yourpass"
$ python helius_account_monitor.py

[HELIUS] 🌐 Fetching from dashboard...
[HELIUS] ✅ Got live data from dashboard
[HELIUS] ✅ Updated rpc_metrics_config.py

Helius Account:
  Credits Used: 24,682
  Credits Remaining: 975,318
  Monthly Budget: 1,000,000
  Usage: 2.5%
  Source: dashboard
```

---

## Summary

✅ **Live dashboard integration** – Get real account usage
✅ **Auto-sync to config** – Updates rpc_metrics_config.py
✅ **Dashboard display** – Shows actual vs instrumented metrics
✅ **Fallback mode** – Works without credentials (uses config)
✅ **Secure** – Uses environment variables, not hardcoded

**Setup time**: 2 minutes
**Update frequency**: Manual (or cron for automation)
**Accuracy**: Live (from Helius dashboard)

---

## Commands Quick Reference

```bash
# Install dependencies
pip install selenium webdriver-manager

# Set environment variables
export HELIUS_EMAIL="your@email.com"
export HELIUS_PASSWORD="password"

# Fetch live usage (one-time)
python helius_dashboard_scraper.py your@email.com password

# Check via monitor (auto-uses env vars)
python helius_account_monitor.py

# Check via API
curl http://localhost:8001/metrics/helius | jq '.helius_account'

# Schedule syncing (every 6 hours)
0 */6 * * * cd /path/to/flex && export HELIUS_EMAIL="..." HELIUS_PASSWORD="..." && python helius_account_monitor.py
```
