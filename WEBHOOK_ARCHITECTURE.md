# Webhook Architecture - Single Handler

## System Overview

```
┌─────────────────────────────────────────────────────┐
│            HELIUS WEBHOOK INCOMING                  │
│        (RAW Solana Transactions)                    │
└────────────────────┬────────────────────────────────┘
                     │ POST /helius/webhook
                     ▼
┌─────────────────────────────────────────────────────┐
│        webhook_integration.py                       │
│  @app.route('/helius/webhook', methods=['POST'])    │
└────────────┬────────────────────────────────────────┘
             │ calls
             ▼
┌─────────────────────────────────────────────────────┐
│        webhook_handler.py                           │
│  handle_helius_webhook(request)                     │
│  ├─ validate_auth_header()                          │
│  ├─ extract_system_transfers() ← Key extraction     │
│  ├─ update_address_activity()                       │
│  └─ enqueue_work()                                  │
└────────────┬────────────────────────────────────────┘
             │ Stores in:
             ▼
        sol_transfers
        address_activity
        work_queue

        ▼ Signals worker

┌─────────────────────────────────────────────────────┐
│        webhook_worker.py (background thread)        │
│  run_worker()                                       │
│  ├─ Fetch work items from work_queue                │
│  ├─ Compute priority scores                         │
│  ├─ Call RPC only if priority >= 80                 │
│  └─ Score risk for creators                         │
└─────────────────────────────────────────────────────┘
```

## Route Mapping

| Route | File | Handler | Purpose |
|-------|------|---------|---------|
| `POST /helius/webhook` | webhook_integration.py | handle_helius_webhook() | Accept Helius webhooks |
| `GET /api/webhook/status` | webhook_integration.py | webhook_status_route() | Health check |
| `GET /webhook-monitor` | main.py (route 18230) | webhook_monitor() | UI dashboard |
| `GET /api/creator-recent-checks/enriched` | webhook_api_enriched.py | - | API for recent creators |
| `GET /api/creators/top-risk` | webhook_api_enriched.py | - | API for top risk creators |
| `GET /api/creator/<addr>/risk-details` | webhook_api_enriched.py | - | API for creator details |

## Database Tables (Webhook-Specific)

### sol_transfers
Stores raw SOL transfers from webhooks:
```sql
signature           TEXT PRIMARY KEY  -- Deduplication key
slot                INTEGER           -- Solana slot
block_time          INTEGER           -- Unix timestamp
source              TEXT              -- Sender address
destination         TEXT              -- Receiver address
lamports            INTEGER           -- Amount in lamports
amount_sol          REAL              -- Amount in SOL
received_at         TIMESTAMP         -- When received
processed           BOOLEAN           -- Analysis status
```

### address_activity
Rolling statistics for each address:
```sql
address             TEXT PRIMARY KEY
last_seen_at        INTEGER           -- Block time of last activity
tx_5m, tx_1h, tx_24h INTEGER          -- Transaction counts (time windows)
sol_in_5m, sol_in_1h, sol_in_24h REAL -- SOL received (time windows)
sol_out_5m, sol_out_1h, sol_out_24h REAL -- SOL sent (time windows)
last_processed_at   INTEGER           -- Last analysis timestamp
```

### work_queue
Addresses to analyze (background worker):
```sql
address             TEXT PRIMARY KEY
priority            REAL              -- Sorting key for worker
reason              TEXT              -- Why queued (e.g., "new_transfer")
next_run_at         INTEGER           -- When to process next
locked_until        INTEGER           -- Lock timestamp for concurrent safety
attempts            INTEGER           -- Analysis attempt count
```

## Data Flow Example

**Webhook arrives**: `{ signature: "ABC...", transaction: {...}, meta: {...} }`

**Step 1: Extraction**
```
extract_system_transfers(tx):
  ├─ Find System Program (11111111111111111111111111111111)
  ├─ Read transfer instruction accounts
  │  ├─ accounts[0] = source
  │  └─ accounts[1] = destination
  ├─ Get balance changes (preBalances/postBalances)
  └─ Return: [(sender, receiver, amount_sol, sig, timestamp)]
```

**Step 2: Storage**
```
sol_transfers <- INSERT (signature, source, destination, amount_sol, ...)
address_activity <- UPDATE (activity stats for sender & receiver)
work_queue <- INSERT (sender, receiver → priority=20, reason="new_transfer")
```

**Step 3: Background Worker**
```
Worker reads work_queue (priority DESC):
  ├─ Compute priority score (activity + tags + network + multi-token - cooldown)
  ├─ If priority >= 80:
  │  └─ Call RPC for historical data
  ├─ Score creator risk (0-100)
  └─ Update creator_analysis table
```

## Configuration

### Environment Variables
```bash
FLEX_DB_PATH=flex_complete_database.db    # Database location
HELIUS_WEBHOOK_AUTH=<optional_header>     # Optional auth validation
```

### Worker Settings (webhook_worker.py)
```python
RPC_MIN_PRIORITY = 80          # Minimum priority to call RPC
RPC_COOLDOWN_SECONDS = 1800    # 30 minutes between RPC calls per address
MAX_RPC_CALLS_PER_HOUR = 100   # Global rate limit

LOCK_DURATION = 120            # How long to lock addresses during processing
BATCH_SIZE = 10                # Addresses to process per batch
WORKER_SLEEP = 1               # Seconds between batches
```

## Monitoring

### Check Webhook Status
```bash
curl http://localhost:5002/api/webhook/status | jq
```

### Follow Live Logs
```bash
tail -f flask.log | grep WEBHOOK
tail -f flask.log | grep WORKER
```

### Expected Log Pattern
```
[WEBHOOK] 2026-03-03 17:55:30 - Received 1 transaction(s)
[WEBHOOK] 2026-03-03 17:55:30 - STORED: CyaE1Vxv... → 6rYLG55Q... (0.015 SOL)
[WEBHOOK] 2026-03-03 17:55:30 - Queued 2 addresses
[WORKER] Fetched 2 work items
[WORKER] Processing CyaE1Vxv... (priority=20.0, reason=new_transfer)
[WORKER] CyaE1Vxv... computed_priority=50.0 (active_5m)
[WORKER] CyaE1Vxv... risk_score=40 level=moderate
```

## Error Handling

### Common Issues

| Error | Cause | Fix |
|-------|-------|-----|
| "No SOL transfers found" | Extraction logic failing | Check webhook format in logs |
| "Missing/mismatched data" | Account keys ≠ balance arrays | Log webhook payload for inspection |
| "Priority too low for RPC" | Address not active enough | Wait for more activity or raise threshold |
| "Database locked" | Concurrent writes | Increase `busy_timeout` (already done) |

### Debugging Commands
```bash
# View last webhook payload
cat last_webhook_payload.json | jq

# Check database
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM sol_transfers"
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM work_queue WHERE priority >= 80"

# Test webhook endpoint directly
curl -X POST http://localhost:5002/helius/webhook \
  -H "Content-Type: application/json" \
  -d @test_webhook.json
```

## Performance Characteristics

- **Webhook processing**: ~50-100ms per transaction (parsing + storage)
- **Worker throughput**: ~10 addresses/second (with RPC calls, much faster without)
- **Database writes**: WAL mode enabled for concurrent reads during writes
- **Deduplication**: Signature PRIMARY KEY prevents duplicate processing

## Security Notes

- ✅ Optional auth header validation (if `HELIUS_WEBHOOK_AUTH` set)
- ✅ Signature deduplication prevents replay attacks
- ✅ RPC rate limiting prevents abuse
- ✅ Priority scoring prevents bot/spam flooding

---
**Last Updated**: 2026-03-03
**Version**: M5 (Webhook-First, Low-RPC)
