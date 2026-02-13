# Cross-Funder Coordinator Quick Reference

## What Are Cross-Funder Coordinators?

Accounts that send dust amounts (nanosatoshis) to multiple intermediary funders, which then distribute SOL to multiple creators. This is a **signaling mechanism** for coordinated pump & dump operations.

## The 4 Identified Coordinators

| Address | Confidence | Funders | Creators | Risk | Status |
|---------|-----------|---------|----------|------|--------|
| `po27vzv7...` | HIGH | 3 | 4 | 🔴 CRITICAL | Actively feeding Hyperunit |
| `pohJj8FS...` | HIGH | 3 | 3 | 🔴 CRITICAL | Overlaps with po27vzv7 |
| `HLSHeeM2Q...` | MEDIUM | 2 | 2 | 🟠 HIGH | Shares funder with po27vzv7 |
| `GUZv3UAzUA...` | MEDIUM | 2 | 2 | 🟠 HIGH | Uses Hyperunit router |

## Key Indicators

### ✅ How We Identified Them
1. Queried for senders funding 2+ funders
2. Counted unique creators reached through each funder
3. Checked for infrastructure overlap
4. Found 4 accounts with 2+ creator reach

### 🚩 Red Flags
- **Dust transfers:** Near-zero SOL amounts (not real funding)
- **Multiple funders:** Using 2-3 intermediaries instead of direct transfer
- **Creator overlap:** Same creators funded by multiple coordinators
- **Infrastructure reuse:** Using same Hyperunit wallets

## Current Database

### Table: `network_coordinators`
```sql
SELECT coordinator_address, creator_count, network_confidence
FROM network_coordinators
ORDER BY creator_count DESC;
```

Returns:
- 4 coordinator records
- Confidence levels (HIGH/MEDIUM)
- Creator counts (2-4 each)
- Suspicious flags (dust_transfers, high_fanout, etc.)

### Table: `address_tags`
```sql
SELECT address FROM address_tags
WHERE tag_type = 'role' AND tag_value = 'cross_funder_coordinator';
```

Returns: All 4 coordinators tagged for quick lookup

## API Integration

### Endpoint: `/api/network-coordinators`

**Response:**
```json
{
  "total": 4,
  "high_confidence": 2,
  "medium_confidence": 2,
  "coordinators": [
    {
      "address": "po27vzv7pSZYsroDopmGVVBVAqxg4GcyZXxmCkoejFB",
      "creator_count": 4,
      "creators": ["HYWo71Wk9...", "VKdxpr9eWF...", ...],
      "confidence": "high",
      "flags": ["dust_transfers", "high_funder_fanout", "high_creator_reach"]
    },
    ...
  ]
}
```

## Shared Infrastructure

**Most Reused Funders:**
1. `4khTDC81icSpJbew...` (Hyperunit Router) - Used by 3 coordinators
2. `9s4gzvCoG5eQv1GA...` (Hyperunit Aggregator) - Used by 2 coordinators
3. `HWPgjY8hzRY6uaLn...` (Unknown) - Used by 2 coordinators

**Implication:** Shared infrastructure = single coordinated operation, not independent actors

## Target Creators

Creators funded by these coordinators (HIGH RISK for rug):

1. `HYWo71Wk9PNDe5sB...` - PRIMARY TARGET
   - Funded by: po27vzv7
   - Total SOL: 1,924 (from 565 funders)
   - Status: Monitor closely

2. `ELcnvdHEWTrLa4f...` - SHARED TARGET
   - Funded by: po27vzv7, pohJj8FS
   - Total SOL: 5.34
   - Status: Reused by 2 coordinators

3. `58Hx4stSpAVZKa1...` - SHARED TARGET
   - Funded by: po27vzv7, pohJj8FS, GUZv3UAzUA
   - Total SOL: 3.52
   - Status: Reused by 3 coordinators ⚠️

4. `39MjnPdBEdG5pPY...` - SHARED TARGET
   - Funded by: pohJj8FS, HLSHeeM2Q
   - Total SOL: Unknown
   - Status: Reused by 2 coordinators

## Using the Data

### For Risk Scoring
```python
# Check if a creator is coord-funded
if creator_address in coordinator_targets:
    risk_score += 25  # Major increase
    if creator_funding_coordinator.confidence == 'high':
        risk_score += 5  # Additional penalty
```

### For Monitoring
```python
# Alert on new dust transfers to these funders
suspicious_funders = [
    "4khTDC81icSpJbew...",
    "9SLPTL41SPsYkgds...",
    "9s4gzvCoG5eQv1GA...",
    "HWPgjY8hzRY6uaLn...",
    "H9vjQD9Mw71PtHa6...",
    "2rJb7HxUmwKyKB9T..."
]

# Watch for new dust amounts to these addresses
def check_dust_signals():
    for funder in suspicious_funders:
        recent_dust = fetch_recent_transfers(funder, max_amount=0.01)
        if recent_dust:
            alert(f"Dust signal detected to {funder}")
```

### For Investigation
```python
# Get full funding chain for a coordinator
SELECT fit.sender_address, fit.amount_sol, cf.creator_address, ta.risk_level
FROM funder_incoming_transfers fit
JOIN creator_funders cf ON fit.funder_address = cf.funder_address
JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
WHERE fit.sender_address = 'po27vzv7pSZYsroDopmGVVBVAqxg4GcyZXxmCkoejFB'
ORDER BY cf.creator_address, cf.amount_sol DESC;
```

## Files & Scripts

| File | Purpose |
|------|---------|
| `analyze_cross_funder_coordinators.py` | Identifies and classifies coordinators |
| `visualize_coordinator_network.py` | Shows network topology ASCII visualization |
| `COORDINATOR_ANALYSIS.md` | Detailed per-coordinator analysis |
| `FUNDING_NETWORK_SUMMARY.md` | Executive summary of findings |
| `main.py` | Contains `/api/network-coordinators` endpoint |

## Running Analysis

### Update Coordinators (if needed)
```bash
python3 analyze_cross_funder_coordinators.py
```

### Visualize Network
```bash
python3 visualize_coordinator_network.py
```

### Query via API
```bash
curl http://localhost:5002/api/network-coordinators | jq
```

## Risk Metrics

| Metric | Value | Interpretation |
|--------|-------|-----------------|
| HIGH confidence coordinators | 2 | Strong evidence of coordination |
| MEDIUM confidence coordinators | 2 | Likely part of same ring |
| Shared funders | 3 | Infrastructure reuse = central control |
| Target creators | 7+ | Multi-creator operation |
| Total SOL traced | 1,924+ | Significant capital |

## Detection Logic Used

```
For each sender:
  1. Count DISTINCT funders they send to
  2. For each funder, count DISTINCT creators
  3. If reaches 2+ creators through different funders:
     - Sender is a "coordinator"
     - Confidence = HIGH if 3+ creators via 3+ funders
     - Confidence = MEDIUM if 2 creators via 2 funders
  4. Check for infrastructure overlap with other coordinators
  5. Tag appropriately
```

## Important Notes

⚠️ **These addresses are suspected malicious** - Exercise caution in any interactions

✅ **High confidence findings** - Based on infrastructure overlap and creator targeting

🔐 **All data is on-chain** - Verifiable through Solscan/blockchain explorers

📊 **API endpoint available** - Integrate findings into UI/monitoring systems

---

**Last Updated:** 2026-02-13
**Status:** ACTIVE MONITORING
**Confidence Level:** HIGH
