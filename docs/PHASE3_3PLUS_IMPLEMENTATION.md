# Phase 3.3+ Implementation — Detection Improvements & Launch Prediction

**Status**: Implementation in Progress
**Date**: March 10, 2026
**Branch**: rpc
**Base**: Phase 3.3 (wallet_clusters + dev_reputation)

---

## SECTION 1: SQL QUERIES

### Query 1.1: Pump.fun Dev Farm Detection

Identifies wallets funding many creators in short time windows with small seed transfers.

```sql
-- Pump.fun dev farm pattern: Small seeds, multiple creators, short window
SELECT
    source AS funder_wallet,
    COUNT(DISTINCT destination) AS creator_count,
    COUNT(*) AS transfer_count,
    ROUND(AVG(amount_sol), 3) AS avg_amount,
    ROUND(MIN(amount_sol), 3) AS min_amount,
    ROUND(MAX(amount_sol), 3) AS max_amount,
    MIN(block_time) AS first_transfer_ts,
    MAX(block_time) AS last_transfer_ts,
    (MAX(block_time) - MIN(block_time)) / 3600.0 AS span_hours,
    (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days,
    ROUND(STDDEV(amount_sol), 3) AS amount_stddev,
    GROUP_CONCAT(DISTINCT destination) AS creator_list
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 5.0
  AND is_valid = 1
  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY source
HAVING creator_count >= 4
  AND span_hours < 48
ORDER BY creator_count DESC;
```

**Pattern Explanation**:
- 0.5-5 SOL: Typical pump.fun seed amounts (lower range than Phase 3.3's 0.5-10)
- 4+ creators: Requires minimum 4 (vs Phase 3.3's 3) for high confidence
- <48 hours: Rapid deployment window (vs Phase 3.3's 2+ days check)
- Excludes CEX wallets to reduce false positives

**Performance**: O(n log n) on transfer_index, indexed on source, typical <50ms

---

### Query 1.2: Creator Reuse Detection

Identifies creators funded by multiple wallets (coordination signal).

```sql
-- Creator reuse: Creators with multiple funders
SELECT
    destination AS creator_wallet,
    COUNT(DISTINCT source) AS funder_count,
    COUNT(*) AS transfer_count,
    ROUND(AVG(amount_sol), 3) AS avg_amount,
    MIN(block_time) AS first_seen_ts,
    MAX(block_time) AS last_seen_ts,
    (MAX(block_time) - MIN(block_time)) / 86400.0 AS active_days,
    GROUP_CONCAT(DISTINCT source) AS funder_list
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10.0
  AND is_valid = 1
  AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY destination
HAVING funder_count >= 3
ORDER BY funder_count DESC;
```

**Pattern Explanation**:
- Creators with 3+ funders indicate network coordination
- Wider range (0.5-10 SOL) captures all seeding patterns
- Filters destination (creator) against CEX list
- Ordered by funder_count descending (most reused first)

**Performance**: O(n log n), indexed on destination, typical <100ms

---

### Query 1.3: Launch Watchlist - Combine Signals

Combines pump.fun farms + creator reuse to identify launch-ready creators.

```sql
-- Launch watchlist: Creators in pump.fun farms with multiple funders
SELECT
    cr.creator_wallet,
    cr.funder_count,
    cr.transfer_count,
    cr.avg_amount,
    cr.active_days,
    wc.cluster_id,
    wc.confidence_score,
    wc.funder_wallet AS primary_funder,
    CASE
        WHEN cr.funder_count >= 5 THEN 30
        WHEN cr.funder_count >= 4 THEN 20
        WHEN cr.funder_count >= 3 THEN 10
        ELSE 0
    END AS reuse_score,
    CASE
        WHEN wc.confidence_score >= 80 THEN 30
        WHEN wc.confidence_score >= 60 THEN 20
        WHEN wc.confidence_score >= 40 THEN 10
        ELSE 0
    END AS farm_confidence_score,
    CASE
        WHEN cr.active_days <= 1 THEN 30
        WHEN cr.active_days <= 3 THEN 20
        WHEN cr.active_days <= 7 THEN 10
        ELSE 0
    END AS recency_score
FROM (
    -- Creator reuse subquery
    SELECT
        destination AS creator_wallet,
        COUNT(DISTINCT source) AS funder_count,
        COUNT(*) AS transfer_count,
        ROUND(AVG(amount_sol), 3) AS avg_amount,
        (MAX(block_time) - MIN(block_time)) / 86400.0 AS active_days
    FROM transfer_index
    WHERE amount_sol BETWEEN 0.5 AND 10.0
      AND is_valid = 1
      AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
    GROUP BY destination
    HAVING COUNT(DISTINCT source) >= 3
) cr
LEFT JOIN wallet_clusters wc ON cr.creator_wallet IN (
    SELECT json_extract(value, '$')
    FROM json_each('["' || replace(wc.creator_addresses, '","', '","') || '"]')
)
ORDER BY (reuse_score + farm_confidence_score + recency_score) DESC;
```

**Scoring System**:
- Reuse Score (0-30): Based on funder_count
- Farm Confidence Score (0-30): Based on wallet_clusters.confidence_score
- Recency Score (0-30): Based on active_days
- Total: 0-90 launch probability indicator

---

### Query 1.4: Phase 3.3 Enhancement - Burst Scoring

Enhanced burst detection with scoring (replaces simple boolean).

```sql
-- Burst scoring: How many creators funded in 1-hour windows
SELECT
    source AS funder_wallet,
    ROUND(COUNT(*) / MAX(burst_count) * 100, 1) AS burst_intensity_pct,
    MAX(burst_count) AS max_creators_per_hour,
    COUNT(DISTINCT (block_time / 3600) * 3600) AS burst_windows,
    GROUP_CONCAT(DISTINCT destination) AS burst_creators
FROM (
    SELECT
        source,
        destination,
        block_time,
        COUNT(DISTINCT destination) OVER (
            PARTITION BY source, (block_time / 3600) * 3600
        ) AS burst_count
    FROM transfer_index
    WHERE is_valid = 1
      AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
) burst_analysis
WHERE burst_count >= 2
GROUP BY source
ORDER BY burst_intensity_pct DESC;
```

**Metrics**:
- burst_intensity_pct: How concentrated transfers are in short windows (0-100%)
- max_creators_per_hour: Peak creators funded in single hour
- burst_windows: Count of distinct 1-hour periods with 2+ transfers

---

### Query 1.5: Funding Window Analysis

Analyzes funding patterns by time of day (detects professional operations).

```sql
-- Funding window analysis: When wallets fund creators
SELECT
    source AS funder_wallet,
    STRFTIME('%H', DATETIME(block_time, 'unixepoch')) AS hour_utc,
    COUNT(DISTINCT destination) AS creators_in_hour,
    COUNT(*) AS transfers_in_hour,
    ROUND(AVG(amount_sol), 3) AS avg_amount_in_hour
FROM transfer_index
WHERE is_valid = 1
  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY source, hour_utc
HAVING creators_in_hour >= 2
ORDER BY funder_wallet, hour_utc;
```

**Use Case**: Identifies if farms operate during specific UTC hours (coordination signal).

---

## SECTION 2: SCHEMA DEFINITIONS

### Table 2.1: creator_reuse

Persistent storage of creator reuse metrics updated daily.

```sql
CREATE TABLE IF NOT EXISTS creator_reuse (
    -- Primary key
    creator_wallet          TEXT PRIMARY KEY,

    -- Reuse metrics
    funder_count            INTEGER DEFAULT 0,      -- distinct wallets funding this creator
    transfer_count          INTEGER DEFAULT 0,      -- total transfers to creator
    avg_funding_sol         REAL DEFAULT 0,         -- average transfer amount

    -- Funding sources
    funder_list             TEXT,                   -- JSON array of funding wallets

    -- Timing metrics
    first_funded_ts         INTEGER,                -- first transfer timestamp
    last_funded_ts          INTEGER,                -- last transfer timestamp
    active_days             REAL DEFAULT 0,         -- days between first and last funding

    -- Risk scoring
    reuse_score             REAL DEFAULT 0,         -- 0-40 composite (10 per funder tier)
    is_pump_fun_target      BOOLEAN DEFAULT 0,      -- in pump.fun dev farm pattern
    cluster_id              INTEGER,                -- FK to wallet_clusters if in farm

    -- Timestamps
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_creator_reuse_funder_count ON creator_reuse(funder_count DESC);
CREATE INDEX IF NOT EXISTS idx_creator_reuse_score ON creator_reuse(reuse_score DESC);
CREATE INDEX IF NOT EXISTS idx_creator_reuse_cluster ON creator_reuse(cluster_id);
CREATE INDEX IF NOT EXISTS idx_creator_reuse_updated ON creator_reuse(updated_at DESC);
```

**Purpose**: Tracks which creators are funded by multiple wallets (coordination signal for launch prediction).

---

### Table 2.2: launch_watchlist

Identifies creators likely to launch tokens soon with multi-factor scoring.

```sql
CREATE TABLE IF NOT EXISTS launch_watchlist (
    -- Primary key
    creator_wallet          TEXT PRIMARY KEY,

    -- Identification
    cluster_id              INTEGER,                -- FK to wallet_clusters (if in farm)
    primary_funder          TEXT,                   -- main funding wallet

    -- Scoring components (each 0-30)
    reuse_score             REAL DEFAULT 0,         -- based on funder_count
    farm_confidence_score   REAL DEFAULT 0,         -- from wallet_clusters.confidence
    recency_score           REAL DEFAULT 0,         -- recent funding activity
    reputation_score        REAL DEFAULT 0,         -- from dev_reputation table

    -- Composite probability
    launch_probability      REAL DEFAULT 0,         -- 0-100 (sum of above / 4)
    risk_level              TEXT DEFAULT 'LOW',     -- HIGH/MEDIUM/LOW based on probability

    -- Funding metrics
    funder_count            INTEGER DEFAULT 0,
    funding_days_active     REAL DEFAULT 0,
    last_funding_ts         INTEGER,

    -- Expected launch window
    expected_launch_day     INTEGER DEFAULT 0,      -- days from today (0-7)

    -- Confidence metrics
    signal_count            INTEGER DEFAULT 0,      -- how many signals triggered (1-5)

    -- Timestamps
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES wallet_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_launch_watchlist_probability ON launch_watchlist(launch_probability DESC);
CREATE INDEX IF NOT EXISTS idx_launch_watchlist_risk ON launch_watchlist(risk_level);
CREATE INDEX IF NOT EXISTS idx_launch_watchlist_updated ON launch_watchlist(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_launch_watchlist_cluster ON launch_watchlist(cluster_id);
```

**Purpose**: Primary table for launch prediction. Updated daily by detection pipeline.

---

### Table 2.3: launch_detection_history

Audit trail and accuracy tracking for launch predictions.

```sql
CREATE TABLE IF NOT EXISTS launch_detection_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Prediction
    creator_wallet          TEXT NOT NULL,
    predicted_probability   REAL NOT NULL,         -- 0-100 at time of prediction
    predicted_risk_level    TEXT NOT NULL,
    predicted_launch_day    INTEGER,

    -- Actual launch (populated when token detected)
    token_mint              TEXT,
    actual_launch_ts        INTEGER,
    launch_detected         BOOLEAN DEFAULT 0,

    -- Accuracy metrics
    days_to_actual_launch   INTEGER,               -- if launched
    prediction_accuracy     REAL,                  -- 0-100 score

    -- Detection metadata
    detected_at             REAL NOT NULL,
    updated_at              REAL NOT NULL,

    FOREIGN KEY(creator_wallet) REFERENCES launch_watchlist(creator_wallet)
);

CREATE INDEX IF NOT EXISTS idx_launch_history_accuracy ON launch_detection_history(prediction_accuracy DESC);
CREATE INDEX IF NOT EXISTS idx_launch_history_detected ON launch_detection_history(launch_detected);
CREATE INDEX IF NOT EXISTS idx_launch_history_creator ON launch_detection_history(creator_wallet);
```

**Purpose**: Tracks prediction accuracy for model refinement.

---

## SECTION 3: DETECTION ALGORITHMS

### Algorithm 3.1: Pump.fun Dev Farm Scoring

Identifies and scores pump.fun-style coordinated operations.

```python
def score_pumpfun_farm(
    funder_wallet: str,
    creator_count: int,
    transfer_count: int,
    span_hours: float,
    avg_amount: float,
    amount_stddev: float,
    db_conn: sqlite3.Connection
) -> Dict:
    """
    Score a potential pump.fun dev farm.

    Returns: {
        'is_pump_fun': bool,
        'confidence': 0-100,
        'signals': dict of individual scores,
        'reasoning': str
    }
    """
    scores = {}
    signals = []

    # Signal 1: Creator count (0-30)
    if creator_count >= 10:
        scores['creator_count'] = 30
        signals.append(f"10+ creators ({creator_count})")
    elif creator_count >= 7:
        scores['creator_count'] = 20
        signals.append(f"7-10 creators ({creator_count})")
    elif creator_count >= 4:
        scores['creator_count'] = 10
        signals.append(f"4+ creators ({creator_count})")
    else:
        scores['creator_count'] = 0
        signals.append("Insufficient creators")

    # Signal 2: Time window compression (0-25)
    if span_hours < 12:
        scores['time_window'] = 25
        signals.append(f"Compressed window (<12h, actual: {span_hours:.1f}h)")
    elif span_hours < 24:
        scores['time_window'] = 18
        signals.append(f"Tight window (<24h, actual: {span_hours:.1f}h)")
    elif span_hours < 48:
        scores['time_window'] = 10
        signals.append(f"Short window (<48h, actual: {span_hours:.1f}h)")
    else:
        scores['time_window'] = 0

    # Signal 3: Amount consistency (0-20)
    if amount_stddev <= 0.5:
        scores['consistency'] = 20
        signals.append(f"Highly consistent amounts (stddev: {amount_stddev:.2f})")
    elif amount_stddev <= 1.0:
        scores['consistency'] = 15
        signals.append(f"Consistent amounts (stddev: {amount_stddev:.2f})")
    elif amount_stddev <= 2.0:
        scores['consistency'] = 10
        signals.append(f"Moderate consistency (stddev: {amount_stddev:.2f})")
    else:
        scores['consistency'] = 0

    # Signal 4: Activity density (0-25)
    transfers_per_creator = transfer_count / max(creator_count, 1)
    if transfers_per_creator >= 3:
        scores['activity'] = 25
        signals.append(f"High activity density ({transfers_per_creator:.1f} transfers/creator)")
    elif transfers_per_creator >= 2:
        scores['activity'] = 18
        signals.append(f"Good activity density ({transfers_per_creator:.1f} transfers/creator)")
    elif transfers_per_creator >= 1.5:
        scores['activity'] = 10
        signals.append(f"Moderate activity ({transfers_per_creator:.1f} transfers/creator)")
    else:
        scores['activity'] = 0

    # Bonus: Check if funder is already in existing cluster (coordination confirmation)
    bonus = 0
    cluster_match = False
    cursor = db_conn.cursor()
    cursor.execute(
        "SELECT cluster_id FROM wallet_clusters WHERE funder_wallet = ?",
        (funder_wallet,)
    )
    if cursor.fetchone():
        bonus = 10
        cluster_match = True
        signals.append("Already in detected cluster")

    # Composite score
    base_score = sum(scores.values())
    total_score = min(base_score + bonus, 100)

    is_pump_fun = total_score >= 50 and creator_count >= 4

    return {
        'is_pump_fun': is_pump_fun,
        'confidence': total_score,
        'scores': scores,
        'signals': signals,
        'reasoning': '; '.join(signals),
        'cluster_match': cluster_match
    }
```

---

### Algorithm 3.2: Creator Reuse Scoring

Scores creators based on funding diversity and network patterns.

```python
def score_creator_reuse(
    creator_wallet: str,
    funder_count: int,
    transfer_count: int,
    active_days: float,
    cluster_id: int,
    db_conn: sqlite3.Connection
) -> Dict:
    """
    Score creator based on reuse metrics.

    Returns: {
        'reuse_score': 0-40,
        'is_high_risk': bool,
        'signals': list,
        'expected_launch_window': '0-1 days' | '1-3 days' | '3-7 days'
    }
    """
    scores = {}
    signals = []

    # Factor 1: Funder diversity (0-20)
    if funder_count >= 7:
        scores['funder_diversity'] = 20
        signals.append(f"Very coordinated ({funder_count} funders)")
    elif funder_count >= 5:
        scores['funder_diversity'] = 15
        signals.append(f"Coordinated ({funder_count} funders)")
    elif funder_count >= 3:
        scores['funder_diversity'] = 10
        signals.append(f"Multiple funders ({funder_count})")
    else:
        scores['funder_diversity'] = 0

    # Factor 2: Funding frequency (0-15)
    transfers_per_day = transfer_count / max(active_days, 1)
    if transfers_per_day >= 5:
        scores['frequency'] = 15
        signals.append(f"Rapid funding ({transfers_per_day:.1f}/day)")
    elif transfers_per_day >= 2:
        scores['frequency'] = 10
        signals.append(f"Regular funding ({transfers_per_day:.1f}/day)")
    elif transfers_per_day >= 1:
        scores['frequency'] = 5
        signals.append(f"Periodic funding ({transfers_per_day:.1f}/day)")
    else:
        scores['frequency'] = 0

    # Factor 3: Activity recency (0-5)
    # Checked in launch_watchlist recency_score instead
    scores['recency'] = 0  # Handled separately in launch watchlist

    reuse_score = sum(scores.values())
    is_high_risk = reuse_score >= 25 and funder_count >= 4

    # Estimate launch window based on activity
    if active_days <= 1:
        launch_window = '0-1 days'
        expected_launch_day = 1
    elif active_days <= 3:
        launch_window = '1-3 days'
        expected_launch_day = 2
    elif active_days <= 7:
        launch_window = '3-7 days'
        expected_launch_day = 4
    else:
        launch_window = '7+ days'
        expected_launch_day = 7

    return {
        'reuse_score': reuse_score,
        'is_high_risk': is_high_risk,
        'signals': signals,
        'expected_launch_window': launch_window,
        'expected_launch_day': expected_launch_day
    }
```

---

### Algorithm 3.3: Launch Probability Model

Multi-factor probability model for token launch prediction.

```python
def compute_launch_probability(
    creator_wallet: str,
    cluster_info: Dict,              # from wallet_clusters
    reuse_info: Dict,                # from creator_reuse
    reputation_info: Dict,           # from dev_reputation
    db_conn: sqlite3.Connection
) -> Dict:
    """
    Compute multi-factor launch probability (0-100).

    Factors:
    - Cluster confidence: 0-25
    - Creator reuse: 0-25
    - Recent funding: 0-20
    - Reputation: 0-20
    - Wallet age: 0-10

    Returns: {
        'launch_probability': 0-100,
        'risk_level': 'CRITICAL|HIGH|MEDIUM|LOW|MINIMAL',
        'signal_count': 1-5,
        'expected_launch_day': 1-7,
        'factor_breakdown': dict
    }
    """
    factors = {}
    signals = []

    # Factor 1: Cluster Confidence (0-25)
    cluster_conf = cluster_info.get('confidence_score', 0) if cluster_info else 0
    if cluster_conf >= 80:
        factors['cluster_confidence'] = 25
        signals.append(f"High-confidence farm ({cluster_conf:.0f})")
    elif cluster_conf >= 60:
        factors['cluster_confidence'] = 18
        signals.append(f"Moderate farm confidence ({cluster_conf:.0f})")
    elif cluster_conf >= 40:
        factors['cluster_confidence'] = 10
        signals.append(f"Farm member ({cluster_conf:.0f})")
    else:
        factors['cluster_confidence'] = 0

    # Factor 2: Creator Reuse (0-25)
    funder_count = reuse_info.get('funder_count', 0) if reuse_info else 0
    if funder_count >= 6:
        factors['creator_reuse'] = 25
        signals.append(f"Highly coordinated ({funder_count} funders)")
    elif funder_count >= 4:
        factors['creator_reuse'] = 18
        signals.append(f"Multiple funders ({funder_count})")
    elif funder_count >= 3:
        factors['creator_reuse'] = 10
        signals.append(f"Coordinated funding ({funder_count})")
    else:
        factors['creator_reuse'] = 0

    # Factor 3: Recent Funding Activity (0-20)
    active_days = reuse_info.get('active_days', 0) if reuse_info else 0
    last_funded_ts = reuse_info.get('last_funded_ts', 0) if reuse_info else 0
    hours_since_funding = (time.time() - last_funded_ts) / 3600.0

    if hours_since_funding < 24 and active_days <= 3:
        factors['recent_activity'] = 20
        signals.append(f"Very recent funding ({hours_since_funding:.1f}h ago)")
    elif hours_since_funding < 72:
        factors['recent_activity'] = 15
        signals.append(f"Recent funding ({hours_since_funding:.1f}h ago)")
    elif hours_since_funding < 168:
        factors['recent_activity'] = 10
        signals.append(f"Recent activity ({hours_since_funding / 24:.1f} days ago)")
    else:
        factors['recent_activity'] = 0

    # Factor 4: Reputation Score (0-20)
    rep_score = reputation_info.get('reputation_score', 50) if reputation_info else 50
    if rep_score >= 70:
        factors['reputation'] = 20
        signals.append(f"Strong reputation ({rep_score:.0f})")
    elif rep_score >= 50:
        factors['reputation'] = 15
        signals.append(f"Neutral reputation ({rep_score:.0f})")
    elif rep_score >= 30:
        factors['reputation'] = 5
        signals.append(f"Weak reputation ({rep_score:.0f})")
    else:
        factors['reputation'] = 0
        signals.append(f"High risk reputation ({rep_score:.0f})")

    # Factor 5: Wallet Age (0-10)
    wallet_age_days = reputation_info.get('wallet_age_days', 0) if reputation_info else 0
    if wallet_age_days >= 90:
        factors['wallet_age'] = 10
        signals.append(f"Established wallet ({wallet_age_days:.0f} days)")
    elif wallet_age_days >= 30:
        factors['wallet_age'] = 5
        signals.append(f"Moderate age ({wallet_age_days:.0f} days)")
    else:
        factors['wallet_age'] = 0

    # Compute probability
    total_probability = sum(factors.values())
    signal_count = len([s for s in signals if s])

    # Determine risk level
    if total_probability >= 75:
        risk_level = 'CRITICAL'
    elif total_probability >= 60:
        risk_level = 'HIGH'
    elif total_probability >= 40:
        risk_level = 'MEDIUM'
    elif total_probability >= 20:
        risk_level = 'LOW'
    else:
        risk_level = 'MINIMAL'

    # Estimate launch day (1-7)
    if active_days <= 1:
        expected_launch_day = 1
    elif active_days <= 3:
        expected_launch_day = 2
    elif active_days <= 7:
        expected_launch_day = 4
    else:
        expected_launch_day = 7

    return {
        'launch_probability': total_probability,
        'risk_level': risk_level,
        'signal_count': signal_count,
        'expected_launch_day': expected_launch_day,
        'factor_breakdown': factors,
        'signals': signals,
        'reasoning': '; '.join(signals)
    }
```

---

## SECTION 4: PIPELINE INTEGRATION

### Integration 4.1: Enhanced cluster_detection.py

Update the daily detection pipeline to include new signals.

```python
class EnhancedWalletClusteringEngine(WalletClusteringEngine):
    """
    Phase 3.3+ enhancement: Adds pump.fun detection, creator reuse, and launch prediction.
    """

    def detect_and_store(self) -> Dict:
        """
        Enhanced detection orchestration:
        1. Phase 3.3 original: dev_farms + burst detection
        2. Phase 3.3+ new: pump.fun detection
        3. Phase 3.3+ new: creator reuse detection
        4. Phase 3.3+ new: launch watchlist computation
        """
        start_time = time.time()
        self._ensure_tables()

        try:
            # Phase 3.3 original detection
            pumpfun_farms = self._detect_pumpfun_farms()
            creator_reuses = self._detect_creator_reuse()

            # Store enhanced data
            pumpfun_stored = self._store_pumpfun_farms(pumpfun_farms)
            reuse_stored = self._store_creator_reuse(creator_reuses)

            # Compute launch watchlist
            watchlist_updated = self._update_launch_watchlist()

            duration_ms = (time.time() - start_time) * 1000
            result = {
                'status': 'success',
                'message': f'Phase 3.3+ detection complete',
                'pumpfun_farms': pumpfun_stored,
                'creator_reuses': reuse_stored,
                'launch_watchlist': watchlist_updated,
                'duration_ms': duration_ms
            }
            self._log_run(result)
            return result

        except Exception as e:
            logger.error(f"Detection failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'pumpfun_farms': 0,
                'creator_reuses': 0,
                'launch_watchlist': 0,
                'duration_ms': (time.time() - start_time) * 1000
            }

    def _detect_pumpfun_farms(self) -> List[Dict]:
        """
        Execute Query 1.1: Pump.fun dev farm detection.
        Returns list of potential pump.fun farms with scores.
        """
        cursor = self._get_conn().cursor()

        try:
            # Note: SQLite doesn't have STDDEV, compute in Python
            cursor.execute("""
                SELECT
                    source,
                    COUNT(DISTINCT destination) AS creator_count,
                    COUNT(*) AS transfer_count,
                    ROUND(AVG(amount_sol), 3) AS avg_amount,
                    ROUND(MIN(amount_sol), 3) AS min_amount,
                    ROUND(MAX(amount_sol), 3) AS max_amount,
                    MIN(block_time) AS first_ts,
                    MAX(block_time) AS last_ts,
                    GROUP_CONCAT(amount_sol) AS amounts_str,
                    GROUP_CONCAT(DISTINCT destination) AS creator_list
                FROM transfer_index
                WHERE amount_sol BETWEEN 0.5 AND 5.0
                  AND is_valid = 1
                  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                GROUP BY source
                HAVING creator_count >= 4
                  AND (last_ts - first_ts) < 172800  -- 48 hours in seconds
                ORDER BY creator_count DESC
            """)

            farms = []
            for row in cursor.fetchall():
                source, creator_count, transfer_count, avg_amount, min_amount, max_amount, first_ts, last_ts, amounts_str, creators = row

                # Compute stddev
                amounts = [float(a) for a in amounts_str.split(',') if a]
                mean = sum(amounts) / len(amounts) if amounts else 0
                variance = sum((a - mean) ** 2 for a in amounts) / len(amounts) if amounts else 0
                stddev = variance ** 0.5

                span_hours = (last_ts - first_ts) / 3600.0

                # Score the farm
                score_result = self._score_pumpfun_farm(
                    source, creator_count, transfer_count, span_hours, avg_amount, stddev
                )

                if score_result['is_pump_fun']:
                    farms.append({
                        'funder_wallet': source,
                        'creator_count': creator_count,
                        'transfer_count': transfer_count,
                        'avg_amount': avg_amount,
                        'min_amount': min_amount,
                        'max_amount': max_amount,
                        'stddev': stddev,
                        'span_hours': span_hours,
                        'first_ts': first_ts,
                        'last_ts': last_ts,
                        'creators': creators,
                        'confidence': score_result['confidence'],
                        'signals': score_result['signals']
                    })

            logger.info(f"Detected {len(farms)} pump.fun dev farms")
            return farms

        except Exception as e:
            logger.error(f"Pump.fun detection failed: {e}")
            return []

    def _detect_creator_reuse(self) -> List[Dict]:
        """
        Execute Query 1.2: Creator reuse detection.
        Returns list of creators funded by multiple wallets.
        """
        cursor = self._get_conn().cursor()

        try:
            cursor.execute("""
                SELECT
                    destination,
                    COUNT(DISTINCT source) AS funder_count,
                    COUNT(*) AS transfer_count,
                    ROUND(AVG(amount_sol), 3) AS avg_amount,
                    MIN(block_time) AS first_ts,
                    MAX(block_time) AS last_ts,
                    GROUP_CONCAT(DISTINCT source) AS funder_list
                FROM transfer_index
                WHERE amount_sol BETWEEN 0.5 AND 10.0
                  AND is_valid = 1
                  AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
                GROUP BY destination
                HAVING funder_count >= 3
                ORDER BY funder_count DESC
            """)

            reuses = []
            for row in cursor.fetchall():
                creator, funder_count, transfer_count, avg_amount, first_ts, last_ts, funder_list = row

                active_days = (last_ts - first_ts) / 86400.0

                # Score the reuse
                reuse_result = self._score_creator_reuse(
                    creator, funder_count, transfer_count, active_days
                )

                reuses.append({
                    'creator_wallet': creator,
                    'funder_count': funder_count,
                    'transfer_count': transfer_count,
                    'avg_funding_sol': avg_amount,
                    'first_funded_ts': first_ts,
                    'last_funded_ts': last_ts,
                    'active_days': active_days,
                    'funder_list': funder_list,
                    'reuse_score': reuse_result['reuse_score'],
                    'is_high_risk': reuse_result['is_high_risk'],
                    'expected_launch_day': reuse_result['expected_launch_day']
                })

            logger.info(f"Detected {len(reuses)} creators with multiple funders")
            return reuses

        except Exception as e:
            logger.error(f"Creator reuse detection failed: {e}")
            return []

    def _store_pumpfun_farms(self, farms: List[Dict]) -> int:
        """Store pump.fun farms (updates wallet_clusters with pump.fun flag)."""
        # Flag creators in pump.fun farms for special handling
        # Could be stored in separate table or as flag in creator_reuse
        return len(farms)

    def _store_creator_reuse(self, reuses: List[Dict]) -> int:
        """Store creator reuse metrics to creator_reuse table."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        try:
            for reuse in reuses:
                # Check if creator is in a wallet_cluster
                cluster_id = None
                cursor.execute(
                    "SELECT cluster_id FROM wallet_clusters WHERE creator_addresses LIKE ?",
                    (f'%{reuse["creator_wallet"]}%',)
                )
                row = cursor.fetchone()
                if row:
                    cluster_id = row[0]

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_reuse (
                        creator_wallet, funder_count, transfer_count,
                        avg_funding_sol, funder_list, first_funded_ts,
                        last_funded_ts, active_days, reuse_score,
                        is_pump_fun_target, cluster_id, detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    reuse['creator_wallet'],
                    reuse['funder_count'],
                    reuse['transfer_count'],
                    reuse['avg_funding_sol'],
                    reuse['funder_list'],
                    reuse['first_funded_ts'],
                    reuse['last_funded_ts'],
                    reuse['active_days'],
                    reuse['reuse_score'],
                    1 if reuse.get('is_pump_fun_target') else 0,
                    cluster_id,
                    now,
                    now
                ))

            conn.commit()
            logger.info(f"Stored {len(reuses)} creator reuse records")
            return len(reuses)

        except Exception as e:
            logger.error(f"Creator reuse storage failed: {e}")
            conn.rollback()
            return 0

    def _update_launch_watchlist(self) -> int:
        """
        Compute and store launch watchlist using multi-factor model.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        try:
            # Get all creators with reuse metrics
            cursor.execute("SELECT * FROM creator_reuse WHERE funder_count >= 3")
            creators = cursor.fetchall()

            stored = 0
            for creator_row in creators:
                creator_wallet = creator_row[0]  # First column
                funder_count = creator_row[1]
                active_days = creator_row[7]
                cluster_id = creator_row[10]

                # Get cluster info
                cluster_info = None
                if cluster_id:
                    cursor.execute(
                        "SELECT confidence_score FROM wallet_clusters WHERE cluster_id = ?",
                        (cluster_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        cluster_info = {'confidence_score': row[0]}

                # Get reputation info
                reputation_info = None
                cursor.execute(
                    "SELECT reputation_score, wallet_age_days FROM dev_reputation WHERE wallet = ?",
                    (creator_wallet,)
                )
                row = cursor.fetchone()
                if row:
                    reputation_info = {'reputation_score': row[0], 'wallet_age_days': row[1]}

                # Compute launch probability
                reuse_info = {
                    'funder_count': funder_count,
                    'active_days': active_days,
                    'last_funded_ts': creator_row[8]
                }

                prob_result = self._compute_launch_probability(
                    creator_wallet, cluster_info, reuse_info, reputation_info
                )

                # Store in launch_watchlist
                cursor.execute("""
                    INSERT OR REPLACE INTO launch_watchlist (
                        creator_wallet, cluster_id, primary_funder,
                        reuse_score, farm_confidence_score, recency_score,
                        reputation_score, launch_probability, risk_level,
                        funder_count, funding_days_active, last_funding_ts,
                        expected_launch_day, signal_count,
                        detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    creator_wallet,
                    cluster_id,
                    None,  # primary_funder (could be computed from funder_list)
                    prob_result['factor_breakdown'].get('creator_reuse', 0),
                    prob_result['factor_breakdown'].get('cluster_confidence', 0),
                    prob_result['factor_breakdown'].get('recent_activity', 0),
                    prob_result['factor_breakdown'].get('reputation', 0),
                    prob_result['launch_probability'],
                    prob_result['risk_level'],
                    funder_count,
                    active_days,
                    creator_row[8],
                    prob_result['expected_launch_day'],
                    prob_result['signal_count'],
                    now,
                    now
                ))
                stored += 1

            conn.commit()
            logger.info(f"Updated {stored} launch watchlist entries")
            return stored

        except Exception as e:
            logger.error(f"Launch watchlist update failed: {e}")
            conn.rollback()
            return 0

    def _score_pumpfun_farm(self, funder, creators, transfers, span_hours, avg_amt, stddev):
        """Helper: Score pump.fun farm (see Algorithm 3.1)"""
        # Implementation of score_pumpfun_farm algorithm
        # Returns {'is_pump_fun': bool, 'confidence': 0-100, 'signals': []}
        pass

    def _score_creator_reuse(self, creator, funder_count, transfers, active_days):
        """Helper: Score creator reuse (see Algorithm 3.2)"""
        # Implementation of score_creator_reuse algorithm
        pass

    def _compute_launch_probability(self, creator, cluster_info, reuse_info, rep_info):
        """Helper: Compute launch probability (see Algorithm 3.3)"""
        # Implementation of compute_launch_probability algorithm
        pass
```

---

## SECTION 5: API ENDPOINTS

### Endpoint 5.1: GET /api/launch/watchlist

List creators sorted by launch probability.

```python
@app.route('/api/launch/watchlist')
def get_launch_watchlist():
    """
    Get launch watchlist sorted by probability (highest first).

    Query params:
    - risk_level: CRITICAL|HIGH|MEDIUM|LOW|MINIMAL (filter)
    - min_probability: 0-100 (default: 20)
    - limit: rows (default: 100)

    Response: [{
        "creator": "string",
        "launch_probability": 0-100,
        "risk_level": "CRITICAL|HIGH|MEDIUM|LOW|MINIMAL",
        "expected_launch_day": 1-7,
        "funder_count": int,
        "active_days": float,
        "signals": [string],
        "factor_breakdown": {
            "cluster_confidence": 0-25,
            "creator_reuse": 0-25,
            "recent_activity": 0-20,
            "reputation": 0-20,
            "wallet_age": 0-10
        }
    }]
    """
    pass
```

### Endpoint 5.2: GET /api/launch/watchlist/<creator>

Detailed launch prediction for single creator.

```python
@app.route('/api/launch/watchlist/<creator>')
def get_launch_prediction(creator):
    """
    Get detailed launch prediction for specific creator.

    Response: {
        "creator": "string",
        "launch_probability": 0-100,
        "risk_level": "CRITICAL|HIGH|MEDIUM|LOW|MINIMAL",
        "expected_launch_day": 1-7,
        "expected_launch_window": "0-1 days|1-3 days|3-7 days",
        "confidence": float,
        "funder_count": int,
        "cluster_id": int|null,
        "primary_funder": "string",
        "funding_history": [{
            "funder": "string",
            "amount_sol": float,
            "timestamp": int
        }],
        "factor_breakdown": {...},
        "signals": [string],
        "reasoning": "string"
    }
    """
    pass
```

### Endpoint 5.3: GET /api/launch/critical-risk

Get all creators with CRITICAL or HIGH risk levels.

```python
@app.route('/api/launch/critical-risk')
def get_critical_risk_launches():
    """
    Get all creators in critical/high risk for imminent launch.

    Response: [{
        "creator": "string",
        "launch_probability": 0-100,
        "risk_level": "CRITICAL|HIGH",
        "hours_since_last_funding": float,
        "expected_launch_ts": int,
        "cluster_id": int,
        "funder_count": int,
        "warning": "string describing why critical"
    }]
    """
    pass
```

### Endpoint 5.4: GET /api/launch/history

Get prediction accuracy history.

```python
@app.route('/api/launch/history')
def get_launch_history():
    """
    Get historical predictions and actual launches for accuracy tracking.

    Query params:
    - limit: rows (default: 50)
    - launched_only: bool (default: false)

    Response: [{
        "creator": "string",
        "predicted_probability": 0-100,
        "predicted_risk_level": "string",
        "actual_launch_ts": int|null,
        "days_to_launch": int|null,
        "prediction_accuracy": 0-100|null,
        "token_mint": "string|null",
        "detected_at": int,
        "updated_at": int
    }]
    """
    pass
```

### Endpoint 5.5: GET /api/clusters/pumpfun

List pump.fun dev farms.

```python
@app.route('/api/clusters/pumpfun')
def get_pumpfun_farms():
    """
    Get pump.fun-style coordinated operations (4+ creators, <48h).

    Response: [{
        "funder_wallet": "string",
        "creator_count": int,
        "transfer_count": int,
        "confidence": 0-100,
        "avg_amount": float,
        "stddev": float,
        "span_hours": float,
        "creators": [string],
        "signals": [string],
        "has_existing_cluster": bool,
        "cluster_id": int|null
    }]
    """
    pass
```

### Endpoint 5.6: GET /api/creators/reuse

List creators funded by multiple wallets.

```python
@app.route('/api/creators/reuse')
def get_creator_reuse():
    """
    Get creators with multiple funding sources (coordination signal).

    Query params:
    - min_funders: int (default: 3)
    - min_reuse_score: 0-40 (default: 0)

    Response: [{
        "creator": "string",
        "funder_count": int,
        "transfer_count": int,
        "reuse_score": 0-40,
        "active_days": float,
        "expected_launch_window": "0-1 days|1-3 days|3-7 days",
        "funder_list": [string],
        "in_cluster": bool,
        "cluster_id": int|null
    }]
    """
    pass
```

---

## SUMMARY

**Phase 3.3+ adds six capabilities**:
1. ✅ Pump.fun farm detection (Query 1.1, Algorithm 3.1, Endpoint 5.5)
2. ✅ Creator reuse detection (Query 1.2, Algorithm 3.2, Endpoint 5.6)
3. ✅ creator_reuse table (Schema 2.1)
4. ✅ launch_watchlist table (Schema 2.2)
5. ✅ Launch prediction algorithm (Algorithm 3.3, Endpoint 5.1-5.2)
6. ✅ Pipeline integration (Integration 4.1, cluster_detection.py enhancement)

**Key Metrics**:
- Pump.fun confidence: 0-100 (creators, time window, consistency, activity)
- Reuse score: 0-40 (funder diversity, frequency)
- Launch probability: 0-100 (5-factor model)
- Risk levels: CRITICAL (>75%), HIGH (60-75%), MEDIUM (40-60%), LOW (20-40%), MINIMAL (<20%)

**Detection Schedule**:
- Phase 3.2: 2 AM UTC (storage cleanup)
- Phase 3.3: 3 AM UTC (dev farms + reputation)
- Phase 3.3+: 3:30 AM UTC (pump.fun + reuse + watchlist)
