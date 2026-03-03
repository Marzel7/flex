# Creator Queue - Quick Reference

**What**: The `work_queue` table tracks which creators need processing, their priority, and processing history.

**Where**: `flex_complete_database.db` - tables: `work_queue` + `address_activity`

---

## Check Queue Status (3 Ways)

### 1. Python Script (Recommended)
```bash
python3 check_creator_queue.py
```

**Shows**:
- Total creators in queue
- Priority distribution (critical/elevated/moderate/low)
- Top 10 critical priority creators
- Recently processed creators
- Currently processing
- Queue statistics

### 2. SQL Query
```bash
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM work_queue"
```

### 3. Real-Time Watch
```bash
watch -n 5 'sqlite3 flex_complete_database.db "SELECT COUNT(*) as total, COUNT(CASE WHEN priority >= 80 THEN 1 END) as critical FROM work_queue"'
```

---

## Key Information

### Queue Contents

| Column | Meaning | Example |
|--------|---------|---------|
| `address` | Creator wallet | `5ZpgwwHAxs5kuer...` |
| `priority` | Score (0-100+) | `75.5` |
| `reason` | Why queued | `"new_transfer"` |
| `attempts` | Times checked | `3` |
| `last_processed_at` | When checked | `1772552611` (unix timestamp) |
| `locked_until` | Currently processing? | `0` = not locked |
| `next_run_at` | Eligible for processing | `1772552910` |

### Processing Status

| Status | Meaning | Locked? |
|--------|---------|---------|
| READY | Can be processed now | No |
| WAITING | Not eligible yet | No |
| PROCESSING | Currently being processed | Yes |

---

## Common Queries

### How many creators are queued?
```sql
SELECT COUNT(*) FROM work_queue;
```

### How many are critical priority?
```sql
SELECT COUNT(*) FROM work_queue WHERE priority >= 80;
```

### What's the highest priority?
```sql
SELECT MAX(priority) FROM work_queue;
```

### Which creators were recently checked?
```sql
SELECT
    SUBSTR(address, 1, 8),
    attempts,
    datetime(last_processed_at, 'unixepoch')
FROM work_queue
ORDER BY last_processed_at DESC
LIMIT 10;
```

### Which creators have never been checked?
```sql
SELECT COUNT(*) FROM work_queue WHERE attempts = 0;
```

### Currently processing?
```sql
SELECT COUNT(*) FROM work_queue
WHERE locked_until > strftime('%s', 'now');
```

---

## How Queue Gets Populated

```
Helius Webhook arrives
    ↓
webhook_handler.py extracts transfers
    ↓
enqueue_addresses() adds source + destination to work_queue
    (priority = 50.0, reason = "new_transfer")
    ↓
Creator appears in work_queue with:
    - address: creator wallet
    - priority: 50.0 (initial)
    - reason: "new_transfer"
    - attempts: 0 (not yet processed)
    ↓
Worker fetches from queue
    ↓
compute_priority() updates priority based on:
    - Activity (recency, volume)
    - Tags (watchlist, suspicious)
    - Network (coordination, C2C)
    - Multi-token (multiple tokens created)
    - Cooldown (time since last check)
    ↓
Creator is processed
    (last_processed_at updated)
    ↓
Requeued for next batch (next_run_at = now + 5 minutes)
    (attempts incremented)
```

---

## Priority Levels

| Level | Score | Meaning |
|-------|-------|---------|
| Critical | ≥ 80 | High risk, process ASAP |
| Elevated | 60-79 | Notable risk |
| Moderate | 40-59 | Some risk factors |
| Low | < 40 | Minimal risk |

---

## Timeline: What Happens

### T+0: Webhook arrives
```
INSERT INTO work_queue
  (address, priority, reason, attempts, locked_until)
  VALUES (?, 50.0, 'new_transfer', 0, 0)
```

### T+1-5s: Worker processes
```
UPDATE work_queue
  SET locked_until = now + 120  -- Lock for 2 minutes
  WHERE address = ?

-- Compute priority
SELECT ... FROM address_activity WHERE address = ?

-- Score (activity + tags + network + multi_token - cooldown)
priority = 50.0 + 25 + 0 - 0 = 75.0

UPDATE work_queue
  SET priority = 75.0

-- Update stats
UPDATE address_activity
  SET last_processed_at = now
  WHERE address = ?
```

### T+5m: Requeue
```
UPDATE work_queue
  SET
    locked_until = 0,           -- Unlock
    next_run_at = now + 300,    -- 5 minutes later
    attempts = attempts + 1
  WHERE address = ?
```

---

## Real Example Output

```
================================================================================
CREATOR QUEUE STATUS
================================================================================

📊 QUEUE OVERVIEW
  Total Creators:             42
  Critical Priority:          5  (priority >= 80)
  Elevated Priority:          8  (priority 60-79)
  Moderate Priority:          15  (priority 40-59)
  Low Priority:               14  (priority < 40)
  Currently Processing:       2
  Never Checked:              3

🔴 TOP 10 CRITICAL PRIORITY (Ready to Process)
--------------------------------------------------------------------------------
  5Zpgww... | Priority:  82.5 | ✅ READY | Checked:  3x | high_activity + coordinated
  HZUZfV... | Priority:  81.0 | ✅ READY | Checked:  2x | rapid_token_launches
  8m1Uxe... | Priority:  80.5 | 🔒 LOCKED |  Checked:  1x | new_transfer + active_1h
  ...

✅ RECENTLY PROCESSED CREATORS (Last Check)
--------------------------------------------------------------------------------
  5Zpgww... | Priority:  82.5 | Checked:  3x | Tx/1h:   5 | Volume:   2.3450 SOL | 12m ago
  HZUZfV... | Priority:  81.0 | Checked:  2x | Tx/1h:   3 | Volume:   1.1200 SOL | 45m ago
  ...

🔒 CURRENTLY PROCESSING
--------------------------------------------------------------------------------
  8m1Uxe... | Priority:  80.5 | 58s remaining | new_transfer + active_1h
  bwamJz... | Priority:  55.0 | 102s remaining | high_volume_12tx

📈 QUEUE STATISTICS
--------------------------------------------------------------------------------
  Total Creators:       42
  Average Priority:     52.3 (range: 15.0 - 82.5)
  Average Times Checked: 2.1
```

---

## Troubleshooting

### Queue is empty
**Problem**: No creators in queue
**Cause**: No webhooks received yet
**Solution**: Send webhooks to `/helius/webhook` endpoint

### Creator stuck in queue
**Problem**: Same creator, same priority, never processed
**Cause**: Worker not running or priority < 80
**Check**:
```bash
ps aux | grep webhook_worker
```

### High attempts but never processed
**Problem**: Creator queued many times but last_processed_at is NULL
**Cause**: Worker processing but not updating address_activity
**Check**: Webhook worker logs

### Priority not updating
**Problem**: Priority stays same over time
**Cause**: Activity not changing OR cooldown penalty active
**Check**: Activity query:
```sql
SELECT tx_1h, sol_in_1h, sol_out_1h FROM address_activity
WHERE address = ?;
```

---

## For Developers

### Schema

**work_queue**:
```sql
CREATE TABLE work_queue (
    address TEXT PRIMARY KEY,
    priority REAL,
    reason TEXT,
    next_run_at INTEGER,
    locked_until INTEGER,
    attempts INTEGER,
    updated_at TIMESTAMP
);
```

**address_activity**:
```sql
CREATE TABLE address_activity (
    address TEXT PRIMARY KEY,
    last_seen_at INTEGER,
    tx_5m INTEGER, tx_1h INTEGER, tx_24h INTEGER,
    sol_in_5m REAL, sol_in_1h REAL, sol_in_24h REAL,
    sol_out_5m REAL, sol_out_1h REAL, sol_out_24h REAL,
    last_processed_at INTEGER,
    last_rpc_fetch_at INTEGER,
    updated_at TIMESTAMP
);
```

### Indexes
```sql
CREATE INDEX idx_work_queue_priority ON work_queue(priority DESC);
CREATE INDEX idx_work_queue_next_run ON work_queue(next_run_at ASC);
CREATE INDEX idx_address_activity_last_seen ON address_activity(last_seen_at DESC);
```

---

## Summary

**Creator Queue Tracks**:
- Which creators need processing
- Their priority (activity + tags + network + tokens)
- Processing history (when checked, how many times)
- Current status (READY/PROCESSING/WAITING)

**Check Status**: `python3 check_creator_queue.py`

**Key Columns**:
- `priority` - Processing order (higher = first)
- `last_processed_at` - When checked
- `attempts` - Times processed
- `locked_until` - Currently processing?

**Current Status**: Queue populated by webhooks as they arrive

---

*Generated: 2026-03-03*
*Claude Code*
