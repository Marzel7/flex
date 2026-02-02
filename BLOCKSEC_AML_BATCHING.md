# BlockSec AML API Batching System

## Overview

The BlockSec AML API provides address labels (exchanges, infrastructure, etc.) but is limited to **10 calls/day**. This system implements smart batching to maximize label coverage while staying within limits.

**Rate Limit**: 10 calls/day = 1 batch every 2.4 hours (144 minutes)
**Max Batch Size**: 100 addresses per call
**Coverage**: ~1-2 batches/day (48-72+ addresses labeled)

---

## How It Works

### 1. **Automatic Collection** (Non-blocking)
Every time the extractor finds a new funder or recipient, the address is queued for labeling:
```
New Token Migration
    ↓
Extract creator funding
    ↓
Identify funders & recipients
    ↓
Trigger BlockSec batch attempt (async)
    ↓
Get unlabeled addresses (max 100)
    ↓
Check: Enough time passed? (144 min since last batch)
    ↓
YES: Submit batch → NO: Queue for next cycle
```

### 2. **Rate Limiting** (Automatic)
- Tracks last batch submission time in `blocksec_batch_log` table
- Only submits new batch if 2.4 hours have passed
- Otherwise queues addresses for next scheduled batch

### 3. **Label Caching** (Permanent)
Results are cached in `blocksec_aml_cache` table:
- Once an address is labeled, never queried again
- Results persist across restarts
- Reduces API load over time

### 4. **Activity-Based Prioritization** (Smart Selection)
Not all addresses are equally important. The system prioritizes **active accounts**:
- Only includes addresses used by **2+ different creators**
- Filters out one-off transactions (noise)
- Orders by activity count (most-used first)
- Ensures API budget targets real infrastructure

**Examples**:
- ✅ INCLUDE: Wallet funded creators A, B, C (used 3 times)
- ❌ SKIP: Wallet funded only creator A (used 1 time)
- ✅ INCLUDE: Address received from 5 different creators (used 5 times)
- ❌ SKIP: Address received from only 1 creator (used 1 time)

**Fallback**: If no highly-active addresses exist, falls back to any unlabeled addresses to ensure batches aren't empty.

### 5. **Mapping Integration** (Automatic)
When a label is received:
- ✅ **CEX addresses** → Added to `CEX_ACCOUNTS` mapping
- ✅ **Infrastructure** → Added to `INFRASTRUCTURE_ACCOUNTS` mapping
- ✅ **Future recognition** → Immediately used in next extraction

---

## Database Tables

### `blocksec_aml_cache`
Stores label results from BlockSec API:
```sql
address             -- Address queried (PRIMARY KEY)
label_name          -- "Binance Hot Wallet", "deBridge", etc.
category            -- "exchange", "bridge", "relay", etc.
risk_score          -- 0-1 confidence score from BlockSec
risk_level          -- "neutral", "low", "medium", "high"
aml_status          -- "clean", "suspicious", "risky", etc.
raw_response        -- Full label JSON from API
queried_at          -- When label was retrieved
source              -- "blocksec" (for origin tracking)
```

### `blocksec_batch_log`
Tracks batch submissions and rate limiting:
```sql
batch_id            -- Hash of submission (PRIMARY KEY)
batch_size          -- Number of addresses in batch
addresses_submitted -- Comma-separated address list
submitted_at        -- TIMESTAMP of submission
api_response        -- Response from API (truncated)
status              -- "success" or "failed"
```

---

## API Configuration

**Endpoint**: `https://aml.blocksec.com/address-label/api/v3/batch-labels`
**Chain ID**: -3 (Solana)
**Auth**: API-KEY header
**Request Format**:
```json
{
  "chain_id": -3,
  "addresses": [
    "address1",
    "address2",
    ...
  ]
}
```

**Response Format**:
```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "address1": {
      "address": "address1",
      "labels": [
        {
          "label": "Binance Hot Wallet",
          "category": "exchange",
          "score": 0.95,
          "risk_level": "neutral",
          "aml_status": "clean"
        }
      ]
    }
  }
}
```

---

## Usage Examples

### Check Batching Status
```bash
python3 -c "
from blocksec_aml_batcher import BlockSecAMLBatcher
batcher = BlockSecAMLBatcher()
stats = batcher.get_batch_stats()
for key, value in stats.items():
    print(f'{key}: {value}')
"
```

**Output**:
```
cached_addresses: 156
successful_batches: 3
rate_limit: 10 calls/day
batch_interval_minutes: 144
max_per_batch: 100
next_batch_in_minutes: 45
can_batch_now: False
```

### Manually Trigger Batch
```bash
python3 -c "
import asyncio
from blocksec_aml_batcher import auto_batch_new_addresses
result = asyncio.run(auto_batch_new_addresses())
print(result)
"
```

**Output**:
```json
{
  "success": true,
  "batch_id": "a3f8e2c9...",
  "count": 87,
  "results": {
    "address1": {
      "label_name": "Binance Hot Wallet",
      "category": "exchange",
      "risk_score": 0.95
    },
    "address2": { ... }
  }
}
```

### Get Cached Label
```python
from blocksec_aml_batcher import BlockSecAMLBatcher
batcher = BlockSecAMLBatcher()

label = batcher.get_cached_label("8iBa3q2N...")
if label:
    print(f"Label: {label['label_name']}")
    print(f"Score: {label['risk_score']}")
```

---

## Workflow Integration

### In `realtime_creator_funding_extractor.py`

**Trigger point** (line ~1240):
```python
# Trigger BlockSec AML batching (caches new addresses for batch submission)
asyncio.create_task(self._try_blocksec_batch())
```

**Method** (added):
```python
async def _try_blocksec_batch(self):
    """Try to submit a batch to BlockSec AML API"""
    try:
        from blocksec_aml_batcher import auto_batch_new_addresses
        result = await auto_batch_new_addresses()
        if result and result.get("success"):
            print(f"[BLOCKSEC] Batch submitted: {result['count']} addresses", flush=True)
    except Exception as e:
        print(f"[BLOCKSEC] Error: {e}", flush=True)
```

---

## What Gets Added to Mappings

### CEX Mapping
When a BlockSec label like "Binance Hot Wallet" is received:
```python
from infra_mapping import add_cex_account

add_cex_account(
    address="8iBa3q2N...",
    name="Binance Hot Wallet",
    exchange="Binance",  # Extracted from label
    description="Auto-detected via BlockSec AML (confidence: high)",
    tags=["blocksec", "aml", "auto-detected"],
    risk_level="neutral"
)
```

### Infrastructure Mapping
When a BlockSec label like "deBridge" is received:
```python
from infra_mapping import add_infrastructure_account

add_infrastructure_account(
    address="2snHHre...",
    name="deBridge",
    category="bridge",  # From BlockSec API
    description="Auto-detected via BlockSec AML",
    tags=["blocksec", "aml", "auto-detected", "bridge"],
    risk_level="neutral"
)
```

---

## Rate Limit Math

```
24 hours / 10 calls = 2.4 hours between batches
144 minutes between batches

Expected token migrations/day: 20-30
New addresses per token: 2-3 funders/recipients
Total new addresses/day: 40-90

Batch capacity: 100 addresses per call
Batches available: 10/day

With smart batching:
- 1-2 full batches/day is optimal
- Covers 100-200+ new addresses
- Well within 10 call limit
```

---

## Log Output

### Successful Batch
```
[BLOCKSEC] Found 87 unlabeled addresses, preparing batch...
[BLOCKSEC] Submitting batch a3f8e2c9... (87 addresses)
[BLOCKSEC] 8iBa3q2N... → Binance Hot Wallet (exchange, score: 0.95)
[BLOCKSEC] ✓ Added to CEX mapping: Binance Hot Wallet
[BLOCKSEC] 2snHHre... → deBridge (bridge, score: 0.89)
[BLOCKSEC] ✓ Added to Infrastructure mapping: deBridge
[BLOCKSEC] Batch a3f8e2c9 submitted successfully
[BLOCKSEC] Batch complete: 87 addresses, 82 labeled
```

### Rate Limited
```
[BLOCKSEC] Found 42 unlabeled addresses, preparing batch...
[BLOCKSEC] Rate limited. Next batch in 45 minutes
```

---

## Monitoring

### Check Cache Size
```sql
SELECT COUNT(*) FROM blocksec_aml_cache;
```

### Check Batch History
```sql
SELECT
    batch_id,
    batch_size,
    status,
    submitted_at
FROM blocksec_batch_log
ORDER BY submitted_at DESC
LIMIT 10;
```

### Check Added Mappings
```bash
# See all addresses added by BlockSec
python3 -c "
from infra_mapping import get_accounts_by_tag
blocksec_accounts = get_accounts_by_tag('blocksec')
for addr, info in list(blocksec_accounts.items())[:5]:
    print(f'{info[\"name\"]}: {addr[:16]}...')
"
```

---

## Troubleshooting

### Batch Never Submits
1. Check `blocksec_batch_log` for submission history
2. Verify at least 2.4 hours have passed since last batch
3. Check if any unlabeled addresses exist: `SELECT COUNT(*) FROM creator_funders WHERE funder_address NOT IN (SELECT address FROM blocksec_aml_cache)`

### API Returns Error
1. Verify API key is correct in `blocksec_aml_batcher.py`
2. Check network connectivity
3. Verify Solana chain ID is -3 (for mainnet)
4. Review response in `blocksec_batch_log`

### Mappings Not Updated
1. Ensure `infra_mapping.py` has `add_cex_account()` and `add_infrastructure_account()` functions
2. Check for import errors in logs
3. Verify addresses aren't already in mappings

---

## Performance Notes

- **Batch submission**: ~2-5 seconds per API call
- **Database caching**: <100ms for label lookups
- **No extraction slowdown**: All operations are non-blocking via `asyncio.create_task()`

---

## Next Steps

1. ✅ System is deployed and running
2. Batches will auto-submit when:
   - At least 1 unlabeled address exists
   - 2.4 hours have passed since last batch
3. Labeled addresses are added to mappings and cached forever
4. Future extractions immediately recognize labeled addresses

---

**Status**: ✅ Production Ready
**Last Updated**: 2026-02-02
**Key Files**: `blocksec_aml_batcher.py`, `realtime_creator_funding_extractor.py`
