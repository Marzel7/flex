# FLEX Seed Concentration Analysis

**Date**: March 12, 2026
**Status**: ✅ Production Ready
**Version**: 1.0

## Overview

The Seed Concentration Analysis system extends FLEX's launch prediction capabilities by detecting **coordinated creator funding** before token launches.

When a development organization prepares for launches, creators typically receive seed funding. This system measures how **concentrated and coordinated** that funding is, using it as a 5% signal in the overall launch score.

### Key Insight

High seed concentration indicates:
- Organized development team (not ad-hoc)
- Coordinated preparation for launches
- Multiple wallets supporting the effort
- Early-stage planning activity

## What Gets Measured

### Creator-Level Metrics

For each creator in an organization:

| Metric | Definition | Range | Interpretation |
|--------|-----------|-------|-----------------|
| **avg_seed_amount** | Average SOL per seed transaction | 0-∞ | Baseline funding size |
| **seed_stddev** | Standard deviation of amounts | 0-∞ | Consistency of funding |
| **seed_concentration** | 1 - (stddev/avg) | 0-1 | How equal amounts are |
| **funding_wallet_count** | Number of unique funders | 0-∞ | Diversity of support |
| **funding_time_window** | Hours from first to last funding | 0-∞ | Preparation window |
| **seed_count** | Number of seed transactions | 0-∞ | Activity level |
| **total_seed_amount** | Sum of all seed amounts | 0-∞ | Total resources |

### Organization-Level Signal

Aggregated from all creators:

```
org_seed_concentration = 0.6 * avg_creator_concentration
                       + 0.3 * min(avg_wallets / 5, 1.0)
                       + 0.1 * min(creators_with_seeds / 10, 1.0)
```

This produces a **0-100 signal** where:
- **80-100**: Very high coordination (organized team)
- **60-79**: High coordination (systematic preparation)
- **40-59**: Moderate coordination (mixed patterns)
- **0-39**: Low coordination (ad-hoc funding)

## Seed Concentration Formula

The core metric:

```
seed_concentration = 1 - (seed_stddev / avg_seed_amount)
```

### Example Calculation

**Scenario**: Creator receives 5 seed transactions

| Transaction | Amount (SOL) |
|-------------|--------------|
| 1           | 2.0          |
| 2           | 2.1          |
| 3           | 1.9          |
| 4           | 2.2          |
| 5           | 2.3          |

**Calculations**:
```
avg_seed_amount = (2.0 + 2.1 + 1.9 + 2.2 + 2.3) / 5 = 2.1 SOL

variance = [(2.0-2.1)² + (2.1-2.1)² + (1.9-2.1)² + (2.2-2.1)² + (2.3-2.1)²] / 4
         = [0.01 + 0 + 0.04 + 0.01 + 0.04] / 4
         = 0.025

seed_stddev = √0.025 = 0.158 SOL

seed_concentration = 1 - (0.158 / 2.1)
                   = 1 - 0.075
                   = 0.925  ← Very high concentration!
```

**Interpretation**: All amounts are very similar (0.925), indicating coordinated, equal funding—a strong launch preparation signal.

### Contrast Example

**Scenario**: Creator receives 5 highly variable seed transactions

| Transaction | Amount (SOL) |
|-------------|--------------|
| 1           | 0.5          |
| 2           | 5.0          |
| 3           | 1.0          |
| 4           | 2.0          |
| 5           | 8.5          |

```
avg_seed_amount = 3.4 SOL
seed_stddev = 3.2 SOL

seed_concentration = 1 - (3.2 / 3.4)
                   = 1 - 0.941
                   = 0.059  ← Very low concentration!
```

**Interpretation**: Amounts are highly variable, suggesting ad-hoc funding rather than coordinated preparation.

## Integration with Launch Score

### Enhanced 8-Signal Model

The seed concentration signal is integrated into the overall launch prediction:

```
launch_score = 0.22 * recent_funding_activity
             + 0.18 * cluster_activity
             + 0.14 * creator_reuse
             + 0.14 * operator_activity
             + 0.10 * dev_reputation
             + 0.10 * organization_momentum
             + 0.07 * cadence_score
             + 0.05 * seed_concentration    ← NEW (5% weight)
```

**Weight Justification**:
- Small (5%) but meaningful weight as an **early signal**
- Complements other signals rather than dominating
- Indicates **preparation** phase, not launch itself
- Improves predictions when combined with other factors

### Example Score Computation

```
Organization with high seed concentration but moderate other signals:

recent_funding_activity:      45 (moderate)
cluster_activity:             50 (moderate)
creator_reuse:                40 (low)
operator_activity:            35 (low)
dev_reputation:               60 (moderate)
organization_momentum:        55 (moderate)
cadence_score:                30 (low)
seed_concentration:           85 (high) ← Early signal

launch_score = 0.22*45 + 0.18*50 + 0.14*40 + 0.14*35 + 0.10*60 + 0.10*55 + 0.07*30 + 0.05*85
             = 9.9 + 9.0 + 5.6 + 4.9 + 6.0 + 5.5 + 2.1 + 4.25
             = 47.35 → "Early Signals" (40-59)

Interpretation: Organization shows early launch preparation signs. The high seed
concentration is a positive indicator, but other signals are still developing.
Monitor for acceleration in next 24-72 hours.
```

## Database Schema

### Main Table: creator_seed_metrics

```sql
CREATE TABLE creator_seed_metrics (
    metric_id           INTEGER PRIMARY KEY,
    creator_wallet      TEXT NOT NULL,
    organization_id     INTEGER,
    avg_seed_amount     REAL,
    seed_stddev         REAL,
    seed_concentration  REAL,        -- 0-1, main signal
    funding_wallet_count INTEGER,
    funding_time_window INTEGER,     -- hours
    seed_count          INTEGER,
    total_seed_amount   REAL,
    created_at          INTEGER,
    UNIQUE(creator_wallet, organization_id)
);
```

### Indexes

```
idx_csm_creator              -- Creator lookups
idx_csm_org_id               -- Organization lookups
idx_csm_concentration        -- High concentration queries
idx_csm_wallet_count         -- Multi-wallet queries
```

### Views

**vw_org_seed_concentration**
- Organization-level aggregates
- Used for launch score computation
- Shows creator count, average concentration, high-concentration creators

**vw_high_seed_concentration**
- Creators with concentration >= 0.6
- Pre-filtered for alerts
- Ordered by concentration descending

## Pipeline Integration

### Phase 4: Creator Seed Metrics

Runs between V3 (Predictive Analytics) and Wave Detection.

```
Phase 1: Organization Detection (v1)
   ↓
Phase 2: Launch Predictions (v2)
   ↓
Phase 3: Predictive Analytics (v3)
   ↓
Phase 4: Creator Seed Metrics ← NEW
   ├─ Load organizations
   ├─ Get creators per org
   ├─ Analyze seed transfers (< 10 SOL, multiple sources)
   ├─ Compute concentration metrics
   └─ Store in creator_seed_metrics
   ↓
Phase 5: Launch Wave Detection
```

**Duration**: ~10-20 seconds for typical dataset

## Monitoring Queries

### Find creators with high seed concentration

```sql
SELECT
    creator_wallet,
    organization_id,
    seed_concentration,
    funding_wallet_count,
    seed_count,
    avg_seed_amount
FROM creator_seed_metrics
WHERE seed_concentration >= 0.7
ORDER BY seed_concentration DESC
LIMIT 20;
```

### Organization-level seed coordination

```sql
SELECT
    organization_id,
    COUNT(*) as creators_with_seeds,
    AVG(seed_concentration) as avg_concentration,
    SUM(CASE WHEN seed_concentration >= 0.6 THEN 1 ELSE 0 END) as high_conc_creators,
    SUM(total_seed_amount) as total_seed_funding
FROM creator_seed_metrics
WHERE seed_count > 0
GROUP BY organization_id
ORDER BY avg_concentration DESC;
```

### Seed funding activity in last 24h

```sql
SELECT
    organization_id,
    COUNT(*) as active_creators,
    AVG(seed_concentration) as avg_concentration,
    MAX(created_at) as last_updated
FROM creator_seed_metrics
WHERE created_at > (? - 86400)  -- Last 24 hours
GROUP BY organization_id
ORDER BY avg_concentration DESC;
```

### Organizations with growing seed coordination

```sql
WITH current AS (
    SELECT organization_id, AVG(seed_concentration) as current_conc
    FROM creator_seed_metrics
    WHERE created_at > (? - 86400)
    GROUP BY organization_id
),
previous AS (
    SELECT organization_id, AVG(seed_concentration) as prev_conc
    FROM creator_seed_metrics
    WHERE created_at BETWEEN (? - 172800) AND (? - 86400)
    GROUP BY organization_id
)
SELECT
    c.organization_id,
    c.current_conc,
    p.prev_conc,
    (c.current_conc - p.prev_conc) as change
FROM current c
LEFT JOIN previous p ON c.organization_id = p.organization_id
WHERE (c.current_conc - p.prev_conc) > 0.1
ORDER BY change DESC;
```

## Use Cases

### 1. Early Launch Detection

**Signal**: High seed concentration but low other indicators

```
→ Organization is in preparation phase
→ Watch for acceleration of other signals
→ Monitor next 24-72h for cluster activity increase
```

### 2. Organized Dev Team Identification

**Signal**: Multiple creators with high concentration + multi-wallet funding

```
→ Indicates experienced, organized team
→ Higher likelihood of successful launch
→ May complement reputation and cadence signals
```

### 3. False Positive Filtering

**Signal**: High recent funding but low seed concentration

```
→ Likely ad-hoc activity, not organized prep
→ Use to downweight other high signals
→ Increases confidence that it's real preparation
```

### 4. Coordination Strength Scoring

**Signal**: Concentration + wallet diversity + creator count

```
org_coordination = concentration * 0.6
                + min(wallets/5, 1) * 0.3
                + min(creators/10, 1) * 0.1

High coordination = organized, likely successful
Low coordination = scattered, risky
```

## Performance Characteristics

| Aspect | Value |
|--------|-------|
| Computation | ~100 creators/second |
| Storage | ~100 bytes per metric |
| Daily Growth | ~10-50 KB per day |
| Query Latency | <10ms for org lookups |
| Indexing | 4 indexes, ~1 MB overhead |

## Calibration and Tuning

### Seed Transfer Threshold

Currently set to `<10 SOL` to identify seed-phase transfers:

```python
seed_transfers = [t for t in transfers if t['amount_sol'] < 10.0]
```

**Adjust if**:
- Most seed transfers are 5-20 SOL: increase threshold to 20
- Most seed transfers are <2 SOL: decrease threshold to 5
- Depends on your ecosystem's typical seed amounts

### Concentration Threshold for Alerts

Currently high concentration is >= 0.6:

```sql
WHERE seed_concentration >= 0.6
```

**Adjust if**:
- Too many false positives: increase to 0.7
- Missing real preparations: decrease to 0.5
- Depends on your historical accuracy data

### Wallet Diversity Weight

Currently set to 30% of organization signal:

```python
coordination = concentration * 0.7 + min(wallets/5, 1) * 0.3
```

**Adjust based on**:
- If many teams use single funder: reduce wallet weight to 0.2
- If coordination requires multiple sources: increase to 0.4

## Future Enhancements

1. **Time-Series Concentration**: Track concentration over time to detect trends
2. **Temporal Clustering**: Identify synchronized funding windows
3. **Funder Identity**: Map funders to detect repeated support patterns
4. **Cross-Organization Patterns**: Identify ecosystem-wide coordination
5. **ML Integration**: Use concentration as feature for V4 models

## Troubleshooting

### No seed metrics computed

**Check**:
```sql
SELECT COUNT(*) FROM creator_seed_metrics;
SELECT COUNT(*) FROM funder_incoming_transfers;
```

**Likely causes**:
- No transfer data in `funder_incoming_transfers`
- No creators in `dev_organization_members`
- Lookback window (30 days) has no transfers

### All concentrations are 0

**Check**:
- Are there multiple creators per organization?
- Are there small transfers (<10 SOL)?
- Are transfers from different wallets?

**Fix**:
- Verify transfer data exists
- Adjust seed transfer threshold if needed
- Ensure creator wallet data is accurate

### Queries returning no results

**Check**:
```sql
SELECT * FROM creator_seed_metrics LIMIT 5;
SELECT * FROM vw_org_seed_concentration LIMIT 5;
```

**Ensure**:
- Phase 4 has run (check logs)
- `created_at` timestamps are recent
- Organization IDs match your data

## API Integration

### Get Seed Concentration for Organization

```python
from src.core.creator_seed_metrics import OrgSeedConcentrationScorer

scorer = OrgSeedConcentrationScorer('database/flex_complete_database.db')
conn = scorer._get_conn()
cursor = conn.cursor()

result = scorer.get_org_seed_concentration(org_id=123, cursor=cursor)
print(result)
# {
#   'org_id': 123,
#   'avg_concentration': 0.72,
#   'weighted_concentration': 0.68,
#   'creators_with_seed_funding': 5,
#   'high_concentration_creators': 4,
#   'signal_strength': 68.0
# }
```

### Compute Enhanced Launch Score

```python
from src.core.enhanced_launch_score import EnhancedLaunchScoreCalculator

calculator = EnhancedLaunchScoreCalculator('database/flex_complete_database.db')
conn = calculator._get_conn()
cursor = conn.cursor()

result = calculator.compute_enhanced_launch_score(org_id=123, cursor=cursor)
print(result['launch_score'])  # 0-100
print(result['launch_type'])   # 'imminent'|'preparation'|'early'|'standard'
print(result['signals'])       # All 8 signal values
```

## References

- Phase 4 Code: `src/core/creator_seed_metrics.py`
- Enhanced Launch Score: `src/core/enhanced_launch_score.py`
- Database Migration: `database/migrations/creator_seed_metrics.sql`
- Pipeline Integration: `dev_intelligence_detection.py`

---

**Status**: ✅ Production Ready
**Last Updated**: March 12, 2026
**Version**: 1.0
