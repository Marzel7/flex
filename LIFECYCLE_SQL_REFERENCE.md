# Token Lifecycle - SQL Query Reference

Copy-paste ready queries for analysis and debugging.

---

## System Health Checks

### How many tokens are we monitoring?

```sql
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN monitor_status = 'active' THEN 1 ELSE 0 END) as active,
    SUM(CASE WHEN monitor_status = 'stopped' THEN 1 ELSE 0 END) as stopped,
    SUM(CASE WHEN monitor_status = 'completed' THEN 1 ELSE 0 END) as completed
FROM token_monitoring_state;
```

### How many tokens have been classified?

```sql
SELECT COUNT(*) as classified_tokens FROM token_outcomes;
```

### Overall outcome distribution

```sql
SELECT
    outcome,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM token_outcomes), 2) as percentage
FROM token_outcomes
GROUP BY outcome
ORDER BY count DESC;
```

---

## Cluster Analysis

### Worst-performing clusters (rug farms)

```sql
SELECT
    cluster_name,
    network_name,
    total_tokens,
    rug_count,
    slow_rug_count,
    success_count,
    ROUND(rug_rate * 100, 1) as rug_pct,
    ROUND(success_rate * 100, 1) as success_pct,
    ROUND(median_peak_market_cap, 0) as median_peak_mc
FROM cluster_outcome_stats
WHERE total_tokens >= 5
ORDER BY rug_rate DESC
LIMIT 30;
```

### Best-performing clusters (consistent winners)

```sql
SELECT
    cluster_name,
    network_name,
    total_tokens,
    success_count,
    rug_count,
    ROUND(success_rate * 100, 1) as success_pct,
    ROUND(rug_rate * 100, 1) as rug_pct,
    ROUND(median_peak_market_cap, 0) as median_peak_mc
FROM cluster_outcome_stats
WHERE total_tokens >= 5
ORDER BY success_rate DESC
LIMIT 30;
```

### Outcome split by network

```sql
SELECT
    network_name,
    outcome,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / (
        SELECT COUNT(*) FROM token_outcomes
        WHERE network_name IN (
            SELECT DISTINCT network_name FROM token_analysis ta
            WHERE ta.cluster_id = o.cluster_id
        )
    ), 2) as pct_of_network
FROM token_outcomes o
WHERE cluster_id IS NOT NULL
GROUP BY network_name, outcome
ORDER BY network_name, outcome;
```

---

## Token Classification

### Recently classified tokens (last 24 hours)

```sql
SELECT
    mint,
    outcome,
    ROUND(peak_market_cap, 0) as peak_mc,
    ROUND(final_market_cap, 0) as final_mc,
    ROUND(max_drawdown_pct, 1) as dd_pct,
    time_to_peak_minutes,
    lifecycle_duration_minutes,
    cluster_name,
    DATETIME(classified_at, 'unixepoch') as completed
FROM token_outcomes
WHERE classified_at > UNIXEPOCH('now') - 86400
ORDER BY classified_at DESC
LIMIT 100;
```

### Fastest rugs (peaked in < 30 minutes)

```sql
SELECT
    mint,
    outcome,
    ROUND(peak_market_cap, 0) as peak_mc,
    time_to_peak_minutes,
    ROUND(max_drawdown_pct, 1) as dd_pct,
    lifecycle_duration_minutes,
    cluster_name
FROM token_outcomes
WHERE outcome IN ('rug', 'slow_rug')
AND time_to_peak_minutes < 30
ORDER BY time_to_peak_minutes ASC, peak_market_cap ASC
LIMIT 50;
```

### Biggest winners (peak > $1M)

```sql
SELECT
    mint,
    outcome,
    ROUND(peak_market_cap, 0) as peak_mc,
    ROUND(final_market_cap, 0) as final_mc,
    ROUND(max_drawdown_pct, 1) as dd_pct,
    cluster_name
FROM token_outcomes
WHERE peak_market_cap > 1_000_000
ORDER BY peak_market_cap DESC
LIMIT 50;
```

### Successful tokens that held value (>50% of peak)

```sql
SELECT
    mint,
    ROUND(peak_market_cap, 0) as peak_mc,
    ROUND(final_market_cap, 0) as final_mc,
    ROUND(100.0 * final_market_cap / peak_market_cap, 1) as retained_pct,
    cluster_name
FROM token_outcomes
WHERE outcome = 'success'
AND final_market_cap >= (0.5 * peak_market_cap)
ORDER BY retained_pct DESC
LIMIT 50;
```

---

## Market Cap Distribution

### Distribution of peak market caps

```sql
SELECT
    (CASE
        WHEN peak_market_cap < 10_000 THEN '<$10k'
        WHEN peak_market_cap < 50_000 THEN '$10k-$50k'
        WHEN peak_market_cap < 100_000 THEN '$50k-$100k'
        WHEN peak_market_cap < 250_000 THEN '$100k-$250k'
        WHEN peak_market_cap < 500_000 THEN '$250k-$500k'
        WHEN peak_market_cap < 1_000_000 THEN '$500k-$1M'
        ELSE '>$1M'
    END) as mc_range,
    COUNT(*) as token_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM token_outcomes), 2) as pct_of_total,
    SUM(CASE WHEN outcome = 'rug' THEN 1 ELSE 0 END) as rug_count,
    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success_count
FROM token_outcomes
GROUP BY mc_range
ORDER BY peak_market_cap ASC;
```

### How quickly do tokens peak?

```sql
SELECT
    (CASE
        WHEN time_to_peak_minutes < 5 THEN '<5 min'
        WHEN time_to_peak_minutes < 15 THEN '5-15 min'
        WHEN time_to_peak_minutes < 30 THEN '15-30 min'
        WHEN time_to_peak_minutes < 60 THEN '30-60 min'
        WHEN time_to_peak_minutes < 120 THEN '1-2 hours'
        ELSE '>2 hours'
    END) as peak_time,
    COUNT(*) as token_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM token_outcomes), 2) as percentage,
    ROUND(AVG(CASE WHEN outcome = 'rug' THEN 1 ELSE 0 END) * 100, 1) as rug_rate_pct
FROM token_outcomes
WHERE time_to_peak_minutes IS NOT NULL
GROUP BY peak_time
ORDER BY time_to_peak_minutes ASC;
```

---

## Drawdown Analysis

### Average drawdown by outcome

```sql
SELECT
    outcome,
    COUNT(*) as token_count,
    ROUND(AVG(max_drawdown_pct), 1) as avg_dd_pct,
    ROUND(MIN(max_drawdown_pct), 1) as min_dd_pct,
    ROUND(MAX(max_drawdown_pct), 1) as max_dd_pct,
    ROUND(AVG(time_from_peak_to_finish_minutes), 0) as avg_time_to_recover_min
FROM token_outcomes
GROUP BY outcome
ORDER BY avg_dd_pct DESC;
```

### Severe drawdowns (>90%)

```sql
SELECT
    mint,
    outcome,
    ROUND(peak_market_cap, 0) as peak_mc,
    ROUND(final_market_cap, 0) as final_mc,
    ROUND(max_drawdown_pct, 1) as dd_pct,
    time_from_peak_to_finish_minutes as min_to_bottom,
    cluster_name
FROM token_outcomes
WHERE max_drawdown_pct > 90
ORDER BY max_drawdown_pct DESC
LIMIT 50;
```

---

## Timeline Analysis

### Outcomes over past 7 days

```sql
SELECT
    DATE(DATETIME(classified_at, 'unixepoch')) as day,
    outcome,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / (
        SELECT COUNT(*) FROM token_outcomes
        WHERE classified_at > UNIXEPOCH('now') - (7 * 86400)
    ), 1) as pct_of_day
FROM token_outcomes
WHERE classified_at > UNIXEPOCH('now') - (7 * 86400)
GROUP BY day, outcome
ORDER BY day DESC, outcome;
```

### Tokens monitored over time (growth)

```sql
SELECT
    DATE(DATETIME(started_at, 'unixepoch')) as day,
    COUNT(*) as started_today,
    (SELECT COUNT(*) FROM token_monitoring_state tms2
     WHERE DATE(DATETIME(tms2.started_at, 'unixepoch')) <= day) as cumulative_total
FROM token_monitoring_state
WHERE started_at > UNIXEPOCH('now') - (30 * 86400)
GROUP BY day
ORDER BY day DESC;
```

---

## Debugging & Troubleshooting

### Check tokens still being monitored (active)

```sql
SELECT
    mint,
    monitor_status,
    snapshot_count,
    ROUND(last_market_cap, 0) as last_mc,
    ROUND(peak_market_cap, 0) as peak_mc,
    DATETIME(last_snapshot_at, 'unixepoch') as last_update,
    DATETIME(started_at, 'unixepoch') as started
FROM token_monitoring_state
WHERE monitor_status = 'active'
ORDER BY last_snapshot_at DESC
LIMIT 50;
```

### Check tokens that should have been stopped (but weren't)

```sql
SELECT
    tms.mint,
    ROUND(tms.last_market_cap, 0) as current_mc,
    ROUND(tms.peak_market_cap, 0) as peak_mc,
    (UNIXEPOCH('now') - tms.last_snapshot_at) / 60 as min_since_update,
    tms.snapshot_count,
    DATETIME(tms.started_at, 'unixepoch') as started
FROM token_monitoring_state tms
WHERE tms.monitor_status = 'active'
AND (
    tms.last_market_cap < 5_000
    OR (UNIXEPOCH('now') - tms.last_snapshot_at) > 3600
    OR (UNIXEPOCH('now') - tms.started_at) > (7 * 86400)
)
ORDER BY tms.last_snapshot_at ASC
LIMIT 50;
```

### View classification logic for specific token

```sql
SELECT
    mint,
    outcome,
    outcome_score,
    peak_market_cap,
    final_market_cap,
    max_drawdown_pct,
    time_to_peak_minutes,
    classification_reason
FROM token_outcomes
WHERE mint = 'EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6';
```

### Get token lifecycle trajectory

```sql
SELECT
    timestamp,
    ROUND(price_usd, 8) as price,
    ROUND(market_cap_usd, 0) as mc,
    ROUND(100.0 * market_cap_usd / (
        SELECT MAX(market_cap_usd) FROM token_lifecycle_snapshots
        WHERE mint = 'EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6'
    ), 1) as pct_of_peak,
    DATETIME(timestamp, 'unixepoch') as time
FROM token_lifecycle_snapshots
WHERE mint = 'EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6'
ORDER BY timestamp ASC;
```

---

## Performance Tips

### Speed up cluster stats recompute

If `cluster_outcome_stats` gets slow:

```sql
-- Create TEMP table with pre-computed aggregates
CREATE TEMP TABLE _temp_outcomes AS
SELECT
    cluster_id,
    outcome,
    COUNT(*) as count
FROM token_outcomes
GROUP BY cluster_id, outcome;

-- Then join in UPDATE (faster)
UPDATE cluster_outcome_stats
SET
    rug_count = (SELECT count FROM _temp_outcomes WHERE outcome='rug' AND cluster_id=cluster_outcome_stats.cluster_id),
    success_count = (SELECT count FROM _temp_outcomes WHERE outcome='success' AND cluster_id=cluster_outcome_stats.cluster_id)
WHERE cluster_id IN (SELECT DISTINCT cluster_id FROM _temp_outcomes);
```

### Archive old snapshots

```sql
-- Move snapshots older than 30 days to archive (if you have one)
DELETE FROM token_lifecycle_snapshots
WHERE created_at < UNIXEPOCH('now') - (30 * 86400)
AND snapshot_id NOT IN (
    -- Keep first and last snapshot per token
    SELECT MIN(snapshot_id) FROM token_lifecycle_snapshots
    UNION ALL
    SELECT MAX(snapshot_id) FROM token_lifecycle_snapshots
);
```

### Rebuild indexes for performance

```sql
REINDEX idx_monitoring_status;
REINDEX idx_outcomes_outcome;
REINDEX idx_lcs_mint_time;
```

---

## Export / Reporting

### Export to CSV (all classified tokens)

```sql
-- Run and save output to CSV
SELECT
    mint,
    outcome,
    ROUND(peak_market_cap, 2) as peak_mc,
    ROUND(final_market_cap, 2) as final_mc,
    ROUND(max_drawdown_pct, 2) as dd_pct,
    time_to_peak_minutes,
    lifecycle_duration_minutes,
    cluster_name,
    DATETIME(classified_at, 'unixepoch') as classified_at
FROM token_outcomes
ORDER BY classified_at DESC;
```

### Export cluster health report

```sql
SELECT
    cluster_name,
    network_name,
    total_tokens,
    rug_count,
    slow_rug_count,
    success_count,
    neutral_count,
    ROUND(rug_rate * 100, 2) as rug_pct,
    ROUND(success_rate * 100, 2) as success_pct,
    ROUND(median_peak_market_cap, 0) as median_peak_mc,
    DATETIME(computed_at, 'unixepoch') as computed_at
FROM cluster_outcome_stats
ORDER BY rug_rate DESC;
```

