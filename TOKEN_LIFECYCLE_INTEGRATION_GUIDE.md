# Token Lifecycle System - Integration Guide

## Overview

You now have a complete token lifecycle tracking and classification system:

- **token_lifecycle.py**: Core monitoring logic, snapshot recording, classification
- **lifecycle_analytics.py**: Analytical queries and reporting
- **DATABASE**: 4 new tables with indexes

This guide shows how to integrate into your existing infrastructure.

---

## Quick Start

### 1. Initialize Tables

```python
from src.core.token_lifecycle import TokenLifecycleManager

manager = TokenLifecycleManager('database/flex_complete_database.db')
# Tables created automatically on first run
```

### 2. Start Monitoring Tokens

```python
# When a new token is detected
manager.start_monitoring(
    mint="EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6",
    cluster_id="cluster_abc123",
    creator="wallet_xyz"
)
```

### 3. Record Price Updates

```python
from src.core.token_lifecycle import TokenSnapshot
from datetime import datetime

snapshot = TokenSnapshot(
    mint="EPjFWdd5Au17hunZf0LCU5gS43sPUkAeP89SUNqmjV6",
    timestamp=int(datetime.now().timestamp()),
    price_usd=0.123,
    market_cap_usd=1_234_567,
    liquidity_usd=567_890,
    volume_24h=123_456,
    price_source="pool",
    cluster_id="cluster_abc123",
    creator="wallet_xyz"
)

manager.record_snapshot(snapshot)
```

### 4. Run Monitoring Cycle

```python
from src.core.token_lifecycle import LifecycleMonitoringWorker

worker = LifecycleMonitoringWorker('database/flex_complete_database.db')

# Run once per minute (or as needed)
results = worker.run_cycle()
print(f"Checked: {results['mints_checked']}, Stopped: {results['mints_stopped']}")
```

### 5. Query Results

```python
from src.core.lifecycle_analytics import LifecycleAnalytics

analytics = LifecycleAnalytics('database/flex_complete_database.db')

# Worst clusters
for cluster in analytics.worst_performing_clusters(limit=20):
    print(f"{cluster.cluster_name}: {cluster.rug_rate:.1%} rug rate")

# Best clusters
for cluster in analytics.best_performing_clusters(limit=20):
    print(f"{cluster.cluster_name}: {cluster.success_rate:.1%} success rate")

# Overall stats
stats = analytics.overall_stats()
print(f"Success rate: {stats['success_rate']:.1%}")
print(f"Rug rate: {stats['rug_rate']:.1%}")
```

---

## Integration Points

### Hook 1: New Token Detection

When a token is added to `tracked_tokens`, also start monitoring:

```python
# In your token detection code
mint = "..."
cluster_id = "..."

# Insert into tracked_tokens (existing)
# ...

# Start lifecycle monitoring (new)
manager.start_monitoring(mint, cluster_id)
```

### Hook 2: Price Updates

Your existing SSE price stream should feed snapshots:

```python
# In your price_stream handler
for price_update in updates:
    mint = price_update.mint
    price_usd = price_update.price_usd
    market_cap = price_update.market_cap

    # Record in token_lifecycle_snapshots
    snapshot = TokenSnapshot(
        mint=mint,
        timestamp=int(datetime.now().timestamp()),
        price_usd=price_usd,
        market_cap_usd=market_cap,
        price_source=price_update.source,
        cluster_id=get_cluster_id(mint)
    )
    manager.record_snapshot(snapshot)
```

### Hook 3: Monitoring Loop

Add a background worker that runs every 1-5 minutes:

```python
# In your main.py or scheduler
import threading
from src.core.token_lifecycle import LifecycleMonitoringWorker

worker = LifecycleMonitoringWorker(DB_PATH)

def lifecycle_monitoring_loop():
    while True:
        try:
            results = worker.run_cycle()
            if results['mints_stopped'] > 0:
                logger.info(f"[LIFECYCLE] {results['mints_stopped']} tokens classified this cycle")
        except Exception as e:
            logger.error(f"[LIFECYCLE] Worker error: {e}")

        time.sleep(300)  # Run every 5 minutes

# Start in background
threading.Thread(target=lifecycle_monitoring_loop, daemon=True).start()
```

### Hook 4: Cluster Stats Update

Periodically recompute cluster aggregates:

```python
# Once per hour or after major batches
manager.compute_cluster_stats()

# Or specific cluster
manager.compute_cluster_stats(cluster_id="cluster_abc123")
```

---

## Configuration

All thresholds are in `LifecycleConfig` (in token_lifecycle.py):

```python
# To customize, edit these values:
LifecycleConfig.RUG_THRESHOLD_MC = 5_000           # Stop if < $5k
LifecycleConfig.RUG_PEAK_MC = 100_000              # Rug if peak < $100k
LifecycleConfig.SUCCESS_PEAK_MC = 250_000          # Success if peak > $250k
LifecycleConfig.STALL_DETECTION_HOURS = 2         # 2 hours of low MC
```

### Tuning Guide

**For aggressive rug detection** (catch more rugs):
```python
LifecycleConfig.RUG_THRESHOLD_MC = 10_000          # Higher threshold
LifecycleConfig.STALL_DETECTION_HOURS = 1         # Faster stall detection
LifecycleConfig.RUG_DRAWDOWN_MIN_PCT = 70         # Lower drawdown required
```

**For conservative classification** (fewer false positives):
```python
LifecycleConfig.RUG_THRESHOLD_MC = 2_000           # Lower threshold
LifecycleConfig.STALL_DETECTION_HOURS = 4         # Slower stall detection
LifecycleConfig.RUG_DRAWDOWN_MIN_PCT = 90         # Higher drawdown required
```

---

## Database Schema Reference

### token_monitoring_state
Current status of each token being monitored.

```
mint                    TEXT PRIMARY KEY
monitor_status          active | stopped | completed
started_at              UNIX timestamp
stopped_at              UNIX timestamp (if stopped)
peak_market_cap         Highest MC observed
peak_market_cap_at      Timestamp of peak
last_market_cap         Current MC
last_snapshot_at        Last time we got a price update
snapshot_count          How many snapshots collected
outcome                 rug | slow_rug | success | neutral
```

### token_lifecycle_snapshots
Time-series data (can grow large - implement pruning after 30 days).

```
snapshot_id             AUTO INCREMENT
mint                    Token mint
timestamp               UNIX timestamp of this data point
price_usd               Price at this time
market_cap_usd          Market cap at this time
liquidity_usd           Liquidity at this time
price_source            Where price came from (pool, dexscreener, etc)
cluster_id              Cluster this token belongs to
creator                 Token creator wallet
created_at              When we recorded this
```

### token_outcomes
Final classification (one row per classified token).

```
mint                    TEXT PRIMARY KEY
outcome                 rug | slow_rug | success | neutral
outcome_score           0-1 confidence
peak_market_cap         Highest MC ever reached
final_market_cap        MC at time of classification
max_drawdown_pct        Percent decline from peak
time_to_peak_minutes    How long to reach peak
classification_reason   String explaining why (e.g., "peak_mc=50000<100000 && dd=85%>80%")
cluster_id              Cluster it came from
classified_at           UNIX timestamp
lifecycle_duration_min  Total time monitored
```

### cluster_outcome_stats
Pre-computed aggregate stats per cluster.

```
cluster_id              TEXT PRIMARY KEY
cluster_name            Human readable name
network_name            Network (Solana mainnet, testnet, etc)
total_tokens            Number of tokens classified
rug_count               How many rugs
success_count           How many successes
rug_rate                rug_count / total_tokens
success_rate            success_count / total_tokens
median_peak_market_cap  Median peak MC for cluster
computed_at             UNIX timestamp of computation
```

---

## Monitoring Recommendations

### Update Frequency

- **Token age 0-30 min**: Every 30-60 seconds (rapid changes possible)
- **Token age 30 min-6 hr**: Every 5 minutes (normal monitoring)
- **Token age 6+ hr**: Every 30 minutes (slow decay)
- **No price updates**: Skip until data available

### Stop Conditions (in priority order)

1. **Rug** (< $5k market cap) → Stop immediately
2. **Stall** (< $50k for 2+ hours) → Stop after confirmed
3. **Inactivity** (no updates for 60 min) → Stop
4. **Age** (7+ days) → Stop regardless of status
5. **Success** (sustained high MC) → Can continue or stop

### Snapshot Pruning

```python
# After 30 days, archive old snapshots (keep for 1 year)
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cutoff_date = int(datetime.now().timestamp()) - (30 * 86400)

# Move to archive (if you have one)
cursor.execute("""
    DELETE FROM token_lifecycle_snapshots
    WHERE created_at < ? AND snapshot_count > 1000
""", (cutoff_date,))

conn.commit()
```

---

## Analytics Queries

### 1. Which clusters produce most rugs?

```python
analytics = LifecycleAnalytics(DB_PATH)
worst_clusters = analytics.worst_performing_clusters(limit=20)

for cluster in worst_clusters:
    print(f"{cluster.cluster_name}: {cluster.rug_rate:.1%} rug rate ({cluster.total_tokens} tokens)")
```

### 2. Which clusters are consistently good?

```python
best_clusters = analytics.best_performing_clusters(limit=20)

for cluster in best_clusters:
    print(f"{cluster.cluster_name}: {cluster.success_rate:.1%} success rate")
```

### 3. Overall health

```python
stats = analytics.overall_stats()
print(f"Total classified: {stats['total_classified']}")
print(f"Success rate: {stats['success_rate']:.1%}")
print(f"Rug rate: {stats['rug_rate']:.1%}")
print(f"Avg peak MC: ${stats['avg_peak_mc']:,.0f}")
```

### 4. Fastest rugs (best indicators of cluster risk)

```python
fastest = analytics.fastest_rugs(limit=20)
for token in fastest:
    print(f"{token['mint'][:16]}... peaked in {token['time_to_peak_minutes']}min")
```

### 5. Biggest winners (high confidence success cases)

```python
winners = analytics.biggest_winners(limit=20)
for token in winners:
    print(f"{token['mint'][:16]}... → ${token['peak_market_cap']:,.0f}")
```

### 6. Recently classified tokens

```python
recent = analytics.recently_classified(limit=50)
for token in recent:
    print(f"{token['mint'][:16]}... {token['outcome']} (${token['peak_market_cap']:,.0f})")
```

---

## Performance Considerations

### Index Strategy

All tables have indexes on:
- Primary keys (mint, cluster_id)
- Time-based queries (timestamp, classified_at)
- Lookup columns (monitor_status, outcome)

### Query Optimization

When querying large result sets:

```python
# ✅ Good: Fast (indexed lookup)
SELECT * FROM token_outcomes WHERE outcome = 'rug'

# ✅ Good: Fast (time index)
SELECT * FROM token_lifecycle_snapshots
WHERE mint = ? AND timestamp > ?

# ❌ Slow: Full table scan
SELECT * FROM token_lifecycle_snapshots
WHERE YEAR(timestamp) = 2024
```

### Storage Estimation

Assuming:
- 10,000 tokens monitored
- Average 100 snapshots per token
- Each snapshot ~100 bytes

**Total storage**: ~100 MB (manageable)

After pruning old snapshots (30 days):
- 1,000 active tokens
- 48 snapshots per token (5 min intervals)
- ~5 MB active (very efficient)

---

## Debugging

### Check monitoring state

```python
manager = TokenLifecycleManager(DB_PATH)
active_mints = manager.get_active_tokens()
print(f"Currently monitoring {len(active_mints)} tokens")
```

### Check token trajectory

```python
analytics = LifecycleAnalytics(DB_PATH)
trajectory = analytics.token_trajectory("EPjF...")

for point in trajectory:
    print(f"{point.timestamp}: ${point.price_usd} (${point.market_cap_usd:,.0f}) → {point.pct_of_peak:.1f}% of peak")
```

### Check classification logic

```python
# Look at classification_reason in token_outcomes
SELECT mint, outcome, classification_reason
FROM token_outcomes
ORDER BY classified_at DESC
LIMIT 20;
```

---

## Next Steps

1. **Integrate price feed** (Hook 2) - Start recording snapshots
2. **Integrate token detection** (Hook 1) - Start monitoring new tokens
3. **Deploy monitoring loop** (Hook 3) - Run classification every 5 min
4. **Validate on historical data** - Backfill token_monitoring_state, run on existing tokens
5. **Tune thresholds** - Adjust RUG_THRESHOLD_MC, SUCCESS_PEAK_MC, etc.
6. **Add dashboard** - Use lifecycle_analytics queries for UI
7. **Monitor patterns** - Use cluster_consistency() to identify bad networks

---

## Support

For questions, check:
- `TOKEN_LIFECYCLE_SYSTEM_DESIGN.md` - Full architecture
- `src/core/token_lifecycle.py` - Implementation details
- `src/core/lifecycle_analytics.py` - Query examples
- Docstrings in classes/methods

