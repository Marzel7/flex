# Webhook Creator Ranking - Quick Start (5 min)

## Copy Files

```bash
cp webhook_creator_ranker.py /path/to/flex/
cp webhook_api_enriched.py /path/to/flex/
```

## Update main.py

Add two lines after Flask app creation:

```python
from webhook_api_enriched import setup_enriched_routes

# ... after init_webhook_system(app):
setup_enriched_routes(app)
```

## Restart Flask

```bash
pkill -f "python3 main.py"
sleep 2
python3 main.py > flask.log 2>&1 &
```

## Test

```bash
# Recent creators with risk scores
curl http://localhost:5002/api/creator-recent-checks/enriched | jq '.recent_checks[0]'

# Top risk creators
curl http://localhost:5002/api/creators/top-risk | jq '.top_risk_creators[0]'

# Specific creator details
curl http://localhost:5002/api/creator/5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ/risk-details | jq
```

## Expected Response

```json
{
  "creator_address": "...",
  "risk_score": 40,
  "risk_level": "moderate",
  "component_scores": {
    "activity": 40,
    "self_funding": 0,
    "distribution": -25,
    "concentration": 0,
    "network": 0,
    "token_behavior": 25
  },
  "risk_reasons": ["active_5m(3tx)", "distribution(15recipients/3transfers)"]
}
```

## Monitor

```bash
tail -f flask.log | grep -E "WEBHOOK|WORKER|RANKER"
```

Watch for:
```
[WORKER] Processing 5Zpgww... (priority=45.0, reason=new_transfer)
[WORKER] 5Zpgww... risk_score=40 level=moderate
```

## Understand Scoring

```
Score = activity + patterns + networks + tokens

Activity:     +40 (active 5m) / +25 (active 1h) / +15 (high volume) / +20 (high value)
Patterns:     -30 (self-fund) / -25 (distribution) / -20 (concentration) / -10 (dust)
Networks:     -35 (coordinated) / -30 (C2C) / -20 (funding net) / -15 (chains)
Tokens:       -25 (multi-token) / -40 (rapid launch) / -20 (risky)

Result: score → risk_level
  ≥80  → critical   🔴
  ≥60  → elevated   🟠
  ≥40  → moderate   🟡
  <40  → low        🟢
```

## Endpoints

| Endpoint | Purpose | Result |
|----------|---------|--------|
| `/api/creator-recent-checks/enriched` | Recent creators with scores | List sorted by risk_score DESC |
| `/api/creators/top-risk` | Top 25 riskiest | Highest scores first |
| `/api/creator/<addr>/risk-details` | Full breakdown | All components + stats |

## Customize

Edit `webhook_creator_ranker.py`:

```python
# Change weights
SCORING_WEIGHTS = {
    "active_5m": 50,  # More active bonus
    "self_funding": -40,  # Stronger penalty
    ...
}

# Change thresholds
RISK_THRESHOLDS = {
    "critical": 90,  # Higher bar for critical
    "elevated": 70,
    "moderate": 50,
    "low": 0,
}
```

## What It Does

1. **Receives** webhook with SOL transfer
2. **Updates** activity stats in real-time
3. **Queues** both sender and receiver for scoring
4. **Scores** on: activity + patterns + networks + tokens
5. **Serves** enriched API with risk breakdown

## Logs to Expect

```
[WEBHOOK] 15:40:01 - Received 1 transaction(s)
[WEBHOOK] 15:40:01 - STORED: 5Zpgww... → HZUZfV... (0.0002 SOL)
[WEBHOOK] 15:40:01 - Queued 2 addresses

[WORKER] Processing 5Zpgww... (priority=45.0, reason=new_transfer)
[WORKER] 5Zpgww... computed_priority=75.5
[WORKER] 5Zpgww... risk_score=40 level=moderate
```

## Done! 🎉

New endpoints live:
- ✅ `/api/creator-recent-checks/enriched`
- ✅ `/api/creators/top-risk`
- ✅ `/api/creator/<addr>/risk-details`

All creators now scored in real-time as webhooks arrive!
