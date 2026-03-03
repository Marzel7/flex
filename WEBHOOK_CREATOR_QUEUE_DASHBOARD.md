# Creator Queue Status Dashboard

**Purpose**: Monitor which creators have been queued, when they were last checked, and what their status is.

**Data Source**:
- `work_queue` table - What's queued for processing
- `address_activity` table - When creators were last processed

---

## Quick Status Query

### Check Queue Overview
```bash
sqlite3 flex_complete_database.db << 'SQL'
SELECT
    COUNT(*) as total_in_queue,
    COUNT(CASE WHEN priority >= 80 THEN 1 END) as critical,
    COUNT(CASE WHEN priority >= 60 AND priority < 80 THEN 1 END) as elevated,
    COUNT(CASE WHEN priority >= 40 AND priority < 60 THEN 1 END) as moderate,
    COUNT(CASE WHEN priority < 40 THEN 1 END) as low
FROM work_queue;
SQL
```

---

## Creator Queue Schema

### work_queue Table
```sql
CREATE TABLE work_queue (
    address TEXT PRIMARY KEY,
    priority REAL,           -- Computed score (higher = process first)
    reason TEXT,             -- Why queued: "new_transfer", "high_activity", etc.
    next_run_at INTEGER,     -- Unix timestamp, eligible for processing after this
    locked_until INTEGER,    -- Prevents concurrent processing
    attempts INTEGER,        -- How many times processed
    updated_at TIMESTAMP     -- When row was last updated
);
```

### address_activity Table
```sql
CREATE TABLE address_activity (
    address TEXT PRIMARY KEY,
    last_seen_at INTEGER,        -- Last transfer timestamp
    tx_5m, tx_1h, tx_24h,        -- Transaction counts
    sol_in_5m, sol_in_1h, sol_in_24h,    -- SOL received
    sol_out_5m, sol_out_1h, sol_out_24h, -- SOL sent
    last_processed_at INTEGER,   -- When worker last processed this creator
    last_rpc_fetch_at INTEGER,   -- When last RPC call made
    updated_at TIMESTAMP
);
```

---

## Useful Queries

### 1. All Creators in Queue (What Needs Processing)

```sql
SELECT
    SUBSTR(address, 1, 8) || '...' as creator,
    ROUND(priority, 1) as priority,
    reason,
    CASE
        WHEN locked_until > strftime('%s', 'now') THEN 'PROCESSING'
        WHEN next_run_at <= strftime('%s', 'now') THEN 'READY'
        ELSE 'WAITING'
    END as status,
    attempts,
    updated_at
FROM work_queue
ORDER BY priority DESC;
```

**Shows**:
- Creator address (abbreviated)
- Priority score
- Why queued
- Current status (PROCESSING/READY/WAITING)
- How many times processed
- Last update time

---

### 2. High Priority Creators (Due Soon)

```sql
SELECT
    SUBSTR(address, 1, 8) || '...' as creator,
    ROUND(priority, 1) as priority,
    reason,
    datetime(next_run_at, 'unixepoch') as eligible_at,
    datetime('now') as current_time
FROM work_queue
WHERE next_run_at <= strftime('%s', 'now')
ORDER BY priority DESC
LIMIT 20;
```

**Shows**:
- Creators that are READY to process
- When they became eligible
- Top 20 by priority

---

### 3. Recently Processed Creators (Check History)

```sql
SELECT
    SUBSTR(address, 1, 8) || '...' as creator,
    ROUND(priority, 1) as priority,
    attempts as times_checked,
    tx_1h,
    sol_in_1h,
    sol_out_1h,
    CASE
        WHEN last_processed_at > strftime('%s', 'now') - 300 THEN '< 5m'
        WHEN last_processed_at > strftime('%s', 'now') - 3600 THEN '< 1h'
        WHEN last_processed_at > strftime('%s', 'now') - 86400 THEN '< 24h'
        ELSE '> 24h'
    END as last_checked,
    datetime(last_processed_at, 'unixepoch') as last_check_time
FROM work_queue
WHERE attempts > 0
ORDER BY last_processed_at DESC
LIMIT 20;
```

**Shows**:
- Creators already processed
- How many times checked
- Recent activity (transactions, SOL)
- When last checked
- Time since last check

---

### 4. Currently Locked (Being Processed Right Now)

```sql
SELECT
    SUBSTR(address, 1, 8) || '...' as creator,
    ROUND(priority, 1) as priority,
    reason,
    datetime(locked_until, 'unixepoch') as lock_expires_at,
    CAST((locked_until - strftime('%s', 'now')) AS INTEGER) as seconds_remaining
FROM work_queue
WHERE locked_until > strftime('%s', 'now')
ORDER BY locked_until ASC;
```

**Shows**:
- Creators currently being processed
- When the lock expires
- Seconds remaining in lock

---

### 5. Priority Distribution

```sql
SELECT
    CASE
        WHEN priority >= 80 THEN 'CRITICAL (>=80)'
        WHEN priority >= 60 THEN 'ELEVATED (60-79)'
        WHEN priority >= 40 THEN 'MODERATE (40-59)'
        ELSE 'LOW (<40)'
    END as priority_level,
    COUNT(*) as count,
    ROUND(AVG(priority), 1) as avg_priority,
    ROUND(AVG(attempts), 1) as avg_checks
FROM work_queue
GROUP BY priority_level
ORDER BY AVG(priority) DESC;
```

**Shows**:
- How many creators in each priority tier
- Average priority in each tier
- Average number of times checked

---

### 6. Which Creators Haven't Been Checked Yet?

```sql
SELECT
    SUBSTR(address, 1, 8) || '...' as creator,
    ROUND(priority, 1) as priority,
    reason,
    attempts,
    updated_at
FROM work_queue
WHERE attempts = 0
ORDER BY priority DESC;
```

**Shows**:
- Creators queued but never processed
- Why they were queued
- Their priority

---

### 7. Activity in Last Hour

```sql
SELECT
    SUBSTR(address, 1, 8) || '...' as creator,
    tx_1h as transfers_1h,
    ROUND(sol_in_1h, 4) as sol_in,
    ROUND(sol_out_1h, 4) as sol_out,
    ROUND(sol_in_1h + sol_out_1h, 4) as total_volume,
    ROUND(priority, 1) as priority
FROM work_queue wq
JOIN address_activity aa ON wq.address = aa.address
WHERE tx_1h > 0
ORDER BY (sol_in_1h + sol_out_1h) DESC
LIMIT 20;
```

**Shows**:
- Most active creators in last hour
- Transaction counts
- SOL volumes
- Priority score

---

## Python Script: Creator Queue Monitor

### Check Queue Status Programmatically

```python
import sqlite3
import time
from datetime import datetime

DB_PATH = "flex_complete_database.db"

def get_queue_status():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Overall stats
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN priority >= 80 THEN 1 END) as critical,
            COUNT(CASE WHEN locked_until > ? THEN 1 END) as processing
        FROM work_queue
    """, (int(time.time()),))

    total, critical, processing = cursor.fetchone()

    print(f"""
    ╔═══════════════════════════════════════╗
    ║  CREATOR QUEUE STATUS                 ║
    ╠═══════════════════════════════════════╣
    ║  Total in Queue:    {total:>22} ║
    ║  Critical Priority: {critical:>22} ║
    ║  Currently Processing: {processing:>15} ║
    ╚═══════════════════════════════════════╝
    """)

    # Top priority creators
    print("\nTOP 10 HIGHEST PRIORITY:\n")
    cursor.execute("""
        SELECT
            SUBSTR(address, 1, 12),
            ROUND(priority, 1),
            reason,
            attempts
        FROM work_queue
        ORDER BY priority DESC
        LIMIT 10
    """)

    for address, priority, reason, attempts in cursor.fetchall():
        print(f"  {address}  |  Priority: {priority:>5.1f}  |  {reason:>20}  |  Attempts: {attempts}")

    # Recent checks
    print("\n\nRECENTLY PROCESSED:\n")
    cursor.execute("""
        SELECT
            SUBSTR(address, 1, 12),
            attempts,
            datetime(last_processed_at, 'unixepoch')
        FROM work_queue
        WHERE last_processed_at IS NOT NULL
        ORDER BY last_processed_at DESC
        LIMIT 10
    """)

    for address, attempts, last_check in cursor.fetchall():
        print(f"  {address}  |  Times checked: {attempts:>2}  |  {last_check}")

    conn.close()

if __name__ == "__main__":
    get_queue_status()
```

**Run**:
```bash
python3 queue_monitor.py
```

---

## Dashboard Metrics

### Queue Health Indicators

| Metric | Query | Meaning |
|--------|-------|---------|
| Total in Queue | `COUNT(*) FROM work_queue` | How many creators need processing |
| Critical Priority | `COUNT(*) WHERE priority >= 80` | High-risk creators |
| Currently Processing | `COUNT(*) WHERE locked_until > now` | Being processed right now |
| Never Checked | `COUNT(*) WHERE attempts = 0` | Queued but not yet processed |
| Active in 1h | `COUNT(*) WHERE tx_1h > 0` | Recently active |

---

## Monitoring Workflow

### Every 5 seconds (auto-refresh)
1. Query `work_queue` for current priority distribution
2. Show top 10 high-priority creators
3. Count currently processing

### Every minute
1. Check for creators moved from "never checked" to "processed"
2. Track average time in queue before first check

### Every hour
1. Generate priority distribution report
2. Identify "stuck" creators (same priority for hours)
3. Check which priority tiers are being processed

---

## Integration with Webhook System

### Flow
```
1. Webhook arrives → webhook_handler.py
2. Extract transfer → store in sol_transfers
3. Update address_activity (rolling stats)
4. Enqueue to work_queue (priority = 50.0 initial)
   └─ reason = "new_transfer"
   └─ next_run_at = now (eligible immediately)

5. Worker fetches → fetch_next_work()
6. Locks row → locked_until = now + 120s
7. Recompute priority → compute_priority()
   ├─ Activity (recency, volume)
   ├─ Tags (watchlist, suspicious)
   ├─ Network (coordinated, C2C)
   ├─ Multi-token
   └─ Cooldown penalty
8. Mark processed → last_processed_at = now
9. Requeue → next_run_at = now + 300 (5 minutes)
10. Unlock → locked_until = 0
```

---

## What Each Column Means

### work_queue Table

| Column | Meaning | Example |
|--------|---------|---------|
| `address` | Creator wallet address | `5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ` |
| `priority` | Computed score (0-100+) | `75.5` (high priority) |
| `reason` | Why queued | `"new_transfer + active_1h"` |
| `next_run_at` | Unix timestamp, eligible after | `1772552910` |
| `locked_until` | Unix timestamp, being processed until | `0` (not locked) |
| `attempts` | Times processed | `3` (processed 3 times) |
| `updated_at` | When row was last updated | `2026-03-03 15:40:40` |

### address_activity Table

| Column | Meaning |
|--------|---------|
| `address` | Creator wallet |
| `last_seen_at` | Last transfer timestamp |
| `tx_1h` | Transfers in last hour |
| `sol_in_1h` | SOL received in last hour |
| `sol_out_1h` | SOL sent in last hour |
| `last_processed_at` | When worker last scored this creator |
| `last_rpc_fetch_at` | When last RPC call made |

---

## Real-Time Monitoring

### Watch Queue Changes

```bash
watch -n 5 'sqlite3 flex_complete_database.db "SELECT COUNT(*) as total, COUNT(CASE WHEN priority >= 80 THEN 1 END) as critical FROM work_queue"'
```

This updates every 5 seconds showing:
- Total creators in queue
- How many are critical priority

---

## Summary

**Creator Queue Tracks**:
- ✅ Which creators need processing
- ✅ Their priority (activity + tags + network + tokens)
- ✅ When they were last checked
- ✅ How many times they've been processed
- ✅ Current status (READY/PROCESSING/WAITING)

**Key Columns**:
- `priority` - Score determining processing order
- `last_processed_at` - When last checked
- `attempts` - Times already checked
- `locked_until` - Currently processing?
- `next_run_at` - When eligible again

**Check Status With**:
```bash
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM work_queue"
```

---

*Generated: 2026-03-03*
*Claude Code*
