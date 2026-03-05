# Creator Analysis Queue Guide

## Overview

The creator analysis queue is an **async background system** that automatically analyzes creator addresses from webhook transactions. No RPC calls - all analysis uses only database queries.

**Key Feature**: Fires immediately when webhooks arrive, doesn't block webhook processing.

## How It Works

### 1. Webhook Triggers Queue Entry
When `/helius/webhook` receives a transaction:
1. Stores transfer to `sol_transfers` table
2. Updates address activity stats
3. **Queues both source and destination** for creator analysis with priority=15.0

```python
# In webhook_handler.py:handle_helius_webhook()
queue_for_creator_analysis(conn, list(all_addresses), priority=15.0)
```

### 2. Worker Processes Queue
Background worker (webhook_worker.py:run_worker) continuously:
1. Fetches up to 5 creators from queue (highest priority first)
2. Analyzes each using DB-only queries
3. Caches findings as JSON
4. Reschedules based on activity level

```python
creator_items = fetch_next_creator_analysis(conn, batch_size=5)
for creator_address, priority, status, locked_until in creator_items:
    process_creator_analysis(conn, creator_address)
```

### 3. Findings Are Cached
Results stored in `creator_analysis_queue.findings_cached` column as JSON:

```json
{
  "outgoing_transfers": 42,
  "total_sol_sent": 5.123456789,
  "unique_recipients": 12,
  "self_funded_intermediates": 0,
  "circular_funding_sources": 2,
  "cross_funded_creators": 5,
  "direct_funders": 3,
  "risk_score": 45,
  "risk_level": "MEDIUM",
  "analyzed_at": 1709500000
}
```

## Database Schema

### creator_analysis_queue Table

```sql
CREATE TABLE creator_analysis_queue (
    creator_address TEXT PRIMARY KEY,
    priority REAL DEFAULT 0.0,              -- Higher = analyze sooner
    status TEXT DEFAULT 'pending',          -- pending, analyzing, complete, retry
    last_analyzed_at INTEGER,               -- Unix timestamp of last analysis
    next_analysis_at INTEGER DEFAULT 0,     -- When to reanalyze (unix timestamp)
    locked_until INTEGER DEFAULT 0,         -- Lock for distributed processing
    attempts INTEGER DEFAULT 0,             -- Retry counter
    findings_cached TEXT,                   -- JSON findings
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)

-- Indexes for efficient querying
CREATE INDEX idx_creator_analysis_status ON creator_analysis_queue(status)
CREATE INDEX idx_creator_analysis_priority ON creator_analysis_queue(priority DESC)
CREATE INDEX idx_creator_analysis_next ON creator_analysis_queue(next_analysis_at ASC)
```

### Status Lifecycle

```
pending → analyzing → complete → [pending again after adaptive delay]
                  ↓
               (on error)
                  ↓
                retry → [same flow]
```

## Risk Scoring Algorithm

Analysis extracts 7 signals from database queries:

| Signal | Weight | Cap |
|--------|--------|-----|
| Self-funded intermediates | +5 per | 50 |
| Circular funding sources | +10 per | 40 |
| Cross-funding 10+ creators | +30 | - |
| 100+ outgoing transfers | +20 | - |
| 50+ unique recipients | +15 | - |

**Risk Levels**:
- LOW: 0-39 points
- MEDIUM: 40-69 points
- HIGH: 70-100 points

## Adaptive Requeue Schedule

After analysis completes, next_analysis_at is scheduled based on activity:

```
High Activity (100+ transfers)  → Reanalyze in 1 hour
Moderate (20-99 transfers)      → Reanalyze in 6 hours
Low (<20 transfers)             → Reanalyze in 24 hours
```

High-volume senders are monitored closely; low-activity addresses checked less frequently.

## Usage & Testing

### Run Test Suite
Triggers 10 test webhooks and monitors queue processing:

```bash
python3 test_creator_analysis_queue.py
```

Shows:
- Queue status before webhooks
- Queue status after webhooks
- Cached findings from completed analyses
- Risk scores and levels

### Monitor Queue Status
```bash
# Count items by status
sqlite3 flex_complete_database.db
> SELECT status, COUNT(*) FROM creator_analysis_queue GROUP BY status;

# View high-priority items
> SELECT creator_address, priority, status FROM creator_analysis_queue
  ORDER BY priority DESC LIMIT 10;

# View completed analyses
> SELECT creator_address, json_extract(findings_cached, '$.risk_level') as risk,
         json_extract(findings_cached, '$.risk_score') as score
  FROM creator_analysis_queue WHERE status='complete' ORDER BY score DESC LIMIT 10;
```

### View Findings for Specific Address
```bash
sqlite3 flex_complete_database.db
> SELECT json_pretty(findings_cached) FROM creator_analysis_queue
  WHERE creator_address='YOUR_ADDRESS_HERE';
```

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Webhook blocking time | 0ms (async queue) |
| Max creators analyzed/iteration | 5 |
| Analysis time per creator | ~50-200ms (DB queries only) |
| Worker sleep between batches | 1s |
| Database locks | Brief (120s per item max) |
| RPC calls | **Zero** - all database |

## Integration Points

### Adding to API Endpoints
To expose cached findings via REST:

```python
@app.route("/api/creator-analysis-findings/<creator>", methods=["GET"])
def get_creator_findings(creator):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT findings_cached, status, last_analyzed_at
        FROM creator_analysis_queue
        WHERE creator_address = ?
    """, (creator,))
    row = cur.fetchone()
    conn.close()

    if row and row[0]:
        return jsonify(json.loads(row[0])), 200
    return jsonify({"status": "not_analyzed"}), 404
```

### Triggering Manual Analysis
Force immediate reanalysis:

```python
def requeue_creator(creator_address, priority=50.0):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = int(time.time())

    cur.execute("""
        UPDATE creator_analysis_queue
        SET status='pending', priority=?, next_analysis_at=?,
            attempts=0, locked_until=0
        WHERE creator_address=?
    """, (priority, now, creator_address))

    conn.commit()
    conn.close()
```

## Debugging

### Check Worker Logs
```bash
# If using flask.log
tail -f flask.log | grep "CREATOR_ANALYSIS\|WORKER"

# Or check recent entries
tail -100 flask.log | grep CREATOR_ANALYSIS
```

### Debug Specific Address
```bash
# Check queue entry
sqlite3 flex_complete_database.db
> SELECT * FROM creator_analysis_queue WHERE creator_address LIKE 'ADDRESS_PREFIX%'\G

# Check activity data
> SELECT * FROM address_activity WHERE address LIKE 'ADDRESS_PREFIX%'\G

# Check outgoing transfers
> SELECT COUNT(*), SUM(amount_sol) FROM sol_transfers WHERE source LIKE 'ADDRESS_PREFIX%';
```

### Force Retry
```bash
sqlite3 flex_complete_database.db
> UPDATE creator_analysis_queue
  SET status='retry', attempts=0, next_analysis_at=0
  WHERE creator_address='YOUR_ADDRESS';
```

## Implementation Files

| File | Changes |
|------|---------|
| webhook_handler.py | Added `queue_for_creator_analysis()`, calls it in `handle_helius_webhook()` |
| webhook_worker.py | Added `fetch_next_creator_analysis()`, `process_creator_analysis()`, calls both in `run_worker()` |
| test_creator_analysis_queue.py | Complete test suite |

## Future Enhancements

1. **API endpoint** - Expose cached findings via `/api/creator-findings/<address>`
2. **WebSocket updates** - Push analysis results to UI in real-time
3. **Alert thresholds** - Notify when risk_score crosses HIGH threshold
4. **Batch analysis** - Analyze related addresses together
5. **Historical tracking** - Keep analysis version history for trend detection
