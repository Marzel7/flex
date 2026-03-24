# Token Lifecycle System - Predictive Intelligence Engine

Complete design for early signal detection, improved classification, and cluster intelligence.

---

## OVERVIEW

Transform the lifecycle system from **reactive classification** (hours after launch) to **predictive intelligence** (5-10 minutes after launch).

**Key capabilities:**
- Predict likely success/rug within 5-10 minutes (70%+ accuracy)
- Score cluster quality early (before many tokens classified)
- Optimize monitoring (dynamic frequency, smart stops)
- Enable real-time decision making (monitor good tokens, skip bad ones)

---

## PART 1: EARLY SIGNAL ENGINE

### 1.1 Schema Changes

Add these fields to `token_monitoring_state`:

```sql
ALTER TABLE token_monitoring_state ADD COLUMN (
    -- Early prediction (computed at 5-10 minutes)
    early_score REAL DEFAULT 0,                    -- 0-1, higher = more confident
    early_label TEXT,                              -- 'likely_rug' | 'likely_runner' | 'unknown'
    early_prediction_computed_at INTEGER,

    -- Timeline milestones
    time_to_10k_mc_seconds INTEGER,               -- When did MC cross $10k?
    time_to_50k_mc_seconds INTEGER,               -- When did MC cross $50k?
    time_to_100k_mc_seconds INTEGER,              -- When did MC cross $100k?

    -- Velocity metrics (updated every snapshot)
    velocity_current_pct_per_min REAL DEFAULT 0,  -- Current % growth per minute
    velocity_peak_pct_per_min REAL DEFAULT 0,     -- Peak velocity ever observed
    velocity_decay_rate REAL DEFAULT 0,            -- How fast is velocity declining?

    -- Early drawdown (first critical metric)
    early_drawdown_pct REAL DEFAULT 0,            -- % decline from peak in first 5 min
    has_recovered_from_early_dip INT DEFAULT 0,   -- Did it bounce back?

    -- Liquidity quality
    liquidity_to_mc_ratio REAL DEFAULT 0,         -- Liquidity / Market Cap (0-1, higher = better)
    liquidity_trend TEXT DEFAULT 'flat',          -- 'increasing' | 'decreasing' | 'flat'

    -- Risk signals
    early_rug_score REAL DEFAULT 0,               -- 0-1, likelihood of rug
    early_success_score REAL DEFAULT 0,           -- 0-1, likelihood of success
    early_warning_flags TEXT DEFAULT ''           -- CSV: 'dead_pool', 'flash_crash', 'no_volume'
);
```

### 1.2 Early Score Computation Function

```python
def compute_early_score(mint, current_age_minutes):
    """
    Score token within first 5-10 minutes.
    Returns: (early_score, early_label, confidence, signals, warnings)

    Score = weighted sum of positive and negative signals

    Early labels:
    - 'likely_rug': early_rug_score >= 0.65 AND confidence >= 0.6
    - 'likely_runner': early_success_score >= 0.60 AND confidence >= 0.6
    - 'unknown': lower confidence or mixed signals

    Note: These are PREDICTIONS, not final classifications.
    Accuracy: ~70-75% (vs 85-90% for full lifecycle classification)
    """

    if current_age_minutes < 5:
        return None  # Wait for more data

    if current_age_minutes > 15:
        return None  # Switch to full classification after 15 min

    # Get current metrics
    metrics = get_current_metrics(mint)
    snapshots = get_snapshots(mint, limit=20)  # Last 20 snapshots

    if not snapshots or len(snapshots) < 3:
        return None  # Not enough data

    # ===== RUG PROBABILITY CALCULATION =====

    rug_score = 0
    rug_signals = []

    # Signal 1: No velocity (dead pool or whale dump)
    if metrics['velocity_current_pct_per_min'] < 0.5:
        rug_score += 0.25
        rug_signals.append('no_velocity')

    # Signal 2: Negative velocity (price declining)
    if metrics['velocity_current_pct_per_min'] < -0.5:
        rug_score += 0.30
        rug_signals.append('negative_velocity')

    # Signal 3: Early crash (50%+ loss from peak in <5 min)
    if metrics['early_drawdown_pct'] > 50 and current_age_minutes <= 5:
        rug_score += 0.40
        rug_signals.append(f'early_crash_{metrics["early_drawdown_pct"]:.0f}pct')

    # Signal 4: Never recovered from early dip
    if (metrics['early_drawdown_pct'] > 30 and
        not metrics['has_recovered_from_early_dip']):
        rug_score += 0.20
        rug_signals.append('no_recovery_from_dip')

    # Signal 5: Poor liquidity ratio (pump on low liquidity)
    if metrics['liquidity_to_mc_ratio'] < 0.05:  # Liquidity < 5% of MC
        rug_score += 0.20
        rug_signals.append(f'poor_liquidity_{100*metrics["liquidity_to_mc_ratio"]:.1f}pct')

    # Signal 6: Liquidity declining (rug pull signals)
    if metrics['liquidity_trend'] == 'decreasing':
        rug_score += 0.25
        rug_signals.append('liquidity_declining')

    # Signal 7: Never reached $10k MC (failed launch)
    if (current_age_minutes >= 10 and
        metrics.get('peak_market_cap', 0) < 10_000):
        rug_score += 0.35
        rug_signals.append('never_reached_10k')

    # Signal 8: Rapid velocity decay (losing momentum fast)
    if metrics['velocity_decay_rate'] > 0.8:  # Losing 80% of velocity per minute
        rug_score += 0.20
        rug_signals.append('rapid_velocity_decay')

    # Signal 9: Extended dead pool (no trades for 60+ seconds)
    if metrics.get('seconds_since_last_trade', 0) > 60:
        rug_score += 0.30
        rug_signals.append(f'dead_pool_{metrics.get("seconds_since_last_trade", 0)}sec')

    # Normalize rug score (0-1)
    early_rug_score = min(rug_score, 1.0)

    # ===== SUCCESS PROBABILITY CALCULATION =====

    success_score = 0
    success_signals = []

    # Signal 1: Strong initial velocity (>10% per minute)
    if metrics['velocity_current_pct_per_min'] > 10:
        success_score += 0.25
        success_signals.append(f'strong_velocity_{metrics["velocity_current_pct_per_min"]:.1f}pct_per_min')

    # Signal 2: Reached $50k MC quickly (<5 min)
    if (metrics.get('time_to_50k_mc_seconds', 999999) < 300 and
        metrics.get('peak_market_cap', 0) >= 50_000):
        success_score += 0.30
        success_signals.append(f'reached_50k_in_{metrics.get("time_to_50k_mc_seconds", 0)}sec')

    # Signal 3: Stable price (low volatility = confidence)
    if metrics['price_volatility_5min'] < 0.25:  # <25% volatility
        success_score += 0.20
        success_signals.append(f'stable_price_vol_{100*metrics["price_volatility_5min"]:.1f}pct')

    # Signal 4: Growing volume (increasing interest)
    if metrics['volume_trend'] == 'increasing':
        success_score += 0.20
        success_signals.append('volume_increasing')

    # Signal 5: Good liquidity support (>10% of MC)
    if metrics['liquidity_to_mc_ratio'] > 0.10:
        success_score += 0.25
        success_signals.append(f'good_liquidity_{100*metrics["liquidity_to_mc_ratio"]:.1f}pct')

    # Signal 6: Liquidity growing (builders adding support)
    if metrics['liquidity_trend'] == 'increasing':
        success_score += 0.15
        success_signals.append('liquidity_growing')

    # Signal 7: Positive momentum (accelerating growth)
    if metrics['velocity_decay_rate'] < 0.3:  # Not losing momentum
        success_score += 0.15
        success_signals.append(f'momentum_momentum={1-metrics["velocity_decay_rate"]:.2f}')

    # Signal 8: Buy pressure > sell pressure
    if metrics['buy_volume_ratio'] > 0.65:  # >65% buys
        success_score += 0.20
        success_signals.append(f'buy_pressure_{100*metrics["buy_volume_ratio"]:.0f}pct')

    # Signal 9: Growing holder count (new investors joining)
    if metrics['holder_count_change_pct'] > 50:  # +50% holders in 5 min
        success_score += 0.15
        success_signals.append(f'holders_growing_{100*metrics["holder_count_change_pct"]:.0f}pct')

    # Normalize success score (0-1)
    early_success_score = min(success_score, 1.0)

    # ===== DETERMINE EARLY LABEL =====

    # Calculate overall confidence
    score_diff = abs(early_success_score - early_rug_score)
    base_confidence = min(max(early_success_score, early_rug_score) * 0.8, 1.0)

    # Increase confidence if signals align
    if score_diff > 0.2:  # Clear winner
        confidence = min(base_confidence + 0.15, 1.0)
    else:  # Mixed signals
        confidence = base_confidence

    # Assign early label
    if early_rug_score >= 0.65 and confidence >= 0.60:
        early_label = 'likely_rug'
    elif early_success_score >= 0.60 and confidence >= 0.60:
        early_label = 'likely_runner'
    else:
        early_label = 'unknown'

    # Collect warnings
    warnings = []
    if 'dead_pool' in rug_signals:
        warnings.append('dead_pool')
    if 'poor_liquidity' in rug_signals:
        warnings.append('low_liquidity')
    if 'flash_crash' in rug_signals:
        warnings.append('flash_crash')

    return {
        'early_score': max(early_success_score, early_rug_score),
        'early_label': early_label,
        'confidence': confidence,
        'early_rug_score': early_rug_score,
        'early_success_score': early_success_score,
        'rug_signals': rug_signals,
        'success_signals': success_signals,
        'warnings': warnings,
        'recommendation': (
            'STOP_MONITORING' if early_label == 'likely_rug' else
            'PRIORITIZE' if early_label == 'likely_runner' else
            'CONTINUE_MONITORING'
        )
    }
```

### 1.3 Update Monitoring State with Early Signals

```python
def record_early_signal(manager, mint, early_signal):
    """
    Store early prediction in token_monitoring_state.
    Use for real-time decision making.
    """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now = int(datetime.now().timestamp())

    cursor.execute("""
        UPDATE token_monitoring_state
        SET
            early_score = ?,
            early_label = ?,
            early_prediction_computed_at = ?,
            early_rug_score = ?,
            early_success_score = ?,
            early_warning_flags = ?
        WHERE mint = ?
    """, (
        early_signal['early_score'],
        early_signal['early_label'],
        now,
        early_signal['early_rug_score'],
        early_signal['early_success_score'],
        ','.join(early_signal['warnings']),
        mint
    ))

    conn.commit()
    conn.close()
```

---

## PART 2: IMPROVED CLASSIFICATION LOGIC

### 2.1 Enhanced Rules with Velocity & Momentum

```python
def classify_outcome_v2(mint, token_metrics):
    """
    Enhanced classification incorporating velocity, momentum, floor holding.
    More nuanced than V1.
    """

    # Get metrics
    peak_mc = token_metrics['peak_market_cap']
    final_mc = token_metrics['final_market_cap']
    max_dd = token_metrics['max_drawdown_pct']
    ttp = token_metrics['time_to_peak_minutes']

    velocity_decay = token_metrics.get('velocity_decay_rate', 0)
    sustained_floor = final_mc / peak_mc if peak_mc > 0 else 0
    lifecycle_duration = token_metrics['lifecycle_duration_minutes']

    score = 0
    confidence = 0
    reason_parts = []

    # ===== RUG CLASSIFICATION =====
    if (peak_mc < 100_000 and
        ttp < 30 and
        max_dd > 80):

        # Additional check: Did it crash FAST?
        time_to_50pct_dd = token_metrics.get('time_from_peak_to_50pct_drawdown_min', 999)

        if time_to_50pct_dd < 5:  # Crashed in <5 min = clear rug
            score = 1.0
            confidence = 0.95
            reason_parts.append(f'rug_signature:peak={peak_mc:.0f}<100k,ttp={ttp}<30min,dd={max_dd:.1f}>80,crash_time={time_to_50pct_dd}<5min')
        else:  # Slower decay = slow_rug instead
            score = 0.5  # Not a rug
            reason_parts.append('gradual_not_fast_failure')

    # ===== SLOW RUG CLASSIFICATION =====
    elif (peak_mc >= 50_000 and
          max_dd >= 80 and
          final_mc < 5_000):

        # Check it wasn't fast (that would be rug)
        time_to_50pct_dd = token_metrics.get('time_from_peak_to_50pct_drawdown_min', 999)

        if time_to_50pct_dd > 10:  # Took >10 min to lose 50% = gradual
            score = 1.0
            confidence = 0.92
            reason_parts.append(f'slow_rug:peak={peak_mc:.0f}>50k,dd={max_dd:.1f}>80,final={final_mc:.0f}<5k,gradual_decline={time_to_50pct_dd}>10min')
        else:  # Actually just a rug with longer tail
            score = 0.8  # More likely rug
            reason_parts.append('rapid_but_labeled_slow_rug')

    # ===== SUCCESS CLASSIFICATION =====
    elif (peak_mc >= 250_000 and
          final_mc >= 50_000 and
          max_dd <= 75):

        # Additional checks for true success
        # Check 1: Did it sustain above 50% of peak?
        if sustained_floor >= 0.5:
            score = 1.0
            confidence = 0.90
            reason_parts.append(f'success_sustained:peak={peak_mc:.0f}>250k,final={final_mc:.0f}>50k,dd={max_dd:.1f}<75,retention={sustained_floor:.1%}')

        # Check 2: Even if below 50%, still high absolute value
        elif final_mc >= 100_000:
            score = 0.9
            confidence = 0.85
            reason_parts.append(f'success_partial:peak={peak_mc:.0f},final={final_mc:.0f}>100k,retention={sustained_floor:.1%}')
        else:
            score = 0.6
            reason_parts.append(f'success_but_low_retention:{sustained_floor:.1%}')

    # ===== RUNNER (new category: early success indicators, not yet graduated) =====
    elif (peak_mc >= 250_000 and
          final_mc >= 100_000 and
          lifecycle_duration < 240):  # <4 hours

        # Still rising or stable, not classified yet
        score = 0.8
        confidence = 0.80
        reason_parts.append(f'runner:peak={peak_mc:.0f},final={final_mc:.0f},still_active_<4h')

    # ===== NEUTRAL (EVERYTHING ELSE) =====
    else:
        score = 0.5
        confidence = 0.5
        reason_parts.append('neutral_mixed_or_unclear_signals')

    # Determine final outcome
    if score >= 0.85:
        if reason_parts[0].startswith('rug_'):
            outcome = 'rug'
        elif reason_parts[0].startswith('slow_rug'):
            outcome = 'slow_rug'
        elif reason_parts[0].startswith('success') or reason_parts[0].startswith('runner'):
            outcome = 'success'
        else:
            outcome = 'neutral'
    elif score >= 0.6:
        outcome = 'slow_rug' if 'slow_rug' in reason_parts[0] else 'neutral'
    else:
        outcome = 'neutral'

    return {
        'outcome': outcome,
        'outcome_score': confidence,
        'reason': ' && '.join(reason_parts),
        'metrics': {
            'peak_mc': peak_mc,
            'final_mc': final_mc,
            'max_drawdown': max_dd,
            'time_to_peak': ttp,
            'sustained_floor': sustained_floor,
            'velocity_decay': velocity_decay
        }
    }
```

---

## PART 3: DYNAMIC MONITORING CADENCE

### 3.1 Adaptive Snapshot Frequency

```python
def get_snapshot_interval_seconds(token_metrics):
    """
    Return sampling interval based on token lifecycle stage.
    Reduces snapshots by ~60% while maintaining signal quality.

    Target: Dense early data, sparse late data
    """

    age_minutes = (time.time() - token_metrics['started_at']) / 60
    current_mc = token_metrics['last_market_cap']
    velocity = abs(token_metrics['velocity_current_pct_per_min'])
    momentum = token_metrics.get('momentum_score', 0)

    # Phase 1: Launch chaos (0-10 minutes) - Every snapshot matters
    if age_minutes < 10:
        if velocity > 20:  # Explosive growth
            return 15  # seconds - capture momentum
        elif velocity < -5:  # Rapid collapse
            return 15  # seconds - capture crash
        else:
            return 30  # seconds - capture activity

    # Phase 2: Early growth/collapse (10-60 minutes) - Less frequent
    elif age_minutes < 60:
        if current_mc < 5_000:  # Dead token
            return 300  # 5 minutes - slow sampling
        elif current_mc < 50_000:  # Struggling
            return 120  # 2 minutes
        else:  # Growing
            return 60  # 1 minute

    # Phase 3: Mid stage (1-6 hours) - Infrequent
    elif age_minutes < 360:
        if current_mc < 10_000:
            return 600  # 10 minutes
        elif current_mc < 100_000:
            return 300  # 5 minutes
        else:
            return 180  # 3 minutes

    # Phase 4: Late stage (6+ hours) - Minimal sampling
    else:
        return 600  # 10 minutes

    return 300  # Default fallback
```

### 3.2 Smart Stop Conditions

```python
def should_stop_monitoring_v2(mint, token_metrics):
    """
    Decide if token should stop being monitored.
    More nuanced to avoid premature stops and catch recoveries.

    Returns: (should_stop, reason)
    """

    age_minutes = (time.time() - token_metrics['started_at']) / 60
    current_mc = token_metrics['last_market_cap']
    peak_mc = token_metrics['peak_market_cap']
    momentum = token_metrics.get('momentum_score', 0)
    seconds_since_trade = token_metrics.get('seconds_since_last_trade', 0)
    early_label = token_metrics.get('early_label', 'unknown')

    # STOP 1: Definite rug - immediate stop
    if (early_label == 'likely_rug' and
        age_minutes > 10):
        return True, 'early_rug_signal_confirmed'

    # STOP 2: Dead pool - no activity
    if (seconds_since_trade > 600 and  # 10 min no trades
        age_minutes > 30):
        return True, 'no_activity_10min'

    # STOP 3: Confirmed failure (MC collapsed & staying low)
    if (current_mc < 1_000 and
        momentum < -0.9 and
        age_minutes > 10):
        return True, 'confirmed_failure'

    # STOP 4: Success - graduated
    if (current_mc > 250_000 and
        current_mc > peak_mc * 0.5 and
        momentum > 0.2 and
        age_minutes > 120):  # 2+ hours successful
        return True, 'success_graduated'

    # STOP 5: Age limit
    if age_minutes > (7 * 24 * 60):  # 7 days
        return True, 'aged_out'

    # DON'T STOP: Recovery case
    if (current_mc < 50_000 and
        momentum > 0.6 and
        seconds_since_trade < 60):  # Active trading with upward momentum
        return False, None  # Keep watching, might recover

    # DON'T STOP: Early runner
    if early_label == 'likely_runner':
        return False, None  # Let it play out

    # Default: keep monitoring
    return False, None
```

---

## PART 4: IMPROVED CLUSTER INTELLIGENCE

### 4.1 Enhanced Cluster Scoring

```python
def compute_cluster_score_v2(cluster_id):
    """
    Multi-dimensional cluster scoring.
    Not just success rate, but quality, consistency, and predictability.

    Final Score = (success_rate * 0.3) +
                  (consistency_score * 0.3) +
                  (early_signal_accuracy * 0.2) +
                  (avg_peak_mc_normalized * 0.2)
    """

    outcomes = get_cluster_outcomes(cluster_id)

    if len(outcomes) < 5:
        return None  # Not enough data

    # ===== METRIC 1: Success Rate =====
    success_count = sum(1 for o in outcomes if o['outcome'] == 'success')
    rug_count = sum(1 for o in outcomes if o['outcome'] in ['rug', 'slow_rug'])
    success_rate = success_count / len(outcomes)

    # ===== METRIC 2: Consistency Score =====
    # Low variance across outcomes = predictable behavior
    peak_mcs = [o['peak_market_cap'] for o in outcomes if o['peak_market_cap'] > 0]

    if peak_mcs:
        peak_mc_cv = np.std(peak_mcs) / np.mean(peak_mcs)  # Coefficient of variation
        consistency_score = max(1 - peak_mc_cv, 0)  # Invert: lower variance = higher score
    else:
        consistency_score = 0.5

    # ===== METRIC 3: Early Signal Accuracy =====
    early_predictions = get_cluster_early_predictions(cluster_id, lookback_days=7)

    if len(early_predictions) > 5:
        # Check if early predictions matched final outcomes
        correct = 0
        for pred in early_predictions:
            if pred['early_label'] == 'likely_rug' and pred['final_outcome'] in ['rug', 'slow_rug']:
                correct += 1
            elif pred['early_label'] == 'likely_runner' and pred['final_outcome'] == 'success':
                correct += 1

        early_signal_accuracy = correct / len(early_predictions)
    else:
        early_signal_accuracy = 0.5  # Neutral if not enough history

    # ===== METRIC 4: Average Peak MC (normalized) =====
    avg_peak_mc = np.mean(peak_mcs) if peak_mcs else 0
    network_avg_peak = get_network_avg_peak_mc()
    peak_mc_score = min(avg_peak_mc / network_avg_peak, 2.0)  # Cap at 2.0

    # ===== FINAL CLUSTER SCORE =====
    cluster_score = (
        success_rate * 0.30 +
        consistency_score * 0.30 +
        early_signal_accuracy * 0.20 +
        min(peak_mc_score / 2.0, 1.0) * 0.20  # Normalize to 0-1
    )

    return {
        'cluster_id': cluster_id,
        'cluster_name': get_cluster_name(cluster_id),
        'score': cluster_score,
        'success_rate': success_rate,
        'rug_rate': rug_count / len(outcomes),
        'consistency_score': consistency_score,
        'early_signal_accuracy': early_signal_accuracy,
        'avg_peak_mc': avg_peak_mc,
        'token_count': len(outcomes),
        'quality_tier': (
            'A+' if cluster_score >= 0.85 else
            'A' if cluster_score >= 0.75 else
            'B' if cluster_score >= 0.60 else
            'C' if cluster_score >= 0.40 else
            'F'
        ),
        'recommendation': (
            'FOCUS_HEAVILY' if cluster_score >= 0.85 else
            'FOCUS' if cluster_score >= 0.70 else
            'MONITOR' if cluster_score >= 0.50 else
            'DEPRIORITIZE' if cluster_score >= 0.30 else
            'AVOID'
        )
    }
```

### 4.2 Cluster Schema Enhancement

```sql
ALTER TABLE cluster_outcome_stats ADD COLUMN (
    -- Quality metrics
    consistency_score REAL DEFAULT 0,               -- 0-1, lower variance = higher
    early_signal_accuracy REAL DEFAULT 0,           -- % of early predictions correct
    overall_cluster_score REAL DEFAULT 0,           -- Weighted quality score
    quality_tier TEXT DEFAULT 'C',                  -- A+ | A | B | C | F
    recommendation TEXT DEFAULT 'MONITOR',          -- FOCUS_HEAVILY | FOCUS | MONITOR | etc

    -- Timeline metrics
    avg_time_to_peak_minutes INT DEFAULT 0,        -- Average time to reach peak
    median_time_to_peak_minutes INT DEFAULT 0,     -- Median time to peak
    avg_lifecycle_duration_minutes INT DEFAULT 0,  -- How long tokens last

    -- Early prediction metrics
    early_rug_accuracy REAL DEFAULT 0,             -- % of early_rug predictions that were rugs
    early_runner_accuracy REAL DEFAULT 0,          -- % of early_runner predictions that succeeded

    -- Momentum metrics
    avg_velocity_growth_pct_per_min REAL DEFAULT 0,-- Average MC growth rate
    volatility_index REAL DEFAULT 0,               -- Price volatility norm

    -- Updated timestamp
    computed_at INTEGER NOT NULL
);
```

---

## PART 5: STORAGE & PERFORMANCE OPTIMIZATIONS

### 5.1 Snapshot Downsampling Strategy

```python
def downsample_snapshots():
    """
    Compress old snapshots to reduce storage.
    Keep dense early data, compress late data.

    Storage reduction: ~70% after 1 day
    """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now = int(time.time())

    # After 6 hours, compress to 1 per 10 minutes
    cutoff_6h = now - (6 * 3600)

    cursor.execute("""
        WITH numbered_snaps AS (
            SELECT
                *,
                ROW_NUMBER() OVER (PARTITION BY mint, DATETIME(timestamp, 'unixepoch', 'start of hour') ORDER BY timestamp) as rn
            FROM token_lifecycle_snapshots
            WHERE timestamp < ?
            AND timestamp > ?
        )
        DELETE FROM token_lifecycle_snapshots
        WHERE snapshot_id IN (
            SELECT snapshot_id FROM numbered_snaps
            WHERE rn % 12 > 0  -- Keep 1 of every 12 (every 10 min if originally 50s intervals)
        )
    """, (cutoff_6h, now - (30 * 86400)))  # Delete >30 days old

    # After 24 hours, compress to hourly
    cutoff_24h = now - (24 * 3600)

    cursor.execute("""
        WITH numbered_snaps AS (
            SELECT
                *,
                ROW_NUMBER() OVER (PARTITION BY mint, DATETIME(timestamp, 'unixepoch', 'start of hour') ORDER BY timestamp) as rn
            FROM token_lifecycle_snapshots
            WHERE timestamp < ?
            AND timestamp > ?
        )
        DELETE FROM token_lifecycle_snapshots
        WHERE snapshot_id IN (
            SELECT snapshot_id FROM numbered_snaps
            WHERE rn % 60 > 0  -- Keep only first of each hour
        )
    """, (cutoff_24h, cutoff_6h))

    conn.commit()
    conn.close()

    logger.info("[LIFECYCLE] Snapshots downsampled")
```

### 5.2 Query Optimization Examples

```sql
-- FAST: Use cluster_outcome_stats instead of joining tokens
SELECT
    cluster_name,
    overall_cluster_score,
    quality_tier,
    recommendation,
    success_rate,
    consistency_score,
    early_signal_accuracy
FROM cluster_outcome_stats
WHERE overall_cluster_score > 0.70
ORDER BY overall_cluster_score DESC;

-- FAST: Use pre-computed early signal table
SELECT
    mint,
    early_label,
    early_score,
    confidence,
    cluster_name,
    time_to_50k_mc_seconds
FROM token_monitoring_state
WHERE early_label = 'likely_runner'
AND early_score > 0.70
AND cluster_id IN (
    SELECT cluster_id FROM cluster_outcome_stats
    WHERE quality_tier IN ('A+', 'A')
);

-- FAST: Cluster trend analysis
SELECT
    cluster_name,
    DATE(DATETIME(classified_at, 'unixepoch')) as day,
    COUNT(*) as tokens_classified,
    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes,
    ROUND(100.0 * SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) / COUNT(*), 1) as success_pct
FROM token_outcomes
WHERE cluster_id IN (SELECT cluster_id FROM cluster_outcome_stats WHERE quality_tier IN ('A+', 'A'))
GROUP BY cluster_name, day
ORDER BY day DESC;
```

---

## PART 6: IMPLEMENTATION ROADMAP

### Phase 1: Early Signal Engine (2-3 days)

**Files to create/modify:**
- Create `src/core/lifecycle_early_signals.py` (early scoring logic)
- Modify `token_monitoring_state` schema (add velocity, early scores)
- Create `token_early_signals` table (store predictions)

**Tasks:**
- [ ] Add schema fields (velocity, early scores, timeline milestones)
- [ ] Implement `compute_early_score()` function
- [ ] Implement `record_early_signal()` function
- [ ] Add early signal computation to monitoring loop (every 5 min after 5 min age)
- [ ] Test on sample tokens (validate 70%+ accuracy)

**Success criteria:**
- Early predictions stored in monitoring_state
- Can query `SELECT * WHERE early_label = 'likely_rug'` to identify risks early
- Can query early_runner tokens to prioritize monitoring

### Phase 2: Improved Classification (1-2 days)

**Files to modify:**
- Update `src/core/token_lifecycle.py` (`_classify()` method)

**Tasks:**
- [ ] Replace V1 rules with V2 enhanced rules
- [ ] Add velocity decay tracking
- [ ] Add peak_type classification (flash vs sustainable)
- [ ] Implement recovery detection
- [ ] Update test cases to validate new logic

**Success criteria:**
- Fewer rug vs slow_rug misclassifications
- Can distinguish flash pumps from real growth
- < 10% false positive rate

### Phase 3: Dynamic Monitoring (1 day)

**Files to modify:**
- Update monitoring loop integration

**Tasks:**
- [ ] Implement `get_snapshot_interval_seconds()`
- [ ] Implement `should_stop_monitoring_v2()`
- [ ] Update price snapshot collection to respect dynamic intervals
- [ ] Update stop condition evaluation

**Success criteria:**
- 60% reduction in snapshots while maintaining signal quality
- No premature stops of recovering tokens
- < 5% of tokens over-monitored (> 10 days)

### Phase 4: Cluster Intelligence (1-2 days)

**Files to modify:**
- Create `src/core/lifecycle_cluster_intelligence.py`
- Modify `cluster_outcome_stats` schema

**Tasks:**
- [ ] Add schema fields (consistency_score, early_signal_accuracy, etc)
- [ ] Implement `compute_cluster_score_v2()`
- [ ] Implement cluster aggregation in monitoring loop
- [ ] Create cluster ranking queries

**Success criteria:**
- Can identify A+ tier clusters (>75% quality)
- Can identify F tier clusters (avoid)
- Early signal accuracy tracked per cluster

### Phase 5: Storage Optimization (1 day)

**Files to modify:**
- Create pruning/downsampling scheduled job

**Tasks:**
- [ ] Implement `downsample_snapshots()` function
- [ ] Schedule to run daily
- [ ] Archive >30 day snapshots
- [ ] Test query performance on compressed data

**Success criteria:**
- Storage reduced 70% (150MB → 45MB for 10k tokens)
- Query performance unchanged or improved
- Data integrity maintained

---

## PART 7: USAGE EXAMPLES

### Real-Time Decision Making

```python
# When new token detected
def on_token_detected(mint, cluster_id):
    manager.start_monitoring(mint, cluster_id)

# At 5 minutes
def at_5_minutes(mint):
    early_signal = compute_early_score(mint, age_minutes=5)

    if early_signal['early_label'] == 'likely_rug':
        manager.stop_monitoring(mint, reason='early_rug_detected')
        return

    if early_signal['early_label'] == 'likely_runner':
        prioritize_monitoring(mint)  # Increase frequency, notify team

# Query best clusters to focus on
best_clusters = []
for cluster_id in get_all_clusters():
    score = compute_cluster_score_v2(cluster_id)
    if score and score['overall_cluster_score'] > 0.75:
        best_clusters.append(score)

best_clusters.sort(key=lambda x: x['overall_cluster_score'], reverse=True)

for cluster in best_clusters[:20]:
    print(f"{cluster['cluster_name']}: {cluster['quality_tier']} ({cluster['overall_cluster_score']:.2f})")
    print(f"  Success rate: {100*cluster['success_rate']:.1f}%")
    print(f"  Early signal accuracy: {100*cluster['early_signal_accuracy']:.1f}%")
    print(f"  Recommendation: {cluster['recommendation']}")
```

### Monitoring Loop Integration

```python
def lifecycle_monitoring_loop():
    """Background loop running every 5 minutes"""

    while True:
        try:
            active_mints = manager.get_active_tokens()

            for mint in active_mints:
                # Get current state
                state = get_monitoring_state(mint)
                age_minutes = (time.time() - state['started_at']) / 60

                # Compute early signal (5-15 min window)
                if 5 <= age_minutes <= 15:
                    early_signal = compute_early_score(mint, age_minutes)
                    if early_signal:
                        record_early_signal(manager, mint, early_signal)

                        # Act on signal
                        if early_signal['recommendation'] == 'STOP_MONITORING':
                            manager.stop_monitoring(mint, reason=early_signal['early_label'])

                # Check stop conditions
                should_stop, reason = manager.evaluate_stop_conditions_v2(mint)
                if should_stop:
                    outcome = manager.classify_outcome(mint)
                    manager.stop_monitoring(mint, outcome)

                    # Update cluster stats
                    manager.compute_cluster_stats(state['cluster_id'])

            # Prune old snapshots
            downsample_snapshots()

        except Exception as e:
            logger.error(f"[LIFECYCLE_LOOP] Error: {e}")

        time.sleep(300)  # 5 minutes
```

---

## PART 8: SUCCESS METRICS

Track these to validate implementation:

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Early rug prediction accuracy | N/A | > 75% | % of early_rug that ended as rug/slow_rug |
| Early runner prediction accuracy | N/A | > 70% | % of early_runner that ended as success |
| Early prediction confidence | N/A | avg > 0.65 | Average confidence score of predictions |
| Storage per 10k tokens | 150 MB | 45 MB | Total snapshots table size |
| Cluster quality differentiation | N/A | A+/F spread > 0.5 | Score difference between best/worst |
| Monitoring efficiency | N/A | > 75% | % of snapshots that influenced classification |
| False positive rate | ? | < 10% | % of neutral tokens misclassified |
| Early stop false positive | N/A | < 5% | % of stopped tokens that recovered |

---

## SUMMARY

This design transforms your lifecycle system from reactive to predictive:

**Early Signals**: Score tokens at 5-10 min (70%+ accuracy)
**Smart Classification**: Better rug vs slow_rug differentiation, velocity/momentum signals
**Dynamic Monitoring**: Adaptive frequency (60% less storage), smart stops
**Cluster Intelligence**: Multi-dimensional scoring, early vs final accuracy tracking
**Storage**: 70% compression after 6 hours, 30-day archival
**Real-time decisions**: Stop obvious rugs early, prioritize runners

All rule-based, explainable, and debuggable. No ML needed.

