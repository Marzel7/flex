# Phase 1: Early Signal Engine - Quick Start

## 1-Minute Setup

### Initialize Database
```python
from src.core.token_lifecycle import TokenLifecycleManager
from src.core.lifecycle_schema_v2 import migrate_to_schema_v2

db_path = 'database/flex_complete_database.db'
manager = TokenLifecycleManager(db_path)      # Creates base schema
migrate_to_schema_v2(db_path)                 # Adds V2 fields
```

### Start Monitoring Loop
```python
from src.core.token_lifecycle import LifecycleMonitoringWorker

worker = LifecycleMonitoringWorker(db_path)

while True:
    results = worker.run_cycle()
    print(f"Checked: {results['mints_checked']}")
    print(f"Early scored: {results['mints_early_scored']}")  # NEW
    print(f"Classified: {results['mints_classified']}")

    time.sleep(30)  # Run every 30 seconds
```

The monitoring loop automatically computes early signals for tokens aged 5-15 minutes.

## Key Features

### Early Predictions (at 5-15 minutes)
Tokens are scored against 18 signals (9 rug + 9 success) to predict likely outcome:

```
Score    | Label         | Action
---------|---------------|------------------
0.65+    | likely_rug    | STOP_MONITORING
0.60+    | likely_runner | PRIORITIZE
mixed    | unknown       | CONTINUE_MONITORING
```

### Query Early Predictions
```python
from src.core.lifecycle_early_signals import EarlySignalEngine, EarlyLabel

engine = EarlySignalEngine(db_path)

# Get risky tokens
rugs = engine.get_early_signals_by_label(EarlyLabel.RUG, limit=20)
for rug in rugs:
    print(f"⚠️  {rug['mint'][:16]}... score={rug['early_score']:.2f}")

# Get promising tokens
runners = engine.get_early_signals_by_label(EarlyLabel.RUNNER, limit=20)
for runner in runners:
    print(f"🚀 {runner['mint'][:16]}... score={runner['early_score']:.2f}")
```

### Signal Details
When computing early signal, the engine checks for:

**Rug Signals (9):**
1. No velocity (< 0.5% per min) → indicates dead pool
2. Negative velocity (< -0.5% per min) → price declining fast
3. Early crash (> 50% loss in < 5 min) → flash crash
4. No recovery from dip → can't regain momentum
5. Poor liquidity (< 5% of MC) → pump on low support
6. Liquidity declining → builders pulling support
7. Never reached $10k → failed launch
8. Rapid velocity decay (> 80% per min) → losing momentum
9. Extended dead pool (> 60 sec no trades) → volume dried up

**Success Signals (9):**
1. Strong velocity (> 10% per min) → rapid growth
2. Reached $50k+ quickly (< 5 min) → strong launch
3. Stable price (< 25% volatility) → organic growth
4. Volume increasing → growing interest
5. Good liquidity (> 10% of MC) → strong support
6. Liquidity growing → builders adding support
7. Positive momentum (decay < 30%) → sustaining gains
8. Buy pressure (> 65% buys) → organic demand
9. Holder growth (> 50% in 5 min) → new investors joining

### Early Signal Score Calculation
```python
# Normalized 0-1 score
early_rug_score = min(sum of rug signal weights, 1.0)
early_success_score = min(sum of success signal weights, 1.0)

# Confidence calculation
base_confidence = min(max(rug_score, success_score) * 0.8, 1.0)
if (success_score - rug_score).abs() > 0.2:  # Clear winner
    confidence = min(base_confidence + 0.15, 1.0)
else:  # Mixed signals
    confidence = base_confidence

# Classification
if early_rug_score >= 0.65 AND confidence >= 0.60:
    label = "likely_rug"
elif early_success_score >= 0.60 AND confidence >= 0.60:
    label = "likely_runner"
else:
    label = "unknown"
```

## Example: Processing a Token

```python
from src.core.token_lifecycle import TokenLifecycleManager, TokenSnapshot
from src.core.lifecycle_early_signals import EarlySignalEngine
import time
from datetime import datetime

db = 'database/flex_complete_database.db'
manager = TokenLifecycleManager(db)
engine = EarlySignalEngine(db)

# 1. Start monitoring a token
mint = "ABC123def456abc123def456abc1"
manager.start_monitoring(
    mint=mint,
    cluster_id="cluster_123",
    creator="creator_wallet"
)

# 2. Record price snapshots over 15 minutes
now = int(datetime.now().timestamp())
base_time = now - 900  # 15 minutes ago

for i in range(15):  # Record one snapshot per minute
    time.sleep(60)
    snapshot = TokenSnapshot(
        mint=mint,
        timestamp=now + (i * 60),
        price_usd=get_current_price(mint),  # Your price feed
        market_cap_usd=get_market_cap(mint),
        liquidity_usd=get_liquidity(mint),
        volume_24h=get_volume(mint),
        price_source="your_price_feed",
        cluster_id="cluster_123",
        creator="creator_wallet"
    )
    manager.record_snapshot(snapshot)

# 3. At 5-15 minutes, compute early signal
age = manager.get_token_age_minutes(mint)  # Should be ~10
if 5 <= age <= 15:
    early_signal = engine.compute_early_score(mint, current_age_minutes=age)
    if early_signal:
        engine.record_early_signal(early_signal)

        print(f"Early prediction for {mint[:16]}...")
        print(f"  Outcome: {early_signal.early_label.value}")
        print(f"  Confidence: {early_signal.confidence:.2f}")
        print(f"  Action: {early_signal.recommendation.value}")

        if early_signal.early_label.value == "likely_rug":
            print(f"  ⚠️  Alert signals: {', '.join(early_signal.rug_signals[:3])}")
        elif early_signal.early_label.value == "likely_runner":
            print(f"  🚀 Positive signals: {', '.join(early_signal.success_signals[:3])}")

# 4. Continue monitoring until outcome is clear (2-4 hours)
# The monitoring loop will automatically classify when conditions are met
```

## Database Queries

### Find Likely Rugs
```sql
SELECT
    mint,
    early_score,
    early_rug_score,
    early_warning_flags,
    last_market_cap,
    cluster_id
FROM token_monitoring_state
WHERE early_label = 'likely_rug'
ORDER BY early_score DESC
LIMIT 20;
```

### Find Likely Runners
```sql
SELECT
    mint,
    early_score,
    early_success_score,
    last_market_cap,
    peak_market_cap,
    cluster_id
FROM token_monitoring_state
WHERE early_label = 'likely_runner'
ORDER BY early_score DESC
LIMIT 20;
```

### Track Early Prediction Accuracy
```sql
-- Compare early prediction vs actual outcome
SELECT
    m.mint,
    m.early_label,
    m.early_score,
    o.outcome,
    CASE
        WHEN m.early_label = 'likely_rug' AND o.outcome = 'rug' THEN 'CORRECT'
        WHEN m.early_label = 'likely_rug' AND o.outcome != 'rug' THEN 'FALSE_POSITIVE'
        WHEN m.early_label = 'likely_runner' AND o.outcome = 'success' THEN 'CORRECT'
        WHEN m.early_label = 'likely_runner' AND o.outcome != 'success' THEN 'FALSE_POSITIVE'
        ELSE 'UNKNOWN'
    END as prediction_accuracy
FROM token_monitoring_state m
LEFT JOIN token_outcomes o ON m.mint = o.mint
WHERE m.early_label IS NOT NULL
AND o.outcome IS NOT NULL;
```

## Configuration

Early signal computation happens automatically for tokens aged 5-15 minutes. To adjust:

**In `src/core/lifecycle_early_signals.py`, `compute_early_score()` method:**

```python
# Current thresholds
if current_age_minutes < 5:
    return None  # Wait for more data

if current_age_minutes > 15:
    return None  # Switch to full classification

# To change:
# if current_age_minutes < 7:  # Start at 7 minutes
# if current_age_minutes > 20:  # End at 20 minutes
```

**Signal weight thresholds:**
```python
# RUG classification threshold
if early_rug_score >= 0.65 and confidence >= 0.60:
    early_label = EarlyLabel.RUG

# RUNNER classification threshold
elif early_success_score >= 0.60 and confidence >= 0.60:
    early_label = EarlyLabel.RUNNER

# To adjust sensitivity:
# Increase thresholds = fewer predictions (higher precision)
# Decrease thresholds = more predictions (higher recall)
```

## Next Steps

**Validate Phase 1:**
1. Run monitoring loop on 50+ tokens
2. Track early predictions vs actual outcomes
3. Calculate accuracy metrics:
   - True positive rate (correct rug predictions)
   - False positive rate (false rug alarms)
   - True negative rate (correct unknowns)
   - False negative rate (missed rugs)

**If accuracy >= 70%:** Proceed to Phase 2
- Dynamic monitoring cadence (adjust snapshot frequency by token age)
- Adaptive signal thresholds (auto-tune based on cluster performance)
- Storage optimization (compress old snapshots)

**If accuracy < 70%:** Tune Phase 1
- Adjust signal weights (increase/decrease impact of specific signals)
- Change classification thresholds
- Add/remove signals based on false positive analysis

## Testing

Run the test suite:
```bash
python examples/test_early_signals.py
```

Expected output:
- ✅ Test 1: Detects fast rug (95% collapse in < 5 min)
- ✅ Test 2: Detects runner candidates (stable high-cap tokens)
- ✅ Test 3-5: Classification V2 working correctly
- ✅ Test 6-7: Database queries returning results
