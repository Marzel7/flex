# FLEX V3.1 — Quick API Reference

**Status**: ✅ Production Ready
**Version**: 1.0
**Date**: March 12, 2026

---

## Overview

V3.1 adds 7 new REST endpoints for querying behavioral modeling signals:
- Organization momentum (activity trends)
- Launch cadence (pattern-based predictions)
- Team expansion (creator onboarding)
- Enhanced predictions (signal convergence)

---

## Organization-Level Endpoints

### 1. GET /api/orgs/<org_id>/momentum

**Purpose**: Get momentum history for an organization

**Query Parameters**: None

**Example**:
```bash
curl http://localhost:5002/api/orgs/123/momentum
```

**Response**:
```json
{
  "momentum_history": [
    {
      "recorded_date": "2026-03-12",
      "activity_24h": 15,
      "activity_7d_avg": 8.5,
      "momentum": 0.76,
      "momentum_signal": 76,
      "trend": "accelerating"
    },
    {
      "recorded_date": "2026-03-11",
      "activity_24h": 12,
      "activity_7d_avg": 8.5,
      "momentum": 0.41,
      "momentum_signal": 41,
      "trend": "stable"
    }
  ]
}
```

**Fields**:
- `momentum_signal`: -100 to +100 (negative=decay, positive=acceleration)
- `trend`: One of `accelerating`, `stable`, `decelerating`
- `activity_24h`: Transaction count in 24h window
- `activity_7d_avg`: Average activity from previous 7 days

---

### 2. GET /api/orgs/<org_id>/cadence

**Purpose**: Get launch cadence analysis for an organization

**Query Parameters**: None

**Example**:
```bash
curl http://localhost:5002/api/orgs/123/cadence
```

**Response**:
```json
{
  "cadence_analysis": [
    {
      "analysis_date": "2026-03-12",
      "launches_detected": 5,
      "average_interval": 7.2,
      "interval_variability": 0.31,
      "days_since_last_launch": 3,
      "cadence_score": 65,
      "due_for_launch": false,
      "prediction_confidence": 0.72
    }
  ]
}
```

**Fields**:
- `launches_detected`: Total number of tokens created by org
- `average_interval`: Average days between launches
- `interval_variability`: 0-1 (higher=less predictable)
- `cadence_score`: 0-100 (strength of pattern)
- `due_for_launch`: Boolean (predicted next launch soon)
- `prediction_confidence`: 0-1 (pattern consistency)

---

### 3. GET /api/orgs/<org_id>/expansion

**Purpose**: Get team expansion events for an organization

**Query Parameters**: None

**Example**:
```bash
curl http://localhost:5002/api/orgs/123/expansion
```

**Response**:
```json
{
  "expansion_events": [
    {
      "event_date": "2026-03-12",
      "current_creator_count": 12,
      "creators_added_24h": 2,
      "creators_added_7d": 5,
      "expansion_rate": 0.42,
      "expansion_score": 58,
      "expansion_signal": "normal",
      "team_size_change_7d": 5
    }
  ]
}
```

**Fields**:
- `expansion_signal`: One of `rapid` (5+), `normal` (2-4), `stable` (0-1), `shrinking`
- `expansion_score`: 0-100 (magnitude of expansion)
- `expansion_rate`: 0-1 (new creators / total)
- `team_size_change_7d`: Change in creator count over 7 days

---

### 4. GET /api/orgs/<org_id>/enhanced-windows

**Purpose**: Get enhanced launch predictions combining base + behavioral signals

**Query Parameters**: None

**Example**:
```bash
curl http://localhost:5002/api/orgs/123/enhanced-windows
```

**Response**:
```json
{
  "enhanced_windows": [
    {
      "prediction_date": "2026-03-12",
      "base_prob_launch_24h": 62,
      "enhanced_prob_launch_24h": 78,
      "momentum_signal": 45,
      "cadence_score": 65,
      "expansion_score": 58,
      "data_quality_score": 85,
      "enhancement_factor": 1.26,
      "combined_confidence": 0.74
    }
  ]
}
```

**Fields**:
- `base_prob_launch_24h`: Original V3 prediction (0-100)
- `enhanced_prob_launch_24h`: V3 + V3.1 signals (0-100)
- `enhancement_factor`: Multiplier (enhanced / base)
- `combined_confidence`: 0-1 (all signals converge)
- `data_quality_score`: 0-100 (signal completeness)

**Interpretation**:
- enhancement_factor > 1.5 = Strong behavioral support
- combined_confidence > 0.7 = High reliability
- All three signals (momentum, cadence, expansion) > 50 = Very strong signal

---

## Discovery Endpoints

### 5. GET /api/orgs/v31/momentum-driven

**Purpose**: Get organizations with high positive momentum (accelerating)

**Query Parameters**:
- `limit`: Max results (default 50, max 500)

**Example**:
```bash
curl "http://localhost:5002/api/orgs/v31/momentum-driven?limit=20"
```

**Response**:
```json
{
  "momentum_driven_launches": [
    {
      "organization_id": 123,
      "operator_wallet": "8GhGLVZ6n38hpFGBqb6r6CfSfXzKLHwmXgbQzBNREEch",
      "prob_launch_24h": 72,
      "momentum_signal": 85,
      "trend": "accelerating",
      "activity_24h": 18,
      "activity_7d_avg": 6.2
    }
  ]
}
```

**Filter**: `momentum > 0.3 AND trend = 'accelerating'`

**Use Case**: Find organizations building momentum toward launch

---

### 6. GET /api/orgs/v31/cadence-due

**Purpose**: Get organizations due for launch based on cadence patterns

**Query Parameters**:
- `limit`: Max results (default 50, max 500)

**Example**:
```bash
curl "http://localhost:5002/api/orgs/v31/cadence-due?limit=20"
```

**Response**:
```json
{
  "cadence_due_launches": [
    {
      "organization_id": 456,
      "operator_wallet": "9AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIjKlMn",
      "days_since_last_launch": 8,
      "average_interval": 7.5,
      "cadence_score": 72,
      "prediction_confidence": 0.85
    }
  ]
}
```

**Filter**: `due_for_launch = 1 AND prediction_confidence >= 0.6`

**Use Case**: Predict launch timing based on historical patterns

---

### 7. GET /api/orgs/v31/expansion-driven

**Purpose**: Get organizations with rapid team expansion

**Query Parameters**:
- `limit`: Max results (default 50, max 500)

**Example**:
```bash
curl "http://localhost:5002/api/orgs/v31/expansion-driven?limit=30"
```

**Response**:
```json
{
  "expansion_driven_launches": [
    {
      "organization_id": 789,
      "operator_wallet": "AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTt",
      "current_creator_count": 18,
      "creators_added_7d": 6,
      "expansion_score": 74,
      "expansion_signal": "rapid",
      "team_size_change_7d": 6
    }
  ]
}
```

**Filter**: `expansion_signal IN ('rapid', 'normal') AND creators_added_7d >= 2`

**Use Case**: Identify organizations preparing for coordinated launches

---

### 8. GET /api/orgs/v31/high-confidence

**Purpose**: Get organizations with converging signals (high confidence predictions)

**Query Parameters**:
- `limit`: Max results (default 50, max 500)

**Example**:
```bash
curl "http://localhost:5002/api/orgs/v31/high-confidence?limit=20"
```

**Response**:
```json
{
  "high_confidence_launches": [
    {
      "organization_id": 999,
      "operator_wallet": "XxYyZzAaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQq",
      "enhanced_prob_launch_24h": 89,
      "momentum_signal": 72,
      "cadence_score": 68,
      "expansion_score": 65,
      "combined_confidence": 0.84,
      "enhancement_factor": 1.43
    }
  ]
}
```

**Filter**: `combined_confidence >= 0.7 AND enhanced_prob_launch_24h >= 70`

**Use Case**: Find highest-confidence launch predictions with signal convergence

---

## Practical Examples

### Find Orgs Likely to Launch Tomorrow
```bash
# High momentum + High base prediction
curl "http://localhost:5002/api/orgs/v31/momentum-driven?limit=10"

# Or check high-confidence for top candidates
curl "http://localhost:5002/api/orgs/v31/high-confidence?limit=5"
```

### Monitor Organization Activity
```bash
# Check specific org momentum trend
curl "http://localhost:5002/api/orgs/123/momentum"

# Check enhanced prediction with all signals
curl "http://localhost:5002/api/orgs/123/enhanced-windows"
```

### Identify Team Expansion Prep
```bash
# Get all orgs with rapid expansion
curl "http://localhost:5002/api/orgs/v31/expansion-driven?limit=30"

# Then check their momentum
curl "http://localhost:5002/api/orgs/456/momentum"
```

### Verify Prediction Patterns
```bash
# Get due-for-launch orgs
curl "http://localhost:5002/api/orgs/v31/cadence-due?limit=20"

# Then check actual launch history
curl "http://localhost:5002/api/orgs/789/cadence"
```

---

## Response Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request (invalid parameters) |
| 404 | Organization not found |
| 500 | Server error |

---

## Rate Limiting

No rate limiting on development server. For production, recommend:
- 100 requests per minute per IP
- 10,000 requests per day per IP

---

## Caching Recommendations

**Frontend caching durations**:
```javascript
const CACHE_DURATION = {
  momentum: 300000,        // 5 minutes
  cadence: 600000,        // 10 minutes
  expansion: 600000,      // 10 minutes
  enhanced_windows: 300000, // 5 minutes
  momentum_driven: 300000, // 5 minutes (volatile)
  cadence_due: 600000,     // 10 minutes (stable)
  expansion_driven: 600000, // 10 minutes (stable)
  high_confidence: 300000   // 5 minutes (volatile)
};
```

---

## Integration with Existing API

V3.1 endpoints are **additions only** — all existing V2/V3 endpoints remain unchanged:

- GET /api/dashboard — System overview
- GET /api/launch-leaderboard — Top predictions
- GET /api/organizations — Org listing
- GET /api/organization/<id> — Org detail
- GET /api/signals/<id> — Signal breakdown
- ...and 15+ more

---

## Dashboard Integration

V3.1 signals are automatically available in the Intelligence Dashboard:

- **Organization Detail Page**: Shows momentum history chart
- **Launch Radar**: Sorted by enhanced_prob_launch_24h (with V3.1)
- **Dashboard Home**: Top candidates include V3.1 signals
- **New Panels** (Phase 2): Behavioral charts and graphs

---

## Troubleshooting

**Empty response for momentum**:
- Org has no snapshots yet (run detection pipeline)
- Check: `SELECT COUNT(*) FROM org_snapshots WHERE organization_id = ?`

**All confidence scores are 0**:
- Database migration not applied
- Run: `sqlite3 flex_complete_database.db < database/migrations/dev_intelligence_v3_1_enhancements.sql`

**404 on organization endpoint**:
- Organization doesn't exist in dev_organizations
- Check: `SELECT COUNT(*) FROM dev_organizations WHERE organization_id = ?`

---

## Performance Tips

1. **Limit results** for discovery endpoints (default 50 is reasonable)
2. **Cache responses** on frontend (see caching recommendations)
3. **Use org-level endpoints** for single org (faster than discovery)
4. **Filter in client** if you need custom sorting

---

## Support

For issues or questions:
1. Check FLEX_V3_1_INTEGRATION_COMPLETE.md for detailed info
2. See FLEX_SYSTEM_STATUS_MARCH12_2026.md for architecture overview
3. Review src/core/dev_intelligence_api.py for implementation details

---

**Version**: 1.0
**Status**: Production Ready
**Last Updated**: March 12, 2026
