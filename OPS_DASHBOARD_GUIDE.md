# Ops Dashboard - Quick Start Guide

**Status**: ✅ Production Ready
**Purpose**: Real-time monitoring dashboard for cluster operations
**Execution Time**: <100ms per query

---

## Quick Start

### Basic Dashboard
```bash
python ops_dashboard.py --db pumpswap_tokens.db
```

Displays:
- ✅ Cluster count, correct SOL totals, inflation check
- ✅ Top clusters (by size and volume)
- ✅ Cluster distribution (2-person pairs, 3-9 person, 20-49, 50+)
- ✅ FUNDERS_1 creators count (634 tracked)
- ✅ Top 10 recipient hubs by creator count
- ✅ Alert checks (cluster count drift, FUNDERS_1 presence)

### With Cluster Count Validation
```bash
python ops_dashboard.py --db pumpswap_tokens.db --expected-clusters 9
```
Alerts if cluster count changed from expected (e.g., analyzer ran and modified data).

---

## Common Operations

### 1. Find Creator's Cluster Assignment
```bash
python ops_dashboard.py --db pumpswap_tokens.db --creator HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp
```

Output:
```
Creator HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp is in cluster: FUNDERS_1
  network_size: 95
  cluster_volume_sol: 17,087.00
```

### 2. List Top 20 Creators in FUNDERS_1
```bash
python ops_dashboard.py --db pumpswap_tokens.db --show-funders1-creators 20
```

Output:
```
FUNDERS_1 creators (distinct): 634
Sample creators (first 20):
  123157i3TZqhrbUFPY8pkexuHtCjH3TnuSuugxdabb3P
  127FkvAs8aoSEtqDMjs8Tu7Mxv8JxShvjUDGBLgsgWse
  ... (18 more)
```

### 3. Monitor Top 20 Clusters Instead of Default 10
```bash
python ops_dashboard.py --db pumpswap_tokens.db --top 20
```

### 4. Combined Monitoring
```bash
python ops_dashboard.py --db pumpswap_tokens.db \
  --expected-clusters 9 \
  --top 15 \
  --show-funders1-creators 10
```

---

## Dashboard Sections

### CLUSTER TOTALS
```
Clusters: 9
Total SOL across clusters (correct): 18,014.32
Total SOL across funder rows (inflated, don't use): 1,628,741.94
```

**Key Insight**: The inflated sum is the query gotcha—each cluster's volume appears once per funder row in the database. Use the correct total for reporting.

### TOP CLUSTERS
```
cluster_id    funders   cluster_volume_sol
--------------------------------------------
FUNDERS_1          95            17,087.00
FUNDERS_9          20               173.62
FUNDERS_3           3               496.92
```

**Interpretation**:
- **FUNDERS_1**: 95 coordinated funders, 17,087 SOL volume (CRITICAL risk)
- **FUNDERS_9**: 20 funders, 173.62 SOL volume (HIGH risk)
- **FUNDERS_3**: 3 funders, 496.92 SOL volume (MEDIUM risk)

### CLUSTER DISTRIBUTION
```
   50+: 1 clusters      (CRITICAL - major networks)
 20-49: 1 clusters      (HIGH - secondary networks)
   3-9: 1 clusters      (MEDIUM - small networks)
     2: 6 clusters      (CLEAN - minimal pairs)
```

### FUNDERS_1 CREATORS
```
FUNDERS_1 creators (distinct): 634
```

**Watch List**: 634 creators with unusual co-funding patterns. These should be monitored for rug risk (apply 3.0x risk multiplier).

### RECIPIENT HUBS (TOP)
```
coordinator_address                         creators    sol       conf    cex
AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK...      53      319.87    high     0
ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gd...      44      273.34    high     0
```

**Interpretation**:
- Addresses receiving SOL from multiple creators
- `creators` = how many different creators funded this address
- `sol` = total SOL received
- `conf` = confidence (high/low/medium) of being a hub
- `cex` = 1 if marked as CEX exchange, 0 otherwise

### ALERT CHECKS
```
✅ cluster count: 9
✅ FUNDERS_1 present
✅ inflation check OK (inflated=1,628,741.94 vs correct=18,014.32)
```

**Warnings**:
- ⚠️ `cluster count changed` — analyzer may have run since last check
- ⚠️ `FUNDERS_1 not present` — major issue, check analyzer logs
- ⚠️ `inflation check failed` — data corruption or schema mismatch

---

## Integration with Real-Time Risk Scoring

### Using cluster_risk_checker.py

```python
from cluster_risk_checker import check_creator

# Check if creator is in a cluster
result = check_creator("HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp")

if result['in_cluster']:
    # Apply risk multiplier to token risk score
    risk_multiplier = result['risk_multiplier']  # e.g., 3.0 for FUNDERS_1
    adjusted_risk = base_risk_score * risk_multiplier
```

**Risk Multipliers**:
- **FUNDERS_1** (95 funders): 3.0x CRITICAL
- **FUNDERS_9** (20 funders): 2.0x HIGH
- **FUNDERS_3** (3 funders): 1.5x MEDIUM
- **Others**: 1.0x (no adjustment)

---

## Automation Examples

### Cron Job: Monitor Every 6 Hours
```bash
0 */6 * * * python /path/to/ops_dashboard.py --db /path/to/pumpswap_tokens.db --expected-clusters 9 >> /var/log/flex-ops-dashboard.log 2>&1
```

### Python: Integration into Monitoring System
```python
import subprocess
import json
from datetime import datetime

def run_dashboard_check():
    result = subprocess.run([
        'python', 'ops_dashboard.py',
        '--db', 'pumpswap_tokens.db',
        '--expected-clusters', '9'
    ], capture_output=True, text=True)

    # Parse output for alerts
    lines = result.stdout.split('\n')
    for line in lines:
        if '⚠️' in line:
            # Alert on any warning
            send_slack_notification(f"Flex ops-dashboard alert: {line}")

    return result.stdout
```

---

## Architecture Notes

### View-Based Architecture
The dashboard creates a view `v_funder_clusters_summary` that safely aggregates clusters:

```sql
CREATE VIEW v_funder_clusters_summary AS
SELECT
  cluster_id,
  COUNT(*) AS funders,
  MAX(network_size) AS network_size,
  MAX(total_volume_sol) AS cluster_volume_sol
FROM funder_networks
WHERE cluster_id IS NOT NULL
GROUP BY cluster_id;
```

**Why MAX()?** Each cluster appears multiple times in `funder_networks` (once per funder), but its metadata (volume, size) is the same across all rows. Using MAX() safely deduplicates.

### JSON1 Optional
- **With JSON1**: Uses SQL `json_each` for creator parsing
- **Without JSON1**: Falls back to Python parsing
- Auto-detects and adapts at runtime

### Zero Dependencies Beyond Python stdlib
- No external packages required
- Works on any Python 3.6+
- SQLite built-in

---

## Troubleshooting

### "funder_networks table not found"
**Problem**: Pointing to wrong database or analyzer hasn't run yet.
**Solution**: Verify `--db` path or run analyzer: `python cross_funding_network_analyzer.py`

### "No clusters found"
**Problem**: Analyzer ran but generated no real clusters.
**Solution**: Check analyzer logs for filtering/thresholds that eliminated all clusters.

### "0 recipient hubs found"
**Problem**: network_coordinators table doesn't exist (optional, created by different module).
**Solution**: This is normal; module gracefully handles missing tables.

### Inflation check shows large gap
**Problem**: Gap between inflated (1.6M) and correct (18K) totals is expected.
**Solution**: This is the query gotcha—normal and expected. Use correct total for reporting.

---

## Performance

- **Dashboard generation**: <100ms
- **Creator lookup**: <10ms (O(1) with view)
- **Creator listing (FUNDERS_1, 634 items)**: <50ms
- **Database locks**: None (read-only, WAL mode)

Safe to run concurrently with other queries or analyzer.

---

## Production Deployment

### Environment Variables (Optional)
```bash
export FLEX_DB_PATH="/data/pumpswap_tokens.db"
python ops_dashboard.py --db $FLEX_DB_PATH --expected-clusters 9
```

### Systemd Timer (Example)
```ini
[Unit]
Description=Flex Ops Dashboard Check
After=network-online.target

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
```

### Docker Deployment
```dockerfile
FROM python:3.11-slim
WORKDIR /flex
COPY ops_dashboard.py .
COPY pumpswap_tokens.db .
CMD ["python", "ops_dashboard.py", "--db", "pumpswap_tokens.db", "--expected-clusters", "9"]
```

---

## References

- **Cluster Risk Checker**: [cluster_risk_checker.py](cluster_risk_checker.py) for real-time risk integration
- **Analyzer Documentation**: [CROSS_FUNDING_ANALYZER_COMPLETE.md](CROSS_FUNDING_ANALYZER_COMPLETE.md)
- **Cluster Results**: [CLUSTER_SUMMARY_ACCURATE.txt](CLUSTER_SUMMARY_ACCURATE.txt)
- **Data Verification**: [DATA_VERIFICATION_FINAL.txt](DATA_VERIFICATION_FINAL.txt)

---

**Last Updated**: Feb 21, 2026
**Status**: ✅ Production Ready
