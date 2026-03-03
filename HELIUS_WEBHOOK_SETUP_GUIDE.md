# Helius Webhook Setup - Real Webhooks

**Status**: Ready to Configure
**Date**: 2026-03-03
**Goal**: Configure Helius to send real-time transaction webhooks to your application

---

## Quick Start (5 Steps)

### Step 1: Start ngrok tunnel
```bash
ngrok http 5002
```

This creates a public HTTPS URL like: `https://abc123.ngrok.io`

Copy the URL - you'll need it in Step 3.

### Step 2: Get your webhook endpoint
Your Helius webhook endpoint is:
```
https://<ngrok-url>/helius/webhook
```

Example:
```
https://abc123.ngrok.io/helius/webhook
```

### Step 3: Run the sync script
```bash
export HELIUS_API_KEY="f084fae8-d111-4337-9960-2d9c5e02a726"
export WEBHOOK_URL="https://abc123.ngrok.io/helius/webhook"
export CREATOR_LIMIT=1000
export CREATE_MISSING=1

python helius_webhook_sync_m5.py --once
```

**What it does**:
1. Gets top 1000 creators from your database (by priority)
2. Creates Helius webhooks (sharded to max 100k addresses each)
3. Configures each webhook to filter only those creators
4. Saves the mapping to your database

### Step 4: Verify webhooks created
```bash
# Check the mapping
sqlite3 flex_complete_database.db << 'SQL'
SELECT * FROM helius_webhook_shards;
SELECT COUNT(*) as total_assignments FROM helius_webhook_assignments;
SQL
```

### Step 5: Start receiving webhooks
Helius will now send transactions for your creators to:
```
https://abc123.ngrok.io/helius/webhook
```

Your Flask app receives them and:
- Stores transfers in `sol_transfers` table
- Updates `address_activity` (rolling stats)
- Enqueues creators to `work_queue` for processing

---

## What Happens After Setup

### Transaction Flow
```
1. Solana blockchain: Transfer happens
   ↓
2. Helius detects transaction for monitored creator
   ↓
3. Helius sends webhook to: https://abc123.ngrok.io/helius/webhook
   ↓
4. webhook_handler.py extracts transfers
   ↓
5. Stores in sol_transfers (deduplication by signature)
   ↓
6. Updates address_activity (tx_1h, sol_in_1h, etc.)
   ↓
7. Enqueues to work_queue (priority = 50.0 initial)
   ↓
8. Webhook worker processes (computes risk score)
   ↓
9. Creator Queue shows up in UI with metrics
```

### Real-Time Monitoring
Once webhooks arrive, you'll see:

**Webhook Monitor** (`/webhook-monitor`):
- Webhooks Received count increasing
- Recent Transfers table populating
- Transfers (24h) metric updating

**Creator Queue** (same page, scroll down):
- Total in Queue increasing
- Top priority creators appearing
- Status changing from WAITING → READY → PROCESSING

---

## Environment Variables Explained

| Variable | Example | Notes |
|----------|---------|-------|
| `HELIUS_API_KEY` | `f084fae8-d111...` | Your Helius API key (from .env) |
| `WEBHOOK_URL` | `https://abc123.ngrok.io/helius/webhook` | Where Helius POSTs (ngrok URL) |
| `CREATOR_LIMIT` | `1000` | How many top creators to monitor (default 1000) |
| `SHARD_SIZE` | `100000` | Max addresses per webhook (Helius limit, default 100k) |
| `CREATE_MISSING` | `1` | Auto-create webhooks if WEBHOOK_IDS not provided |
| `WEBHOOK_IDS` | `id1,id2,id3` | Existing webhook IDs (optional, for updates) |
| `WEBHOOK_TYPE` | `enhanced` | Helius webhook type (enhanced = more data) |
| `TX_TYPES` | `[]` | Transaction types to filter (empty = all) |

---

## Complete Setup Example

```bash
# Terminal 1: Start ngrok
ngrok http 5002
# Copy the URL shown (e.g., https://abc123.ngrok.io)

# Terminal 2: Run the sync
export HELIUS_API_KEY="f084fae8-d111-4337-9960-2d9c5e02a726"
export WEBHOOK_URL="https://abc123.ngrok.io/helius/webhook"
export CREATOR_LIMIT=1000
export CREATE_MISSING=1

python helius_webhook_sync_m5.py --once

# Output:
# [WEBHOOK_SYNC] creators=1000 limit=1000
# [WEBHOOK_SYNC] shards=1 shard_size=100000 required_webhooks=1
# [WEBHOOK_SYNC] updating shard=0 webhook_id=... addresses=1000
# [WEBHOOK_SYNC] ✓ updated webhook_id=... accountAddresses=1000
# [WEBHOOK_SYNC] ✓ mapping persisted to DB tables

# Terminal 3: Start Flask app
python3 main.py

# Terminal 4: Watch webhooks arriving
watch -n 5 'curl -s http://localhost:5002/api/webhook-status | jq'

# Then go to browser:
# http://localhost:5002/webhook-monitor
# Scroll down to see Creator Queue fill up as webhooks arrive!
```

---

## Troubleshooting

### "WEBHOOK_URL is required"
**Problem**: Script won't run
**Solution**:
```bash
export WEBHOOK_URL="https://abc123.ngrok.io/helius/webhook"
python helius_webhook_sync_m5.py --once
```

### "Need X webhook IDs but WEBHOOK_IDS has 0"
**Problem**: Script won't auto-create webhooks
**Solution**:
```bash
export CREATE_MISSING=1
python helius_webhook_sync_m5.py --once
```

### Webhooks not arriving
**Check 1**: Is ngrok running?
```bash
ps aux | grep ngrok
```

**Check 2**: Is ngrok URL correct?
```bash
curl https://abc123.ngrok.io/helius/webhook
```
Should show 405 Method Not Allowed (POST is expected)

**Check 3**: Is Flask app running?
```bash
curl http://localhost:5002/api/webhook-status
```

---

## Monitoring Webhooks

### Check webhook status in your database
```bash
sqlite3 flex_complete_database.db << 'SQL'
-- See shard mapping
SELECT shard_index, webhook_id, shard_size
FROM helius_webhook_shards
ORDER BY shard_index;

-- Count creators per shard
SELECT webhook_id, COUNT(*) as creator_count
FROM helius_webhook_assignments
GROUP BY webhook_id;
SQL
```

### Watch webhook metrics
```bash
# Terminal 1: Monitor webhooks
watch -n 5 'curl -s http://localhost:5002/api/webhook-status | jq ".total_signatures, .total_transfers"'

# Terminal 2: Monitor creator queue
watch -n 5 'curl -s http://localhost:5002/api/creator-queue-status | jq ".total_in_queue, .critical_count"'
```

---

## Summary

**3 Simple Steps**:
1. `ngrok http 5002` → Get public URL
2. `python helius_webhook_sync_m5.py --once` → Create/configure webhooks
3. `python3 main.py` → Start Flask app

**Then**:
- Visit `http://localhost:5002/webhook-monitor`
- Webhooks will arrive as transactions happen
- Creator queue will populate automatically
- All metrics update in real-time

---

*Claude Code*
