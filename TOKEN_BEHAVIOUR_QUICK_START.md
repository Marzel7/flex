# Token Behaviour — Quick Start Guide

**Status:** ✅ Ready to use (no backfill, forward-looking only)

---

## What You Have

Token behaviour classification that monitors tokens as they grow.

**No backfill.** Classification starts **from right now**. As tokens accumulate price history, they get classified automatically.

---

## How to Use

### 1. **Run the Monitor** (Pick one)

**Once, right now:**
```bash
python3 src/core/token_behaviour_monitor.py
```

**Every 10 minutes (background):**
```bash
python3 -c "
from src.core.token_behaviour_monitor import run_periodic_monitor
run_periodic_monitor('database/flex_complete_database.db', interval_secs=600)
"
```

**Or add to your scheduler:**
```bash
# crontab -e
*/10 * * * * cd /path/to/flex && python3 -c "from src.core.token_behaviour_monitor import classify_recent_tokens; classify_recent_tokens('database/flex_complete_database.db')"
```

### 2. **Query Results via API**

Once tokens are classified, query them:

```bash
# Get summary
curl http://localhost:5002/api/token-behaviour/stats/summary

# Get all immediate_rug tokens
curl "http://localhost:5002/api/token-behaviour?category=immediate_rug"

# Get details for one token
curl "http://localhost:5002/api/token-behaviour/GfXVT6i8L23iUT4KNgydz4aSJjBZ8jmY1d9oTzwEfmF"
```

### 3. **Dashboard Page** (Coming Soon)

Once integrated, visit:
```
http://localhost:5002/token-behaviour
```

---

## Requirements for Classification

A token must have:
- ✅ 8+ price snapshots recorded
- ✅ 5+ minutes of history
- ✅ Not classified in last 10 minutes

**Typical timeline:** Token discovered → 5 min later → first classification

---

## Classification Categories

| Category | Meaning | Confidence |
|----------|---------|-----------|
| **immediate_rug** | Spike in first 5 min, crashes 85%+ | High (0.73) |
| **runner** | Strong growth, <50% drawdown | High (0.73) |
| **choppy_runner** | 3x+ gain but volatile | Low (0.26) |
| **rug** | Classic rug: 2x+ → 90% crash | Medium (0.36) |
| **slow_rug** | Gradual decline 70%+ | Low (0.20) |
| **unknown** | Too new or insufficient data | None (0.0) |

---

## Expected Growth

**Day 1:** ~15-30 classified (first tokens reaching 5 min age)
**Day 2-7:** ~50-100 daily
**Steady:** ~10-20 per 10-min cycle (new launches only)

---

## API Reference

### GET /api/token-behaviour
Get classified tokens.

**Params:**
- `category` — filter by category (optional)
- `min_confidence` — minimum confidence 0-1 (default: 0.0)
- `limit` — max results (default: 100)

**Example:**
```bash
curl "http://localhost:5002/api/token-behaviour?category=runner&min_confidence=0.7&limit=10"
```

### GET /api/token-behaviour/{mint}
Get details for one token including 12 features and history.

**Example:**
```bash
curl "http://localhost:5002/api/token-behaviour/GfXVT6i8L23iUT4KNgydz4aSJjBZ8jmY1d9oTzwEfmF"
```

### GET /api/token-behaviour/stats/summary
Get summary by category.

**Returns:**
```json
{
  "total_classified": 42,
  "by_category": {
    "immediate_rug": {"count": 5, "avg_confidence": 0.732, "pct": 11.9},
    "runner": {"count": 1, "avg_confidence": 0.731, "pct": 2.4},
    ...
  }
}
```

---

## Monitoring

Check classification progress:

```python
from src.core.token_behaviour_monitor import get_behaviour_summary

summary = get_behaviour_summary('database/flex_complete_database.db')
print(f"Total: {summary['total_classified']}")
for cat, info in summary['by_category'].items():
    print(f"  {cat}: {info['count']}")
```

---

## Key Files

| File | Purpose |
|------|---------|
| `src/core/token_behavior.py` | Classification engine (feature extraction, rules) |
| `src/core/token_behaviour_monitor.py` | Periodic monitor and query helpers |
| `src/core/flex_dashboard_routes.py` | API endpoints |
| `TOKEN_BEHAVIOUR_FORWARD_LOOKING.md` | Full documentation |

---

## Database

Tables auto-created on first use:

- `token_behavior` — current classification for each mint
- `token_behavior_history` — append-only audit log of all classifications

No manual setup needed.

---

## Tuning

All thresholds in `src/core/token_behavior.py`:

```python
MIN_SNAPSHOTS = 8                      # Adjust to 5 or 10
MIN_LIFETIME_SECS = 180                # Change to 300 for 5 min instead
IMMEDIATE_RUG_TIME_TO_PEAK_MAX = 300   # How fast is "immediate"?
IMMEDIATE_RUG_DRAWDOWN_MIN = 0.85      # How much drop = rug?
RUNNER_MAX_RETURN_MIN = 5.0            # Minimum return multiple for runner
```

Change and re-run monitor to re-classify with new thresholds.

---

## That's It!

Monitor is ready. No more setup needed.

Start the monitor → classify tokens → query API → done.

See `TOKEN_BEHAVIOUR_FORWARD_LOOKING.md` for full details.
