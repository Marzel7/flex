# Funder SOL Transfers - Quick Start Guide

## One-Liner Summary
Complete RPC-based SOL IN/OUT tracking for any address with **progress logging**.

---

## Quick Start

### 1. Test with 3 transactions (2 seconds)
```bash
python3 funder_sol_transfers.py "Fsss6uvqNeapk2zrouXeb8VXYyVUxLR2Yke7VfKxVujB" --max-txs 3 --delay 0.05
``` 

### 2. Full analysis (with progress updates)
```bash
python3 funder_sol_transfers.py "Fsss6uvqNeapk2zrouXeb8VXYyVUxLR2Yke7VfKxVujB"
```

### 3. Custom delay (for rate limiting)
```bash
python3 funder_sol_transfers.py <address> --delay 0.1
```

---

## What Progress Looks Like

```
[RPC] Fetching complete transaction history (paginated)...
[RPC] Page 1: Processing 100 signatures...
      [10] Found 10 IN, 0 OUT
      [20] Found 19 IN, 1 OUT
      [30] Found 29 IN, 1 OUT
      [40] Found 38 IN, 2 OUT
[RPC] ✅ Complete! Fetched 42 total transactions across 1 pages
```

Each update shows:
- **Page N**: Which batch of 100 signatures is being processed
- **[M]**: Number of transactions with SOL deltas found
- **Found X IN, Y OUT**: Count of inflow and outflow transactions

---

## Output Summary

```
====================================================================================================
SUMMARY
====================================================================================================
Total transactions: 42
Total IN:        28.5678 SOL (38 txs)
Total OUT:       5.3124 SOL (2 txs)
Total FEES:      0.0043 SOL
Net:            23.2511 SOL

📥 TOP INFLOWS (Received):
[ 1]   0.5470 SOL | Zqrhg1ry3wsHmEcL...  | 2026-02-12
[ 2]   0.4743 SOL | 4UfLL4pyYUxmY3Ct...  | 2026-02-12
[ 3]   0.1152 SOL | 4VgDXFdSQf5b5ZJE...  | 2026-02-12
     ... and 35 more (26.3313 SOL)

📤 TOP OUTFLOWS (Sent):
[ 1]   3.2500 SOL | BDcQH8KXuxFcNpWu...  | 2026-02-11
[ 2]   2.0624 SOL | 5tzFkiKscXHKJmno...  | 2026-02-10
```

---

## Parameters

| Parameter | Default | Example | Purpose |
|-----------|---------|---------|---------|
| `address` | Required | `Fsss6u...` | Target address |
| `--delay` | 0.15s | `0.05` | RPC call delay (seconds) |
| `--max-txs` | None | `100` | Stop after N transactions |

---

## Use Cases

### 1. Quick Check (1-2 seconds)
```bash
python3 funder_sol_transfers.py <funder> --max-txs 3 --delay 0.05
```
Use when you just want to see if an address has SOL activity.

### 2. Standard Analysis (5-10 minutes)
```bash
python3 funder_sol_transfers.py <funder>
```
Gets complete history. Progress updates every 10 transactions.

### 3. Batch Analysis
```bash
for FUNDER in $(sqlite3 pumpswap_tokens.db \
  "SELECT funder_address FROM creator_funders \
   WHERE creator_address = ? ORDER BY amount_sol DESC LIMIT 20"):
  echo "=== $FUNDER ==="
  python3 funder_sol_transfers.py $FUNDER --max-txs 50 --delay 0.1
done
```

---

## Understanding the Output

### Direction Determination
- **Positive delta** = IN (received SOL)
- **Negative delta** = OUT (sent SOL)

Example:
- Pre-balance: 100 SOL
- Post-balance: 150 SOL
- Delta: +50 SOL → **IN**

### Fee Tracking
- Shown separately for accounting purposes
- Included in fee total
- Not included in NET calculation (already in delta)

### Progress Updates
- Updated every **10 transactions with SOL deltas**
- Not every signature (most have 0 delta)
- Real-time feedback during long runs

---

## Common Patterns

### Pattern: PumpFun Creator Receives Funding
```bash
python3 funder_sol_transfers.py "Fsss6uvqNeapk2zrouXeb8VXYyVUxLR2Yke7VfKxVujB" --max-txs 20

# Output: Mostly IN flows (receiving from funders)
```

### Pattern: CEX Account Exits
```bash
python3 funder_sol_transfers.py "G2YxRa6wt1qePM..." --max-txs 50

# Output: Mix of IN (deposits) and OUT (withdrawals)
# Recipient types: CEX accounts
```

### Pattern: Suspicious Account
```bash
python3 funder_sol_transfers.py "UnknownAddr..." --max-txs 100

# Output: All IN (receiving from various sources)
# Recipient types: Unknown (suspicious pattern)
```

---

## Rate Limiting

### What Happens at 429
- Automatic exponential backoff
- Starts at 0.5 seconds
- Doubles each retry (up to 20 seconds)
- Max 8 retries before giving up

### Recommended Delays
- **Fast** (risky): 0.05s delay
- **Safe** (default): 0.15s delay
- **Slow** (conservative): 0.5s delay

---

## Troubleshooting

### No progress updates appearing
- Tool is still processing signatures
- Check if address has activity
- Try with `--max-txs 3` to test

### "RPC Error"
- Network issue or endpoint overloaded
- Tool will retry with exponential backoff
- Max retries: 8

### Slow processing
- Increase `--delay` to 0.5s if hitting rate limits
- Use `--max-txs` to limit scope
- Process one address at a time

---

## Integration Tips

### With Creator Analysis
```bash
# Step 1: Get creator's funders
CREATOR="<address>"
python3 creator_sol_watch.py $CREATOR

# Step 2: For each funder, get their SOL history
python3 funder_sol_transfers.py <funder> --max-txs 50
```

### For Risk Scoring
```bash
# Funders with mostly OUT = Profit takers (red flag)
# Funders with mostly IN = Legitimate funders (green flag)
# Mixed = Active traders (yellow flag)
```

### For Network Analysis
```bash
# Compare recipients across multiple funders
# Shared recipients = Coordination
python3 funder_sol_transfers.py <funder1> > /tmp/funder1.txt
python3 funder_sol_transfers.py <funder2> > /tmp/funder2.txt
# Compare /tmp/funder1.txt and /tmp/funder2.txt for shared addresses
```

---

## Performance Expectations

| Scenario | Time | Details |
|----------|------|---------|
| 3 txs, 0.05s delay | 2 sec | Quick test |
| 50 txs, 0.1s delay | ~30 sec | Medium run |
| 100 txs, 0.15s delay | ~2 min | Standard run |
| Complete history | 5-30 min | Depends on activity |

---

## Next Steps

1. ✅ Test with a known funder
2. ✅ Review progress logging output
3. ✅ Compare IN vs OUT patterns
4. ✅ Integrate into risk scoring
5. ✅ Run batch analysis for network mapping

---

**Status**: ✅ Ready to use
**Test Command**: `python3 funder_sol_transfers.py "Fsss6uvqNeapk2zrouXeb8VXYyVUxLR2Yke7VfKxVujB" --max-txs 3`
**Expected Time**: ~2 seconds
