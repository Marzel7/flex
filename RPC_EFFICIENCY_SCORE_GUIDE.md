# RPC Efficiency Score - Dashboard Integration Guide
**Date**: March 5, 2026
**Status**: ✅ Ready to Implement
**Component**: Key metric for monitoring RPC optimization effectiveness

---

## 📊 What is the RPC Efficiency Score?

The **efficiency score** measures how effectively the system saves RPC credits:

```
efficiency_score = credits_saved / credits_spent
```

### Example:
```
Credits Spent: 420
Credits Saved: 3,600

Efficiency Score = 3,600 / 420 = 8.57

Meaning: For every 1 credit spent, the system saved 8.57 credits.
```

---

## 📈 Interpretation Guide

| Score | Status | Meaning | Action |
|-------|--------|---------|--------|
| < 1 | ⚠️ POOR | Spending more than saving | ALERT - Investigate |
| 1-3 | 🟡 WEAK | Weak optimization | WARNING - Check caches |
| 3-5 | 🟢 GOOD | Good optimization | Monitor |
| 5-10 | 💚 EXCELLENT | Excellent optimization | Normal operation |
| > 10 | 🚀 OUTSTANDING | Very strong optimization | Exceptional |

### Production Target:
**5-10** (Excellent optimization with all 6 layers active)

---

## 🔧 Implementation Steps

### Step 1: Create Database Views (5 minutes)

```bash
sqlite3 /path/to/flex_complete_database.db < rpc_efficiency_score.sql
```

**Verify views created**:
```bash
sqlite3 /path/to/flex_complete_database.db \
  "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name LIKE 'v_rpc_efficiency%';"
```

Expected: 6 views created

### Step 2: Implement Backend API (10 minutes)

Add these imports to your Flask app:

```python
from rpc_efficiency_api import (
    query_daily_efficiency,
    query_efficiency_24h,
    query_efficiency_all_time,
    query_efficiency_by_section,
    query_health_status,
    get_efficiency_dashboard,
)
```

Add these routes:

```python
@app.route('/api/rpc-efficiency/dashboard')
def api_efficiency_dashboard():
    days = request.args.get('days', 30, type=int)
    return jsonify(get_efficiency_dashboard(days))

@app.route('/api/rpc-efficiency/24h')
def api_efficiency_24h():
    data = query_efficiency_24h()
    return jsonify(asdict(data) if data else {})

@app.route('/api/rpc-efficiency/daily')
def api_efficiency_daily():
    days = request.args.get('days', 30, type=int)
    metrics = query_daily_efficiency(days)
    return jsonify([asdict(m) for m in metrics])

@app.route('/api/rpc-efficiency/health')
def api_efficiency_health():
    days = request.args.get('days', 7, type=int)
    reports = query_health_status(days)
    return jsonify([asdict(r) for r in reports])
```

### Step 3: Add KPI Card to Dashboard (5 minutes)

Add a new KPI card to your dashboard layout:

```html
<div class="kpi-card efficiency-score">
  <div class="label">RPC Efficiency</div>
  <div class="value" id="efficiency-24h">8.57x</div>
  <div class="sublabel">For every 1 credit spent</div>
  <div class="status" id="efficiency-status">Excellent</div>
</div>
```

### Step 4: Add Efficiency Chart (10 minutes)

Add a line chart showing 30-day efficiency trend:

```javascript
// Fetch efficiency data
fetch('/api/rpc-efficiency/daily?days=30')
  .then(r => r.json())
  .then(data => {
    const chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.map(d => d.day),
        datasets: [{
          label: 'Efficiency Score',
          data: data.map(d => d.efficiency_score),
          borderColor: '#22c55e',
          backgroundColor: 'rgba(34, 197, 94, 0.1)',
          fill: true,
          tension: 0.4,
        }]
      },
      options: {
        responsive: true,
        plugins: {
          title: {
            text: 'RPC Efficiency Trend (30 days)'
          }
        },
        scales: {
          y: {
            min: 0,
            title: { text: 'Efficiency Score' }
          }
        }
      }
    });
  });
```

### Step 5: Add Health Status Alerts (5 minutes)

Add alerts for unhealthy efficiency scores:

```javascript
// Check health status
fetch('/api/rpc-efficiency/health?days=7')
  .then(r => r.json())
  .then(reports => {
    reports.forEach(report => {
      if (report.alert_level === 'ALERT') {
        console.warn(
          `⚠️ RPC Efficiency Alert: Score ${report.efficiency_score} ` +
          `on ${report.day} (${report.health_status})`
        );
        // Show alert to user
        showAlert(
          `RPC efficiency dropped to ${report.efficiency_score}. ` +
          `Check cache status.`
        );
      } else if (report.alert_level === 'WARNING') {
        console.warn(
          `⚠️ RPC Efficiency Warning: Score ${report.efficiency_score} ` +
          `on ${report.day}`
        );
      }
    });
  });
```

---

## 📊 Dashboard Layout

### KPI Cards Section (Updated)

```
Before:
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ Credits Spent│Credits Saved │ Savings %    │ Baseline     │
└──────────────┴──────────────┴──────────────┴──────────────┘

After:
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ Credits Spent│Credits Saved │ Savings %    │ Baseline     │  Efficiency  │
│    420       │   3,600      │   89.5%      │   4,020      │    8.57x     │
└──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

### New Charts

Add to your dashboard:

```
Time Series Chart: Efficiency Score Trend
├─ X-Axis: Day (30 days)
├─ Y-Axis: Efficiency Score (0-15)
└─ Line: Green (#22c55e) - Efficiency Score

This chart shows:
• Overall optimization trend
• When efficiency is improving/degrading
• Impact of cache warmup over time
```

### Health Status Widget (Optional)

```
┌────────────────────────────────┐
│ RPC Optimization Health        │
├────────────────────────────────┤
│ Status: 🟢 Excellent           │
│ Score: 8.57                    │
│ Trend: ↗ Improving             │
│ Alert: None                    │
└────────────────────────────────┘
```

---

## 📡 API Response Format

### Daily Efficiency
```json
{
  "day": "2026-03-05",
  "credits_spent": 420,
  "credits_saved": 3600,
  "efficiency_score": 8.57,
  "credits_baseline": 4020,
  "total_requests": 150,
  "cache_hits": 100,
  "cache_hit_rate_pct": 66.7
}
```

### 24-Hour Efficiency
```json
{
  "efficiency_score_24h": 8.57,
  "credits_spent_24h": 420,
  "credits_saved_24h": 3600,
  "credits_baseline_24h": 4020,
  "total_requests_24h": 150,
  "cache_hits_24h": 100,
  "cache_hit_rate_pct_24h": 66.7
}
```

### All-Time Efficiency
```json
{
  "efficiency_score_all_time": 7.85,
  "total_credits_spent": 12450,
  "total_credits_saved": 97650,
  "period_start": "2026-03-01",
  "period_end": "2026-03-05",
  "days_tracked": 5,
  "avg_daily_credits_spent": 2490,
  "avg_daily_credits_saved": 19530,
  "overall_cache_hit_rate_pct": 63.5
}
```

### Health Status
```json
{
  "day": "2026-03-05",
  "efficiency_score": 8.57,
  "health_status": "EXCELLENT",
  "alert_level": "NORMAL",
  "credits_spent": 420,
  "credits_saved": 3600,
  "request_count": 150
}
```

---

## 🎨 Color Coding

Use these colors to represent efficiency levels:

```css
--color-efficiency-poor: #ef4444;           /* Red - < 1 */
--color-efficiency-weak: #f97316;           /* Orange - 1-3 */
--color-efficiency-good: #eab308;           /* Yellow - 3-5 */
--color-efficiency-excellent: #22c55e;      /* Green - 5-10 */
--color-efficiency-outstanding: #0ea5e9;   /* Blue - > 10 */
```

### Example Styling
```css
.efficiency-score {
  background: linear-gradient(135deg, rgba(34, 197, 94, 0.1), rgba(34, 197, 94, 0.05));
  border-left: 4px solid #22c55e;
  padding: 20px;
}

.efficiency-score .value {
  font-size: 36px;
  font-weight: bold;
  color: #22c55e;
}

.efficiency-score.alert {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.1), rgba(239, 68, 68, 0.05));
  border-left-color: #ef4444;
}

.efficiency-score.alert .value {
  color: #ef4444;
}
```

---

## 🔄 Auto-Update Strategy

```javascript
// Update efficiency KPI every 1 minute
setInterval(async () => {
  const response = await fetch('/api/rpc-efficiency/24h');
  const data = await response.json();
  updateEfficiencyKPI(data);
}, 60 * 1000);

// Update efficiency chart every 5 minutes
setInterval(async () => {
  const response = await fetch('/api/rpc-efficiency/daily?days=30');
  const data = await response.json();
  updateEfficiencyChart(data);
}, 5 * 60 * 1000);

// Check health status every 10 minutes
setInterval(async () => {
  const response = await fetch('/api/rpc-efficiency/health?days=7');
  const data = await response.json();
  checkHealthAlerts(data);
}, 10 * 60 * 1000);
```

---

## 📋 Monitoring Best Practices

### Daily Checks
- ✅ Check 24h efficiency score (should be 5-10)
- ✅ Verify no ALERT-level statuses
- ✅ Check trending direction (should be stable or improving)

### Weekly Review
- ✅ Review 7-day average efficiency
- ✅ Compare to previous week
- ✅ Identify any degradation patterns
- ✅ Verify cache hit rates growing

### Monthly Analysis
- ✅ Calculate month-long efficiency average
- ✅ Compare to production targets
- ✅ Analyze by section (which is most efficient?)
- ✅ Plan optimization improvements

---

## 🚨 Alert Triggers

### ALERT (Efficiency < 3)
```
Trigger: efficiency_score < 3
Action: Immediate investigation
Possible causes:
- Fingerprint cache not working
- Creator cache disabled
- New code path bypassing cache
- Cache invalidation issue

Response:
1. Check logs for cache errors
2. Verify cache tables populated
3. Check environment variables
4. Review recent code changes
```

### WARNING (Efficiency 3-5)
```
Trigger: efficiency_score < 5
Action: Monitor closely
Possible causes:
- Cache warming up (day 1-2)
- Seasonal variation in patterns
- Partial cache failure

Response:
1. Monitor trend (should improve)
2. Check for specific failing sections
3. Verify cache hit rates
```

### NORMAL (Efficiency >= 5)
```
Status: Healthy
Action: Continue normal monitoring
This is expected operational state
```

---

## 📊 Expected Values by Stage

### Stage 1: No Caching
```
Efficiency: 0
Credits Spent: 400,000/month
Credits Saved: 0
```

### Stage 2: Basic Caching (Layer 5)
```
Efficiency: 2-3
Credits Spent: 300,000/month
Credits Saved: 600,000-900,000/month
Cache Hit Rate: 40%
```

### Stage 3: Full Optimization (Layers 1-6)
```
Efficiency: 6-10
Credits Spent: 12,500-25,000/month
Credits Saved: 75,000-250,000/month
Cache Hit Rate: 60-80%
```

---

## 🔧 SQL Queries for Analysis

### Current 24h Efficiency
```sql
SELECT *
FROM v_rpc_efficiency_24h;
```

### 30-day Trend
```sql
SELECT day, efficiency_score, cache_hit_rate_pct
FROM v_rpc_efficiency_score
WHERE day >= DATE('now', '-30 days')
ORDER BY day ASC;
```

### Efficiency by Section
```sql
SELECT section, efficiency_score, request_count
FROM v_rpc_efficiency_by_section
WHERE day >= DATE('now', '-30 days')
GROUP BY section
ORDER BY efficiency_score DESC;
```

### Health Status with Alerts
```sql
SELECT *
FROM v_rpc_efficiency_health
WHERE alert_level != 'NORMAL'
ORDER BY day DESC;
```

---

## ✅ Integration Checklist

- [ ] Create SQL views (5 min)
- [ ] Verify 6 views created
- [ ] Copy rpc_efficiency_api.py (2 min)
- [ ] Add Flask routes (5 min)
- [ ] Update dashboard KPI section (5 min)
- [ ] Add efficiency chart (10 min)
- [ ] Add health status alerts (5 min)
- [ ] Test all endpoints (5 min)
- [ ] Verify auto-refresh working (5 min)
- [ ] Deploy to production (5 min)

**Total: 60 minutes**

---

## 📚 Files Delivered

1. ✅ `rpc_efficiency_score.sql` - 6 SQL views + indexes
2. ✅ `rpc_efficiency_api.py` - Complete Python API
3. ✅ `RPC_EFFICIENCY_SCORE_GUIDE.md` - This integration guide

---

**Status**: ✅ READY FOR PRODUCTION

**Next Step**: Run `rpc_efficiency_score.sql` to create views, then implement backend API routes.

