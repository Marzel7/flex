# Flex Production Index

**Status**: ✅ **PRODUCTION READY** (Feb 21, 2026)
**Version**: v2.1 Complete
**Components**: 3 (Analyzer, Risk Checker, Ops Dashboard)

---

## Quick Start (30 seconds)

```bash
# 1. Run the analyzer (one-time or daily)
python3 cross_funding_network_analyzer.py

# 2. Check results
python3 ops_dashboard.py --db pumpswap_tokens.db --expected-clusters 9

# 3. Look up a creator
python3 ops_dashboard.py --db pumpswap_tokens.db --creator HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp

# 4. Integrate into your app
from cluster_risk_checker import check_creator
result = check_creator("HYWo71Wk...")
```

---

## Production Components

### 1. **cross_funding_network_analyzer.py** (51 KB)
Batch analyzer that detects coordinated funding networks.

**What it does**:
- Clusters funders by co-funding patterns (Jaccard ≥0.25)
- Filters SYSTEM artifacts
- Downweights CEX exchanges
- Stores results in `funder_networks` table

**Results**:
- **9 clusters** from 42,016 funders
- **FUNDERS_1**: 95 coordinated funders, 17,087 SOL (CRITICAL)
- **Runtime**: ~3 minutes

**Run it**:
```bash
python3 cross_funding_network_analyzer.py
```

**Read more**: [CROSS_FUNDING_ANALYZER_COMPLETE.md](CROSS_FUNDING_ANALYZER_COMPLETE.md)

---

### 2. **cluster_risk_checker.py** (4.1 KB)
Real-time O(1) module for creator-to-cluster lookups.

**What it does**:
- Caches creator→cluster mapping
- Returns risk multiplier (1.0x to 3.0x)
- Handles missing data gracefully
- No SQL dependencies

**Risk Multipliers**:
- **FUNDERS_1**: 3.0x (CRITICAL)
- **FUNDERS_9**: 2.0x (HIGH)
- **FUNDERS_3**: 1.5x (MEDIUM)
- **Others**: 1.0x (default)

**Usage**:
```python
from cluster_risk_checker import check_creator

result = check_creator("HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp")
# Returns: in_cluster, cluster_id, risk_multiplier, risk_label,
#          network_size, network_volume_sol
```

**Performance**:
- Cold start: <1s
- Per query: <1ms
- Memory: ~2MB

---

### 3. **ops_dashboard.py** (420 lines)
Production monitoring dashboard with alerting.

**What it does**:
- Displays cluster metrics (9 clusters, 18,014.32 SOL)
- Lists top clusters, recipient hubs
- Warns of cluster count changes
- Monitors FUNDERS_1 presence

**Usage**:
```bash
# Full dashboard
python3 ops_dashboard.py --db pumpswap_tokens.db

# With validation
python3 ops_dashboard.py --db pumpswap_tokens.db --expected-clusters 9

# Creator lookup
python3 ops_dashboard.py --db pumpswap_tokens.db --creator <address>

# Watchlist sample
python3 ops_dashboard.py --db pumpswap_tokens.db --show-funders1-creators 20
```

**Performance**: <100ms generation

**Read more**: [OPS_DASHBOARD_GUIDE.md](OPS_DASHBOARD_GUIDE.md)

---

## Key Data Points

| Metric | Value |
|--------|-------|
| Clusters Detected | 9 |
| Coordinated Funders | 130 |
| Total Dataset Funders | 42,016 |
| FUNDERS_1 Size | 95 funders |
| FUNDERS_1 Volume | 17,087 SOL |
| FUNDERS_1 Creators | 634 |
| Correct Total | 18,014.32 SOL |
| Risk Multiplier (FUNDERS_1) | 3.0x CRITICAL |

---

## Critical: The Query Gotcha ⚠️

When querying cluster volumes, **use MAX aggregation**, not SUM:

**❌ WRONG** (inflated by ~90x):
```sql
SELECT SUM(total_volume_sol) FROM funder_networks
WHERE cluster_id IS NOT NULL;
-- Result: 1,628,741.94 (each cluster counted per funder row)
```

**✅ CORRECT**:
```sql
SELECT SUM(cluster_volume_sol) FROM (
  SELECT cluster_id, MAX(total_volume_sol) AS cluster_volume_sol
  FROM funder_networks WHERE cluster_id IS NOT NULL
  GROUP BY cluster_id
);
-- Result: 18,014.32 (correct)
```

**The dashboard handles this automatically** with the `v_funder_clusters_summary` view.

---

## Integration Examples

### Example 1: Token Risk Scoring
```python
from cluster_risk_checker import check_creator

def calculate_token_risk(creator_address, base_risk):
    cluster_info = check_creator(creator_address)
    adjusted_risk = base_risk * cluster_info['risk_multiplier']

    return {
        'base_risk': base_risk,
        'cluster_id': cluster_info['cluster_id'],
        'risk_multiplier': cluster_info['risk_multiplier'],
        'adjusted_risk': adjusted_risk,
        'label': cluster_info['risk_label']
    }

# Usage
result = calculate_token_risk("HYWo71Wk...", base_risk=0.45)
# Returns: adjusted_risk = 0.45 * 3.0 = 1.35 (CRITICAL)
```

### Example 2: Web API Endpoint
```python
@app.get("/api/creators/{address}/cluster-risk")
def get_creator_risk(address: str):
    from cluster_risk_checker import check_creator

    info = check_creator(address)
    return {
        'address': address,
        'in_cluster': info['in_cluster'],
        'cluster_id': info['cluster_id'],
        'risk_multiplier': info['risk_multiplier'],
        'risk_level': info['risk_label'],
        'network_size': info['network_size'],
        'timestamp': datetime.now().isoformat()
    }
```

### Example 3: Cron Monitoring
```bash
#!/bin/bash
# Run analyzer daily
0 2 * * * /opt/flex/cross_funding_network_analyzer.py

# Check for changes
0 3 * * * python3 ops_dashboard.py --db pumpswap_tokens.db --expected-clusters 9 | \
  grep -q "⚠️" && mail -s "ALERT: Flex cluster change" ops@example.com
```

---

## Deployment Checklist

- [ ] **Database**: Verify `pumpswap_tokens.db` exists and is accessible
- [ ] **Analyzer**: Run once to populate `funder_networks` table
- [ ] **Dashboard**: Test basic output
- [ ] **Risk Checker**: Test sample creator lookup
- [ ] **Monitoring**: Set up alerting (cluster count, FUNDERS_1 presence)
- [ ] **Integration**: Add cluster_risk_checker to your risk scoring pipeline
- [ ] **Cron Jobs**: Schedule analyzer runs (daily recommended)
- [ ] **Logging**: Verify output is captured and searchable

---

## File Directory

**Production Components**:
- `cross_funding_network_analyzer.py` — Batch analyzer
- `cluster_risk_checker.py` — Real-time lookups
- `ops_dashboard.py` — Monitoring dashboard

**Documentation**:
- `OPERATIONS_COMPLETE_SUMMARY.md` — This document's companion (detailed)
- `CROSS_FUNDING_ANALYZER_COMPLETE.md` — Analyzer technical reference
- `OPS_DASHBOARD_GUIDE.md` — Dashboard usage & deployment
- `PRODUCTION_INDEX.md` — You are here
- `DATA_VERIFICATION_FINAL.txt` — Verification queries & proofs
- `CLUSTER_SUMMARY_ACCURATE.txt` — Cluster breakdown with correct numbers

**Supporting**:
- `pumpswap_tokens.db` — SQLite database with all results
- `main.py` — Flask web UI (can integrate risk checker)
- `pumpfun_curve_listener.py` — Real-time listener (can call risk checker)

---

## Performance Summary

| Component | Metric | Value |
|-----------|--------|-------|
| **Analyzer** | Runtime | ~3 minutes |
| | CPU | Low (batch) |
| | Database writes | 41,734 rows |
| **Risk Checker** | Startup | <1 second |
| | Per lookup | <1 millisecond |
| | Memory | ~2 MB |
| **Dashboard** | Generation | <100 milliseconds |
| | Creator lookup | <10 milliseconds |
| | Database locks | None (read-only) |

**Safe for**:
- ✅ Real-time API endpoints
- ✅ High-frequency queries
- ✅ Concurrent access (read-only)
- ✅ Production monitoring

---

## Support & Troubleshooting

### "9 clusters is too few / too many"
Check: Did analyzer run? Did SYSTEM filtering/CEX downweighting apply? See [CROSS_FUNDING_ANALYZER_COMPLETE.md](CROSS_FUNDING_ANALYZER_COMPLETE.md) for sensitivity analysis.

### "Creator lookup returns in_cluster: False"
This is correct if creator isn't in a coordinated network. Only 634 out of 42,016 creators are in FUNDERS_1; 130 total across all clusters.

### "Database lock errors"
Use WAL mode (dashboards sets this automatically):
```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```

### "JSON1 not available" warning
Normal. Python fallback handles all data correctly. No action needed.

---

## What's Tracked

**In Clusters** (130 records):
- 95 coordinated funders (FUNDERS_1)
- 20 high-risk funders (FUNDERS_9)
- 3 medium-risk funders (FUNDERS_3)
- 6 x 2-person pairs (FUNDERS_2-8)
- **634 creators** receiving unusual co-funding

**Not in Clusters** (41,886 records):
- Single-creator funders (can't show co-funding)
- Low-overlap funders (below Jaccard threshold)
- SYSTEM addresses (filtered out)

---

## Next Steps

### Immediate (Do First)
1. Run analyzer: `python3 cross_funding_network_analyzer.py`
2. Verify dashboard: `python3 ops_dashboard.py --db pumpswap_tokens.db`
3. Test lookup: `python3 -c "from cluster_risk_checker import check_creator; print(check_creator('HYWo71Wk...'))`

### Short Term (Next Week)
1. Integrate `cluster_risk_checker` into risk scoring pipeline
2. Schedule daily analyzer runs
3. Set up ops_dashboard monitoring (6-hour intervals)
4. Create alerting on cluster changes

### Medium Term (Next Month)
1. Monitor FUNDERS_1 creators (634-person watchlist)
2. Track network growth metrics
3. Correlate with token rug patterns
4. Refine risk multipliers based on data

---

## Questions?

**For Analyzer Details**: See [CROSS_FUNDING_ANALYZER_COMPLETE.md](CROSS_FUNDING_ANALYZER_COMPLETE.md)

**For Dashboard Usage**: See [OPS_DASHBOARD_GUIDE.md](OPS_DASHBOARD_GUIDE.md)

**For Full Architecture**: See [OPERATIONS_COMPLETE_SUMMARY.md](OPERATIONS_COMPLETE_SUMMARY.md)

**For Data Verification**: See [DATA_VERIFICATION_FINAL.txt](DATA_VERIFICATION_FINAL.txt)

---

**Last Updated**: February 21, 2026
**Status**: ✅ PRODUCTION READY
**Tested**: All components verified working
**Ready to Deploy**: YES
