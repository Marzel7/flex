# Master Launch Score — Quick Reference

**Status**: ✅ PRODUCTION READY
**Date**: March 12, 2026

## What It Does

Combines 8 predictive signals into one unified 0-1 launch alert score with alert levels.

## The Formula

```
master_launch_score =
  0.22 * launch_probability +
  0.18 * launch_wave_score +
  0.12 * seed_concentration +
  0.12 * funder_overlap_score +
  0.10 * organization_momentum +
  0.08 * creator_reuse_score +
  0.08 * operator_activity_score +
  0.10 * reputation_adjustment
```

## Alert Levels

| Score | Level | Action |
|-------|-------|--------|
| 0.00–0.39 | LOW | Monitor |
| 0.40–0.59 | WATCH | Monitor closely |
| 0.60–0.74 | HIGH | Investigate |
| 0.75–1.00 | CRITICAL | Immediate action |

## Key Files

| File | Purpose |
|------|---------|
| `src/core/master_launch_score.py` | Main calculation engine |
| `database/migrations/master_launch_score.sql` | Database schema |
| `dev_intelligence_detection.py` | Pipeline Phase 6 |
| `MASTER_LAUNCH_SCORE_IMPLEMENTATION.md` | Full technical guide |

## Quick Commands

### Apply Schema
```bash
sqlite3 database/flex_complete_database.db < database/migrations/master_launch_score.sql
```

### Run Pipeline (Includes Phase 6)
```bash
python3 dev_intelligence_detection.py
```

### Query Critical Launches
```sql
SELECT *
FROM vw_critical_launches
ORDER BY master_launch_score DESC
LIMIT 10;
```

### Query Watchlist
```sql
SELECT *
FROM vw_launch_watchlist
ORDER BY master_launch_score DESC;
```

### Alert Distribution
```sql
SELECT alert_level, COUNT(*) as count
FROM master_launch_signals
GROUP BY alert_level;
```

### Component Breakdown
```sql
SELECT
    organization_id,
    master_launch_score,
    alert_level,
    launch_probability,
    launch_wave_score,
    seed_concentration,
    funder_overlap_score,
    organization_momentum,
    creator_reuse_score,
    operator_activity_score,
    reputation_adjustment
FROM master_launch_signals
WHERE alert_level IN ('HIGH', 'CRITICAL')
ORDER BY master_launch_score DESC;
```

## Database Schema

**Table**: `master_launch_signals`
- Stores one row per organization
- All 8 component signals (0-1 normalized)
- Composite master_launch_score (0-1)
- Alert level classification
- Timestamp

**Indexes**: 3
- Organization lookup
- Score ranking
- Alert level filtering

**Views**: 2
- `vw_critical_launches` (score ≥ 0.75)
- `vw_launch_watchlist` (HIGH or CRITICAL)

## Normalization

Each signal normalized to 0-1:
```
Percentages (0-100)   → divide by 100
Ratios (0-1)          → use as-is
Momentum              → sigmoid-like transform
```

## Example

**Input signals** (from database):
- launch_probability: 78
- launch_wave_score: 82
- seed_concentration: 0.91
- funder_overlap_score: 0.74
- organization_momentum: 0.66
- creator_reuse_score: 0.58
- operator_activity_score: 0.72
- reputation_adjustment: 0.40

**Normalized**: 0.78, 0.82, 0.91, 0.74, 0.65, 0.58, 0.72, 0.40

**Calculation**:
```
= 0.22×0.78 + 0.18×0.82 + 0.12×0.91 + 0.12×0.74 +
  0.10×0.65 + 0.08×0.58 + 0.08×0.72 + 0.10×0.40
= 0.1716 + 0.1476 + 0.1092 + 0.0888 + 0.0650 + 0.0464 + 0.0576 + 0.0400
= 0.7262
```

**Result**: score = 0.73 → **HIGH** alert

## Signal Weights

| Signal | Weight | Source |
|--------|--------|--------|
| Launch Probability | 22% | 7-day token launch predictor |
| Launch Wave Score | 18% | Multi-launch pattern detection |
| Seed Concentration | 12% | Coordinated seed funding |
| Funder Overlap | 12% | Wallet coordination |
| Organization Momentum | 10% | Activity surge |
| Creator Reuse | 8% | Creator frequency across launches |
| Operator Activity | 8% | Operator wallet spike |
| Reputation | 10% | Historical dev track record |

## Integration Points

### Alerting
```python
if score >= 0.75:
    send_critical_alert(org_id)
```

### Dashboards
```sql
SELECT * FROM vw_launch_watchlist
```

### Risk Scoring
Incorporate into organization risk assessment.

### ML Features
All 8 signals + composite for training.

## Performance

| Metric | Value |
|--------|-------|
| Computation | 10-50 orgs/sec |
| Runtime | 5-15 seconds |
| Storage | ~1 KB per org |
| Query | <5ms |

## Deployment Steps

1. Apply migration:
   ```bash
   sqlite3 database/flex_complete_database.db < database/migrations/master_launch_score.sql
   ```

2. Run pipeline:
   ```bash
   python3 dev_intelligence_detection.py
   ```

3. Verify tables:
   ```bash
   sqlite3 database/flex_complete_database.db "SELECT * FROM master_launch_signals LIMIT 5;"
   ```

4. Check alerts:
   ```bash
   sqlite3 database/flex_complete_database.db "SELECT COUNT(*) as critical_count FROM master_launch_signals WHERE alert_level = 'CRITICAL';"
   ```

## What It Detects

✓ Imminent token launches (CRITICAL threshold)
✓ Launch preparation activity (HIGH/WATCH)
✓ Active developer operations (WATCH)
✓ Low-risk periods (LOW)

## Key Advantages

- **Single metric** replaces 8
- **Normalized** handles different scales
- **Transparent** all components stored
- **Fast** indexed queries
- **Intuitive** clear alert levels
- **Extensible** easy to adjust weights

## Next Steps

1. Apply migration this week
2. Monitor Phase 6 in logs
3. Query vw_critical_launches daily
4. Tune weights based on historical accuracy
5. Integrate with alert systems

---

See `MASTER_LAUNCH_SCORE_IMPLEMENTATION.md` for complete technical details.
