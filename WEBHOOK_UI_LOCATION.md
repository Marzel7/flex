# Where Webhooks Appear in the UI

**Location**: Multiple places across the FLEX interface

---

## 1. Dashboard Navigation Button

**Location**: Main dashboard (`/`)

**Visual**: Blue button labeled **"📡 Webhook"**

**Code Location**: [main.py:2355](main.py#L2355)

```html
<button class="action-button"
        onclick="window.location.href = '/webhook-monitor'"
        title="Monitor real-time webhook activity and transfers"
        style="background: rgba(59, 130, 246, 0.2);
               color: var(--color-none);
               border: 1px solid rgba(59, 130, 246, 0.5);
               margin-left: 8px;">
  📡 Webhook
</button>
```

**Appears Among**: Networks | Clusters | Coordinated Funders | Hubs | Creator Analysis | **[📡 Webhook]** | 💰 RPC Metrics

---

## 2. Webhook Monitor Page

**URL**: `http://localhost:5002/webhook-monitor`

**Route**: [main.py:18230](main.py#L18230)

```python
@app.route('/webhook-monitor')
def webhook_monitor():
    """Real-time webhook monitoring dashboard"""
```

**Shows**:
- Webhooks Received (total count)
- Transfers Processed (total count)
- Transfers (24h) (activity in last 24 hours)
- Last Activity (when webhook last arrived)
- Recent Transfers Table (10 latest transfers)
  - Sender
  - Receiver
  - Amount
  - TX Hash
  - Time

**Features**:
- Auto-refreshes every 5 seconds
- Manual refresh button (🔄)
- Status badges (Active/Idle)
- Responsive design

---

## 3. API Endpoints (Not Yet in UI, Available via API)

### Webhook Status API
```
GET /api/webhook/status
```

**Returns**:
```json
{
  "ok": true,
  "total_signatures": 42,
  "total_transfers": 142,
  "last_webhook": "2026-03-03T14:22:15",
  "transfers_today": 89,
  "queue_size": 25,
  "recent_transfers": [...]
}
```

### Creator Recent Checks (Enriched with Risk Scores)
```
GET /api/creator-recent-checks/enriched
```

**Returns**: Recent creators with risk scores (sorted by risk_score DESC)

```json
{
  "recent_checks": [
    {
      "creator_address": "5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ",
      "risk_score": 45,
      "risk_level": "moderate",
      "component_scores": {...},
      "risk_reasons": [...]
    }
  ]
}
```

### Top Risk Creators
```
GET /api/creators/top-risk
```

**Returns**: Top 25 highest-risk creators

### Creator Risk Details
```
GET /api/creator/<address>/risk-details
```

**Returns**: Detailed risk breakdown for specific creator

---

## Navigation Path

### From Main Dashboard

1. Click **[📡 Webhook]** button
2. ↓
3. **Webhook Monitor Page** (`/webhook-monitor`)
   - Shows real-time webhook metrics
   - Recent transfers table
   - Status indicators
4. ← Back button returns to Dashboard

---

## Code Locations

| What | Where |
|------|-------|
| Dashboard button | [main.py:2355](main.py#L2355) |
| Webhook monitor route | [main.py:18230](main.py#L18230) |
| Webhook monitor HTML | [main.py:18230-18370](main.py#L18230-L18370) |
| Webhook status API | [main.py:18143](main.py#L18143) |
| Webhook handler endpoint | [main.py:18022](main.py#L18022) |
| Creator recent checks (enriched) | webhook_api_enriched.py (via init) |
| Top risk creators | webhook_api_enriched.py (via init) |
| Creator risk details | webhook_api_enriched.py (via init) |

---

## How to Access

### Via UI
1. Go to `http://localhost:5002/`
2. Click **[📡 Webhook]** button
3. View real-time webhook monitor

### Via API (Command Line)
```bash
# Webhook status
curl http://localhost:5002/api/webhook/status | jq

# Recent creators with risk scores
curl http://localhost:5002/api/creator-recent-checks/enriched | jq

# Top risk creators
curl http://localhost:5002/api/creators/top-risk | jq

# Specific creator details
curl http://localhost:5002/api/creator/5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ/risk-details | jq
```

---

## Current State

| Component | Status | Location |
|-----------|--------|----------|
| Dashboard button | ✅ Active | Main dashboard |
| Webhook monitor page | ✅ Active | `/webhook-monitor` |
| Webhook status API | ✅ Active | `/api/webhook/status` |
| Webhook ingestion endpoint | ✅ Active | `POST /helius/webhook` |
| Creator enriched API | ✅ Active | `/api/creator-recent-checks/enriched` |
| Top risk API | ✅ Active | `/api/creators/top-risk` |
| Risk details API | ✅ Active | `/api/creator/<>/risk-details` |

All endpoints are **fully operational** and integrated into main.py.

---

## Next Steps (Optional UI Enhancement)

The API endpoints (`/creator-recent-checks/enriched`, `/creators/top-risk`, `/creator/<>/risk-details`) are currently accessible via API but not yet displayed in the main UI.

To add them to the UI:
1. Create a new page (e.g., `/creator-risk-analysis`)
2. Fetch `/api/creator-recent-checks/enriched`
3. Display creators in a table with risk scores
4. Add sorting/filtering by risk level
5. Add a link from the dashboard

**This is optional** - the API is fully functional and can be used directly.

---

*Generated: 2026-03-03*
*Claude Code*
