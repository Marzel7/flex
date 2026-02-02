# BlockSec AML Batching - Quick Start

## One-Liner: What It Does

Automatically labels addresses from BlockSec API every 2.4 hours, caches results forever, and adds them to your CEX/Infrastructure mappings so you never need to query them again.

---

## Setup (Already Done ✅)

✅ `blocksec_aml_batcher.py` created
✅ Database tables created
✅ Integration added to `realtime_creator_funding_extractor.py`
✅ Mapping integration implemented

---

## Check Status

```bash
python3 -c "
from blocksec_aml_batcher import BlockSecAMLBatcher
batcher = BlockSecAMLBatcher()
stats = batcher.get_batch_stats()
print(f'Cached: {stats[\"cached_addresses\"]} addresses')
print(f'Batches: {stats[\"successful_batches\"]} submitted')
print(f'Next batch: {stats[\"next_batch_in_minutes\"]} minutes')
"
```

---

## How It Works

```
Token Migration
  → Extract funders/recipients
  → Queue new addresses for labeling
  → Every 2.4 hours (if 2.4+ hours passed):
     → Get ACTIVE addresses (used by 2+ creators)
     → Submit batch to BlockSec (prioritize signal over noise)
     → Get labels
     → Add to CEX_ACCOUNTS / INFRASTRUCTURE_ACCOUNTS mappings
     → Cache forever
  → Continue extraction (non-blocking)
```

**Smart Prioritization**: Only batches addresses that appear 2+ times (funders of multiple creators or recipients from multiple creators). Ignores one-off transactions.

---

## Database Tables

### blocksec_aml_cache
Stores label results:
```
address          → "8iBa3q2N..."
label_name       → "Binance Hot Wallet"
category         → "exchange"
risk_score       → 0.95
queried_at       → TIMESTAMP
```

### blocksec_batch_log
Tracks batch submissions:
```
batch_id         → Hash of submission
batch_size       → Number of addresses
submitted_at     → TIMESTAMP
status           → "success" or "failed"
```

---

## Rate Limit

- **Limit**: 10 calls per day
- **Between batches**: 2.4 hours (144 minutes)
- **Max addresses per batch**: 100
- **Expected coverage**: 1-2 batches/day covers 40-90+ new addresses

---

## Log Output

Watch for `[BLOCKSEC]` prefix:

```
[BLOCKSEC] Found 87 unlabeled addresses, preparing batch...
[BLOCKSEC] Submitting batch a3f8e2c9... (87 addresses)
[BLOCKSEC] 8iBa3q2N... → Binance Hot Wallet (exchange, score: 0.95)
[BLOCKSEC] ✓ Added to CEX mapping: Binance Hot Wallet
[BLOCKSEC] 2snHHre... → deBridge (bridge, score: 0.89)
[BLOCKSEC] ✓ Added to Infrastructure mapping: deBridge
```

---

## Manual Batch (Optional)

```bash
python3 -c "
import asyncio
from blocksec_aml_batcher import auto_batch_new_addresses
result = asyncio.run(auto_batch_new_addresses())
print(result)
"
```

---

## What Gets Added to Mappings

When BlockSec labels an address, it's automatically added:

**CEX Addresses** → `CEX_ACCOUNTS` mapping
```python
CEX_ACCOUNTS["8iBa..."] = {
    "name": "Binance Hot Wallet",
    "exchange": "Binance",
    "description": "Auto-detected via BlockSec AML",
    "tags": ["blocksec", "aml", "auto-detected"]
}
```

**Infrastructure** → `INFRASTRUCTURE_ACCOUNTS` mapping
```python
INFRASTRUCTURE_ACCOUNTS["2snH..."] = {
    "name": "deBridge",
    "category": "bridge",
    "description": "Auto-detected via BlockSec AML",
    "tags": ["blocksec", "aml", "auto-detected", "bridge"]
}
```

---

## Key Benefit

Once an address is labeled and added to mappings:
- ✅ It's cached forever (never re-queried)
- ✅ Immediately recognized in future extractions
- ✅ No API call needed for known addresses
- ✅ System gets smarter with each batch

**Result**: Self-improving system that requires zero manual work.

---

## Troubleshooting

### No batches submitted?
- Check if 2.4 hours have passed: `next_batch_in_minutes` stat
- Verify unlabeled addresses exist: `SELECT COUNT(*) FROM creator_funders WHERE funder_address NOT IN (SELECT address FROM blocksec_aml_cache);`

### API errors?
- Check blocksec_batch_log table for error details
- Verify API key in `blocksec_aml_batcher.py` line 27

### Mappings not updated?
- Check logs for `[BLOCKSEC] ✓ Added to` messages
- Verify `infra_mapping.py` has `add_cex_account()` and `add_infrastructure_account()` functions

---

## Full Documentation

See `BLOCKSEC_AML_BATCHING.md` for complete details.

---

**Status**: ✅ Production Ready
**Fully Automated**: Yes
**Manual Intervention Required**: None
