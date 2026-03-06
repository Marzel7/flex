# RPC Savings Dashboard - UI Layout & Implementation Guide
**Date**: March 5, 2026
**Purpose**: Visualize real Helius RPC credits spent and saved
**Data Source**: v_rpc_daily_savings and related views in rpc_metrics table

---

## 📊 Dashboard Layout

### Section 1: Top KPI Cards (Real-Time Summary - Last 24 Hours)

Display 4 key metrics in a horizontal card layout:

```
┌─────────────────┬─────────────────┬─────────────────┬─────────────────┐
│ Credits Spent   │ Credits Saved   │ Savings %       │ Baseline        │
│ (24h)           │ (24h)           │ (24h)           │ (24h)           │
├─────────────────┼─────────────────┼─────────────────┼─────────────────┤
│ 420             │ 3,600           │ 89.5%           │ 4,020           │
│ Helius credits  │ Helius credits  │ of baseline     │ credits         │
└─────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

**Data source**: `v_rpc_dashboard_24h`

**JSON fields**:
- `credits_spent_24h`
- `credits_saved_24h`
- `savings_pct_24h`
- `credits_baseline_24h`

---

### Section 2: Time Series Chart (Last 30 Days)

Line chart showing trends:

```
Credits Over Time (30 days)

3500 ┤                                   ╱╲
     │                            ╱╲    ╱  ╲
3000 │                       ╱╲  ╱  ╲  ╱    ╲
     │                  ╱╲  ╱  ╲╱    ╲╱
2500 │             ╱╲  ╱  ╲╱
     │        ╱╲  ╱  ╲╱
2000 │   ╱╲  ╱  ╲╱
     │  ╱  ╲╱
1500 │╱
     │
1000 │────────────────────────────────────────
     │
 500 │────────────────────────────────────────
     │
   0 └──────────────────────────────────────→
     Mar 5  Mar 10  Mar 15  Mar 20  Mar 25  Apr 1

     ─── Credits Spent
     ─── Credits Saved
     ─── Baseline (Spent + Saved)
```

**Chart type**: Line chart with area fill

**Data series**:
1. `credits_spent` - Line with light blue fill
2. `credits_saved` - Line with green fill
3. `credits_baseline` - Dashed line (reference)

**Data source**: `v_rpc_daily_savings` (last 30 days)

**Interaction**: Click to see daily details

---

### Section 3: Savings Breakdown (Stacked Bar)

Show how savings are distributed by cache type:

```
Daily Savings Breakdown (%)

100% ┤
     │ ████████████████████████████████
  90% ┤ ██████████████████████░░░░░░░░░░
  80% ┤ ████████████████░░░░░░░░░░░░░░░░
  70% ┤ ██████████░░░░░░░░░░░░░░░░░░░░░░
  60% ┤ ████████░░░░░░░░░░░░░░░░░░░░░░░░
  50% ┤ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  40% ┤ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  30% ┤
  20% ┤
  10% ┤
   0% └──────────────────────────────────
       Mar5 Mar10 Mar15 Mar20 Mar25 Apr1

       ████ Skip Savings (60%)
       ░░░░ Refresh Savings (28%)
```

**Chart type**: Stacked bar chart

**Data series**:
- `credits_saved_skip` - Full skip cache hits (200 credits each)
- `credits_saved_refresh` - Light refresh hits (150 credits each)

**Data source**: `v_rpc_daily_savings`

**Interaction**: Hover for exact values

---

### Section 4: Cache Event Mix (Pie/Donut Chart)

Show distribution of cache actions:

```
        Skip Events
       /     |     \
      /      |      \
    /        |        \
   /         |         \
  /    40%   |    35%    \
 |  ┌─────────┬─────────┐  |
 |  │ Refresh │  No Cache│ 25%
 |  │ Events  │  Events  │  |
  \ │  25%    │          │ /
   \└─────────┴─────────┘/
    \         |         /
     \        |        /
      \       |       /
       \      |      /
        \     |     /
         Full Scan Events
              (0%)
```

**Chart type**: Pie or donut chart

**Segments**:
- `cache_skip_events` - Green (major savings)
- `cache_refresh_events` - Blue (partial savings)
- `full_scan_events` - Gray (no savings)
- `no_cache_events` - Transparent (no cache)

**Data source**: `v_rpc_daily_savings`

**Display total event count in center** (if donut)

---

### Section 5: Cache Performance Table (Optional)

Detailed metrics by cache action:

```
Cache Performance Analysis
┌──────────┬────────┬──────────────┬─────────────────┬────────────────┐
│ Action   │ Count  │ Total Saved  │ Avg Saved/Event │ Efficiency     │
├──────────┼────────┼──────────────┼─────────────────┼────────────────┤
│ skip     │  2,450 │ 490,000      │ 200.0           │ 200 credits/ev │
│ refresh  │   780  │ 117,000      │ 150.0           │ 150 credits/ev │
│ full_scan│  1,200 │ 0            │ 0.0             │ 0 credits/ev   │
└──────────┴────────┴──────────────┴─────────────────┴────────────────┘
```

**Data source**: `v_rpc_cache_performance`

---

### Section 6: Cumulative Summary Card (Optional)

All-time or period-wide metrics:

```
┌────────────────────────────────────────────────────────────┐
│ Cumulative RPC Savings (All Time)                          │
├────────────────────────────────────────────────────────────┤
│ Period: 2026-03-01 to 2026-03-05                           │
│                                                            │
│ Total Credits Spent:         12,450                        │
│ Total Credits Saved:         89,250                        │
│ Baseline Credits:           101,700                        │
│ Overall Savings Rate:           87.8%                      │
│                                                            │
│ Skip Hits (200cr each):        450 events → 90,000 saved   │
│ Refresh Hits (150cr each):     260 events → 39,000 saved   │
│ Overall Cache Hit Rate:           69.2%                    │
└────────────────────────────────────────────────────────────┘
```

**Data source**: `v_rpc_cumulative_summary`

---

## 🎨 Design Implementation

### Color Scheme

- **Credits Spent**: `#ef4444` (red) - Cost indicator
- **Credits Saved**: `#22c55e` (green) - Positive savings
- **Skip Cache**: `#3b82f6` (blue) - Most effective
- **Refresh Cache**: `#8b5cf6` (purple) - Partial optimization
- **Full Scan**: `#9ca3af` (gray) - No optimization
- **Baseline**: `#6b7280` (dark gray) - Reference line

### Typography

- **KPI Values**: Large, bold, monospace (e.g., "3,600")
- **KPI Labels**: Smaller, uppercase (e.g., "CREDITS SAVED 24H")
- **Chart Titles**: Bold, 16-18px
- **Table Headers**: Bold, slightly gray
- **Percentage Values**: Highlight the % symbol

### Responsive Design

**Desktop** (1920px):
- 4 KPI cards in a row
- 2-column layout for charts below

**Tablet** (768px):
- 2 KPI cards per row
- Full-width stacked charts

**Mobile** (375px):
- 1 KPI card per row
- Full-width stacked everything

---

## 📡 API Integration

### Endpoint 1: Dashboard Summary
```
GET /api/rpc-savings/dashboard?days=30
```

**Response**:
```json
{
  "query_time": "2026-03-05T14:30:00Z",
  "summary_24h": {
    "credits_spent_24h": 420,
    "credits_saved_24h": 3600,
    "credits_baseline_24h": 4020,
    "savings_pct_24h": 89.5,
    "cache_hit_rate_24h": 67.3,
    "skip_savings_24h": 2400,
    "refresh_savings_24h": 1200,
    "skip_count_24h": 12,
    "refresh_count_24h": 8,
    "total_requests_24h": 150
  },
  "daily_metrics": [
    {
      "day": "2026-03-05",
      "credits_spent": 420,
      "credits_saved": 3600,
      "credits_baseline": 4020,
      "savings_pct": 89.5,
      "cache_skip_events": 12,
      "cache_refresh_events": 8,
      "full_scan_events": 5,
      "no_cache_events": 0,
      "credits_saved_skip": 2400,
      "credits_saved_refresh": 1200,
      "total_requests": 150,
      "cache_hit_events": 20,
      "cache_hit_rate_pct": 67.3
    }
    // ... more days
  ],
  "cache_performance": [
    {
      "cache_action": "skip",
      "event_count": 2450,
      "total_credits_saved": 490000,
      "avg_credits_saved": 200.0,
      "min_credits_saved": 200,
      "max_credits_saved": 200,
      "efficiency_per_event": 200.0,
      "first_seen": "2026-03-01",
      "last_seen": "2026-03-05"
    },
    // ...
  ]
}
```

### Endpoint 2: Daily Metrics Only
```
GET /api/rpc-savings/daily?days=30
```

Returns just the `daily_metrics` array (for time-series charts)

### Endpoint 3: 24h Dashboard
```
GET /api/rpc-savings/24h
```

Returns just the `summary_24h` object (for KPI cards)

### Endpoint 4: Section Breakdown
```
GET /api/rpc-savings/section-breakdown?days=30
```

Returns savings breakdown by section (funder_incoming, creator_funding, etc.)

---

## 🔄 Auto-Refresh

**For real-time dashboard**:
- Update KPI cards every 1 minute
- Update charts every 5 minutes
- Use WebSocket if available for live updates

**JavaScript example**:
```javascript
setInterval(async () => {
  const response = await fetch('/api/rpc-savings/24h');
  const data = await response.json();
  updateKPICards(data);
}, 60000);  // Update every minute

setInterval(async () => {
  const response = await fetch('/api/rpc-savings/daily?days=30');
  const data = await response.json();
  updateCharts(data);
}, 300000);  // Update every 5 minutes
```

---

## 📈 Chart Library Recommendations

### Option 1: Chart.js (lightweight)
```javascript
new Chart(ctx, {
  type: 'line',
  data: {
    labels: dailyMetrics.map(m => m.day),
    datasets: [
      {
        label: 'Credits Spent',
        data: dailyMetrics.map(m => m.credits_spent),
        borderColor: '#ef4444',
        fill: true,
        backgroundColor: 'rgba(239,68,68,0.1)',
      },
      {
        label: 'Credits Saved',
        data: dailyMetrics.map(m => m.credits_saved),
        borderColor: '#22c55e',
        fill: true,
        backgroundColor: 'rgba(34,197,94,0.1)',
      }
    ]
  }
});
```

### Option 2: Recharts (React component)
```jsx
<LineChart data={dailyMetrics}>
  <CartesianGrid />
  <XAxis dataKey="day" />
  <YAxis />
  <Tooltip />
  <Legend />
  <Line type="monotone" dataKey="credits_spent" stroke="#ef4444" />
  <Line type="monotone" dataKey="credits_saved" stroke="#22c55e" />
</LineChart>
```

### Option 3: Plotly (interactive)
```python
fig = go.Figure()
fig.add_trace(go.Scatter(
    x=daily['day'],
    y=daily['credits_spent'],
    name='Credits Spent',
    line=dict(color='#ef4444')
))
fig.add_trace(go.Scatter(
    x=daily['day'],
    y=daily['credits_saved'],
    name='Credits Saved',
    line=dict(color='#22c55e')
))
fig.show()
```

---

## 🔍 Drill-Down Interaction

**Click day on chart** → Show detailed view for that day:
- All requests for that day
- Break down by section
- Break down by cache action

**Click section name** → Filter all views to that section

**Click cache action** → Show all requests using that cache type

---

## 📊 Sample Dashboard Mockup

```
╔════════════════════════════════════════════════════════════╗
║ RPC Savings Dashboard                          Last 24h    ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     ║
║  │ 420          │  │ 3,600        │  │ 89.5%        │     ║
║  │ SPENT        │  │ SAVED        │  │ SAVINGS      │     ║
║  └──────────────┘  └──────────────┘  └──────────────┘     ║
║                                                            ║
║  ┌──────────────────────────────────────────────────────┐ ║
║  │ Credits Over Time (30 days)                          │ ║
║  │                                    ╱╲                 │ ║
║  │                               ╱╲  ╱  ╲                │ ║
║  │ Credits Saved ═╗      ╱╲  ╱╲╱  ╲╱    ╲               │ ║
║  │                ║ ╱╲  ╱  ╲╱                             │ ║
║  │ Credits Spent ╚╱  ╲╱                                  │ ║
║  │                                                       │ ║
║  │ Mar 5  Mar10  Mar15  Mar20  Mar25  Mar30             │ ║
║  └──────────────────────────────────────────────────────┘ ║
║                                                            ║
║  ┌────────────────────┐  ┌────────────────────┐          ║
║  │ Savings Breakdown  │  │ Cache Event Mix    │          ║
║  │                    │  │                    │          ║
║  │ ████████████░░░░░░ │  │    Skip (40%)       │          ║
║  │ ████████░░░░░░░░░░ │  │  ╭──────────╮      │          ║
║  │ ████████░░░░░░░░░░ │  │ ╱ Refresh    ╲     │          ║
║  │ ████░░░░░░░░░░░░░░ │  │ ╰──────────╯      │          ║
║  │                    │  │  Full (0%)  NoChe  │          ║
║  │ Skip | Refresh     │  │                    │          ║
║  └────────────────────┘  └────────────────────┘          ║
║                                                            ║
║  All-Time Summary                                          ║
║  ├─ Total Saved: 487,500 credits (since Mar 1)           ║
║  ├─ Cache Hit Rate: 67.3%                                 ║
║  └─ Period: 2026-03-01 to 2026-03-05                     ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
```

---

## ✅ Implementation Checklist

- [ ] Create SQL views (see rpc_savings_dashboard.sql)
- [ ] Implement backend API (see rpc_savings_api.py)
- [ ] Create KPI cards component
- [ ] Create line chart component (time series)
- [ ] Create stacked bar chart (savings breakdown)
- [ ] Create pie chart (event mix)
- [ ] Implement API integration
- [ ] Set up auto-refresh intervals
- [ ] Add drill-down interactions
- [ ] Style with color scheme
- [ ] Test responsive layout
- [ ] Performance test with 30+ days of data
- [ ] Deploy to production

---

**Status**: ✅ Ready to implement

