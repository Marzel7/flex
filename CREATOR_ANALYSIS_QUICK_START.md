# Creator Analysis Queue - Quick Start

## TL;DR

**What**: Async background analysis of creator addresses from webhooks
**How**: Automatic - fires whenever webhooks arrive
**When**: Immediately analyzed by worker thread (non-blocking)
**What it does**: Detects malicious patterns (self-funding, circular funding, etc.)

## 30 Second Setup

1. ✅ Implementation already in place (webhook_handler.py + webhook_worker.py)
2. ✅ Database table created automatically on first run
3. ✅ Worker processes queue in background
4. ✅ No configuration needed

## Test It

```bash
# Run test (triggers 10 webhooks, monitors queue)
python3 test_creator_analysis_queue.py

# Should show:
# - Queue status before webhooks
# - Queue status after webhooks
# - Cached findings from completed analyses
# - Risk scores (LOW/MEDIUM/HIGH)
```

## Monitor It

```bash
# Watch queue status
watch "sqlite3 flex_complete_database.db \"SELECT status, COUNT(*) FROM creator_analysis_queue GROUP BY status;\""

# View findings for top 5 by risk
sqlite3 flex_complete_database.db "SELECT creator_address, json_extract(findings_cached, '$.risk_level'), json_extract(findings_cached, '$.risk_score') FROM creator_analysis_queue WHERE status='complete' ORDER BY json_extract(findings_cached, '$.risk_score') DESC LIMIT 5;"

# View complete findings
sqlite3 flex_complete_database.db
> SELECT json_pretty(findings_cached) FROM creator_analysis_queue WHERE status='complete' LIMIT 1;
```

## How It Works

```
Webhook arrives
    ↓
Stored in sol_transfers + queued for analysis
    ↓
Worker picks it up (within seconds)
    ↓
Analyzes using 7 database queries
    ↓
Caches findings as JSON
    ↓
Done! Analysis ready to expose via API
```

## What Gets Analyzed

For each creator address, extracts:
- How many transfers they sent
- Total SOL distributed
- How many addresses received
- Self-funding schemes detected
- Circular funding patterns
- Cross-funding networks
- Risk score (0-100)
- Risk level (LOW/MEDIUM/HIGH)

## Database Table

```sql
creator_analysis_queue (
  creator_address TEXT PRIMARY KEY,
  status TEXT ('pending', 'analyzing', 'complete', 'retry'),
  priority REAL (higher = sooner),
  findings_cached TEXT (JSON),
  last_analyzed_at INTEGER,
  next_analysis_at INTEGER (when to reanalyze)
)
```

## Integration Points

### Webhook Handler
```python
# Line 562 in webhook_handler.py
queue_for_creator_analysis(conn, list(all_addresses), priority=15.0)
```

### Worker
```python
# Lines 613-616 in webhook_worker.py
creator_items = fetch_next_creator_analysis(conn, batch_size=5)
for creator_address, priority, status, locked_until in creator_items:
    process_creator_analysis(conn, creator_address)
```

## Key Numbers

| Metric | Value |
|--------|-------|
| Webhook latency added | 0ms (async) |
| Analysis time | ~50-200ms per creator |
| Queue batch size | 5 per iteration |
| Worker sleep | 1s between batches |
| Risk scale | 0-100 |
| No RPC calls | ✅ Database only |

## Risk Scoring Quick Reference

```
Self-funded intermediates    → +5 per (cap 50)
Circular funding sources     → +10 per (cap 40)
Cross-funding 10+ creators   → +30
100+ outgoing transfers      → +20
50+ unique recipients        → +15
                           ────────
Score 0-39 = LOW
Score 40-69 = MEDIUM
Score 70+ = HIGH
```

## Future API Endpoint

(Not yet implemented, but ready)

```python
GET /api/creator-findings/<address>

Returns:
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

## Files

| File | Purpose |
|------|---------|
| webhook_handler.py | Queues addresses for analysis |
| webhook_worker.py | Analyzes addresses in background |
| test_creator_analysis_queue.py | Test suite |
| CREATOR_ANALYSIS_QUEUE_GUIDE.md | Full documentation |

## Troubleshooting

**Queue not processing?**
- Check worker is running: `ps aux | grep webhook_worker`
- Check logs: `tail -f flask.log | grep CREATOR_ANALYSIS`

**Queue stuck on analyzing?**
- May be locked by failed item
- Check: `SELECT * FROM creator_analysis_queue WHERE status='analyzing' AND locked_until < now`
- Reset: `UPDATE creator_analysis_queue SET status='retry', locked_until=0 WHERE creator_address='...'`

**Want to reanalyze?**
- Force to pending: `UPDATE creator_analysis_queue SET status='retry', next_analysis_at=0 WHERE creator_address='...'`

## See Also

- [CREATOR_ANALYSIS_QUEUE_GUIDE.md](CREATOR_ANALYSIS_QUEUE_GUIDE.md) - Full guide
- [ASYNC_ANALYSIS_IMPLEMENTATION_SUMMARY.md](ASYNC_ANALYSIS_IMPLEMENTATION_SUMMARY.md) - Implementation details
