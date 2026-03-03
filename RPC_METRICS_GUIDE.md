# RPC Metrics & Cost Tracking Guide

## Overview

You now have a complete **database-backed RPC metrics system** that:
- ✅ Persists ALL RPC calls to database (cross-process)
- ✅ Compares local instrumentation vs Helius actual charges
- ✅ Identifies which code costs the most
- ✅ Provides real-time data collection

## Quick Start

### Run the Comparison Monitor
```bash
# 2-minute comparison (default)
python rpc_comparison_monitor.py

# Custom duration (e.g., 5 minutes)
python rpc_comparison_monitor.py 300
```

This shows:
- **Local instrumentation**: What we recorded in database
- **Helius billing**: What they actually charged (truth)
- **Difference**: If we're missing any RPC calls

### Query the Database

**Most expensive RPC methods:**
```sql
SELECT method, COUNT(*) as calls, SUM(credits) as cost
FROM rpc_metrics
GROUP BY method
ORDER BY cost DESC;
```

**Most expensive source files:**
```sql
SELECT source_file, COUNT(*) as calls, SUM(credits) as cost
FROM rpc_metrics
GROUP BY source_file
ORDER BY cost DESC;
```

**Cost over time:**
```sql
SELECT 
  strftime('%H:%M', timestamp) as minute,
  COUNT(*) as calls,
  SUM(credits) as credits
FROM rpc_metrics
GROUP BY minute
ORDER BY minute;
```

## Current Cost Breakdown

### By Method
| Method | Calls | Credits | Cost % |
|--------|-------|---------|--------|
| helius_enhanced_transactions_batch | 238 | 23,800 | 92% |
| getSignaturesForAddress | 2,000 | 2,000 | 8% |
| **TOTAL** | **2,238** | **25,800** | **100%** |

### By Source File
| Source | Calls | Credits | Cost % |
|--------|-------|---------|--------|
| funder_incoming_extractor | 238 | 23,800 | 92% |
| creator_outgoing_extractor | 2,000 | 2,000 | 8% |

### What Each Does

**Funder Incoming Extractor (92% of cost):**
- Parses transaction details using Helius Enhanced API
- Takes 100 credits per batch
- Necessary for extracting transfer data

**Creator Outgoing Extractor (8% of cost):**
- Fetches transaction signatures via standard RPC
- Takes 1 credit per call
- Much cheaper but requires follow-up batch parsing

## How It Works

### Data Flow
```
RPC Call Made
    ↓
record_request() called with:
  - section (listener, funder_incoming, etc.)
  - provider (helius_rpc, solana_rpc, etc.)
  - method (getSignaturesForAddress, etc.)
  - status_code (200, 429, etc.)
  - source_file (which file made the call)
    ↓
In-memory counter incremented
    ↓
_persist_rpc_metric() writes to database
    ↓
rpc_metrics table updated immediately
    ↓
Comparison monitor can query at any time
```

### Why This Matters

Before:
- Each process had separate metrics
- Couldn't see cross-process usage
- Comparison monitor showed 0 credits

After:
- All processes write to shared database
- Cross-process metrics aggregated
- Comparison monitor shows accurate data
- Can optimize based on real usage patterns

## Key Files

| File | Purpose |
|------|---------|
| `rpc_metrics_recorder.py` | Records RPC calls + persists to database |
| `rpc_comparison_monitor.py` | Compares local vs Helius charges |
| `rpc_metrics_api.py` | Provides API endpoints for metrics |
| `flex_complete_database.db` | Persistent storage of all RPC calls |

## Optimization Opportunities

Based on current data:

1. **Batch Size Optimization** (high impact)
   - Enhanced Transactions batches cost 100 credits each
   - Currently averaging ~100 signatures per batch
   - Could optimize batch size for cost efficiency

2. **Call Frequency** (medium impact)
   - 2,000 signature fetches (1 credit each)
   - Could batch these differently

3. **Alternative APIs** (research)
   - Explore if standard RPC can replace Enhanced API
   - Trade-off: cost vs data richness

## Testing Changes

To test if optimizations work:

```bash
# Clear old data (optional)
sqlite3 flex_complete_database.db "DELETE FROM rpc_metrics;"

# Make your code changes

# Run comparison monitor
python rpc_comparison_monitor.py 120

# Compare before/after costs
sqlite3 flex_complete_database.db "SELECT SUM(credits) FROM rpc_metrics;"
```

## Status

✅ Database persistence: WORKING
✅ Cross-process aggregation: WORKING
✅ Comparison monitor: WORKING
✅ Real-time cost tracking: WORKING
✅ Source file attribution: FIXED

Ready for optimization work!
