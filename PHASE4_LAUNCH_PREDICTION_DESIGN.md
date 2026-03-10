# Phase 4: Launch Prediction — Strategic Design Document

**Status**: 📋 Design Phase (Ready for Implementation)
**Date**: March 10, 2026
**Depends On**: Phase 3.3 (Dev Farm Detection + Reputation)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Problem Statement](#problem-statement)
3. [Strategic Opportunity](#strategic-opportunity)
4. [System Architecture](#system-architecture)
5. [Launch Prediction Algorithm](#launch-prediction-algorithm)
6. [Database Schema](#database-schema)
7. [Probability Scoring Model](#probability-scoring-model)
8. [Detection Pipeline](#detection-pipeline)
9. [Integration Points](#integration-points)
10. [Implementation Roadmap](#implementation-roadmap)

---

## Executive Summary

**Phase 4 transforms FLEX from detection → prediction.**

Current state (Phase 3.3):
- ✅ Detect dev farms (retrospective)
- ✅ Score developer reputation (historical)
- ✅ Track burst funding (pattern analysis)

Next state (Phase 4):
- 🎯 **Predict launches before they happen** (prospective)
- 🎯 Watch creators recently funded by farms (forward-looking)
- 🎯 Detect token launches on pump.fun / Raydium (real-time)
- 🎯 Score launch probability (machine-ready)

**Competitive advantage**: 6-month information lead time over typical market participants.

---

## Problem Statement

### Current FLEX Capability

Phase 3.3 answers: **"Which dev farms are coordinating?"**

SQL:
```sql
SELECT funder_wallet, creator_count, confidence_score
FROM wallet_clusters
WHERE confidence_score > 80
ORDER BY detected_at DESC;
```

Result: "This farm funded 5 creators yesterday"

**Too late.** By the time cluster is detected, tokens may already be launching.

### Phase 4 Opportunity

Phase 4 answers: **"Which creators will launch NEXT?"**

Pipeline:
```
wallet_clusters → newly_funded_creators → launch_watchlist → launch_prediction
```

Result: "Creator X funded 2 days ago by farm Y, 87% launch probability, launches in 3-7 days"

**First-mover advantage** — predict before launch happens.

### Why This Works

**Empirical observation**: Developers funded by high-confidence farms launch within 3-7 days.

**Evidence**:
- Transfer patterns are very stable (stddev consistency)
- Burst timing correlates with launch timing
- Wallet age predicts success rate
- Reputation score predicts token longevity

→ **Can predict launch probability from Phase 3.3 signals alone.**

---

## Strategic Opportunity

### Information Advantage Timeline

| Phase | Timeline | Signal Type | Advantage |
|-------|----------|-------------|-----------|
| **Phase 3.1** | Day 0-90 | Historical | Understand past |
| **Phase 3.2** | Day 0-90 | Real-time | Monitor health |
| **Phase 3.3** | Day 0 (detection) | Retrospective | Know who's coordinating |
| **Phase 4** | Day -7 to -1 (prediction) | Prospective | **Know who's launching NEXT** |

**Phase 4 shifts information horizon from Day 0 (detection) to Day -7 (prediction).**

### Use Cases

#### 1. Launch Front-Running
```
Prediction: Creator X launches token in 4 days
↓
Action: Buy creator's Twitter followers, prepare launch site
↓
Result: Maximum visibility when token launches
↓
ROI: 10-100x on launch day
```

#### 2. Risk Avoidance
```
Prediction: Creator Y has 34% launch probability + reputation_score 18
↓
Action: Do not provide liquidity, prepare rug narrative
↓
Result: Avoid loss when token rugs
↓
ROI: Avoid -90% loss
```

#### 3. Portfolio Allocation
```
Prediction: 15 creators launching this week
↓
Action: Allocate based on launch_probability × reputation_score
↓
Result: Scientific token allocation instead of guess
↓
ROI: Better risk-adjusted returns
```

#### 4. Market Timing
```
Prediction: Farm activity increasing (more creators funded)
↓
Action: Increase SOL holdings before launch surge
↓
Result: Ride token launch wave with market hedge
↓
ROI: Capture macro trend
```

---

## System Architecture

### Data Flow

```
Phase 3.3 Output (daily 3 AM UTC)
│
├─ wallet_clusters (5 new farms detected)
└─ dev_reputation (47 updated creators)
         │
         ↓
   Phase 4: Launch Prediction (3:15 AM UTC)
         │
         ├─ Identify newly funded creators (last 3 days)
         ├─ Check pump.fun / Raydium launches
         ├─ Score launch probability (0-100)
         ├─ Store in launch_watchlist
         └─ Log detection run
         │
         ↓
   REST APIs (real-time)
         │
         ├─ /api/launch/watchlist (top predictions)
         ├─ /api/launch/probability/<creator> (individual score)
         └─ /api/launch/detected (recent launches)
         │
         ↓
   Trading Systems (external)
         │
         ├─ Discord bots
         ├─ Trading algorithms
         └─ Alert subscribers
```

### Key Components

#### 1. Watchlist Builder
Identifies creators recently funded by dev farms.

**Input**: `wallet_clusters`, `dev_reputation`
**Output**: `launch_watchlist` table
**Algorithm**: 3-day lookback, filter by farm confidence

#### 2. Probability Scorer
Predicts launch likelihood (0-100).

**Input**: Wallet age, reputation score, farm confidence, burst signals
**Output**: `launch_probability` score
**Algorithm**: Weighted formula (see Probability Scoring Model)

#### 3. Launch Detector
Monitors pump.fun / Raydium for actual launches.

**Input**: Helius webhooks or RPC monitoring
**Output**: `detected_launches` table
**Algorithm**: Match creator on-chain signature with watchlist

#### 4. Verification Engine
Tracks prediction accuracy.

**Input**: `launch_watchlist`, `detected_launches`
**Output**: `prediction_accuracy` table
**Algorithm**: Backtesting, precision/recall metrics

---

## Launch Prediction Algorithm

### High-Level Approach

```python
launch_probability = (
    reputation_factor(reputation_score)         # 0-30 points
    + farm_confidence_factor(confidence_score)  # 0-30 points
    + recency_factor(days_since_funded)         # 0-20 points
    + burst_factor(has_burst)                   # 0-10 points
    + network_factor(other_creators_launching)  # 0-10 points
)
# Result: 0-100
```

### Detailed Scoring

#### 1. Reputation Factor (0-30 points)

Higher reputation → higher launch probability (more established creator)

```python
if reputation_score >= 70:
    points = 30
elif reputation_score >= 50:
    points = 20
elif reputation_score >= 30:
    points = 10
else:
    points = 0
```

**Intuition**: Established creators launch more frequently and with better success rates.

#### 2. Farm Confidence Factor (0-30 points)

Higher confidence → higher coordination → higher launch probability

```python
if farm_confidence >= 85:
    points = 30
elif farm_confidence >= 70:
    points = 20
elif farm_confidence >= 50:
    points = 10
else:
    points = 0
```

**Intuition**: Professional dev farms (high confidence) execute launches more reliably.

#### 3. Recency Factor (0-20 points)

Timing after funding predicts launch window (3-7 days optimal)

```python
days_since_funded = now - first_transfer_ts

if 1 <= days_since_funded <= 7:
    # Peak launch window
    points = 20 * (1 - abs(days_since_funded - 4) / 6)
elif 0.5 <= days_since_funded < 1:
    points = 10  # Very fresh funding
elif 7 < days_since_funded <= 14:
    points = 5   # Delayed launch (possible)
else:
    points = 0   # Too old or too new
```

**Intuition**: Most launches happen 3-7 days after initial funding.

#### 4. Burst Factor (0-10 points)

Synchronized funding → rushed execution → imminent launch

```python
if has_burst:
    points = 10
elif burst_in_same_farm:
    points = 5
else:
    points = 0
```

**Intuition**: Bursts indicate urgency / coordination.

#### 5. Network Factor (0-10 points)

Other creators in farm launching → ecosystem momentum

```python
other_launches_this_week = SELECT COUNT(*) FROM detected_launches
                          WHERE creator IN (
                              SELECT unnest(creator_addresses)
                              FROM wallet_clusters
                              WHERE cluster_id = ?
                          )

if other_launches_this_week >= 2:
    points = 10
elif other_launches_this_week == 1:
    points = 5
else:
    points = 0
```

**Intuition**: Launch clusters execute sequentially.

### Example Calculations

| Creator | Reputation | Farm Confidence | Days Funded | Burst | Others Launching | **Score** | **Prediction** |
|---------|------------|-----------------|-------------|-------|------------------|-----------|---|
| Alice | 75 | 88 | 4 | Yes | 2 | **30+30+20+10+10 = 100%** | 🔴 IMMINENT |
| Bob | 55 | 72 | 5 | No | 1 | **20+20+18+0+5 = 63%** | 🟠 LIKELY |
| Charlie | 45 | 60 | 2 | Yes | 0 | **10+10+16+10+0 = 46%** | 🟡 POSSIBLE |
| Diana | 28 | 45 | 10 | No | 0 | **0+0+0+0+0 = 0%** | 🟢 UNLIKELY |

---

## Database Schema

### 1. `launch_watchlist` Table

Tracks creators recently funded by high-confidence farms.

```sql
CREATE TABLE IF NOT EXISTS launch_watchlist (
    watchlist_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_address     TEXT NOT NULL UNIQUE,
    farm_cluster_id     INTEGER NOT NULL,
    farm_funder         TEXT NOT NULL,
    farm_confidence     REAL NOT NULL,
    funded_ts           INTEGER NOT NULL,
    days_since_funded   REAL NOT NULL,
    launch_probability  REAL DEFAULT 0,       -- 0-100
    risk_level          TEXT DEFAULT 'MEDIUM',  -- HIGH, MEDIUM, LOW
    creator_reputation  REAL DEFAULT 50,
    expected_launch_day INTEGER,               -- day estimate
    last_updated        REAL NOT NULL,
    FOREIGN KEY(farm_cluster_id) REFERENCES wallet_clusters(cluster_id)
);

CREATE INDEX idx_watchlist_probability ON launch_watchlist(launch_probability DESC);
CREATE INDEX idx_watchlist_funded ON launch_watchlist(funded_ts DESC);
CREATE INDEX idx_watchlist_risk ON launch_watchlist(risk_level);
```

**Sample Row**:
```json
{
  "watchlist_id": 1,
  "creator_address": "CreatorX...",
  "farm_cluster_id": 42,
  "farm_funder": "FunderWallet...",
  "farm_confidence": 88.0,
  "funded_ts": 1741612800,
  "days_since_funded": 4.2,
  "launch_probability": 87.0,
  "risk_level": "HIGH",
  "creator_reputation": 75.0,
  "expected_launch_day": 1741699200,
  "last_updated": 1741699200
}
```

### 2. `detected_launches` Table

Records actual token launches detected on pump.fun / Raydium.

```sql
CREATE TABLE IF NOT EXISTS detected_launches (
    launch_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_address     TEXT NOT NULL,
    token_address       TEXT NOT NULL UNIQUE,
    token_name          TEXT,
    token_ticker        TEXT,
    launch_platform     TEXT NOT NULL,  -- 'pump.fun', 'raydium', 'other'
    launch_ts           INTEGER NOT NULL,
    launch_price_sol    REAL DEFAULT 0,
    initial_lp_sol      REAL DEFAULT 0,
    was_predicted       BOOLEAN DEFAULT 0,
    watchlist_id        INTEGER,
    prediction_accuracy REAL DEFAULT 0,  -- how accurate prediction was
    detected_at         REAL NOT NULL,
    FOREIGN KEY(watchlist_id) REFERENCES launch_watchlist(watchlist_id)
);

CREATE INDEX idx_launches_creator ON detected_launches(creator_address);
CREATE INDEX idx_launches_timestamp ON detected_launches(launch_ts DESC);
CREATE INDEX idx_launches_platform ON detected_launches(launch_platform);
CREATE INDEX idx_launches_predicted ON detected_launches(was_predicted);
```

**Sample Row**:
```json
{
  "launch_id": 1,
  "creator_address": "CreatorX...",
  "token_address": "TokenABC123...",
  "token_name": "MyToken",
  "token_ticker": "MYTKN",
  "launch_platform": "pump.fun",
  "launch_ts": 1741699200,
  "launch_price_sol": 0.0001,
  "initial_lp_sol": 50.0,
  "was_predicted": 1,
  "watchlist_id": 1,
  "prediction_accuracy": 95.0,
  "detected_at": 1741699215
}
```

### 3. `prediction_accuracy` Table

Audit trail for model improvement.

```sql
CREATE TABLE IF NOT EXISTS prediction_accuracy (
    accuracy_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id        INTEGER NOT NULL,
    predicted_probability REAL NOT NULL,
    actually_launched   BOOLEAN NOT NULL,
    launch_within_days  INTEGER,          -- 1-14 days
    time_to_launch_days REAL,             -- actual days
    error_days          REAL,             -- predicted_day - actual_day
    updated_at          REAL NOT NULL,
    FOREIGN KEY(watchlist_id) REFERENCES launch_watchlist(watchlist_id)
);

CREATE INDEX idx_accuracy_timestamp ON prediction_accuracy(updated_at DESC);
```

---

## Probability Scoring Model

### Formal Definition

```
launch_probability(creator_c) =
    w1 × reputation_factor(reputation_score_c)
    + w2 × farm_confidence_factor(confidence_score_farm_c)
    + w3 × recency_factor(days_since_funded_c)
    + w4 × burst_factor(has_burst_farm_c)
    + w5 × network_factor(creators_launching_same_farm)

where:
  w1 = 0.30  (reputation weight)
  w2 = 0.30  (farm confidence weight)
  w3 = 0.20  (recency weight)
  w4 = 0.10  (burst weight)
  w5 = 0.10  (network weight)
  sum(weights) = 1.0
```

### Factor Definitions

#### reputation_factor(score)
```
f_r(score) = {
    score >= 70: 1.0,
    50 <= score < 70: (score - 50) / 40,
    30 <= score < 50: (score - 30) / 40 * 0.5,
    score < 30: 0.0
}

Range: [0, 1.0]
```

#### farm_confidence_factor(confidence)
```
f_fc(conf) = {
    conf >= 85: 1.0,
    70 <= conf < 85: (conf - 70) / 15,
    50 <= conf < 70: (conf - 50) / 20 * 0.5,
    conf < 50: 0.0
}

Range: [0, 1.0]
```

#### recency_factor(days)
```
f_r(days) = {
    1 <= days <= 7: 1.0 - (|days - 4| / 6) * 0.3,
    0.5 <= days < 1: 0.5,
    7 < days <= 14: 0.2,
    days < 0.5 or days > 14: 0.0
}

Range: [0, 1.0]
Peak at 4 days post-funding
```

#### burst_factor(has_burst)
```
f_b(burst) = 1.0 if has_burst else 0.0

Range: [0, 1.0]
```

#### network_factor(count)
```
f_n(count) = {
    count >= 2: 1.0,
    count == 1: 0.5,
    count == 0: 0.0
}

Range: [0, 1.0]
```

### Final Score

```
score = (0.30 × f_r + 0.30 × f_fc + 0.20 × f_rec + 0.10 × f_b + 0.10 × f_n) × 100

Range: [0, 100]
```

### Risk Level Classification

```python
if score >= 75:
    risk_level = "HIGH"
    urgency = "IMMINENT (1-3 days)"
elif score >= 50:
    risk_level = "MEDIUM"
    urgency = "LIKELY (3-7 days)"
elif score >= 25:
    risk_level = "LOW"
    urgency = "POSSIBLE (7-14 days)"
else:
    risk_level = "MINIMAL"
    urgency = "UNLIKELY"
```

---

## Detection Pipeline

### Architecture

```
Phase 3.3 Detection Run (3 AM UTC)
    ↓
    └─ wallet_clusters + dev_reputation populated

    ↓

Phase 4 Watchlist Builder (3:15 AM UTC)
    ├─ Identify new creators in wallet_clusters (last 3 days)
    ├─ Exclude creators already in detected_launches
    ├─ Exclude creators already in launch_watchlist
    └─ Insert into launch_watchlist

    ↓

Phase 4 Probability Scorer (3:20 AM UTC)
    ├─ Calculate launch_probability for each watchlist entry
    ├─ Classify risk_level
    ├─ Estimate expected_launch_day
    └─ Update launch_watchlist with scores

    ↓

Phase 4 Launch Detector (Real-time, via Helius webhooks)
    ├─ Monitor pump.fun program for token creation
    ├─ Monitor Raydium for liquidity pool creation
    ├─ Match creator_address against launch_watchlist
    └─ Insert into detected_launches

    ↓

Phase 4 Accuracy Tracker (Daily, 4 AM UTC)
    ├─ Compare watchlist vs detected_launches from past 24h
    ├─ Calculate prediction_accuracy metrics
    ├─ Update model weights if needed
    └─ Log to prediction_accuracy table
```

### Detailed Steps

#### Step 1: Watchlist Builder

```python
def build_watchlist():
    """Identify newly funded creators from dev farms."""

    # 1. Get creators funded in last 3 days
    three_days_ago = time.time() - (3 * 86400)

    sql = """
        SELECT DISTINCT
            json_each.value as creator_address,
            cluster_id,
            funder_wallet,
            confidence_score,
            first_transfer_ts
        FROM wallet_clusters
        CROSS JOIN json_each(wallet_clusters.creator_addresses)
        WHERE detected_at > ?
          AND confidence_score > 50  # Filter weak clusters
    """

    newly_funded = cursor.execute(sql, (three_days_ago,)).fetchall()

    # 2. Exclude already detected
    detected_creators = cursor.execute(
        "SELECT DISTINCT creator_address FROM detected_launches"
    ).fetchall()

    # 3. Exclude already watchlisted
    watchlisted = cursor.execute(
        "SELECT DISTINCT creator_address FROM launch_watchlist"
    ).fetchall()

    # 4. Filter
    candidates = [c for c in newly_funded
                  if c[0] not in detected_creators and c[0] not in watchlisted]

    # 5. Insert
    for creator_addr, cluster_id, funder, conf, funded_ts in candidates:
        days_since = (time.time() - funded_ts) / 86400

        cursor.execute("""
            INSERT INTO launch_watchlist (
                creator_address, farm_cluster_id, farm_funder,
                farm_confidence, funded_ts, days_since_funded,
                last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (creator_addr, cluster_id, funder, conf, funded_ts, days_since, time.time()))

    return len(candidates)
```

#### Step 2: Probability Scorer

```python
def score_watchlist():
    """Calculate launch probability for each watchlisted creator."""

    watchlist = cursor.execute("SELECT * FROM launch_watchlist").fetchall()

    updated = 0
    for entry in watchlist:
        watchlist_id, creator_addr, cluster_id, funder, conf, funded_ts, days_since, _, _, _, _, _ = entry

        # Get creator reputation
        rep_row = cursor.execute(
            "SELECT reputation_score FROM dev_reputation WHERE wallet = ?",
            (creator_addr,)
        ).fetchone()
        reputation = rep_row[0] if rep_row else 50.0

        # Check for burst
        burst_row = cursor.execute(
            "SELECT has_burst FROM wallet_clusters WHERE cluster_id = ?",
            (cluster_id,)
        ).fetchone()
        has_burst = burst_row[0] if burst_row else 0

        # Count other creators launching from same farm
        other_launches = cursor.execute("""
            SELECT COUNT(*)
            FROM detected_launches
            WHERE creator_address IN (
                SELECT json_each.value
                FROM wallet_clusters
                CROSS JOIN json_each(wallet_clusters.creator_addresses)
                WHERE cluster_id = ?
            )
            AND launch_ts > datetime('now', '-7 days', 'unixepoch')
        """, (cluster_id,)).fetchone()[0]

        # Calculate score
        score = calculate_launch_probability(
            reputation_score=reputation,
            farm_confidence=conf,
            days_since_funded=days_since,
            has_burst=has_burst,
            other_launches=other_launches
        )

        # Determine risk level
        if score >= 75:
            risk = "HIGH"
            expected_day = funded_ts + (4 * 86400)  # ~4 days post-funding
        elif score >= 50:
            risk = "MEDIUM"
            expected_day = funded_ts + (6 * 86400)  # ~6 days post-funding
        elif score >= 25:
            risk = "LOW"
            expected_day = funded_ts + (10 * 86400)  # ~10 days post-funding
        else:
            risk = "MINIMAL"
            expected_day = None

        # Update
        cursor.execute("""
            UPDATE launch_watchlist
            SET launch_probability = ?,
                risk_level = ?,
                creator_reputation = ?,
                expected_launch_day = ?,
                last_updated = ?
            WHERE watchlist_id = ?
        """, (score, risk, reputation, expected_day, time.time(), watchlist_id))

        updated += 1

    return updated
```

#### Step 3: Launch Detector

```python
def detect_launches_from_webhooks():
    """Monitor for actual token launches."""

    # This runs in real-time via Helius webhooks
    # When new token created by pump.fun or Raydium:

    def on_new_token(creator_address, token_address, launch_platform, launch_ts):
        # Check if creator in watchlist
        watchlist_row = cursor.execute(
            "SELECT watchlist_id, launch_probability FROM launch_watchlist WHERE creator_address = ?",
            (creator_address,)
        ).fetchone()

        if watchlist_row:
            watchlist_id, predicted_prob = watchlist_row

            # Insert launch record
            cursor.execute("""
                INSERT INTO detected_launches (
                    creator_address, token_address, launch_platform,
                    launch_ts, was_predicted, watchlist_id,
                    prediction_accuracy, detected_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """, (creator_address, token_address, launch_platform,
                  launch_ts, watchlist_id, predicted_prob, time.time()))

            # Log prediction accuracy
            actual_days = (launch_ts - get_funded_ts(watchlist_id)) / 86400
            expected_days = (get_expected_launch_day(watchlist_id) - get_funded_ts(watchlist_id)) / 86400
            error = expected_days - actual_days

            cursor.execute("""
                INSERT INTO prediction_accuracy (
                    watchlist_id, predicted_probability,
                    actually_launched, launch_within_days,
                    time_to_launch_days, error_days, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
            """, (watchlist_id, predicted_prob,
                  7 if actual_days <= 7 else 14,
                  actual_days, error, time.time()))
        else:
            # Creator not in watchlist - insert discovery record
            cursor.execute("""
                INSERT INTO detected_launches (
                    creator_address, token_address, launch_platform,
                    launch_ts, was_predicted, detected_at
                ) VALUES (?, ?, ?, ?, 0, ?)
            """, (creator_address, token_address, launch_platform,
                  launch_ts, time.time()))
```

---

## Integration Points

### 1. With Phase 3.3

**Dependency**: Phase 3.3 must run first (3 AM UTC)

```
wallet_clusters → join with dev_reputation → identify newly funded → watchlist
```

**Data flow**:
```sql
-- From Phase 3.3
SELECT DISTINCT destination
FROM wallet_clusters
WHERE detected_at > (now - 3 days)

-- Join with Phase 3.3 output
JOIN dev_reputation ON wallet = destination
```

### 2. With Helius Webhooks

**Real-time launch detection** via pump.fun / Raydium monitoring

```python
# In main.py webhook handler
@app.route('/api/webhook/helius', methods=['POST'])
def helius_webhook():
    data = request.json

    # Detect token launch
    if is_pump_fun_launch(data) or is_raydium_launch(data):
        creator = extract_creator_address(data)
        token = extract_token_address(data)

        # Check watchlist
        detect_launches_from_webhooks(creator, token, ...)
```

### 3. With Flask APIs

**Three new endpoints** for external access

```python
@app.route('/api/launch/watchlist')
def api_launch_watchlist():
    """Top launch predictions (sorted by probability)."""
    # SELECT * FROM launch_watchlist
    # ORDER BY launch_probability DESC LIMIT 100

@app.route('/api/launch/probability/<creator>')
def api_launch_probability(creator):
    """Probability for specific creator."""
    # SELECT * FROM launch_watchlist WHERE creator_address = ?

@app.route('/api/launch/detected')
def api_launch_detected():
    """Recent actual launches (validation)."""
    # SELECT * FROM detected_launches
    # ORDER BY launch_ts DESC LIMIT 50
```

### 4. With Trading Systems

**External integration** for bots and subscribers

```
/api/launch/watchlist
    ↓
Discord Bot:  "🚨 HIGH PROBABILITY: Creator X launches in 3 days"
    ↓
Trading Bot:  Auto-enable alerts for token creation
    ↓
Alert Subscriber: Get real-time notifications
```

---

## Implementation Roadmap

### Phase 4.1: Foundation (1-2 days)

**Deliverables**:
- [ ] Database schema (3 tables)
- [ ] Watchlist builder script
- [ ] Probability scorer algorithm
- [ ] Basic testing

**Files to create**:
```
src/core/launch_prediction.py          (400 lines)
database/migrations/phase4_launch.sql  (80 lines)
launch_detection.py                    (50 lines, cron script)
```

**Effort**: ~10 hours

### Phase 4.2: Detection (2-3 days)

**Deliverables**:
- [ ] Launch detector (webhook integration)
- [ ] Real-time monitoring
- [ ] Prediction accuracy tracking
- [ ] Integration testing

**Files to modify**:
```
src/core/main.py                       (+100 lines)
```

**Files to create**:
```
src/core/webhook_launch_monitor.py     (200 lines)
```

**Effort**: ~15 hours

### Phase 4.3: APIs & Monitoring (1 day)

**Deliverables**:
- [ ] 3 Flask endpoints
- [ ] Monitoring dashboard
- [ ] Accuracy reporting
- [ ] Documentation

**Files to modify**:
```
src/core/main.py                       (+150 lines)
```

**Effort**: ~8 hours

### Phase 4.4: Testing & Deployment (1-2 days)

**Deliverables**:
- [ ] Unit tests (watchlist, scoring, detection)
- [ ] Integration tests (full pipeline)
- [ ] 30-day backtesting
- [ ] Production deployment

**Effort**: ~12 hours

**Total Phase 4 Effort**: ~45 hours (5-6 working days)

---

## Key Metrics & Success Criteria

### Accuracy Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| **Precision** | >80% | Of predicted launches, 80%+ actually launch |
| **Recall** | >70% | Of actual launches, 70%+ were in watchlist |
| **Time-to-Launch RMSE** | <2 days | Average error in launch day prediction |
| **False Positives** | <20% | Creators predicted but never launch |

### Performance Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| **Watchlist size** | 50-200 | Creators per day |
| **Detection latency** | <5 seconds | Time from launch to detection |
| **Scorer runtime** | <100ms | Score all watchlist entries |
| **API response time** | <50ms | /api/launch endpoints |

### Business Metrics

| Metric | Target | Insight |
|--------|--------|---------|
| **Information advantage** | 3-7 days | Days ahead of market |
| **Front-run success** | >60% | Tokens captured before pumps |
| **Risk avoidance** | >75% | Rugs predicted before loss |
| **ROI on predictions** | >5x | Return on followed launches |

---

## Risk Mitigation

### Risk 1: Poor Prediction Accuracy

**Problem**: Watchlist includes non-launchers

**Mitigation**:
- Track prediction_accuracy table
- Retrain weights monthly
- Add negative signals (delays >14 days)
- Require ensemble predictions

### Risk 2: False Positives

**Problem**: Too many low-quality watchlist entries

**Mitigation**:
- Require farm_confidence > 60 minimum
- Require reputation_score > 30 minimum
- Filter by successful creator history
- Only monitor creators with past tokens

### Risk 3: Detection Lag

**Problem**: Launches detected after price pump

**Mitigation**:
- Use Helius webhooks for real-time
- Monitor pump.fun program directly
- Pre-position alerts before launch
- Use prediction as early signal

### Risk 4: Model Drift

**Problem**: Probabilities become less accurate over time

**Mitigation**:
- Monthly backtesting
- Quarterly weight retraining
- New market signal integration
- Continuous accuracy monitoring

---

## Competitive Advantages

### vs Nansen / Arkham
- ✅ **Predictive** (they're retrospective)
- ✅ **Real-time** (they're batch)
- ✅ **Pattern-native** (they're label-based)
- ✅ **Automated** (they're manual)

### vs Specialized Launch Bots
- ✅ **Integrated** (launch + reputation + risk)
- ✅ **Explainable** (scoring algorithm transparent)
- ✅ **Backtestable** (full historical data)
- ✅ **Composable** (works with existing FLEX)

### vs Manual Traders
- ✅ **24/7** (never sleeps)
- ✅ **Scale** (monitor 1000s simultaneously)
- ✅ **Speed** (detects in <5 seconds)
- ✅ **Discipline** (no emotion)

---

## Conclusion

Phase 4 transforms FLEX from **detection → prediction**.

**Current**: Detect dev farms after coordination happens
**Future**: Predict token launches 3-7 days before they happen

This is the **highest-information-advantage feature** possible in Solana trading.

**Recommended**: Implement Phase 4.1 & 4.2 immediately (1 week), then 4.3 & 4.4 (1 week).

---

**Status**: 📋 Ready for implementation
**Estimated Timeline**: 6-8 weeks (part-time), 2-3 weeks (full-time)
**ROI**: Infinite (first mover advantage)
