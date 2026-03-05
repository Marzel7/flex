# Creator Queue Monitor - UI Integration

**Status**: ✅ Implemented
**Date**: 2026-03-03
**Location**: Webhook Monitor Dashboard (`/webhook-monitor`)

---

## What It Does

The Webhook Monitor page now includes a **Creator Queue Status** section that shows:
1. Real-time queue metrics
2. Top 10 highest-priority creators
3. Processing status indicators
4. Auto-refresh every 5 seconds

---

## Queue Metrics (4 Cards)

### 1. Total in Queue
- **Shows**: Number of creators in work_queue
- **Meaning**: How many addresses are awaiting processing
- **Example**: "42" = 42 creators in queue

### 2. Critical Priority
- **Shows**: Count of creators with priority ≥ 80
- **Meaning**: High-risk creators needing immediate attention
- **Status Badges**:
  - 🔴 **Active** - If count > 0 (red)
  - ⚪ **None** - If count = 0 (gray)
- **Use Case**: Identify urgent processing bottlenecks

### 3. Currently Processing
- **Shows**: Number of creators locked by worker
- **Meaning**: Creators being processed right now
- **Lock Duration**: Up to 120 seconds per creator
- **Example**: "2" = 2 creators being processed

### 4. Never Checked
- **Shows**: Count of creators with attempts = 0
- **Meaning**: Queued but not yet processed
- **Use Case**: Identify queue backlog
- **Watchpoint**: If this stays high, worker may be slow

---

## Top Priority Creators Table

Shows the 10 highest-priority creators with these columns:

| Column | Meaning | Example |
|--------|---------|---------|
| **Creator Address** | Wallet address (truncated) | `5Zpgww...` |
| **Priority** | Risk score (0-100+) | `82.5` |
| **Status** | Processing state | `✅ READY` |
| **Attempts** | Times processed | `3` |
| **Reason** | Why queued | `new_transfer` |

### Status Indicators

**Color-Coded by State**:
- 🔒 **PROCESSING** (Amber) - Currently being processed, locked
- ✅ **READY** (Green) - Eligible now, waiting for worker pickup
- ⚪ **WAITING** (Gray) - Not eligible yet (next_run_at in future)

**Color-Coded by Priority**:
- 🔴 Red - Critical (priority ≥ 80)
- 🟠 Amber - Elevated (priority 60-79)
- 🟢 Green - Moderate/Low (priority < 60)

---

## How to Use

### Access the Dashboard

1. Go to main dashboard: `http://localhost:5002`
2. Click **[📡 Webhook]** button
3. Scroll down to **Creator Queue Status**

### Monitor Queue Health

**Check overall queue**:
- Total in Queue = 0? Queue is empty (good for startup)
- Total in Queue > 100? May need to investigate backlog

**Check processing rate**:
- Never Checked > 50%? Workers may be slow
- Currently Processing = 0? Workers idle or queue empty

**Check priority distribution**:
- Critical Priority = 0? No urgent creators
- Critical Priority > 10? Potential risk spike

### Real-Time Updates

- Auto-refreshes every 5 seconds (same as transfers)
- Click **🔄 Refresh Now** to force immediate update
- No page reload needed

---

## Data Source

The queue monitor queries the `work_queue` table:

```sql
-- Get overview stats
SELECT COUNT(*) FROM work_queue                          -- Total
SELECT COUNT(*) FROM work_queue WHERE priority >= 80    -- Critical
SELECT COUNT(*) FROM work_queue WHERE locked_until > ?  -- Processing
SELECT COUNT(*) FROM work_queue WHERE attempts = 0      -- Never Checked

-- Get top creators
SELECT address, priority, status, attempts, reason
FROM work_queue
ORDER BY priority DESC
LIMIT 10
```

---

## API Endpoint

**Endpoint**: `GET /api/creator-queue-status`

**Response**:
```json
{
  "ok": true,
  "total_in_queue": 42,
  "critical_count": 5,
  "currently_processing": 2,
  "never_checked": 3,
  "top_creators": [
    {
      "address": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
      "priority": 82.5,
      "status": "READY",
      "attempts": 3,
      "reason": "new_transfer",
      "locked_until": 1772552910,
      "next_run_at": 1772552911
    },
    ...
  ]
}
```

**Use Cases**:
- Programmatic monitoring
- Integration with other tools
- Custom dashboards
- Alerting systems

---

## Monitoring Workflow

### Every 5 seconds (auto-refresh)
1. Metrics update with current queue state
2. Top 10 creators refresh
3. Status badges update

### Every minute (manual check)
1. Look for spike in "Never Checked" count
2. Check if any critical creators appear
3. Verify "Currently Processing" is not stuck

### Every hour (analysis)
1. Review Critical Priority trend
2. Check processing rate (attempts increasing?)
3. Identify stuck creators (same address, stale attempts)

---

## Troubleshooting

### Queue is empty
**Scenario**: Total in Queue = 0
**Cause**: No webhooks received yet (normal at startup)
**Solution**: Send webhooks to `/helius/webhook` endpoint

### All creators say "WAITING"
**Scenario**: No "READY" creators
**Cause**: next_run_at all in future (normal after requeue)
**Solution**: Wait for requeue delay to expire

### High "Never Checked" count
**Scenario**: Never Checked = 30+
**Cause**: Creators queued but worker not processing
**Solution**:
1. Check worker logs
2. Verify `webhook_worker.py` thread is running
3. Check database locks

### Priority not increasing
**Scenario**: Same creator, same priority for hours
**Cause**: Activity not changing OR cooldown penalty active
**Solution**:
1. Check activity: `SELECT tx_1h FROM address_activity WHERE address = ?`
2. Check cooldown: `SELECT last_processed_at FROM address_activity WHERE address = ?`

---

## Performance Notes

- **Query Speed**: <10ms (indexed on priority, next_run_at)
- **Refresh Frequency**: Every 5 seconds
- **Top Creators Limit**: 10 (shown in table)
- **Database Impact**: Minimal (simple COUNT queries)

---

## Integration with Webhook System

The creator queue is part of the 3-stage webhook pipeline:

```
1. INGESTION (Webhook Handler)
   ↓
   Transfers arrive → Store in sol_transfers
   ↓
   Enqueue creators to work_queue (priority = 50.0)

2. PROCESSING (Webhook Worker)
   ↓
   [Queue Monitor Shows Status Here] ← YOU ARE HERE
   ↓
   Worker picks highest priority → Computes risk score
   ↓
   Requeues with adaptive delay based on priority

3. SERVING (API)
   ↓
   /api/creator-recent-checks/enriched returns scored creators
```

The queue monitor shows what's in **Step 2** in real-time.

---

## Code Locations

| Component | File | Lines |
|-----------|------|-------|
| API endpoint | main.py | 18230-18305 |
| UI metrics section | main.py | 18593-18627 |
| Queue table section | main.py | 18629-18635 |
| loadQueueStatus() function | main.py | 18722-18793 |

---

## Future Enhancements (Optional)

1. **Priority Distribution Chart** - Visual breakdown of priority levels
2. **Processing Rate Graph** - Attempts over time
3. **Queue Depth Trend** - Total creators trend
4. **Filter by Status** - Show only READY, PROCESSING, etc.
5. **Detailed Creator Page** - Click address → full details

---

## Summary

✅ **What You Get**:
- Real-time queue metrics (4 cards)
- Top 10 priority creators table
- Color-coded status indicators
- Auto-refresh every 5 seconds
- API endpoint for integration

✅ **Monitor**:
- How many creators in queue
- How many are critical priority
- How many are being processed
- How many haven't been checked yet
- Which creators are next

✅ **Located At**:
- Main dashboard: Click **[📡 Webhook]** button
- URL: `http://localhost:5002/webhook-monitor`
- Scroll down to **Creator Queue Status**

✅ **API Access**:
- `curl http://localhost:5002/api/creator-queue-status | jq`

---

*Implemented: 2026-03-03*
*Claude Code*
