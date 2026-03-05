# Helius API Authentication & Usage Monitoring

**Status**: ✅ **WORKING**
**Date**: 2026-03-02
**Approach**: REST API Key + Manual Usage Updates

---

## Overview

Your Helius setup uses:
- **API Key**: `f084fae8-d111-4337-9960-2d9c5e02a726` (for RPC calls)
- **Project ID**: `b5b55487-ccfb-43f8-a2fb-766fbb68f8ce`

The API key authenticates your RPC requests. However, **Helius doesn't expose an API endpoint for account usage metrics**, so you need to manually record usage from your dashboard.

---

## Setup Complete ✅

### What's Working

✅ Your API key is validated and working
✅ RPC calls are authenticated properly
✅ Usage snapshots stored in SQLite
✅ Two monitoring scripts available

### What Was Attempted

❌ Helius CLI auth - requires matching wallet (different account)
❌ REST API usage endpoint - doesn't exist publicly
❌ GraphQL endpoint - not available

---

## Two Scripts

### 1. Credential Validator: `helius_api_monitor.py`

Validates your API key is working and checks Helius connectivity.

**Run anytime to validate:**
```bash
python helius_api_monitor.py
```

**Output:**
```
[HELIUS] ✅ API key validated
[HELIUS] ⚠️ API credentials validated, but usage data requires manual update
Project ID:         b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
Dashboard URL:      https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
```

**Test mode (with mock data):**
```bash
python helius_api_monitor.py --test
```

### 2. Manual Usage Updater: `helius_update_usage.py`

Records usage snapshots when you check your dashboard.

**Interactive mode:**
```bash
python helius_update_usage.py
```

Opens an interactive prompt:
```
HELIUS USAGE MANUAL UPDATE
================================================================================
Visit: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce

Enter the values from your Helius dashboard:

Credits Remaining: 975318
Credits Used (today/period): 24682
Credits Used (this month): 24682
```

**Command line mode:**
```bash
python helius_update_usage.py --remaining 975318 --used 24682 --month 24682
```

**Show latest snapshot:**
```bash
python helius_update_usage.py --show
```

---

## Workflow

### Daily Monitoring

1. **Check RPC calls are working**
   ```bash
   python helius_api_monitor.py
   ```
   Should show: "✅ API key validated"

2. **Periodically check dashboard**
   - Visit: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
   - Note the remaining credits

3. **Update local snapshots**
   ```bash
   python helius_update_usage.py --remaining 975318 --used 24682 --month 24682
   ```

### Recommended Schedule

- **Validation**: Daily (automated with cron)
- **Dashboard check**: Weekly or bi-weekly (manual)
- **Usage updates**: Weekly after checking dashboard

---

## Setup Cron Jobs

### Option A: Daily validation (automated)

```bash
0 9 * * * cd /Users/kevinkeaveney/Dev/claude/flex && python helius_api_monitor.py >> /var/log/helius_validation.log 2>&1
```

### Option B: Weekly with reminder

```bash
0 9 * * 1 cd /Users/kevinkeaveney/Dev/claude/flex && python helius_api_monitor.py && echo "Don't forget to check dashboard and run: python helius_update_usage.py" >> /var/log/helius_weekly.log 2>&1
```

### Add to crontab

```bash
crontab -e
# Paste one of the above lines
# Save and exit
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

**Columns:**
- `timestamp` - When inserted into DB
- `credits_remaining` - Current account balance
- `credits_used` - Credits consumed in period
- `credits_used_month` - Monthly total
- `project_id` - Your project ID
- `captured_at` - When data was captured

---

## API Endpoints (in main.py)

If you've integrated these with your Flask app:

```python
# GET /metrics/helius/snapshots - Get recent snapshots
curl http://localhost:8001/metrics/helius/snapshots?limit=20

# Response:
{
  "status": "success",
  "count": 3,
  "snapshots": [
    {
      "timestamp": "2026-03-02T11:37:40",
      "credits_remaining": 975318,
      "credits_used": 24682,
      "credits_used_month": 24682
    },
    ...
  ]
}
```

---

## Troubleshooting

### "API key validated" but no usage data?

This is normal! Helius doesn't expose usage via their public API. You need to:
1. Check the dashboard manually
2. Run `helius_update_usage.py` with the values you see

### API key validation fails?

Check:
```bash
# Verify environment variables
echo $HELIUS_API_KEY
echo $HELIUS_PROJECT_ID

# Or in Python
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('HELIUS_API_KEY'))"
```

### Database is locked?

If you get "database is locked" errors:
```bash
# Check for existing processes
lsof flex_complete_database.db

# Kill if needed
pkill -f helius_api_monitor.py
pkill -f helius_update_usage.py
```

---

## Summary

✅ **API Key Authentication**: Working
✅ **RPC Calls**: Fully functional
✅ **Usage Tracking**: Manual + automatic validation
✅ **Database Storage**: SQLite snapshots
✅ **Scheduling**: Cron-ready

**To get started:**
1. Run: `python helius_api_monitor.py` (validates API)
2. Check dashboard: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce
3. Record usage: `python helius_update_usage.py --remaining XXX --used YYY --month ZZZ`
4. Schedule daily validation with cron

---

## Files

| File | Purpose |
|------|---------|
| `helius_api_monitor.py` | Validates API key, checks connectivity |
| `helius_update_usage.py` | Records usage snapshots (interactive or CLI) |
| `flex_complete_database.db` | SQLite with helius_usage_snapshots table |
| `.env` | Contains HELIUS_API_KEY and HELIUS_PROJECT_ID |

---

## Next Steps

1. ✅ Validate API key: `python helius_api_monitor.py`
2. ✅ Check dashboard for current usage
3. ✅ Record snapshot: `python helius_update_usage.py`
4. ✅ Setup cron for daily validation
5. ✅ Optional: Integrate with Flask API endpoints

That's it! Your Helius authentication is now properly configured.
