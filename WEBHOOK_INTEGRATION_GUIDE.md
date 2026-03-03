# Quick Integration Guide - Webhook M5

## In 5 Minutes

### 1. Create Tables

```bash
sqlite3 flex_complete_database.db < sql_webhook_schema.sql
```

### 2. Add to main.py

At the **top** of your Flask app file:

```python
# ... existing imports ...

# FLEX Webhook M5
from webhook_integration import init_webhook_system
```

Then in your app initialization (after creating Flask app):

```python
# Create Flask app
app = Flask(__name__)

# ... other setup ...

# Initialize webhook system (creates tables, routes, worker thread)
init_webhook_system(app)
```

That's it! 🚀

---

## What Gets Created

**Routes**:
- `POST /helius/webhook` - Accepts RAW Helius webhooks
- `GET /api/webhook/status` - Health check

**Database Tables**:
- `sol_transfers` - Deduplicated SOL transfers
- `address_activity` - Rolling statistics per address
- `work_queue` - Priority queue for processing

**Worker Thread**:
- Runs in background, processes high-priority addresses
- Uses DB-only signals, strict RPC gating

---

## Environment Variables (Optional)

```bash
# Webhook auth (optional)
export HELIUS_WEBHOOK_AUTH="Bearer your-secret-key"

# Database path (optional, defaults to flex_complete_database.db)
export FLEX_DB_PATH="flex_complete_database.db"
```

---

## Test It

### Send a test webhook:

```bash
python3 << 'EOF'
import requests
import json

payload = [{
    "signature": "test_sig_001",
    "slot": 403966256,
    "blockTime": 1772552611,
    "transaction": {
        "message": {
            "accountKeys": [
                {"pubkey": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ"},
                {"pubkey": "HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z"},
                {"pubkey": "11111111111111111111111111111111"}
            ],
            "instructions": [{
                "programIdIndex": 2,
                "parsed": {
                    "type": "transfer",
                    "info": {
                        "source": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
                        "destination": "HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z",
                        "lamports": 200000
                    }
                }
            }]
        },
        "signatures": ["test_sig_001"]
    },
    "meta": {
        "err": None,
        "preBalances": [1000000000, 500000000, 0],
        "postBalances": [999920000, 500200000, 0]
    }
}]

r = requests.post("http://localhost:5002/helius/webhook", json=payload)
print(f"Status: {r.status_code}, Response: {r.text}")
EOF
```

### Check health:

```bash
curl http://localhost:5002/api/webhook/status | jq
```

Expected output:
```json
{
  "ok": true,
  "total_transfers": 1,
  "transfers_1h": 1,
  "queue_size": 2,
  "high_priority_count": 2
}
```

### Query database:

```bash
# Check transfers
sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM sol_transfers"

# Check work queue
sqlite3 flex_complete_database.db "SELECT address, priority FROM work_queue ORDER BY priority DESC LIMIT 5"

# Check activity stats
sqlite3 flex_complete_database.db "SELECT address, tx_1h, sol_in_1h FROM address_activity LIMIT 5"
```

---

## Logs

**Webhook handler logs**:
```
[WEBHOOK] 2026-03-03 15:40:40 - Received 1 transaction(s)
[WEBHOOK] 2026-03-03 15:40:40 - STORED: 5Zpgww... → HZUZfV... (0.000200000 SOL)
```

**Worker logs**:
```
[WORKER] Processing 5Zpgww... (priority=45.0, reason=new_transfer)
[WORKER] 5Zpgww... computed_priority=75.5 (active_1h + high_volume_3tx)
```

Stream them:
```bash
tail -f flask.log | grep -E "WEBHOOK|WORKER"
```

---

## Architecture Quick Reference

**Stage A (Webhook, <50ms)**:
1. Helius sends RAW webhook
2. Extract System Program transfers
3. Deduplicate by signature
4. Update address_activity (rolling stats)
5. Enqueue to work_queue
6. Return 200 immediately

**Stage B (Worker, continuous)**:
1. Fetch highest priority addresses
2. Lock them (prevent concurrent processing)
3. Score using DB-only signals (activity, tags, network, multi-token)
4. Check RPC guardrails (priority >= 80 + cooldown)
5. Process (optional RPC call if gated)
6. Requeue for next run

---

## Key Features ✅

- **Zero RPC polling** - Event-driven only
- **Fast webhook responses** - <50ms with batch inserts
- **Priority scoring** - Activity + tags + network + multi-token
- **Strict RPC gating** - Only high-priority, cooldown-respected addresses
- **Concurrent safe** - Locking prevents race conditions
- **Idempotent** - Dedup by signature, safe to retry
- **Scales to 1000+ addresses** - No per-address polling loop

---

## Files Modified/Created

| File | Status | Purpose |
|------|--------|---------|
| `webhook_handler.py` | New | Flask route + transfer extraction |
| `webhook_worker.py` | New | Priority worker + scoring |
| `webhook_integration.py` | New | Flask integration |
| `sql_webhook_schema.sql` | New | Database schema |
| `main.py` | Update | Add `init_webhook_system(app)` |

---

## Next Steps

1. ✅ Copy files to FLEX directory
2. ✅ Run: `sqlite3 flex_complete_database.db < sql_webhook_schema.sql`
3. ✅ Add `from webhook_integration import init_webhook_system` to main.py
4. ✅ Add `init_webhook_system(app)` after Flask app creation
5. ✅ Restart Flask
6. ✅ Update Helius webhook URL to point to `/helius/webhook` endpoint
7. ✅ Monitor logs: `tail -f flask.log | grep -E "WEBHOOK|WORKER"`

---

Questions? Check `WEBHOOK_ARCHITECTURE_M5.md` for full details.
