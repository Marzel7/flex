# Phase 2 Preview: Full Lifecycle Classification & Optimization

## Overview
After Phase 1 early signals achieve >= 70% accuracy, Phase 2 implements:
1. **Improved Monitoring Efficiency** - Dynamic snapshot frequency based on token age
2. **Smart Stop Conditions** - Recovery detection to prevent premature stops
3. **Cluster Intelligence** - Multi-dimensional cluster scoring beyond just rug rate
4. **Storage Optimization** - Snapshot downsampling and tiering strategy

**Timeline:** 2-3 weeks (parallel with Phase 1 accuracy validation)

## Phase 2 Components

### 1. Dynamic Monitoring Cadence

**Current:** Collect one snapshot per second indefinitely

**Phase 2:** Adaptive frequency based on token age and volatility

```
Age Range        | Cadence  | Purpose
-----------------|----------|------------------------------------------
0-10 min (Young) | 15-30s   | Catch early signals, detect fast crashes
10-60 min        | 60-120s  | Monitor growth trajectory, watch peaks
60-240 min       | 300s     | Track sustained performance
240+ min         | 600s     | Monitor long-term value, detect slow rugs
```

**Benefits:**
- 50-80% reduction in database rows
- Maintains full early signal accuracy
- Reduces computational load for old tokens
- Better resource allocation

**Implementation:**
```python
def get_snapshot_interval_seconds(age_minutes: int) -> int:
    """Adaptive snapshot collection interval"""
    if age_minutes < 10:
        return 20  # Every 20 seconds
    elif age_minutes < 60:
        return 90  # Every 90 seconds
    elif age_minutes < 240:
        return 300  # Every 5 minutes
    else:
        return 600  # Every 10 minutes
```

### 2. Smart Stop Conditions

**Current Logic:**
- Stop if: price down 90%+ AND peak < $100k OR lifecycle > 4 hours
- Risk: stops too early on recovering tokens

**Phase 2 Enhanced:**
- Add recovery detection before stopping
- Check if token bouncing back from dip
- Measure momentum recovery
- Allow grace period for legitimate pullbacks

```python
def evaluate_stop_conditions_v2(mint: str) -> Tuple[bool, str]:
    """Enhanced with recovery detection"""

    # Check recovery
    recovery = ClassificationEngineV2.detect_recovery(mint, snapshots)
    if recovery['is_recovering'] and recovery['recovery_ratio'] > 1.5:
        return False, "Recovering from early dip"

    # Check if rug crash happened
    if age < 30 and max_drawdown > 90:
        # Fast rug - stop immediately
        return True, "Fast rug detected"

    # Check slow decay
    if age > 240 and max_drawdown > 80 and final_mc < 5000:
        # Slow rug - stop after monitoring
        return True, "Slow rug detected"

    # Still growing/stable
    if velocity > 0 and liquidity_trend == 'increasing':
        return False, "Still growing"

    return False, "Continues monitoring"
```

**Benefits:**
- Avoid false stops on volatile legitimate tokens
- Better accuracy on slow-moving tokens
- Distinguish real rugs from dips

### 3. Cluster Intelligence Scoring

**Current:** Just rug_rate and success_rate

**Phase 2:** Multi-dimensional cluster score

```
Cluster Score = (success_rate × 0.30)
              + (consistency_score × 0.30)
              + (early_signal_accuracy × 0.20)
              + (avg_peak_mc_normalized × 0.20)

Where:
- success_rate: % of tokens reaching $50k+ final MC
- consistency_score: how similar tokens perform (low variance = good)
- early_signal_accuracy: % of 5-min predictions that match actual outcome
- avg_peak_mc_normalized: average peak market cap, scaled 0-1
```

**SQL to compute:**
```sql
WITH token_stats AS (
    SELECT
        cluster_id,
        COUNT(*) as total_tokens,
        SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success_count,
        SUM(CASE WHEN outcome = 'rug' THEN 1 ELSE 0 END) as rug_count,
        AVG(peak_market_cap) as avg_peak_mc,
        STDDEV(peak_market_cap) as mc_stddev,
        AVG(max_drawdown_pct) as avg_drawdown
    FROM token_outcomes
    WHERE cluster_id IS NOT NULL
    GROUP BY cluster_id
),
early_accuracy AS (
    SELECT
        cluster_id,
        COUNT(*) as early_predictions,
        SUM(CASE WHEN
            (early_label = 'likely_rug' AND outcome IN ('rug', 'slow_rug')) OR
            (early_label = 'likely_runner' AND outcome = 'success')
        THEN 1 ELSE 0 END) as correct_predictions
    FROM token_monitoring_state m
    JOIN token_outcomes o ON m.mint = o.mint
    WHERE m.early_label IS NOT NULL
    GROUP BY cluster_id
)
UPDATE cluster_outcome_stats cos
SET
    consistency_score = (1.0 - (ts.mc_stddev / NULLIF(ts.avg_peak_mc, 0))),
    success_momentum = ts.success_count::float / NULLIF(ts.total_tokens, 1),
    early_signal_accuracy = ea.correct_predictions::float / NULLIF(ea.early_predictions, 1),
    recommended_action = CASE
        WHEN (ts.success_count::float / ts.total_tokens) >= 0.6 THEN 'prioritize'
        WHEN (ts.rug_count::float / ts.total_tokens) >= 0.7 THEN 'avoid'
        ELSE 'watch'
    END
FROM token_stats ts
LEFT JOIN early_accuracy ea ON ts.cluster_id = ea.cluster_id
WHERE cos.cluster_id = ts.cluster_id;
```

**Benefits:**
- Identify creator patterns (some always produce high-quality tokens)
- Predict token success probability based on cluster history
- Recommend monitoring priorities by cluster
- Track early signal accuracy to identify which signals work best

### 4. Storage Optimization

**Current:** Every snapshot stored indefinitely (1-2 rows/sec × 24 hrs = 86k-172k rows/token)

**Phase 2:** Snapshot tiering and compression

```
Age         | Storage Strategy
------------|------------------------------------------
0-4 hours   | REALTIME - All snapshots (1-30s interval)
4-24 hours  | HOURLY - Keep 1 per hour
24-7 days   | DAILY - Keep 1 per day
7+ days     | ARCHIVED - Compress to summary stats
```

**Implementation:**
```python
def archive_old_snapshots(mint: str, db_path: str):
    """Compress snapshots older than 7 days"""
    conn = sqlite3.connect(db_path)

    # Compute 7-day summary
    summary = conn.execute("""
        SELECT
            AVG(price_usd) as avg_price,
            MIN(price_usd) as min_price,
            MAX(price_usd) as max_price,
            AVG(market_cap_usd) as avg_mc,
            SUM(volume_24h) as total_volume,
            MIN(timestamp) as period_start,
            MAX(timestamp) as period_end
        FROM token_lifecycle_snapshots
        WHERE mint = ? AND timestamp < ?
    """, (mint, now - 7*86400))

    # Delete original snapshots
    conn.execute("""
        DELETE FROM token_lifecycle_snapshots
        WHERE mint = ? AND timestamp < ?
    """, (mint, now - 7*86400))

    # Insert archive record
    conn.execute("""
        INSERT INTO snapshot_archives
        (mint, period_start, period_end, avg_price, min_price, max_price, ...)
        VALUES (?, ?, ?, ...)
    """, summary)

    conn.commit()
```

**Storage Impact:**
- 4 hours: ~86k rows (100 MB at 1KB/row)
- 4-24 hours: ~20 rows (2 KB)
- 24-7 days: ~7 rows (1 KB)
- **7+ days: 1 archive record (100 bytes)**

**Total for 1000 tokens over 7 days:** ~100 GB → ~15 GB (85% reduction)

## Phase 2 Implementation Order

**Week 1: Foundation**
1. Implement adaptive snapshot intervals
2. Add recovery detection to classification engine
3. Update monitoring loop to use new cadence
4. Test on 50 tokens, measure DB growth

**Week 2: Intelligence**
5. Compute cluster intelligence scores
6. Add cluster-based recommendations to monitoring
7. Track early signal accuracy per cluster
8. Dashboard: show cluster health scores

**Week 3: Optimization**
9. Implement snapshot downsampling (hourly/daily tiers)
10. Implement archive strategy (7-day compression)
11. Optimize indexes for tiered queries
12. Performance testing and tuning

## Success Criteria

- ✅ Storage growth reduced by 50%+ (while maintaining accuracy)
- ✅ Early signal accuracy >= 70% across all clusters
- ✅ Monitoring efficiency improved (fewer DB writes, faster queries)
- ✅ Can identify high-quality creator clusters vs pump-and-dump clusters
- ✅ False stop rate < 5% (recovery detection working)
- ✅ Can query 1000-token datasets in < 1 second

## Integration with Phase 1

Phase 1 early signals **feed into Phase 2**:

```
Phase 1 (5-15 min)
    ↓
early_label: likely_rug / likely_runner / unknown
early_score: 0-1 confidence
early_warning_flags: CSV of detected signals
    ↓
Phase 2 (every cycle)
    ↓
• Cluster accuracy tracking:
  "Of 100 'likely_rug' tokens from cluster X,
   how many actually became rugs?"
    ↓
• Adaptive monitoring:
  "Cluster X's early signals are 85% accurate,
   so STOP monitoring immediately when likely_rug"
  vs
  "Cluster Y's early signals are 50% accurate,
   so CONTINUE monitoring even if likely_rug"
    ↓
• Cluster scoring:
  "Cluster X: early_signal_accuracy = 0.85,
   consistency = 0.92, success_rate = 0.65
   → Overall score = (0.65×0.3 + 0.92×0.3 + 0.85×0.2 + ...) = 0.82"
```

## Key Metrics Tracked by Phase 2

**Per Cluster:**
- `consistency_score`: 0-1, how uniform token outcomes are
- `success_momentum`: trending success rate over time
- `early_signal_accuracy`: % of early predictions matching actual outcomes
- `recommended_action`: prioritize | watch | avoid
- `avg_time_to_peak_minutes`: cluster-typical peak time
- `volatility_index`: average volatility of cluster tokens

**Per Token:**
- `peak_type`: flash (< 2 min) | sustainable (5+ min) | final
- `peak_hold_time_minutes`: how long peak was maintained
- `peak_confidence`: 0-1 in peak classification

## Files to Create/Modify

**New Files:**
- `src/core/lifecycle_adaptive_monitoring.py` - Dynamic cadence logic
- `src/core/lifecycle_cluster_intelligence.py` - Cluster scoring
- `src/core/lifecycle_storage_optimizer.py` - Snapshot archival

**Modify:**
- `src/core/token_lifecycle.py` - Enhanced stop conditions
- `src/core/lifecycle_classification_v2.py` - Add recovery detection
- `src/core/lifecycle_schema_v2.py` - Add `snapshot_archives` table

## Validation Strategy

**Before Phase 2 Deployment:**
1. Backtest on historical token data (100+ tokens)
2. Compare Phase 1-only vs Phase 1+Phase 2:
   - Accuracy (should improve or stay same)
   - Storage (should decrease by 50%+)
   - Query speed (should stay < 100ms)
3. A/B test on small cluster (50 tokens)
4. Gradual rollout: 10% → 50% → 100%

---

**Phase 1 Complete. Ready for Phase 2 when accuracy >= 70% confirmed.**
