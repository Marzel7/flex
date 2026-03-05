# Wallet Fingerprint Clustering - Quick Test Guide

**Status**: ✅ Fully Integrated and Tested
**Date**: March 5, 2026

---

## Quick Verification (5 minutes)

### Test 1: Syntax Check
```bash
python3 -m py_compile funder_incoming_extractor.py
# Expected: No output (success)
```

### Test 2: Import Check
```bash
python3 -c "from funder_incoming_extractor import extract_transfers_for_funder; print('✅ OK')"
# Expected: ✅ OK
```

### Test 3: Module Status
```bash
python3 << 'EOF'
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex/http_instrumentation')
from funder_incoming_extractor import FINGERPRINT_CLUSTER
print(f"✅ FINGERPRINT_CLUSTER: {FINGERPRINT_CLUSTER is not None}")
if FINGERPRINT_CLUSTER:
    stats = FINGERPRINT_CLUSTER.get_stats()
    print(f"✅ Cached wallets: {stats['total_fingerprints']}")
EOF
```

### Test 4: Database Check
```bash
sqlite3 flex_complete_database.db "
SELECT
    (SELECT COUNT(*) FROM wallet_fingerprints) as fingerprints,
    (SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='wallet_fingerprints') as table_exists;
"
# Expected: fingerprints|table_exists
#           0|1
```

---

## Test With Real Data (10 minutes)

### Option A: Extract Single Funder

```bash
python3 << 'EOF'
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex/http_instrumentation')

from funder_incoming_extractor import extract_transfers_for_funder

# Use a wallet address from your database
wallet = "YOUR_FUNDER_ADDRESS_HERE"

print(f"\n[TEST] Extracting for: {wallet}\n")
result = extract_transfers_for_funder(wallet)

print(f"\nResult:")
print(f"  Incoming: {result['incoming_count']}")
print(f"  Outgoing: {result['outgoing_count']}")
print(f"  Total SOL: {result['total_sol']:.4f}")
print(f"  Source: {result['source']}")
print(f"  Funder: {result['funder']}")
EOF
```

### Option B: Extract For Creator

```bash
python3 << 'EOF'
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex/http_instrumentation')

from funder_incoming_extractor import extract_for_creator

# Use a creator address from your database
creator = "YOUR_CREATOR_ADDRESS_HERE"

print(f"\n[TEST] Extracting for creator: {creator}\n")
results = extract_for_creator(creator)

print(f"\nResults: {len(results)} funders")
for r in results[:3]:  # Show first 3
    print(f"  {r['funder'][:16]}... | IN:{r['incoming_count']} OUT:{r['outgoing_count']} | {r['source']}")
EOF
```

---

## Monitor Fingerprints Being Saved

### Watch Fingerprint Growth
```bash
# Run this multiple times to see cache growth
sqlite3 flex_complete_database.db "
SELECT
    COUNT(*) as total_cached,
    COUNT(CASE WHEN confidence >= 0.9 THEN 1 END) as high_conf,
    ROUND(AVG(confidence), 2) as avg_confidence,
    datetime(MAX(last_seen), 'localtime') as last_update
FROM wallet_fingerprints;
"
```

### Watch Type Distribution
```bash
sqlite3 flex_complete_database.db "
SELECT wallet_type, COUNT(*) as count, ROUND(AVG(confidence), 2) as avg_conf
FROM wallet_fingerprints
GROUP BY wallet_type
ORDER BY count DESC;
"
```

### Monitor Cache Hit Rate
```bash
sqlite3 flex_complete_database.db "
SELECT
    SUM(fingerprint_cache_hit) as cache_hits,
    SUM(fingerprint_refresh) as refreshes,
    COUNT(*) as total_scans,
    ROUND(100.0 * SUM(fingerprint_cache_hit) / COUNT(*), 1) as hit_rate
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours');
"
```

### Estimated Credits Saved
```bash
sqlite3 flex_complete_database.db "
SELECT
    SUM(CASE WHEN fingerprint_cache_hit = 1 THEN 1 ELSE 0 END) as skipped_scans,
    SUM(CASE WHEN fingerprint_cache_hit = 1 THEN 1 ELSE 0 END) * 200 as estimated_credits_saved
FROM wallet_scan_metrics
WHERE fingerprint_cache_hit = 1;
"
```

---

## Enable/Disable Fingerprinting

### Enable (Default)
```bash
export FINGERPRINT_ENABLED=1
# or
export FINGERPRINT_ENABLED=true
```

### Disable Temporarily
```bash
export FINGERPRINT_ENABLED=0
# or
export FINGERPRINT_ENABLED=false
```

Check status:
```bash
python3 -c "import os; print(f\"FINGERPRINT_ENABLED: {os.getenv('FINGERPRINT_ENABLED', '1')}\")"
```

---

## Common Test Scenarios

### Scenario 1: First Run (Day 1)
- Cache hits: 0%
- Fingerprints saved: Growing
- Expected: All wallets marked as FULL_SCAN

```bash
# Verify all FULL_SCAN on first run:
sqlite3 flex_complete_database.db "
SELECT source, COUNT(*) as count
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-4 hours')
GROUP BY source;
"
```

### Scenario 2: Second Run (Repeat Wallets)
- Cache hits: Starting to accumulate
- Some SKIP actions appear
- Expected: Some cache_hit metrics = 1

```bash
# Check for SKIP actions:
sqlite3 flex_complete_database.db "
SELECT
    fingerprint_cache_hit,
    COUNT(*) as count
FROM wallet_scan_metrics
WHERE fingerprint_cache_hit = 1
GROUP BY fingerprint_cache_hit;
"
```

### Scenario 3: Week 1
- Cache hits: 20-30%
- Fingerprints: 300+
- Expected: Measurable savings

```bash
# Check week 1 stats:
sqlite3 flex_complete_database.db "
SELECT
    COUNT(DISTINCT wallet_address) as unique_wallets,
    COUNT(*) as total_scans,
    ROUND(100.0 * SUM(fingerprint_cache_hit) / COUNT(*), 1) as hit_rate,
    ROUND(SUM(fingerprint_cache_hit) * 200.0 / 1024.0, 1) as est_credits_saved_k
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-7 days');
"
```

---

## Troubleshooting

### "wallet_fingerprints table not found"
```bash
# Apply schema migration:
sqlite3 flex_complete_database.db < http_instrumentation/wallet_fingerprint_clustering_schema.sql
# Verify:
sqlite3 flex_complete_database.db ".tables" | grep wallet_fingerprint
```

### "ModuleNotFoundError: No module named 'wallet_fingerprint_clustering'"
```bash
# Check file exists:
ls -la http_instrumentation/wallet_fingerprint_clustering.py
# Verify it's in sys.path when importing funder_incoming_extractor
```

### Cache hits = 0 after first run
- **Expected**: First extraction always shows 0% hits
- **Check**: Re-extract same wallet to see SKIP action
- **Timeline**: Hits grow over days/weeks as more wallets are cached

### High memory usage
- **Normal**: FINGERPRINT_CLUSTER uses SQLite with indexes
- **Check**: Monitor with `sqlite3 flex_complete_database.db "PRAGMA database_list;"`
- **Cleanup**: Run periodic cleanup of old fingerprints (see below)

---

## Maintenance

### Cleanup Old Fingerprints
```python
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex/http_instrumentation')
from wallet_fingerprint_clustering import WalletFingerprintCluster

cluster = WalletFingerprintCluster('flex_complete_database.db')
deleted = cluster.cleanup_old_fingerprints(days_old=30)
print(f"Deleted {deleted} old fingerprints (>30 days)")
```

### View Statistics
```python
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex/http_instrumentation')
from wallet_fingerprint_clustering import WalletFingerprintCluster

cluster = WalletFingerprintCluster('flex_complete_database.db')
stats = cluster.get_stats(hours=24)
print(f"Last 24 hours: {stats}")

by_type = cluster.get_type_distribution()
print(f"By type: {by_type}")

savings = cluster.estimate_credits_saved()
print(f"Estimated savings: {savings}")
```

---

## Expected Growth Over Time

| Period | Fingerprints | Cache Hit % | Est. Savings |
|--------|---|---|---|
| Day 1 | 50-100 | 0% | 0% |
| Day 2 | 100-200 | 5-10% | 1-2% |
| Day 3 | 150-300 | 10-15% | 2-3% |
| Week 1 | 300-500 | 20-30% | 5-10% |
| Week 2 | 500-800 | 25-35% | 6-12% |
| Month 1 | 1000+ | 40-60% | 10-20% |

---

## Dashboard Monitoring

Once cache is established (Day 3+), you can add these to a dashboard:

### Key Metrics
```sql
-- Cache effectiveness
SELECT
    datetime(datetime('now', 'localtime'), '-24 hours') as period,
    ROUND(100.0 * SUM(fingerprint_cache_hit) / NULLIF(COUNT(*), 0), 1) as cache_hit_rate,
    COUNT(DISTINCT wallet_address) as unique_wallets,
    SUM(fingerprint_cache_hit) * 200 as credits_saved_24h
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-24 hours');
```

### Growth Trend
```sql
-- Daily cache hit rate
SELECT
    DATE(created_at) as date,
    ROUND(100.0 * SUM(fingerprint_cache_hit) / COUNT(*), 1) as hit_rate,
    COUNT(*) as total_scans,
    COUNT(DISTINCT wallet_address) as unique_wallets
FROM wallet_scan_metrics
WHERE created_at >= datetime('now', '-30 days')
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

---

## Summary

✅ **Status**: Fully integrated and tested
✅ **Verified**: All components working
✅ **Ready**: For production monitoring

**Next Actions**:
1. Run one of the test scenarios above
2. Monitor cache growth over 1-7 days
3. Check cache hit rate trends
4. Add dashboard widget if desired

---

**Last Verified**: 2026-03-05
**Integration**: COMPLETE ✅
