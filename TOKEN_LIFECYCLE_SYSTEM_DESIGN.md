# Token Lifecycle & Outcome Tracking System - Complete Design

## Executive Summary

Build a rule-based token classification system that tracks lifecycle from launch → peak → outcome.
Classify tokens as: **rug**, **slow_rug**, **success**, **neutral**.
Aggregate by cluster to identify good vs bad launching networks.

---

## Database Schema

### Table 1: token_monitoring_state
Tracks which tokens are actively monitored and when to stop.

```sql
CREATE TABLE token_monitoring_state (
  mint TEXT PRIMARY KEY,
  monitor_status TEXT CHECK(monitor_status IN ('active', 'stopped', 'completed')),
  started_at INTEGER NOT NULL,
  stopped_at INTEGER,
  stop_reason TEXT,

  -- Peak tracking
  peak_market_cap REAL DEFAULT 0,
  peak_market_cap_at INTEGER,
  peak_price REAL DEFAULT 0,

  -- Current state (cached)
  last_market_cap REAL DEFAULT 0,
  last_price REAL DEFAULT 0,
  last_snapshot_at INTEGER,

  -- Lifecycle counters
  snapshot_count INTEGER DEFAULT 0,
  hours_monitored REAL DEFAULT 0,
  inactivity_minutes INTEGER DEFAULT 0,

  -- Outcome (when completed)
  outcome TEXT,
  outcome_computed_at INTEGER,

  FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
);

CREATE INDEX idx_monitoring_status ON token_monitoring_state(monitor_status);
CREATE INDEX idx_monitoring_stopped_at ON token_monitoring_state(stopped_at DESC);
```

### Table 2: token_lifecycle_snapshots
Time-series data per token (can be large - plan for pruning).

```sql
CREATE TABLE token_lifecycle_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  timestamp INTEGER NOT NULL,

  -- Price / market cap
  price_usd REAL,
  market_cap_usd REAL,
  liquidity_usd REAL DEFAULT 0,
  volume_24h REAL DEFAULT 0,

  -- Source
  price_source TEXT,

  -- Reference data (denormalized for fast queries)
  cluster_id TEXT,
  creator TEXT,

  created_at INTEGER NOT NULL,

  FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
);

CREATE INDEX idx_lcs_mint_time ON token_lifecycle_snapshots(mint, timestamp DESC);
CREATE INDEX idx_lcs_cluster_time ON token_lifecycle_snapshots(cluster_id, timestamp DESC);
CREATE INDEX idx_lcs_created ON token_lifecycle_snapshots(created_at DESC);
```

### Table 3: token_outcomes
Final classification (one row per token).

```sql
CREATE TABLE token_outcomes (
  mint TEXT PRIMARY KEY,
  outcome TEXT NOT NULL CHECK(outcome IN ('rug', 'slow_rug', 'success', 'neutral')),
  outcome_score REAL,  -- 0-1, how confident we are

  -- Peak metrics
  peak_market_cap REAL DEFAULT 0,
  peak_price REAL DEFAULT 0,
  time_to_peak_minutes INTEGER DEFAULT 0,

  -- Final metrics
  final_market_cap REAL DEFAULT 0,
  final_price REAL DEFAULT 0,

  -- Drawdown
  max_drawdown_pct REAL DEFAULT 0,
  time_from_peak_to_finish_minutes INTEGER DEFAULT 0,

  -- Classification logic trace (for debugging)
  classification_reason TEXT,

  -- Cluster context
  cluster_id TEXT,
  cluster_name TEXT,

  -- Dates
  classified_at INTEGER NOT NULL,
  lifecycle_duration_minutes INTEGER,

  FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)
);

CREATE INDEX idx_outcomes_outcome ON token_outcomes(outcome);
CREATE INDEX idx_outcomes_cluster ON token_outcomes(cluster_id);
CREATE INDEX idx_outcomes_classified_at ON token_outcomes(classified_at DESC);
```

### Table 4: cluster_outcome_stats (denormalized aggregates)
Pre-computed cluster analytics for fast dashboards.

```sql
CREATE TABLE cluster_outcome_stats (
  cluster_id TEXT PRIMARY KEY,
  cluster_name TEXT,
  network_name TEXT,

  -- Counts
  total_tokens INTEGER DEFAULT 0,
  rug_count INTEGER DEFAULT 0,
  slow_rug_count INTEGER DEFAULT 0,
  success_count INTEGER DEFAULT 0,
  neutral_count INTEGER DEFAULT 0,

  -- Rates
  rug_rate REAL DEFAULT 0,
  success_rate REAL DEFAULT 0,

  -- Metrics
  median_peak_market_cap REAL DEFAULT 0,
  median_final_market_cap REAL DEFAULT 0,
  median_max_drawdown_pct REAL DEFAULT 0,
  median_time_to_peak_minutes REAL DEFAULT 0,

  -- Computed
  computed_at INTEGER NOT NULL,

  FOREIGN KEY(cluster_id) REFERENCES super_clusters(cluster_id)
);

CREATE INDEX idx_cluster_stats_network ON cluster_outcome_stats(network_name);
```

---

## Monitoring Pipeline

### State Machine

```
1. New Token Detected
   ├─ Insert into token_monitoring_state (status='active')
   └─ Start snapshot collection

2. Active Monitoring (every 1-5 min)
   ├─ Collect snapshot → token_lifecycle_snapshots
   ├─ Update peak_market_cap if new high
   ├─ Check stop conditions

3. Stop Conditions (evaluate each cycle)
   ├─ IF market_cap < $5k for 30 min → STOP (rug)
   ├─ IF market_cap < $50k for 2+ hours → STOP (stall)
   ├─ IF no updates for 60 minutes → STOP (inactive)
   ├─ IF token age > 7 days → STOP (graduated)
   └─ IF market_cap > threshold for sustained period → STOP (success)

4. On Stop
   ├─ Set status='stopped'
   ├─ Classify outcome (see Classification Logic below)
   ├─ Insert into token_outcomes
   ├─ Update cluster_outcome_stats
   └─ Optionally prune old snapshots

5. Optional: Pause & Resume
   ├─ Pause if cluster is "bad" to save resources
   └─ Resume if cluster reputation improves
```

### Snapshot Cadence (Adaptive)

```
Age            Interval      Reason
─────────────────────────────────────
0-30 min       10-30 sec     Rapid changes possible
30 min-6 hr    1-5 min       Normal monitoring
6+ hr          15-60 min     Slow decay
No change      Skip          Save I/O
```

---

## Classification Logic

### Rug (Fast Failure)
```
IF peak_market_cap < $100k
   AND time_to_peak < 30 minutes
   AND max_drawdown > 80%
THEN outcome = 'rug'
   confidence = high
```

### Slow Rug (Gradual Decay)
```
IF peak_market_cap >= $50k
   AND max_drawdown >= 80%
   AND final_market_cap < $5k
THEN outcome = 'slow_rug'
   confidence = high
```

### Success (Sustained Growth)
```
IF peak_market_cap >= $250k
   AND final_market_cap >= $50k
   AND max_drawdown < 75%
THEN outcome = 'success'
   confidence = high
```

### Neutral (Everything Else)
```
ELSE outcome = 'neutral'
```

### Scoring Function (0-1 confidence)
```
confidence = (rule_match_count / total_rules) * specificity_multiplier

Example:
- Rug: Matches 4/4 rules → 1.0
- Slow rug: Matches 3/3 rules → 1.0
- Success: Matches 3/3 rules → 1.0
- Neutral: Default → 0.5
```

---

## SQL Queries for Analytics

### Query 1: Which clusters produce most rugs?
```sql
SELECT
  cluster_name,
  network_name,
  total_tokens,
  rug_count,
  ROUND(rug_rate * 100, 2) as rug_percentage,
  ROUND(success_rate * 100, 2) as success_percentage
FROM cluster_outcome_stats
WHERE total_tokens >= 10
ORDER BY rug_rate DESC
LIMIT 20;
```

### Query 2: Best-performing clusters
```sql
SELECT
  cluster_name,
  network_name,
  success_count,
  ROUND(success_rate * 100, 2) as success_percentage,
  ROUND(median_peak_market_cap, 0) as median_peak_mc
FROM cluster_outcome_stats
WHERE total_tokens >= 10
ORDER BY success_rate DESC
LIMIT 20;
```

### Query 3: Token lifecycle trajectory (single token)
```sql
SELECT
  timestamp,
  price_usd,
  market_cap_usd,
  ROUND(100 * (market_cap_usd / (
    SELECT MAX(market_cap_usd) FROM token_lifecycle_snapshots
    WHERE mint = ?
  )), 2) as pct_of_peak
FROM token_lifecycle_snapshots
WHERE mint = ?
ORDER BY timestamp ASC
LIMIT 1000;
```

### Query 4: Outcome distribution by network
```sql
SELECT
  network_name,
  outcome,
  COUNT(*) as count,
  ROUND(100 * COUNT(*) / (
    SELECT COUNT(*) FROM token_outcomes
    WHERE cluster_id IN (
      SELECT cluster_id FROM cluster_outcome_stats
      WHERE network_name = o.network_name
    )
  ), 2) as percentage
FROM token_outcomes o
GROUP BY network_name, outcome
ORDER BY network_name, outcome;
```

### Query 5: Recently completed tokens
```sql
SELECT
  t.mint,
  o.outcome,
  o.peak_market_cap,
  o.final_market_cap,
  ROUND(o.max_drawdown_pct, 2) as drawdown,
  o.time_to_peak_minutes,
  c.cluster_name,
  DATETIME(o.classified_at, 'unixepoch') as completed_at
FROM token_outcomes o
JOIN tracked_tokens t ON o.mint = t.mint
LEFT JOIN super_clusters c ON o.cluster_id = c.cluster_id
WHERE o.classified_at > UNIXEPOCH('now') - 86400
ORDER BY o.classified_at DESC
LIMIT 100;
```

---

## Implementation Strategy

### Phase 1: Schema & Setup (Day 1)
- [ ] Create the 4 new tables
- [ ] Create indexes
- [ ] Backfill token_monitoring_state for existing tokens

### Phase 2: Monitoring Worker (Day 2)
- [ ] Build monitoring loop
  - Poll active tokens
  - Collect snapshots
  - Update state
  - Evaluate stop conditions
- [ ] Implement snapshot collection (leverage existing price_stream)
- [ ] Test on 10-20 tokens

### Phase 3: Classification & Outcomes (Day 3)
- [ ] Implement classification rules
- [ ] Build outcome computation
- [ ] Test edge cases
- [ ] Add classification debugging output

### Phase 4: Aggregation & Reporting (Day 4)
- [ ] Compute cluster_outcome_stats
- [ ] Build analytical queries
- [ ] Add dashboard integration
- [ ] Performance tune large queries

### Phase 5: Optimization & Tuning (Day 5)
- [ ] Snapshot pruning strategy
- [ ] Query optimization
- [ ] Monitoring efficiency (adaptive cadence)
- [ ] Alerts for anomalies

---

## Configuration & Thresholds

All thresholds should be tunable constants:

```python
LIFECYCLE_CONFIG = {
    # Stop conditions
    'rug_threshold_mc': 5_000,              # Stop if < $5k for sustained period
    'stall_threshold_mc': 50_000,          # Stop if < $50k for 2 hours
    'inactivity_threshold_min': 60,        # Stop if no updates for 60 min
    'max_monitoring_age_days': 7,          # Stop after 7 days regardless

    # Classification
    'rug_peak_mc': 100_000,                 # Peak < $100k → potential rug
    'rug_time_to_peak_min': 30,            # Peak within 30 min → rug
    'rug_drawdown_min_pct': 80,            # Drawdown > 80% → rug

    'slow_rug_peak_mc': 50_000,            # Peak > $50k
    'slow_rug_drawdown_min_pct': 80,       # Drawdown > 80%
    'slow_rug_final_mc': 5_000,            # Final < $5k

    'success_peak_mc': 250_000,            # Peak > $250k
    'success_final_mc': 50_000,            # Final > $50k
    'success_max_drawdown_pct': 75,        # Max drawdown < 75%

    # Snapshot cadence (minutes)
    'cadence_early': (0, 30, 0.5),         # Age 0-30 min: snapshot every 30 sec
    'cadence_normal': (30, 360, 5),        # Age 30 min-6 hr: every 5 min
    'cadence_late': (360, 10080, 30),      # Age 6+ hr: every 30 min

    # Storage
    'snapshot_retention_days': 30,         # Keep snapshots for 30 days then archive
    'aggregate_snapshot_interval_h': 24,   # Aggregate to hourly after 24 hours
}
```

---

## Benefits

1. **Pattern Recognition**: Identify which clusters consistently produce rugs
2. **Predictive Signals**: Use rug-ness patterns as features for next generation
3. **Resource Optimization**: Stop monitoring dead tokens early, focus on winners
4. **Explainability**: Every classification has a clear reason
5. **Debuggability**: Full snapshot history for every token
6. **Scalability**: Efficient querying via indexes and aggregates

---

## Edge Cases & Handling

| Case | Handling |
|------|----------|
| Token never reaches ATH | Classify as neutral, monitor longer |
| Gap in price data (24h+) | Treat as inactive, stop monitoring |
| Extreme outlier price | Flag as data error, skip snapshot |
| Very young token (< 5 min) | Defer classification until older |
| Token migrates to different pool | Track via mint (immutable) |
| Cluster renamed/merged | Use cluster_id as primary key |

---

## Next Steps

1. Run Phase 1 schema creation
2. Backfill monitoring_state for existing tokens from token_analysis
3. Build Phase 2 monitoring worker
4. Start collecting real data
5. Validate classification rules with historical tokens
6. Build dashboard queries

