# Phase 4: Advanced Dev Farm Intelligence System

**Status**: Design & Implementation
**Date**: March 10, 2026
**Base**: Phase 3.3+ (launch prediction)
**Effort**: ~40 hours
**Goal**: Build multi-layered dev farm ecosystem detection with launch wave prediction

---

## Executive Summary

Phase 4 transforms FLEX from **point detection** (individual farms + creators) to **ecosystem detection** (networked operations across multiple funders/creators).

**New Capabilities**:
1. ✅ Dev farm wallet detection (existing Phase 3.3)
2. ✅ Creator reuse detection (existing Phase 3.3+)
3. **NEW**: Dev farm ecosystems (funders sharing creators)
4. **NEW**: Pump.fun launch waves (coordinated multi-creator launches)
5. **NEW**: Launch watchlist enhancements (ecosystem-level signals)
6. **NEW**: Daily pipeline integration with ecosystem scoring

---

## SECTION 1: SQL QUERIES FOR FARM DETECTION

### Query 1.1: Identify Dev Farm Wallets (Existing - Phase 3.3)

```sql
-- Phase 3.3: Individual dev farms
SELECT
    source AS funder_wallet,
    COUNT(DISTINCT destination) AS creator_count,
    COUNT(*) AS transfer_count,
    ROUND(AVG(amount_sol), 3) AS avg_amount,
    MIN(block_time) AS first_ts,
    MAX(block_time) AS last_ts,
    (MAX(block_time) - MIN(block_time)) / 86400.0 AS span_days,
    GROUP_CONCAT(DISTINCT destination) AS creator_list
FROM transfer_index
WHERE amount_sol BETWEEN 0.5 AND 10.0
  AND is_valid = 1
  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY source
HAVING creator_count >= 3
  AND span_days >= 2
ORDER BY creator_count DESC;
```

---

### Query 1.2: Detect Creator Reuse (Existing - Phase 3.3+)

```sql
-- Phase 3.3+: Creators funded by multiple wallets
SELECT
    destination AS creator_wallet,
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
ORDER BY funder_count DESC;
```

---

### Query 1.3: NEW - Detect Dev Farm Ecosystems

**Pattern**: Funders who share multiple creators (multi-hop coordination)

```sql
-- Phase 4: Ecosystem detection - funders sharing creators
SELECT
    f1.source AS funder_1,
    f2.source AS funder_2,
    COUNT(DISTINCT f1.destination) AS shared_creators,
    COUNT(*) AS shared_transfers,
    ROUND(AVG(f1.amount_sol), 3) AS avg_amount_f1,
    ROUND(AVG(f2.amount_sol), 3) AS avg_amount_f2,
    MIN(LEAST(f1.block_time, f2.block_time)) AS earliest_funding,
    MAX(GREATEST(f1.block_time, f2.block_time)) AS latest_funding,
    (MAX(GREATEST(f1.block_time, f2.block_time)) - MIN(LEAST(f1.block_time, f2.block_time))) / 86400.0 AS coordination_span_days,
    GROUP_CONCAT(DISTINCT f1.destination) AS creators
FROM transfer_index f1
INNER JOIN transfer_index f2 ON f1.destination = f2.destination
WHERE f1.source < f2.source  -- Avoid duplicates
  AND f1.amount_sol BETWEEN 0.5 AND 10.0
  AND f2.amount_sol BETWEEN 0.5 AND 10.0
  AND f1.is_valid = 1
  AND f2.is_valid = 1
  AND f1.source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
  AND f2.source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY f1.source, f2.source
HAVING shared_creators >= 2
  AND coordination_span_days >= 1
ORDER BY shared_creators DESC, coordination_span_days ASC;
```

**Key Insight**: If two wallets fund the same creator, they're likely coordinated. If they share 2+ creators, they're definitely part of same operation.

---

### Query 1.4: NEW - Detect Pump.fun Launch Waves

**Pattern**: Multiple creators receiving funding within short time window + rapid token launches

```sql
-- Phase 4: Pump.fun launch waves - rapid multi-creator coordinated launches
SELECT
    CAST(block_time / 3600 AS INTEGER) * 3600 AS hour_window,  -- Group by hour
    COUNT(DISTINCT source) AS funder_count,
    COUNT(DISTINCT destination) AS creator_count,
    COUNT(*) AS transfer_count,
    ROUND(AVG(amount_sol), 3) AS avg_amount,
    ROUND(MIN(amount_sol), 3) AS min_amount,
    ROUND(MAX(amount_sol), 3) AS max_amount,
    GROUP_CONCAT(DISTINCT source) AS funders,
    GROUP_CONCAT(DISTINCT destination) AS creators,
    MIN(amount_sol) AS lowest_transfer,
    MAX(amount_sol) AS highest_transfer
FROM transfer_index
WHERE amount_sol BETWEEN 0.1 AND 5.0  -- Pump.fun typical range (lower than general)
  AND is_valid = 1
  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
  AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY hour_window
HAVING creator_count >= 4  -- 4+ creators in single hour
  AND funder_count >= 2    -- 2+ different funders
ORDER BY hour_window DESC;
```

**Key Insight**: Launch waves show coordinated timing. If 4+ creators get funded in same hour by multiple wallets = high-confidence pump.fun operation.

---

### Query 1.5: NEW - Ecosystem Network Analysis (Graph Query)

**Pattern**: Map all funders + creators in ecosystem to identify clusters

```sql
-- Phase 4: Ecosystem network - build funder-creator bipartite graph
WITH funder_creator_pairs AS (
    SELECT DISTINCT
        source AS funder,
        destination AS creator,
        COUNT(*) AS transfer_count,
        ROUND(AVG(amount_sol), 3) AS avg_amount,
        MIN(block_time) AS first_funding,
        MAX(block_time) AS last_funding
    FROM transfer_index
    WHERE amount_sol BETWEEN 0.5 AND 10.0
      AND is_valid = 1
      AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
      AND destination NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
    GROUP BY source, destination
),
ecosystem_funders AS (
    SELECT DISTINCT funder
    FROM funder_creator_pairs
),
ecosystem_creators AS (
    SELECT DISTINCT creator
    FROM funder_creator_pairs
)
SELECT
    'funder' AS entity_type,
    funder AS entity_address,
    COUNT(DISTINCT creator) AS connections,
    COUNT(*) AS total_transfers,
    ROUND(AVG(avg_amount), 3) AS avg_amount_per_creator,
    MIN(first_funding) AS earliest_activity,
    MAX(last_funding) AS latest_activity
FROM funder_creator_pairs
GROUP BY funder
UNION ALL
SELECT
    'creator' AS entity_type,
    creator AS entity_address,
    COUNT(DISTINCT funder) AS connections,
    COUNT(*) AS total_transfers_received,
    ROUND(AVG(avg_amount), 3) AS avg_amount_per_funder,
    MIN(first_funding) AS earliest_activity,
    MAX(last_funding) AS latest_activity
FROM funder_creator_pairs
GROUP BY creator
ORDER BY entity_type, connections DESC;
```

---

### Query 1.6: NEW - Launch Wave Timing Correlation

**Pattern**: Creators funded in same launch wave likely to launch tokens together

```sql
-- Phase 4: Launch wave creators - likely to coordinate token launches
SELECT
    destination AS creator,
    COUNT(DISTINCT source) AS funders_in_wave,
    COUNT(DISTINCT
        CASE WHEN (block_time / 3600 * 3600) = (
            SELECT (block_time / 3600 * 3600)
            FROM transfer_index t2
            WHERE t2.destination = transfer_index.destination
            LIMIT 1
        ) THEN source ELSE NULL END
    ) AS simultaneous_funders,
    MIN(block_time) AS wave_start,
    MAX(block_time) AS wave_end,
    (MAX(block_time) - MIN(block_time)) / 3600.0 AS wave_duration_hours,
    COUNT(*) AS transfers_in_wave,
    ROUND(AVG(amount_sol), 3) AS avg_amount,
    GROUP_CONCAT(DISTINCT source) AS all_funders
FROM transfer_index
WHERE amount_sol BETWEEN 0.1 AND 5.0
  AND is_valid = 1
  AND source NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
GROUP BY destination
HAVING COUNT(DISTINCT source) >= 3  -- Funded by 3+ wallets in same wave
ORDER BY simultaneous_funders DESC, COUNT(*) DESC;
```

---

## SECTION 2: SCHEMA DEFINITIONS

### Table 2.1: dev_farm_ecosystems

Maps funders that share creators (multi-hop coordination).

```sql
CREATE TABLE IF NOT EXISTS dev_farm_ecosystems (
    -- Primary key
    ecosystem_id        INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Funder pair
    funder_1            TEXT NOT NULL,
    funder_2            TEXT NOT NULL,

    -- Coordination metrics
    shared_creators     INTEGER DEFAULT 0,      -- creators both fund
    shared_transfers    INTEGER DEFAULT 0,      -- total transfers to shared creators
    avg_amount_f1       REAL DEFAULT 0,
    avg_amount_f2       REAL DEFAULT 0,

    -- Timing metrics
    earliest_funding    INTEGER,                -- first transfer by either funder
    latest_funding      INTEGER,                -- last transfer by either funder
    coordination_span_days REAL DEFAULT 0,      -- days between earliest/latest

    -- Creators involved
    creators_list       TEXT,                   -- JSON array of shared creators

    -- Ecosystem scoring
    coordination_score  REAL DEFAULT 0,         -- 0-100 (funders_togetherness)
    ecosystem_size      INTEGER DEFAULT 0,      -- total unique creators funded by both
    is_active_ecosystem BOOLEAN DEFAULT 1,      -- still coordinating

    -- Timestamps
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,

    UNIQUE(funder_1, funder_2)  -- Unique pair (funder_1 < funder_2)
);

CREATE INDEX IF NOT EXISTS idx_ecosystems_coordination_score
    ON dev_farm_ecosystems(coordination_score DESC);
CREATE INDEX IF NOT EXISTS idx_ecosystems_shared_creators
    ON dev_farm_ecosystems(shared_creators DESC);
CREATE INDEX IF NOT EXISTS idx_ecosystems_funder_1
    ON dev_farm_ecosystems(funder_1);
CREATE INDEX IF NOT EXISTS idx_ecosystems_funder_2
    ON dev_farm_ecosystems(funder_2);
CREATE INDEX IF NOT EXISTS idx_ecosystems_active
    ON dev_farm_ecosystems(is_active_ecosystem);
```

---

### Table 2.2: launch_waves

Captures pump.fun-style coordinated launches within time windows.

```sql
CREATE TABLE IF NOT EXISTS launch_waves (
    -- Primary key
    wave_id             INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Wave timing
    wave_hour           INTEGER NOT NULL,       -- UNIX timestamp rounded to hour
    wave_start_ts       INTEGER,
    wave_end_ts         INTEGER,

    -- Participants
    funder_count        INTEGER DEFAULT 0,
    creator_count       INTEGER DEFAULT 0,
    transfer_count      INTEGER DEFAULT 0,

    -- Amount metrics
    avg_amount          REAL DEFAULT 0,
    min_amount          REAL DEFAULT 0,
    max_amount          REAL DEFAULT 0,
    amount_stddev       REAL DEFAULT 0,

    -- Entities
    funders_list        TEXT,                   -- JSON array
    creators_list       TEXT,                   -- JSON array

    -- Wave characteristics
    wave_intensity      REAL DEFAULT 0,         -- 0-100 (concentration of transfers)
    coordination_signal REAL DEFAULT 0,         -- 0-100 (likelihood of coordinated launch)
    pump_fun_confidence REAL DEFAULT 0,         -- 0-100

    -- Detection
    is_pump_fun_wave    BOOLEAN DEFAULT 0,
    is_verified_launch  BOOLEAN DEFAULT 0,      -- populated when tokens launch

    -- Timestamps
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,

    UNIQUE(wave_hour)  -- One wave per hour
);

CREATE INDEX IF NOT EXISTS idx_waves_pump_fun_confidence
    ON launch_waves(pump_fun_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_waves_creator_count
    ON launch_waves(creator_count DESC);
CREATE INDEX IF NOT EXISTS idx_waves_timestamp
    ON launch_waves(wave_hour DESC);
CREATE INDEX IF NOT EXISTS idx_waves_verified
    ON launch_waves(is_verified_launch);
```

---

### Table 2.3: ecosystem_member_tracking

Tracks which creators/funders belong to which ecosystems.

```sql
CREATE TABLE IF NOT EXISTS ecosystem_member_tracking (
    -- Primary key
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Ecosystem reference
    ecosystem_id        INTEGER NOT NULL,

    -- Member (either funder or creator)
    member_address      TEXT NOT NULL,
    member_type         TEXT NOT NULL,         -- 'funder' or 'creator'

    -- Member metrics
    connections_in_ecosystem INTEGER DEFAULT 0,
    transfer_count_in_ecosystem INTEGER DEFAULT 0,
    avg_amount_in_ecosystem REAL DEFAULT 0,

    -- Activity window
    first_activity_ts   INTEGER,
    last_activity_ts    INTEGER,
    active_days         REAL DEFAULT 0,

    -- Role assessment
    is_ecosystem_leader BOOLEAN DEFAULT 0,     -- funds most creators
    is_ecosystem_hub    BOOLEAN DEFAULT 0,     -- funded by most funders
    ecosystem_importance_score REAL DEFAULT 0, -- 0-100

    -- Timestamps
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,

    FOREIGN KEY(ecosystem_id) REFERENCES dev_farm_ecosystems(ecosystem_id),
    UNIQUE(ecosystem_id, member_address)
);

CREATE INDEX IF NOT EXISTS idx_ecosystem_members_type
    ON ecosystem_member_tracking(member_type);
CREATE INDEX IF NOT EXISTS idx_ecosystem_members_importance
    ON ecosystem_member_tracking(ecosystem_importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_ecosystem_members_leader
    ON ecosystem_member_tracking(is_ecosystem_leader);
```

---

### Table 2.4: launch_wave_creators

Links creators to their launch waves with prediction data.

```sql
CREATE TABLE IF NOT EXISTS launch_wave_creators (
    -- Primary key
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- References
    wave_id             INTEGER NOT NULL,
    creator_wallet      TEXT NOT NULL,

    -- Wave context
    funder_count_in_wave INTEGER DEFAULT 0,
    simultaneous_funders INTEGER DEFAULT 0,

    -- Launch prediction
    wave_launch_probability REAL DEFAULT 0,    -- 0-100
    predicted_launch_ts INTEGER,               -- Estimated time of launch
    expected_token_mint TEXT,                  -- If known

    -- Coordination signals
    same_hour_funders_count INTEGER DEFAULT 0,
    sequential_funding BOOLEAN DEFAULT 0,      -- funded right after previous creator

    -- Verification
    token_launched      BOOLEAN DEFAULT 0,
    actual_launch_ts    INTEGER,
    prediction_accuracy REAL,                  -- 0-100 if launched

    -- Timestamps
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,

    FOREIGN KEY(wave_id) REFERENCES launch_waves(wave_id),
    UNIQUE(wave_id, creator_wallet)
);

CREATE INDEX IF NOT EXISTS idx_wave_creators_probability
    ON launch_wave_creators(wave_launch_probability DESC);
CREATE INDEX IF NOT EXISTS idx_wave_creators_launched
    ON launch_wave_creators(token_launched);
```

---

### Table 2.5: ecosystem_evolution_log

Audit trail of ecosystem changes and growth.

```sql
CREATE TABLE IF NOT EXISTS ecosystem_evolution_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Ecosystem reference
    ecosystem_id        INTEGER NOT NULL,

    -- Change event
    event_type          TEXT NOT NULL,         -- 'new_member', 'new_connection', 'merger', 'inactive'
    event_details       TEXT,                  -- JSON with details

    -- Metrics at time of event
    funder_count_at_event INTEGER,
    creator_count_at_event INTEGER,
    coordination_score_at_event REAL,

    -- Timeline
    event_ts            REAL NOT NULL,

    FOREIGN KEY(ecosystem_id) REFERENCES dev_farm_ecosystems(ecosystem_id)
);

CREATE INDEX IF NOT EXISTS idx_evolution_ecosystem
    ON ecosystem_evolution_log(ecosystem_id);
CREATE INDEX IF NOT EXISTS idx_evolution_event_type
    ON ecosystem_evolution_log(event_type);
CREATE INDEX IF NOT EXISTS idx_evolution_timestamp
    ON ecosystem_evolution_log(event_ts DESC);
```

---

## SECTION 3: SCORING ALGORITHMS

### Algorithm 3.1: Ecosystem Coordination Scoring (0-100)

Measures how tightly coordinated two funders are.

```python
def score_ecosystem_coordination(
    shared_creators: int,
    shared_transfers: int,
    coordination_span_days: float,
    avg_amount_f1: float,
    avg_amount_f2: float,
    ecosystem_size: int  # Total unique creators funded by both
) -> Dict:
    """
    Score coordination between two funders.

    Factors:
    - Shared creators count (0-30): 2→10, 3→18, 4+→30
    - Consistency of amounts (0-25): Similar amounts = more coordination
    - Timing concentration (0-25): Rapid funding = more coordination
    - Ecosystem scope (0-20): More creators = larger operation
    """

    scores = {}
    signals = []

    # Factor 1: Shared creator count (0-30)
    if shared_creators >= 4:
        scores['shared_creators'] = 30
        signals.append(f"4+ shared creators ({shared_creators})")
    elif shared_creators >= 3:
        scores['shared_creators'] = 18
        signals.append(f"3 shared creators")
    elif shared_creators >= 2:
        scores['shared_creators'] = 10
        signals.append(f"2 shared creators")
    else:
        scores['shared_creators'] = 0

    # Factor 2: Amount consistency (0-25)
    amount_diff = abs(avg_amount_f1 - avg_amount_f2)
    if amount_diff < 0.5:
        scores['consistency'] = 25
        signals.append(f"Highly consistent funding (diff: {amount_diff:.2f})")
    elif amount_diff < 1.0:
        scores['consistency'] = 18
        signals.append(f"Consistent funding (diff: {amount_diff:.2f})")
    elif amount_diff < 2.0:
        scores['consistency'] = 10
        signals.append(f"Moderate consistency (diff: {amount_diff:.2f})")
    else:
        scores['consistency'] = 0

    # Factor 3: Timing concentration (0-25)
    if coordination_span_days <= 1:
        scores['timing'] = 25
        signals.append("Simultaneous funding (<24h)")
    elif coordination_span_days <= 3:
        scores['timing'] = 18
        signals.append(f"Rapid coordination ({coordination_span_days:.1f} days)")
    elif coordination_span_days <= 7:
        scores['timing'] = 10
        signals.append(f"Coordinated funding ({coordination_span_days:.1f} days)")
    else:
        scores['timing'] = 0

    # Factor 4: Ecosystem scope (0-20)
    if ecosystem_size >= 5:
        scores['scope'] = 20
        signals.append(f"Large ecosystem ({ecosystem_size} creators)")
    elif ecosystem_size >= 4:
        scores['scope'] = 15
        signals.append(f"Medium ecosystem ({ecosystem_size} creators)")
    elif ecosystem_size >= 3:
        scores['scope'] = 10
        signals.append(f"Small ecosystem ({ecosystem_size} creators)")
    else:
        scores['scope'] = 0

    total_score = sum(scores.values())

    return {
        'coordination_score': min(total_score, 100),
        'is_coordinated': total_score >= 50,
        'signals': signals,
        'scores': scores,
        'reasoning': '; '.join(signals)
    }
```

---

### Algorithm 3.2: Launch Wave Intensity (0-100)

Measures concentration of transfers in time window.

```python
def score_launch_wave_intensity(
    creator_count: int,
    funder_count: int,
    transfer_count: int,
    span_hours: float,
    avg_amount: float,
    stddev_amount: float
) -> Dict:
    """
    Score intensity of launch wave.

    High intensity = many creators funded in short time by multiple funders
    = likely coordinated pump.fun operation
    """

    scores = {}
    signals = []

    # Factor 1: Creator concentration (0-30)
    creators_per_hour = creator_count / max(span_hours, 0.25)
    if creators_per_hour >= 8:
        scores['concentration'] = 30
        signals.append(f"Extreme concentration ({creators_per_hour:.0f} creators/h)")
    elif creators_per_hour >= 4:
        scores['concentration'] = 22
        signals.append(f"High concentration ({creators_per_hour:.0f} creators/h)")
    elif creators_per_hour >= 2:
        scores['concentration'] = 15
        signals.append(f"Moderate concentration ({creators_per_hour:.0f} creators/h)")
    else:
        scores['concentration'] = 0

    # Factor 2: Multi-funder participation (0-25)
    if funder_count >= 4:
        scores['multi_funder'] = 25
        signals.append(f"4+ coordinated funders")
    elif funder_count >= 3:
        scores['multi_funder'] = 18
        signals.append(f"3 coordinated funders")
    elif funder_count >= 2:
        scores['multi_funder'] = 10
        signals.append(f"2 coordinated funders")
    else:
        scores['multi_funder'] = 0

    # Factor 3: Transfer density (0-20)
    transfers_per_creator = transfer_count / max(creator_count, 1)
    if transfers_per_creator >= 3:
        scores['density'] = 20
        signals.append(f"High density ({transfers_per_creator:.1f} tx/creator)")
    elif transfers_per_creator >= 2:
        scores['density'] = 12
        signals.append(f"Moderate density ({transfers_per_creator:.1f} tx/creator)")
    else:
        scores['density'] = 0

    # Factor 4: Amount consistency (0-25)
    if stddev_amount <= 0.5:
        scores['uniformity'] = 25
        signals.append(f"Uniform amounts (σ={stddev_amount:.2f})")
    elif stddev_amount <= 1.0:
        scores['uniformity'] = 18
        signals.append(f"Consistent amounts (σ={stddev_amount:.2f})")
    elif stddev_amount <= 2.0:
        scores['uniformity'] = 10
        signals.append(f"Variable amounts (σ={stddev_amount:.2f})")
    else:
        scores['uniformity'] = 0

    total_score = sum(scores.values())

    return {
        'wave_intensity': min(total_score, 100),
        'is_launch_wave': total_score >= 50,
        'signals': signals,
        'scores': scores
    }
```

---

### Algorithm 3.3: Ecosystem-Level Launch Prediction

Predicts token launches based on ecosystem maturity + wave signals.

```python
def predict_ecosystem_launches(
    ecosystem_coordination_score: float,    # 0-100
    launch_wave_scores: List[Dict],        # Recent waves in ecosystem
    member_reputation: Dict,                # creator reputation data
    ecosystem_age_days: float,
    recent_activity_hours: float
) -> Dict:
    """
    Predict launches for ecosystem members.

    Logic:
    - Established ecosystems (score >70) launch regularly
    - Recent waves predict imminent launches
    - High-reputation members less likely to rug
    """

    factors = {}

    # Factor 1: Ecosystem maturity (0-30)
    if ecosystem_coordination_score >= 75:
        factors['maturity'] = 30
        label = "Highly organized ecosystem"
    elif ecosystem_coordination_score >= 60:
        factors['maturity'] = 20
        label = "Established ecosystem"
    else:
        factors['maturity'] = 10
        label = "Emerging ecosystem"

    # Factor 2: Recent wave activity (0-30)
    recent_waves = [w for w in launch_wave_scores if w.get('age_hours', 24) < 24]
    if len(recent_waves) >= 2:
        factors['recent_waves'] = 30
        label += " with rapid activity"
    elif len(recent_waves) >= 1:
        factors['recent_waves'] = 20
        label += " with recent activity"
    else:
        factors['recent_waves'] = 5

    # Factor 3: Creator reputation (0-25)
    avg_reputation = sum(m.get('reputation_score', 50) for m in member_reputation) / len(member_reputation) if member_reputation else 50
    if avg_reputation >= 70:
        factors['reputation'] = 25
    elif avg_reputation >= 50:
        factors['reputation'] = 15
    else:
        factors['reputation'] = 5

    # Factor 4: Ecosystem age (0-15)
    if ecosystem_age_days >= 30:
        factors['age'] = 15
    elif ecosystem_age_days >= 14:
        factors['age'] = 10
    else:
        factors['age'] = 5

    launch_probability = sum(factors.values())

    # Determine expected launch timeframe
    if launch_probability >= 75:
        expected_days = 1
        risk_level = "CRITICAL"
    elif launch_probability >= 60:
        expected_days = 2
        risk_level = "HIGH"
    elif launch_probability >= 45:
        expected_days = 4
        risk_level = "MEDIUM"
    else:
        expected_days = 7
        risk_level = "LOW"

    return {
        'ecosystem_launch_probability': launch_probability,
        'expected_launch_days': expected_days,
        'risk_level': risk_level,
        'factors': factors,
        'signals': [label]
    }
```

---

## SECTION 4: PIPELINE INTEGRATION

### Integration 4.1: Enhanced Daily Detection Pipeline

```python
class AdvancedFarmIntelligenceEngine:
    """
    Phase 4: Ecosystem-level dev farm detection.

    Extends Phase 3.3+ with:
    - Ecosystem detection (funders sharing creators)
    - Launch wave analysis (pump.fun coordination)
    - Member tracking and evolution
    """

    def detect_and_store(self) -> Dict:
        """
        Enhanced detection orchestration:
        1. Detect dev farm ecosystems (2-hop coordination)
        2. Detect launch waves (time-windowed multi-creator funding)
        3. Score ecosystem members
        4. Update launch watchlist with ecosystem signals
        """
        start_time = time.time()
        self._ensure_tables()

        try:
            # Step 1: Ecosystem detection
            ecosystems = self._detect_ecosystems()
            ecosystems_stored = self._store_ecosystems(ecosystems)

            # Step 2: Launch wave detection
            launch_waves = self._detect_launch_waves()
            waves_stored = self._store_launch_waves(launch_waves)

            # Step 3: Ecosystem member analysis
            members_analyzed = self._analyze_ecosystem_members()

            # Step 4: Link creators to waves
            creators_linked = self._link_creators_to_waves()

            # Step 5: Update launch watchlist with ecosystem signals
            watchlist_enhanced = self._enhance_launch_watchlist_with_ecosystems()

            duration_ms = (time.time() - start_time) * 1000

            return {
                'status': 'success',
                'ecosystems_detected': ecosystems_stored,
                'launch_waves_detected': waves_stored,
                'members_analyzed': members_analyzed,
                'creators_linked': creators_linked,
                'watchlist_enhanced': watchlist_enhanced,
                'duration_ms': duration_ms,
                'message': f'Phase 4: {ecosystems_stored} ecosystems, {waves_stored} waves, {watchlist_enhanced} watchlist updates'
            }

        except Exception as e:
            logger.error(f"Advanced farm detection failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'duration_ms': (time.time() - start_time) * 1000
            }

    def _detect_ecosystems(self) -> List[Dict]:
        """Detect funders sharing creators (Query 1.3)."""
        # Executes Query 1.3, scores with Algorithm 3.1
        pass

    def _detect_launch_waves(self) -> List[Dict]:
        """Detect pump.fun launch waves (Query 1.4)."""
        # Executes Query 1.4, scores with Algorithm 3.2
        pass

    def _analyze_ecosystem_members(self) -> int:
        """Analyze each member's importance within ecosystem."""
        # Uses Query 1.5 network analysis
        pass

    def _link_creators_to_waves(self) -> int:
        """Link creators in ecosystems to launch waves."""
        # Uses Query 1.6 launch wave timing
        pass

    def _enhance_launch_watchlist_with_ecosystems(self) -> int:
        """Update launch_watchlist with ecosystem-level signals."""
        # Applies Algorithm 3.3 ecosystem launch prediction
        # Adds ecosystem_id, ecosystem_coordination_score, wave_signal fields
        pass
```

### Integration 4.2: Updated Daily Schedule

```
2:00 AM UTC  → Phase 3.2: Storage cleanup
3:00 AM UTC  → Phase 3.3: Dev farm detection
3:30 AM UTC  → Phase 3.3+: Creator reuse + Launch prediction
4:00 AM UTC  → Phase 4: Ecosystem detection + Launch waves (NEW)
```

---

## SECTION 5: REST API ENDPOINTS

### Endpoint 5.1: GET /api/ecosystem/detect

List detected dev farm ecosystems.

```python
@app.route('/api/ecosystem/detect')
def get_ecosystems():
    """
    Get detected dev farm ecosystems sorted by coordination score.

    Query params:
    - min_score: 0-100 (default: 60)
    - min_creators: int (default: 2)
    - limit: rows (default: 50)

    Response: [{
        "ecosystem_id": int,
        "funder_1": "address",
        "funder_2": "address",
        "shared_creators": int,
        "coordination_score": 0-100,
        "coordination_span_days": float,
        "creators": [string],
        "is_active": bool
    }]
    """
    pass
```

### Endpoint 5.2: GET /api/ecosystem/<ecosystem_id>

Get detailed ecosystem information with member metrics.

```python
@app.route('/api/ecosystem/<int:ecosystem_id>')
def get_ecosystem_detail(ecosystem_id):
    """
    Get ecosystem with all member details.

    Response: {
        "ecosystem_id": int,
        "funders": [{
            "address": "string",
            "connections": int,
            "is_leader": bool,
            "importance_score": 0-100,
            "activity_days": float
        }],
        "creators": [{
            "address": "string",
            "funder_count": int,
            "is_hub": bool,
            "importance_score": 0-100,
            "launch_probability": 0-100
        }],
        "coordination_score": 0-100,
        "ecosystem_size": int,
        "evolution_timeline": [{...}]
    }
    """
    pass
```

### Endpoint 5.3: GET /api/launch-waves

List detected pump.fun launch waves.

```python
@app.route('/api/launch-waves')
def get_launch_waves():
    """
    Get detected pump.fun launch waves.

    Query params:
    - min_confidence: 0-100 (default: 70)
    - min_creators: int (default: 4)
    - hours_ago: int (default: 168)  -- Last N hours

    Response: [{
        "wave_id": int,
        "wave_hour": int,  -- UNIX timestamp
        "creator_count": int,
        "funder_count": int,
        "pump_fun_confidence": 0-100,
        "creators": [string],
        "funders": [string],
        "wave_intensity": 0-100,
        "is_verified_launch": bool
    }]
    """
    pass
```

### Endpoint 5.4: GET /api/ecosystem/members/<ecosystem_id>

Get ecosystem member network visualization data.

```python
@app.route('/api/ecosystem/members/<int:ecosystem_id>')
def get_ecosystem_members(ecosystem_id):
    """
    Get bipartite graph data for visualization.

    Response: {
        "ecosystem_id": int,
        "nodes": {
            "funders": [{
                "id": "address",
                "name": "Funder 1",
                "importance": 0-100,
                "connections": int,
                "size": int  -- viz size
            }],
            "creators": [{
                "id": "address",
                "name": "Creator 1",
                "importance": 0-100,
                "connections": int,
                "size": int
            }]
        },
        "edges": [{
            "source": "funder_address",
            "target": "creator_address",
            "weight": int,  -- transfer count
            "amount_total": float
        }]
    }
    """
    pass
```

### Endpoint 5.5: GET /api/launch-waves/<wave_id>/creators

Get creators in launch wave with launch predictions.

```python
@app.route('/api/launch-waves/<int:wave_id>/creators')
def get_wave_creators(wave_id):
    """
    Get all creators in a launch wave with launch predictions.

    Response: [{
        "creator_wallet": "string",
        "funders_in_wave": int,
        "wave_launch_probability": 0-100,
        "predicted_launch_ts": int,
        "reputation_score": 0-100,
        "ecosystem_id": int,
        "token_launched": bool,
        "actual_launch_ts": int
    }]
    """
    pass
```

### Endpoint 5.6: GET /api/ecosystem/evolution/<ecosystem_id>

Get ecosystem evolution timeline.

```python
@app.route('/api/ecosystem/evolution/<int:ecosystem_id>')
def get_ecosystem_evolution(ecosystem_id):
    """
    Get timeline of ecosystem changes and growth.

    Response: [{
        "event_ts": int,
        "event_type": "new_member|new_connection|merger|inactive",
        "details": "string description",
        "metrics_at_event": {
            "funder_count": int,
            "creator_count": int,
            "coordination_score": 0-100
        }
    }]
    """
    pass
```

### Endpoint 5.7: GET /api/ecosystem/predict-launches

Get ecosystem-wide launch predictions.

```python
@app.route('/api/ecosystem/predict-launches')
def predict_ecosystem_launches():
    """
    Get all ecosystems ranked by launch probability.

    Query params:
    - min_probability: 0-100 (default: 60)
    - days_horizon: int (default: 7)

    Response: [{
        "ecosystem_id": int,
        "ecosystem_launch_probability": 0-100,
        "expected_launch_days": 1-7,
        "risk_level": "CRITICAL|HIGH|MEDIUM|LOW",
        "creator_count": int,
        "recent_waves": int,
        "predicted_creators": [string]
    }]
    """
    pass
```

---

## ARCHITECTURE DECISIONS

### Why 2-Hop Ecosystem Detection (vs 1-Hop)?

**1-Hop** (Phase 3.3): Individual dev farm
- Funder A funds creators X, Y, Z
- Limited visibility

**2-Hop** (Phase 4): Ecosystem
- Funder A funds X, Y, Z
- Funder B funds X, Y (shares with A)
- Funder C funds X, W (shares with both)
- Network reveals operation spanning multiple wallets
- **Competitive advantage**: Most platforms don't detect multi-wallet coordination

### Why Separate Launch Waves from Individual Farms?

**Individual Farm (Phase 3.3+)**: Single wallet, multiple creators
**Launch Wave (Phase 4)**: Multiple wallets, multiple creators, same time window

Launch waves reveal:
- Synchronized execution (technical sophistication)
- Token coordination (likely launching same protocol)
- Larger operation size (more capital, more impact)

### Why Ecosystem Evolution Tracking?

Ecosystems evolve:
- **Formation**: New funder joins, starts sharing creator
- **Growth**: New members added, ecosystem expands
- **Consolidation**: Mergers, funneling through single wallet
- **Decline**: Members go inactive

Tracking changes predicts:
- When ecosystem is most active (imminent launches)
- Organizational structure changes
- Takeover/acquisition signals

---

## COMPETITIVE ADVANTAGES

1. **Multi-Wallet Coordination Detection** — Most platforms see wallets independently
2. **Ecosystem Visualization** — Graph-based understanding of operations
3. **Launch Wave Prediction** — Synchronized launches predicted by timing patterns
4. **Evolution Timeline** — Track ecosystem lifecycle and predict phase changes
5. **Member Importance Scoring** — Identify leaders vs followers in networks
6. **Bipartite Graph Analysis** — Proper network science (funder-creator relationships)

---

## IMPLEMENTATION PRIORITY

**Must Have** (enables core Phase 4):
1. Ecosystem detection (Query 1.3, Table 2.1, Algorithm 3.1)
2. Launch wave detection (Query 1.4, Table 2.2, Algorithm 3.2)
3. Member tracking (Table 2.3, Query 1.5)
4. Ecosystem launch prediction (Algorithm 3.3)
5. API endpoints 1-3

**Nice to Have** (enhances Phase 4):
6. Evolution tracking (Table 2.5)
7. Wave creator linking (Table 2.4)
8. API endpoints 4-7
9. Visualization endpoints

---

## DATABASE IMPACT

### Storage
```
dev_farm_ecosystems: 0.5-2 MB (grows with funders)
launch_waves: 0.5-2 MB (hourly entries, auto-prune old)
ecosystem_member_tracking: 1-3 MB (all members)
launch_wave_creators: 0.5-2 MB (creators per wave)
ecosystem_evolution_log: <0.5 MB (audit trail)
Total: 3-10 MB overhead
```

### Performance
```
Ecosystem detection: 50-200ms (depends on transfer_index size)
Launch wave detection: 20-100ms
Member analysis: 30-150ms
Total daily run: 150-500ms (vs 100-350ms for Phase 3.3+)
```

---

## NEXT STEPS

1. Implement ecosystem detection algorithms
2. Add ecosystem tables to database schema
3. Integrate into daily pipeline
4. Build API endpoints
5. Add ecosystem visualization
6. Test on real data
7. Monitor accuracy of ecosystem predictions vs actual launches

**Expected Timeline**: 40 hours implementation + 10 hours testing = 50 hours total
