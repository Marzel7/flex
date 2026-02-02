# CEX Detection Quick Start

## TL;DR - How It Works

```
Token migrates → Funding extraction finds funders → Known CEX tagged immediately
                                                  └─→ Unknown addresses classified asynchronously
```

**Key Point**: Everything happens automatically. You don't need to do anything.

---

## What Changed

### 1. New Files Added
- `automatic_cex_detection.py` - Core detection logic
- `test_automatic_cex_detection.py` - Test suite (all passing ✓)
- `AUTOMATIC_CEX_DETECTION.md` - Full documentation
- `INTEGRATION_GUIDE.md` - How it integrates with your system

### 2. Modified Files
- `realtime_creator_funding_extractor.py` - Added async CEX classification trigger (line 1129)

### 3. Database Changes
- New `address_classification` table - Stores multi-layer classification results
- Enhanced `cex_wallets` table - Now includes `discovery_source` field

---

## How to Use

### Option 1: Just Run It (Default)

The system starts automatically when your listener detects a new token:

```bash
python3 pumpfun_curve_listener.py
```

When a token is processed:
1. Funding extraction finds funders
2. Known CEX from mapping are tagged immediately
3. New addresses are classified in background (doesn't block)
4. Results logged with `[AUTO-CEX]` prefix

### Option 2: Test It

```bash
python3 test_automatic_cex_detection.py
```

Expected output:
```
TEST 1: Known CEX Address Classification
✓ Binance 2            → cex_confirmed        (score: 150)
✓ Coinbase             → cex_confirmed        (score: 150)
...
```

### Option 3: Manual Classification

```python
import asyncio
from automatic_cex_detection import AutomaticCEXDetector

async def test():
    async with AutomaticCEXDetector() as detector:
        result = await detector.classify_address("ADDRESS")
        print(f"{result.classification.value} (score: {result.confidence_score})")

asyncio.run(test())
```

---

## What You'll See in Logs

### Immediate CEX Detection (from mapping)
```
[REALTIME_FUNDING] Extracting creator funding...
[FUNDING] 🏛️ CEX FUNDER DETECTED: Binance Hot Wallet → creator (50.00 SOL)
```

### Automatic CEX Detection (async, in background)
```
[AUTO-CEX] 🎯 CONFIRMED: Binance 8iBa3q2N... (score: 150)
[AUTO-CEX] ⚠️ LIKELY: UnknownAddr... (score: 75)
[AUTO-CEX] Classification complete: 6 classified, 1 confirmed, 2 likely
```

---

## Database Queries

### See All Classified Addresses

```sql
SELECT address, classification, confidence_score, solscan_label
FROM address_classification
ORDER BY confidence_score DESC
LIMIT 10;
```

### Find CEX-Funded Creators

```sql
SELECT creator_address, SUM(amount_sol) as cex_sol
FROM creator_funders
WHERE is_cex = 1
GROUP BY creator_address
ORDER BY cex_sol DESC;
```

### See What Was Auto-Detected

```sql
SELECT cex_address, exchange_name, confidence_level, discovered_date
FROM cex_wallets
WHERE discovery_source = 'automatic_detection'
ORDER BY discovered_date DESC;
```

---

## Confidence Scores Explained

| Score | Classification | Meaning | Action |
|-------|---|---|---|
| 150 | `cex_confirmed` | Already in CEX_ACCOUNTS | ✓ Auto-added to database |
| 100+ | `cex_confirmed` | Solscan labels it as CEX | ✓ Auto-added to database |
| 60-99 | `cex_likely` | Multiple signals match | ⚠️ Flag for review |
| 30-59 | `cex_possible` | Some signals match | ℹ️ Informational only |
| <30 | `unknown` | Insufficient evidence | — No action |

---

## Common Questions

### Q: Will this slow down token processing?

**A**: No. The classification runs asynchronously in the background after funding extraction completes. Token processing doesn't wait for it.

```
Timeline:
T+0.0s  Token detected
T+2.5s  Funding extraction done → spawn async classification
T+2.5s  Continue with next token (NO WAIT)
T+5.0s  Classification completes in background
```

### Q: Will it auto-add bad addresses to the database?

**A**: Only if confidence ≥100 (very high). Lower scores are just logged, not added.

### Q: What APIs does it use?

**A**:
1. Solscan API (Solscan labels for direct exchange detection)
2. Bonfida API (SNS domain resolution)
3. Helius API (transaction pattern analysis)

All calls are batched for efficiency.

### Q: Can I see what it found?

**A**: Yes, check logs or query the database:

```sql
-- See all classifications
SELECT classification, COUNT(*) FROM address_classification GROUP BY classification;

-- See auto-detected CEX
SELECT * FROM cex_wallets WHERE discovery_source = 'automatic_detection';
```

### Q: What if APIs fail?

**A**: The system gracefully degrades. If Solscan is down, it continues with SNS and heuristics. Never blocks token processing.

### Q: How do I add a new CEX manually?

**A**: Edit `infra_mapping.py` and add to `CEX_ACCOUNTS` dict, or use the `/api/cex-wallets` endpoint:

```bash
curl -X POST http://localhost:5002/api/cex-wallets \
  -H "Content-Type: application/json" \
  -d '{
    "address": "EXCHANGE_ADDRESS",
    "name": "Exchange Name",
    "exchange": "Exchange",
    "wallet_type": "Hot Wallet"
  }'
```

---

## Architecture at a Glance

```
pumpfun_curve_listener.py
    ↓ detects migration
realtime_creator_funding_extractor.py
    ├─ [SYNC] Extract funders
    ├─ [SYNC] Check CEX mapping
    ├─ [SYNC] Save to database
    └─ [ASYNC] Trigger auto-classification
              ↓
    automatic_cex_detection.py
        ├─ Query Solscan
        ├─ Query Bonfida SNS
        ├─ Analyze transactions
        ├─ Score confidence
        ├─ Save to address_classification
        └─ If confirmed: add to cex_wallets
```

---

## Files at a Glance

| File | Purpose | Updated |
|------|---------|---------|
| automatic_cex_detection.py | Core detection logic | NEW |
| test_automatic_cex_detection.py | Test suite | NEW |
| AUTOMATIC_CEX_DETECTION.md | Full docs | NEW |
| INTEGRATION_GUIDE.md | System integration | NEW |
| realtime_creator_funding_extractor.py | Funding extraction | Modified |
| infra_mapping.py | CEX mappings | Existing |
| pumpswap_tokens.db | Database | Tables added |

---

## What's Happening Now

Right now, the system is:

✅ **Active** - Ready to detect CEX wallets
✅ **Integrated** - Hooked into funding extraction
✅ **Tested** - All test suites passing
✅ **Documented** - Full guides and examples provided
✅ **Non-blocking** - Doesn't slow down token processing
✅ **Autonomous** - Runs automatically, no manual steps needed

When your listener detects the next token migration, you'll see:

```
[REALTIME_FUNDING] Processing creator...
[FUNDING] 🏛️ CEX FUNDER DETECTED: Binance...
[AUTO-CEX] Classification complete: X classified, Y confirmed
```

---

## Next Steps

1. **Monitor logs** - Watch for `[AUTO-CEX]` indicators
2. **Check database** - Query `address_classification` table periodically
3. **Review detections** - Verify auto-detected addresses manually if desired
4. **Integrate into risk scoring** - (Optional) Use CEX funding as risk factor
5. **Track discoveries** - Monitor how many new CEX wallets are discovered

---

## Support

If something isn't working:

1. Check logs for `[AUTO-CEX]` or `[REALTIME_FUNDING]` entries
2. Run test suite: `python3 test_automatic_cex_detection.py`
3. Query database:
   ```sql
   SELECT * FROM address_classification LIMIT 5;
   SELECT * FROM cex_wallets WHERE discovery_source = 'automatic_detection';
   ```
4. Check if Solscan/Bonfida APIs are responding (look for warnings in logs)

---

**That's it!** The system is ready to go. Just run your listener and watch the automatic CEX detection work in the background.
