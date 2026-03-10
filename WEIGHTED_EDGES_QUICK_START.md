# Weighted Edge Metrics — Quick Start Guide

## Overview

The enhanced graph-based dev farm detection now measures **cluster strength** using edge weights (transfer count + amount + timing). This allows detecting genuinely coordinated farms from pattern analysis.

---

## Key Metrics Explained

### Time Concentration (0-1)
**What**: How burst-like transfers are on an edge
**High (0.85-1.0)**: Transfers bunched within same hour (strong coordination signal)
**Medium (0.5-0.8)**: Spread across multiple hours
**Low (0-0.5)**: Distributed across days/weeks

**SQL**:
```sql
-- Find burst patterns
SELECT * FROM farm_cluster_edges
WHERE time_concentration >= 0.85
ORDER BY time_concentration DESC;
```

### Composite Weight (0-100)
**What**: Combined strength of a single edge (source → destination)
**Formula**:
- Transfer count (0-30 points)
- Total amount (0-30 points)
- Time concentration (0-40 points)

**High (80-100)**: Heavy, consistent, burst-like funding
**Medium (50-80)**: Moderate coordination
**Low (0-50)**: Weak signal

**SQL**:
```sql
-- Find strongest individual funding paths
SELECT source_wallet, dest_wallet, composite_weight, transfer_count
FROM farm_cluster_edges
WHERE composite_weight >= 85
ORDER BY composite_weight DESC;
```

### Cluster Strength (0-100)
**What**: Overall coordination strength of entire dev farm cluster
**Formula**:
- 60% from edge weights (volume + consistency)
- 40% from timing patterns (burst concentration)

**Interpretation**:
- **90-100**: Extremely tight coordination (burst + high volume)
- **80-90**: Strong coordination
- **70-80**: Clear coordination pattern
- **50-70**: Likely coordination
- **0-50**: Weak signal

**SQL**:
```sql
-- Find strongest coordinated farms
SELECT cluster_id, funder_count, creator_count, cluster_strength
FROM farm_clusters
WHERE cluster_strength >= 85
ORDER BY cluster_strength DESC;
```

---

## Common Queries

### 1. Find Strongest Dev Farms (by Coordination)
```sql
SELECT
    cluster_id,
    funder_count,
    creator_count,
    cluster_strength,
    avg_composite_weight,
    avg_time_concentration,
    farm_risk_score
FROM farm_clusters
WHERE cluster_strength >= 80
ORDER BY cluster_strength DESC;
```

**Use**: Detect most likely coordinated dev farms regardless of risk score.

### 2. Find Burst Patterns (Pump.fun-like)
```sql
SELECT
    cluster_id,
    funder_count,
    creator_count,
    avg_time_concentration,
    cluster_strength
FROM farm_clusters
WHERE avg_time_concentration >= 0.85
ORDER BY avg_time_concentration DESC;
```

**Use**: Identify 1-hour burst funding patterns.

### 3. Find Heavy Hitters (Max Edge Weight)
```sql
SELECT
    cluster_id,
    max_edge_weight,
    avg_composite_weight,
    funder_count,
    creator_count
FROM farm_clusters
WHERE max_edge_weight >= 15
ORDER BY max_edge_weight DESC;
```

**Use**: Find clusters where single edge has many transfers.

### 4. Individual Edge Analysis
```sql
SELECT
    fce.cluster_id,
    fce.source_wallet,
    fce.dest_wallet,
    fce.transfer_count,
    fce.total_amount_sol,
    fce.time_concentration,
    fce.composite_weight,
    fc.cluster_strength
FROM farm_cluster_edges fce
JOIN farm_clusters fc USING(cluster_id)
WHERE fce.composite_weight >= 80
ORDER BY fce.composite_weight DESC;
```

**Use**: Inspect individual funding paths and their strength.

### 5. Combine Risk + Strength (Hybrid)
```sql
SELECT
    cluster_id,
    funder_count,
    creator_count,
    farm_risk_score,
    cluster_strength,
    CASE
        WHEN farm_risk_score >= 80 AND cluster_strength >= 80 THEN 'CRITICAL'
        WHEN farm_risk_score >= 60 OR cluster_strength >= 75 THEN 'HIGH'
        WHEN farm_risk_score >= 40 OR cluster_strength >= 60 THEN 'MEDIUM'
        ELSE 'LOW'
    END AS combined_risk
FROM farm_clusters
ORDER BY farm_risk_score DESC, cluster_strength DESC;
```

**Use**: Multi-factor risk assessment (traditional risk + coordination pattern).

### 6. Find Consistently Funded Creators
```sql
SELECT
    fce.dest_wallet,
    COUNT(DISTINCT fce.cluster_id) AS clusters_funded_by,
    AVG(fce.composite_weight) AS avg_edge_strength,
    SUM(fce.transfer_count) AS total_times_funded
FROM farm_cluster_edges fce
GROUP BY dest_wallet
HAVING total_times_funded >= 5
ORDER BY avg_edge_strength DESC;
```

**Use**: Identify creators who receive funding via high-strength edges.

---

## Interpreting Results

### Example 1: Strong Coordination
```
cluster_id: 42
funder_count: 3
creator_count: 8
cluster_strength: 92
avg_composite_weight: 87
avg_time_concentration: 0.91

Interpretation:
- Very tight coordination (cluster_strength = 92)
- Burst patterns detected (avg_time_concentration = 0.91)
- Consistent funding volumes (avg_composite_weight = 87)
- Action: HIGH PRIORITY for investigation
```

### Example 2: Moderate Pattern
```
cluster_id: 128
funder_count: 2
creator_count: 5
cluster_strength: 62
avg_composite_weight: 55
avg_time_concentration: 0.65

Interpretation:
- Moderate coordination signal
- Mixed timing (some burst, some spread)
- Moderate consistency
- Action: MONITOR (possible coordination)
```

### Example 3: Weak Signal
```
cluster_id: 256
funder_count: 2
creator_count: 3
cluster_strength: 35
avg_composite_weight: 38
avg_time_concentration: 0.42

Interpretation:
- Weak coordination signal
- Spread-out timing (no burst)
- Low consistency
- Action: LOW PRIORITY (likely unrelated wallets)
```

---

## Best Practices

1. **Use cluster_strength as primary filter** (better than risk_score alone)
   - `cluster_strength >= 80` → very high confidence coordination

2. **Combine with avg_time_concentration for burst detection**
   - `avg_time_concentration >= 0.85` AND `cluster_strength >= 70` → burst pattern

3. **Examine max_edge_weight for outliers**
   - Single very heavy edge suggests one key funding relationship

4. **Check individual edges** before alerting on entire cluster
   - Some clusters may have 1-2 strong edges + many weak ones

5. **Use vw_high_risk_farms view** (pre-filtered & sorted by strength)
   ```sql
   SELECT * FROM vw_high_risk_farms
   WHERE cluster_strength >= 75;
   ```

---

## Performance Tips

1. **Use indexes** — queries on `cluster_strength`, `farm_risk_score`, `avg_time_concentration` are fast
2. **Limit results** — `LIMIT 100` for large result sets
3. **Batch queries** — Combine multiple filters in WHERE clause
4. **Archive old data** — Tables grow ~1 MB per 50 farms

---

## Example: Real-World Alert

### Scenario
You want to alert on likely dev farm coordination patterns.

### Query
```sql
SELECT
    cluster_id,
    funder_count,
    creator_count,
    cluster_strength,
    avg_time_concentration,
    farm_risk_score,
    detected_at
FROM farm_clusters
WHERE cluster_strength >= 75
  AND avg_time_concentration >= 0.80
  AND funder_count >= 2
  AND creator_count >= 4
ORDER BY cluster_strength DESC;
```

### Interpretation
This returns clusters with:
- ✅ High coordination strength (75+)
- ✅ Burst patterns (0.80+ concentration)
- ✅ Multiple funders (2+)
- ✅ Multiple creators (4+)

### Action
Send alert to risk team: "High-confidence dev farm detected (Cluster {id}, Strength {score})"

---

## Backward Compatibility

- **Existing data**: Not affected (new columns default to 0)
- **New data**: Populated starting next cron run (4:30 AM UTC)
- **Queries**: Can mix old & new columns in same query
- **Views**: `vw_high_risk_farms` updated to show strength metrics

---

## Questions?

See `GRAPH_WEIGHTED_EDGES_ENHANCEMENT.md` for:
- Complete algorithm documentation
- Mathematical formulas
- Implementation details
- Performance analysis
