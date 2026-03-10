# FLEX V3 Quick Start Guide

## Running the Full Intelligence Pipeline

The complete pipeline (v1 → v2 → v3) runs daily:

```bash
python3 dev_intelligence_detection.py
```

**Output** (if organizations exist):
```
2026-03-10 22:55:22 - Starting dev intelligence graph detection (v1)
2026-03-10 22:55:23 - Dev intelligence v1 detection completed: X organizations detected
2026-03-10 22:55:23 - Starting dev intelligence v2 (launch predictions + reputation)
2026-03-10 22:55:24 - Dev intelligence v2 completed: X organizations processed
2026-03-10 22:55:24 - Starting dev intelligence v3 (predictive analytics)
2026-03-10 22:55:25 - Dev intelligence v3 completed: X organizations processed, Y tokens predicted, Z alerts fired
```

## Testing the V3 Engine Directly

```bash
python3 -c "
from src.core.dev_intelligence_v3 import DevIntelligenceV3Engine
engine = DevIntelligenceV3Engine('database/flex_complete_database.db')
result = engine.detect_and_store()
print(f'Status: {result[\"status\"]}')
print(f'Orgs processed: {result.get(\"orgs_processed\", 0)}')
print(f'Duration: {result.get(\"duration_ms\", 0):.0f}ms')
"
```

## REST API Endpoints

### 1. Multi-Window Launch Predictions

**Get all high-probability 24h launches**:
```bash
curl "http://localhost:5002/api/orgs/windows?min_prob_24h=70&limit=20"
```

**Response**:
```json
[
  {
    "organization_id": 1,
    "operator_wallet": "...",
    "prob_launch_24h": 85.5,
    "prob_launch_72h": 72.3,
    "prob_launch_7d": 65.1,
    "signal_burst_24h": 4,
    "signal_recency_24h": 0.8,
    "prediction_date": "2026-03-10"
  }
]
```

**Get single org's 3-window prediction**:
```bash
curl "http://localhost:5002/api/orgs/123/windows"
```

### 2. Organization Snapshots (Time-Series)

**Monitor org activity over past 7 days**:
```bash
curl "http://localhost:5002/api/orgs/123/snapshots?days=7"
```

**Response**:
```json
[
  {
    "snapshot_id": 456,
    "organization_id": 123,
    "snapshot_date": "2026-03-10",
    "active_funders": 3,
    "active_creators": 2,
    "burst_count": 5,
    "weighted_volume": 150.5,
    "graph_density": 0.85,
    "launch_count": 2,
    "rug_count": 0
  }
]
```

### 3. Risk Scoring

**Get organization's risk breakdown**:
```bash
curl "http://localhost:5002/api/orgs/123/risk"
```

**Response**:
```json
{
  "organization_id": 123,
  "risk_score": 42.3,
  "rug_probability": 0.35,
  "instability_score": 15.2,
  "confidence": 0.72,
  "component_rug_prob": 14.0,
  "component_instability": 3.8,
  "component_token_velocity": 8.5,
  "component_blocked_ratio": 16.0,
  "blocked_creator_count": 2,
  "total_creator_count": 5,
  "token_velocity": 0.5
}
```

### 4. Organization Families

**Detect connected org networks**:
```bash
curl "http://localhost:5002/api/orgs/families?min_family_score=30&limit=10"
```

**Response**:
```json
[
  {
    "family_id": 1,
    "organization_ids": [123, 124, 125],
    "avg_family_score": 45.5,
    "max_family_score": 55.0,
    "hub_org_id": 124
  }
]
```

### 5. Real-Time Alerts

**Monitor unacknowledged alerts for org**:
```bash
curl "http://localhost:5002/api/orgs/123/alerts?unacked_only=true&limit=50"
```

**Response**:
```json
[
  {
    "alert_id": 789,
    "organization_id": 123,
    "alert_type": "funding_burst",
    "severity": "high",
    "message": "Funding burst detected: 3 active funders",
    "signal_value": 3,
    "signal_threshold": 3,
    "created_at": 1678476925.123,
    "acknowledged_at": null
  }
]
```

**Get all alerts (last 7 days)**:
```bash
curl "http://localhost:5002/api/orgs/123/alerts?limit=100"
```

### 6. Token Outcome Prediction

**Check token before trading**:
```bash
curl "http://localhost:5002/api/tokens/EPjFWaDojoePp2N9vGNnyb33E1oJCzTwWafPVrpR5HNY/outcome"
```

**Response**:
```json
{
  "mint": "EPjFWaDojoePp2N9vGNnyb33E1oJCzTwWafPVrpR5HNY",
  "prob_rug": 0.25,
  "prob_2x": 0.65,
  "prob_10x": 0.12,
  "expected_quality_score": 72.5,
  "signal_rug_prob": 0.10,
  "signal_creator_risk": 0.08,
  "signal_network_risk": 0.0,
  "signal_blocked": 0.0,
  "creator_wallet": "...",
  "organization_id": 123
}
```

## Understanding the Scores

### Launch Probability Windows

| Window | Scope | Key Signals | Use Case |
|--------|-------|-------------|----------|
| **24h** | Burst + Recency | Activity spike + recent transfers | Short-term launch alert |
| **72h** | Velocity + Coordination | Transfer volume + weighted edges | Medium-term trend |
| **7d** | Full Context | Org score + reputation | Strategic planning |

**Interpretation**:
- **80-100**: High likelihood of launch (imminent activity)
- **50-79**: Moderate (growing momentum)
- **20-49**: Low (establishing phase)
- **0-19**: Minimal (dormant or new)

### Risk Score Components

| Component | Weight | Meaning |
|-----------|--------|---------|
| **rug_prob** | 40% | Token quality (avg rug_probability) |
| **instability** | 0.25% | Funding volatility (snapshot variance) |
| **velocity** | 0.2% | Token launch rate |
| **blocked_ratio** | 0.15% | Creator reputability |

**Interpretation**:
- **0-20**: Safe (low risk)
- **21-40**: Caution (monitor)
- **41-60**: Alert (investigate)
- **61-100**: Critical (avoid)

### Token Outcome Probabilities

| Metric | Range | Interpretation |
|--------|-------|-----------------|
| **prob_rug** | 0-1 | Probability token becomes rug (0 = safe, 1 = certain rug) |
| **prob_2x** | 0-1 | Probability 2x return (higher = better prospects) |
| **prob_10x** | 0-1 | Probability 10x return (rare, depends on prob_2x and not-rug) |
| **quality_score** | 0-100 | Overall expected quality |

**Decision Framework**:
- **quality_score > 70 + prob_rug < 0.2**: Good candidate
- **quality_score < 40**: Avoid
- **prob_rug > 0.6**: High risk rug

## Alert Types

| Alert Type | Trigger | Severity | Action |
|-----------|---------|----------|--------|
| **funding_burst** | 3+ active funders in 24h | HIGH | Monitor for launch |
| **creator_funded** | 2+ active creators in 24h | MEDIUM | Funding distribution |
| **operator_spike** | 5+ burst windows in 24h | HIGH | Intense activity |
| **risk_spike** | risk_score > 60 | CRITICAL | Investigate org |
| **watchlist_promotion** | prob_launch_24h >= 80 | HIGH | Imminent launch |

**Dedup**: Same alert type fires max once per calendar day per org

## Querying Historical Data

### Last 30 days of org activity
```bash
curl "http://localhost:5002/api/orgs/123/snapshots?days=30"
```

### Org relationship strength
```sql
sqlite3 database/flex_complete_database.db "
SELECT org_id_a, org_id_b, relationship_strength, relationship_type
FROM org_relationships
WHERE relationship_strength >= 30
ORDER BY relationship_strength DESC;
"
```

### High-risk organizations
```sql
sqlite3 database/flex_complete_database.db "
SELECT * FROM vw_high_risk_orgs
WHERE risk_score > 60
LIMIT 10;
"
```

### Active organizations today
```sql
sqlite3 database/flex_complete_database.db "
SELECT * FROM vw_active_orgs_24h
ORDER BY active_funders DESC
LIMIT 20;
"
```

## Common Use Cases

### 1. Find imminent launches
```bash
curl "http://localhost:5002/api/orgs/windows?min_prob_24h=75" | jq '.[] | select(.prob_launch_24h > 80)'
```

### 2. Monitor risky orgs
```bash
curl "http://localhost:5002/api/orgs/families" | jq '.[]' > families.json
for fam in $(cat families.json | jq -r '.organization_ids[]'); do
  curl "http://localhost:5002/api/orgs/$fam/risk" | jq 'select(.risk_score > 60)'
done
```

### 3. Check token before buying
```bash
TOKEN=EPjFWaDojoePp2N9vGNnyb33E1oJCzTwWafPVrpR5HNY
curl "http://localhost:5002/api/tokens/$TOKEN/outcome" | jq '.expected_quality_score'
```

### 4. Track org's weekly trend
```bash
for day in {0..6}; do
  echo "Day $day:"
  curl "http://localhost:5002/api/orgs/123/snapshots?days=$((day+1))" | jq '.[0] | {date: .snapshot_date, funders: .active_funders, volume: .weighted_volume}'
done
```

## Troubleshooting

### No organizations showing up
- Ensure v1 graph detection has run: check `dev_organizations` table
- Run: `sqlite3 database/flex_complete_database.db "SELECT COUNT(*) FROM dev_organizations;"`

### Missing snapshots
- Snapshots are created during v3 run
- Run `python3 dev_intelligence_detection.py` to populate

### Alerts not firing
- Check org_alerts table: `SELECT COUNT(*) FROM org_alerts WHERE date(created_at, 'unixepoch') = date('now');`
- Alerts fire once per day per type (calendar-day dedup)
- Check thresholds (active_funders >= 3, etc.)

### API endpoint 404
- Ensure Flask app is running: `python3 -c "from src.core.dev_intelligence_api import dev_intelligence_api; print(dev_intelligence_api)"`
- Check main.py has: `register_dev_intelligence_api(app, db_path)`

## Performance Tips

1. **Batch queries**: Get all families once, iterate locally
2. **Time windows**: Use `?days=7` instead of `?days=90` for snapshots
3. **Pagination**: Use `?limit=50` for large result sets
4. **Caching**: Results are snapshot at query time (no real-time updates)

## Integration with Webhooks

If using Helius webhooks, v3 can analyze created orgs in real-time:

```python
# In webhook handler, after v1 org detection:
from src.core.dev_intelligence_v3 import DevIntelligenceV3Engine

engine = DevIntelligenceV3Engine('database/flex_complete_database.db')
result = engine.detect_and_store()
print(f"V3 analysis: {result['orgs_processed']} orgs, {result['alerts_fired']} alerts")
```

## Next Steps

1. **Monitor launches**: Check `/api/orgs/windows` daily for high-probability entries
2. **Track families**: Use `/api/orgs/families` to understand org relationships
3. **Assess risk**: Review `/api/orgs/<id>/risk` before trading org tokens
4. **React to alerts**: Configure your system to listen to `/api/orgs/<id>/alerts`
5. **Evaluate tokens**: Use `/api/tokens/<mint>/outcome` for pre-trade screening

For detailed architecture and formulas, see `FLEX_V3_IMPLEMENTATION_SUMMARY.md`.
