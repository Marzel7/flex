# Funder Analysis System - Complete Guide & Index

## Quick Navigation

### 🚀 **I Want to...**

#### Get Started Immediately
→ Read: [FUNDER_SOL_TRACKING_QUICK_START.md](FUNDER_SOL_TRACKING_QUICK_START.md) (5 min)
```bash
python3 funder_sol_transfers.py <address> --max-txs 3
```

#### Understand the Complete System
→ Read: [FUNDER_ANALYSIS_COMPLETE_SUMMARY.md](FUNDER_ANALYSIS_COMPLETE_SUMMARY.md) (15 min)
- All 7 tools explained
- Complete data flow
- Workflow examples

#### See Technical Implementation Details
→ Read: [FUNDER_SOL_TRANSFERS_COMPLETE.md](FUNDER_SOL_TRANSFERS_COMPLETE.md) (20 min)
- How RPC pagination works
- Balance delta computation
- Rate limiting mechanism

#### Query Funder Data from Database
→ Use: `funder_outgoing_query.py` (instant, <50ms)
```bash
python3 funder_outgoing_query.py <funder_address> --all
```

#### Detect Coordination Patterns
→ Use: `funder_network_outflows.py`
```bash
python3 funder_network_outflows.py <creator> --limit 20
```

#### Analyze Complete SOL History (With Progress)
→ Use: `funder_sol_transfers.py` ⭐ **NEW**
```bash
python3 funder_sol_transfers.py <address>
# Shows progress: [10] Found 10 IN, 0 OUT every 10 transactions
```

---

## Tool Reference Guide

### Tier 1: Database Queries (Instant - <50ms)

| Tool | Input | Output | Use Case |
|------|-------|--------|----------|
| `creator_sol_watch.py` | Creator | All funders + amounts | Initial analysis |
| `test_funder_network.py` | Creator | Repeat funders (coordinators) | Find suspicious funders |

### Tier 2: RPC-Based Analysis (Minutes - With Progress)

| Tool | Input | Output | Speed | Latest? |
|------|-------|--------|-------|---------|
| `funder_sol_transfers.py` ⭐ | Funder | Complete IN/OUT history | 5-30 min | ✨ **NEW** |
| `funder_outgoing_extractor.py` | Creator | Top N funders' outflows | 2-5 sec/funder | Yes |

### Tier 3: Fast Database Queries (After Extraction - <50ms)

| Tool | Queries | Output | When to Use |
|------|---------|--------|------------|
| `funder_outgoing_query.py` | Recent outflows | Where funder sends SOL | After extraction complete |
| `funder_outgoing_historical.py` | Complete inflows | Profit-taking behavior | Anytime |

### Tier 4: Network Analysis

| Tool | Input | Output | Purpose |
|------|-------|--------|---------|
| `funder_network_outflows.py` | Creator | Shared destinations | Coordination detection |

---

## Documentation Map

### Core Documentation

| File | Topic | Read Time | Audience |
|------|-------|-----------|----------|
| [FUNDER_SOL_TRACKING_QUICK_START.md](FUNDER_SOL_TRACKING_QUICK_START.md) | How to use funder_sol_transfers.py | 5 min | Everyone |
| [FUNDER_SOL_TRANSFERS_COMPLETE.md](FUNDER_SOL_TRANSFERS_COMPLETE.md) | Technical details of RPC implementation | 20 min | Developers |
| [FUNDER_ANALYSIS_COMPLETE_SUMMARY.md](FUNDER_ANALYSIS_COMPLETE_SUMMARY.md) | Complete system overview | 15 min | Everyone |

### Supplementary Documentation

| File | Topic | Focus |
|------|-------|-------|
| [FUNDER_OUTFLOWS_SYSTEM.md](FUNDER_OUTFLOWS_SYSTEM.md) | Extraction & query system | System architecture |
| [FUNDER_OUTFLOWS_SUMMARY.md](FUNDER_OUTFLOWS_SUMMARY.md) | Data saved to database | Integration points |
| [FUNDER_TESTING_GUIDE.md](FUNDER_TESTING_GUIDE.md) | Testing procedures | QA |
| [FUNDER_NETWORK_TESTING_GUIDE.md](FUNDER_NETWORK_TESTING_GUIDE.md) | Coordination testing | Patterns |

---

## Common Workflows

### Workflow 1: Quick Coordinator Scan (1 minute)

**Goal**: Find which funders are likely coordinating

```bash
# Step 1: Show repeat funders
python3 test_funder_network.py <creator> --all

# Output:
# Funder1 funds 15 creators ✅ CEX: Binance
# Funder2 funds 8 creators ❓ UNKNOWN
# Funder3 funds 3 creators ✅ INFRA: Axiom

# Red flags: Unknown accounts funding multiple creators
```

**Decision**: Study Funder2 (unknown, funding 8 creators)

---

### Workflow 2: Complete Funder Profile (10 minutes)

**Goal**: Get complete SOL history for a funder

```bash
# Get all transactions with progress updates
python3 funder_sol_transfers.py <funder_address>

# Output shows:
# [10] Found 10 IN, 0 OUT
# [20] Found 19 IN, 1 OUT
# ... continues until complete ...

# Summary:
# Total IN:  28.56 SOL (38 txs)
# Total OUT: 5.31 SOL (2 txs)
```

**Interpretation**:
- More IN than OUT = Accumulator
- Mostly to CEX = Legitimate arbitrage
- Mostly to unknown = Suspicious

---

### Workflow 3: Deep Network Analysis (30 minutes)

**Goal**: Map complete funder network for a creator

```bash
# Step 1: Extract top 20 funders' outflows to DB
python3 funder_outgoing_extractor.py <creator> --limit 20

# Step 2: Find repeat funders (likely coordinators)
python3 test_funder_network.py <creator> --all

# Step 3: Query each funder's destinations (instant from DB)
for FUNDER in $(sqlite3 pumpswap_tokens.db \
  "SELECT funder_address FROM creator_funders WHERE creator_address = ? LIMIT 20"):
  python3 funder_outgoing_query.py $FUNDER --all
done

# Step 4: Detect coordination patterns
python3 funder_network_outflows.py <creator> --limit 20
# Shows shared destinations = Network!
```

---

## Quick Command Reference

### Get Funders for a Creator
```bash
python3 creator_sol_watch.py <creator_address>
```

### Find Repeat Funders (Coordinators)
```bash
python3 test_funder_network.py <creator_address> --all
```

### Get Complete SOL History (With Progress) ⭐
```bash
python3 funder_sol_transfers.py <funder_address>
python3 funder_sol_transfers.py <funder_address> --max-txs 50  # Limit to 50
python3 funder_sol_transfers.py <funder_address> --delay 0.05 # Faster (risky)
```

### Query Extraction Database (Fast)
```bash
python3 funder_outgoing_query.py <funder_address> --all
```

### Show Profit-Taking Behavior
```bash
python3 funder_outgoing_historical.py <funder_address> --all
```

### Detect Coordination
```bash
python3 funder_network_outflows.py <creator_address> --limit 5
python3 funder_network_outflows.py <creator_address> --all  # All repeat funders
```

---

## Features Spotlight

### ✨ Progress Logging (NEW)
Every RPC operation shows real-time progress:
```
[RPC] Page 1: Processing 100 signatures...
      [10] Found 10 IN, 0 OUT
      [20] Found 19 IN, 1 OUT
      [30] Found 29 IN, 1 OUT
```
This gives continuous feedback during 5-30 minute analyses.

### ✨ Complete Pagination
Unlike RPC endpoints that limit to ~500 recent signatures, we paginate through **ALL** historical signatures.

### ✨ Balance Delta Computation
Extracts exact SOL amounts by comparing:
```
delta_SOL = post_balance[i] - pre_balance[i]
```

### ✨ Automatic Classification
Addresses are automatically classified:
- ✅ CEX (14+ exchanges)
- ✅ INFRA (50+ infrastructure)
- ❓ UNKNOWN (suspicious)

---

## Performance Expectations

| Operation | Time | Details |
|-----------|------|---------|
| Get funders | <50ms | DB query |
| Find repeat funders | <100ms | DB query |
| Quick test (3 txs) | 2 sec | RPC with short delay |
| Medium analysis (50 txs) | 30 sec | RPC with progress |
| Complete history | 5-30 min | Depends on activity |
| Query from DB | <50ms | After extraction |

---

## Troubleshooting

### Issue: No progress updates appearing
**Solution**: Script is still processing. Check with `--max-txs 3` to test.

### Issue: Slow processing
**Solution**: Reduce `--delay` to 0.05 (faster but risky) or limit with `--max-txs 100`

### Issue: RPC errors
**Solution**: Exponential backoff kicks in automatically (max 20 sec delay, 8 retries)

### Issue: Empty database results
**Solution**: Run `funder_outgoing_extractor.py` first to populate `funder_outgoing_transfers` table

---

## Data Sources

### Pre-computed (Database)
- `creator_funders` table: 18,691 pre-migration funder relationships
- `creator_outgoing_transfers` table: Complete creator → recipient flows
- `funder_outgoing_transfers` table: Funder → recipient transfers (built by extractor)

### Real-time (Solana RPC)
- Transaction signatures via `getSignaturesForAddress`
- Transaction details via `getTransaction`
- Balance pre/post via transaction metadata

---

## Integration Points

### For Risk Scoring
Funders with suspicious patterns:
- Mostly IN from unknown sources = RED FLAG
- Mostly OUT to unknown addresses = RED FLAG
- Mixed with CEX = YELLOW FLAG

### For Blocklisting
Identify funders that:
- Fund multiple suspected rug creators
- Send to coordinated networks
- Show profit-taking patterns

### For Network Analysis
Find coordination by:
- Shared recipient addresses across funders
- Similar timing of transfers
- Known exchange accounts

---

## Next Steps

1. **Read** [FUNDER_SOL_TRACKING_QUICK_START.md](FUNDER_SOL_TRACKING_QUICK_START.md)
2. **Test** with quick command: `python3 funder_sol_transfers.py <address> --max-txs 3`
3. **Analyze** a suspicious creator's funders
4. **Integrate** into risk scoring system
5. **Build** funder reputation scores

---

## Version History

### 2026-02-12 (Latest)
- ✨ Added `funder_sol_transfers.py` with progress logging
- ✨ Added real-time feedback for long-running operations
- ✨ Complete pagination support for all historical transactions
- ✨ Comprehensive documentation and quick start guide

### Previous Sessions
- Built extraction and query tools
- Implemented network analysis
- Created CEX/INFRA classification
- Populated 18,691 funder relationships

---

## Questions?

| Question | Answer | Tool/Doc |
|----------|--------|----------|
| How do I get started? | Run funder_sol_transfers.py | [Quick Start](FUNDER_SOL_TRACKING_QUICK_START.md) |
| How does it work? | Full RPC pagination + progress | [Technical Details](FUNDER_SOL_TRANSFERS_COMPLETE.md) |
| What tools are there? | 7 complementary tools | [System Overview](FUNDER_ANALYSIS_COMPLETE_SUMMARY.md) |
| Is my data saved? | Yes, to funder_outgoing_transfers | [Integration](FUNDER_OUTFLOWS_SUMMARY.md) |

---

**Status**: ✅ Complete & Production Ready
**Last Updated**: 2026-02-12
**Ready to Use**: YES

Happy analyzing! 🚀
