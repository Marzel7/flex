# Optimization Metrics UI Integration Guide

**Status:** Production Ready
**Files:** 1 Python API module + Integration instructions
**Complexity:** Simple (copy-paste integration)
**Time:** 15-30 minutes

---

## Overview

This guide shows how to expose Helius optimization metrics to your Flask dashboard.

**Files Provided:**
- `optimization_api.py` — Flask API endpoints (ready to use)
- `OPTIMIZATION_UI_INTEGRATION.md` — This file (integration guide)

---

## Step 1: Register API Routes in main.py

Add this to your `main.py` file (around line 100, after Flask app initialization):

```python
# Import optimization API routes
from http_instrumentation.optimization_api import register_optimization_routes

# After creating Flask app
app = Flask(__name__)

# Register optimization API routes
register_optimization_routes(app, db_path='flex_complete_database.db')
```

That's it! All endpoints are now available.

---

## Step 2: Available API Endpoints

Once registered, you can call these endpoints from your frontend:

### `/api/optimization/efficiency-24h`
**Get optimization efficiency metrics for last 24h**

```json
{
  "status": "success",
  "data": {
    "total_scans": 523,
    "single_page_scans": 421,
    "multi_page_scans": 102,
    "budget_exhausted_count": 2,
    "tombstone_skips": 45,
    "shortlisted_scans": 523,
    "pct_single_page": 80.5,
    "pct_multi_page": 19.5,
    "pct_shortlisted": 100.0
  }
}
```

### `/api/optimization/budget-summary`
**Get budget exhaustion summary**

```json
{
  "status": "success",
  "data": {
    "total_creators": 156,
    "exhausted_count": 2,
    "avg_credits_spent": 185,
    "max_credits_spent": 248,
    "avg_pct_budget_used": 74.0,
    "pct_budget_exhausted": 1.3
  }
}
```

### `/api/optimization/tombstone-stats`
**Get tombstone (empty wallet skip) statistics**

```json
{
  "status": "success",
  "data": {
    "empty_tombstones": 342,
    "shallow_tombstones": 156,
    "total_tombstones": 498,
    "skips_in_24h": 87,
    "estimated_credits_saved_24h": 13050
  }
}
```

### `/api/optimization/shortlist-stats`
**Get funder prefilter shortlist statistics**

```json
{
  "status": "success",
  "data": {
    "total_creators": 156,
    "total_funders": 18234,
    "shortlisted_funders": 3456,
    "cex_count": 234,
    "infra_count": 142,
    "avg_inbound_sol": 0.4521,
    "pct_shortlisted": 18.9
  }
}
```

### `/api/optimization/deep-scan-distribution`
**Get deep scan page distribution**

```json
{
  "status": "success",
  "data": {
    "distribution": {
      "1_pages": {"count": 421, "avg_credits": 52},
      "2_pages": {"count": 65, "avg_credits": 98},
      "3_pages": {"count": 28, "avg_credits": 145},
      "4_pages": {"count": 7, "avg_credits": 192},
      "5_pages": {"count": 2, "avg_credits": 238}
    },
    "total_scans": 523
  }
}
```

### `/api/optimization/timeline`
**Get optimization metrics over last 7 days**

```json
{
  "status": "success",
  "data": [
    {
      "date": "2026-02-27",
      "total_scans": 234,
      "single_page_scans": 187,
      "pct_single_page": 79.9,
      "budget_exhausted": 0,
      "tombstone_skips": 12,
      "avg_credits_per_scan": 89
    },
    ...
  ],
  "days": 7
}
```

### `/api/optimization/summary`
**Get complete optimization summary (all metrics combined)**

```json
{
  "status": "success",
  "data": {
    "efficiency_24h": {...},
    "budget_summary": {...},
    "tombstone_stats": {...},
    "shortlist_stats": {...},
    "deep_scan_distribution": {...},
    "timeline": [...]
  }
}
```

---

## Step 3: Add Optimization Section to Main Dashboard

Add this to your main metrics page HTML (e.g., the `/` route template):

```html
<!-- Optimization Metrics Card -->
<div class="card" style="background: rgba(10, 30, 50, 0.8); border-left: 4px solid #a78bfa;">
  <div class="card-header" style="color: #a78bfa; display: flex; justify-content: space-between; align-items: center;">
    <h3>🎯 Helius Optimization (24h)</h3>
    <span id="opt-status" style="font-size: 12px; color: #888;">Loading...</span>
  </div>
  <div class="card-body">
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">

      <!-- Single-Page Scans -->
      <div>
        <div style="color: #888; font-size: 12px;">Single-Page Scans</div>
        <div style="font-size: 28px; font-weight: bold; color: #22c55e;">
          <span id="opt-single-page-pct">-</span>%
        </div>
        <div style="color: #888; font-size: 12px;">
          <span id="opt-single-page-count">0</span> / <span id="opt-total-scans">0</span>
        </div>
      </div>

      <!-- Budget Exhaustion -->
      <div>
        <div style="color: #888; font-size: 12px;">Budget Exhausted</div>
        <div style="font-size: 28px; font-weight: bold; color: #fbbf24;">
          <span id="opt-budget-exhausted">0</span>
        </div>
        <div style="color: #888; font-size: 12px;">
          <span id="opt-budget-pct">0</span>% of creators
        </div>
      </div>

      <!-- Tombstone Skips -->
      <div>
        <div style="color: #888; font-size: 12px;">Tombstone Skips (24h)</div>
        <div style="font-size: 28px; font-weight: bold; color: #06b6d4;">
          <span id="opt-tombstone-skips">0</span>
        </div>
        <div style="color: #888; font-size: 12px;">
          <span id="opt-credits-saved">0</span> credits saved
        </div>
      </div>

      <!-- Shortlist Effectiveness -->
      <div>
        <div style="color: #888; font-size: 12px;">Shortlist Coverage</div>
        <div style="font-size: 28px; font-weight: bold; color: #60a5fa;">
          <span id="opt-shortlist-pct">-</span>%
        </div>
        <div style="color: #888; font-size: 12px;">
          <span id="opt-shortlist-count">0</span> funders scanned
        </div>
      </div>
    </div>

    <!-- Status Indicators -->
    <div style="border-top: 1px solid #333; padding-top: 15px; margin-top: 15px;">
      <div style="display: flex; gap: 20px; flex-wrap: wrap;">
        <div>
          <div style="color: #888; font-size: 11px;">Status</div>
          <div id="opt-efficiency-status" style="color: #22c55e; font-weight: bold; margin-top: 5px;">
            ✅ Excellent
          </div>
        </div>
        <div>
          <div style="color: #888; font-size: 11px;">Estimated Monthly Savings</div>
          <div id="opt-monthly-savings" style="color: #fbbf24; font-weight: bold; margin-top: 5px;">
            ~$2,000
          </div>
        </div>
        <div>
          <div style="color: #888; font-size: 11px;">Total Tombstones</div>
          <div id="opt-total-tombstones" style="color: #a78bfa; font-weight: bold; margin-top: 5px;">
            342
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
// Load optimization metrics
async function loadOptimizationMetrics() {
  try {
    const response = await fetch('/api/optimization/summary');
    const result = await response.json();

    if (result.status !== 'success') {
      document.getElementById('opt-status').textContent = 'Error loading metrics';
      return;
    }

    const data = result.data;
    const eff = data.efficiency_24h;
    const budget = data.budget_summary;
    const tombstone = data.tombstone_stats;
    const shortlist = data.shortlist_stats;

    // Update efficiency section
    document.getElementById('opt-single-page-pct').textContent = eff.pct_single_page.toFixed(1);
    document.getElementById('opt-single-page-count').textContent = eff.single_page_scans;
    document.getElementById('opt-total-scans').textContent = eff.total_scans;

    // Update budget section
    document.getElementById('opt-budget-exhausted').textContent = budget.exhausted_count;
    document.getElementById('opt-budget-pct').textContent = budget.pct_budget_exhausted.toFixed(1);

    // Update tombstone section
    document.getElementById('opt-tombstone-skips').textContent = tombstone.skips_in_24h;
    document.getElementById('opt-credits-saved').textContent = tombstone.estimated_credits_saved_24h.toLocaleString();

    // Update shortlist section
    document.getElementById('opt-shortlist-pct').textContent = shortlist.pct_shortlisted.toFixed(1);
    document.getElementById('opt-shortlist-count').textContent = shortlist.shortlisted_funders.toLocaleString();

    // Update totals
    document.getElementById('opt-total-tombstones').textContent = tombstone.total_tombstones.toLocaleString();

    // Estimate monthly savings
    const savings24h = tombstone.estimated_credits_saved_24h * 30;
    const monthlySavings = (savings24h * 0.01).toFixed(0);
    document.getElementById('opt-monthly-savings').textContent = `~$${monthlySavings}`;

    // Determine efficiency status
    let status = '✅ Excellent';
    let statusColor = '#22c55e';

    if (eff.pct_single_page < 60) {
      status = '⚠️  Needs Optimization';
      statusColor = '#fbbf24';
    } else if (eff.pct_single_page < 75) {
      status = '✅ Good';
      statusColor = '#22c55e';
    }

    document.getElementById('opt-efficiency-status').textContent = status;
    document.getElementById('opt-efficiency-status').style.color = statusColor;

    document.getElementById('opt-status').textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    console.error('Error loading optimization metrics:', error);
    document.getElementById('opt-status').textContent = 'Failed to load';
  }
}

// Load on page load
document.addEventListener('DOMContentLoaded', loadOptimizationMetrics);

// Refresh every 60 seconds
setInterval(loadOptimizationMetrics, 60000);
</script>
```

---

## Step 4: Create Optimization Dashboard Page (Optional)

Create a new route in `main.py` for a dedicated optimization dashboard:

```python
@app.route('/optimization-dashboard')
def optimization_dashboard():
    """Dedicated optimization metrics dashboard"""
    return render_template('optimization_dashboard.html')
```

Then create `templates/optimization_dashboard.html`:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Helius Optimization Dashboard</title>
    <style>
        body {
            background: #0a1e32;
            color: #e0e0e0;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        .card {
            background: rgba(20, 40, 60, 0.9);
            border: 1px solid #333;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card-header {
            color: #a78bfa;
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 15px;
            border-bottom: 1px solid #333;
            padding-bottom: 10px;
        }
        .grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        .metric {
            background: rgba(10, 30, 50, 0.5);
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #a78bfa;
        }
        .metric-label {
            color: #888;
            font-size: 12px;
            margin-bottom: 5px;
        }
        .metric-value {
            font-size: 32px;
            font-weight: bold;
            color: #22c55e;
        }
        .metric-detail {
            color: #888;
            font-size: 12px;
            margin-top: 5px;
        }
        .chart-container {
            background: rgba(10, 30, 50, 0.5);
            padding: 20px;
            border-radius: 6px;
            margin-top: 15px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 style="color: #a78bfa; margin-bottom: 30px;">🎯 Helius Optimization Dashboard</h1>

        <!-- Summary Metrics -->
        <div class="card">
            <div class="card-header">24-Hour Summary</div>
            <div class="grid-2">
                <div class="metric">
                    <div class="metric-label">Single-Page Scans</div>
                    <div class="metric-value" id="metric-single-page">-</div>
                    <div class="metric-detail" id="metric-single-page-detail">0 / 0 scans</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Multi-Page Scans</div>
                    <div class="metric-value" id="metric-multi-page" style="color: #fbbf24;">-</div>
                    <div class="metric-detail" id="metric-multi-page-detail">0 / 0 scans</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Budget Exhausted</div>
                    <div class="metric-value" id="metric-budget" style="color: #ef4444;">-</div>
                    <div class="metric-detail" id="metric-budget-detail">0 creators</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Tombstone Skips</div>
                    <div class="metric-value" id="metric-tombstone" style="color: #06b6d4;">-</div>
                    <div class="metric-detail" id="metric-tombstone-detail">~0 credits saved</div>
                </div>
            </div>
        </div>

        <!-- Budget Tracking -->
        <div class="card">
            <div class="card-header">Budget Tracking (Last 24h)</div>
            <div class="grid-2">
                <div class="metric">
                    <div class="metric-label">Total Creators Extracted</div>
                    <div class="metric-value" id="metric-creators" style="color: #3b82f6;">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Avg Budget Usage</div>
                    <div class="metric-value" id="metric-avg-budget">-</div>
                    <div class="metric-detail" id="metric-avg-budget-detail">0%</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Max Credits Spent</div>
                    <div class="metric-value" id="metric-max-credits" style="color: #fbbf24;">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Avg Credits Spent</div>
                    <div class="metric-value" id="metric-avg-credits">-</div>
                </div>
            </div>
        </div>

        <!-- Funder Prefilter -->
        <div class="card">
            <div class="card-header">Funder Prefilter Shortlisting</div>
            <div class="grid-2">
                <div class="metric">
                    <div class="metric-label">Total Funders Discovered</div>
                    <div class="metric-value" id="metric-total-funders" style="color: #3b82f6;">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Shortlisted Funders</div>
                    <div class="metric-value" id="metric-shortlisted" style="color: #22c55e;">-</div>
                    <div class="metric-detail" id="metric-shortlisted-pct">0% shortlist</div>
                </div>
                <div class="metric">
                    <div class="metric-label">CEX Funders</div>
                    <div class="metric-value" id="metric-cex" style="color: #a78bfa;">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Infrastructure Funders</div>
                    <div class="metric-value" id="metric-infra" style="color: #a78bfa;">-</div>
                </div>
            </div>
        </div>

        <!-- Tombstone Management -->
        <div class="card">
            <div class="card-header">Tombstone Management</div>
            <div class="grid-2">
                <div class="metric">
                    <div class="metric-label">Empty Wallet Tombstones</div>
                    <div class="metric-value" id="metric-empty-tombstones" style="color: #ef4444;">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Shallow Classification Tombstones</div>
                    <div class="metric-value" id="metric-shallow-tombstones">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">24h Skips Prevented</div>
                    <div class="metric-value" id="metric-skips-24h" style="color: #22c55e;">-</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Estimated Savings</div>
                    <div class="metric-value" id="metric-savings-24h" style="color: #fbbf24;">-</div>
                    <div class="metric-detail">credits / 24h</div>
                </div>
            </div>
        </div>

        <!-- Timeline -->
        <div class="card">
            <div class="card-header">7-Day Trend</div>
            <div class="chart-container">
                <canvas id="timeline-chart" height="300"></canvas>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
    <script>
        let timelineChart = null;

        async function loadAllMetrics() {
            try {
                const response = await fetch('/api/optimization/summary');
                const result = await response.json();

                if (result.status !== 'success') {
                    console.error('Failed to load metrics');
                    return;
                }

                const data = result.data;
                updateMetrics(data);
                updateTimeline(data.timeline);
            } catch (error) {
                console.error('Error loading metrics:', error);
            }
        }

        function updateMetrics(data) {
            const eff = data.efficiency_24h;
            const budget = data.budget_summary;
            const tombstone = data.tombstone_stats;
            const shortlist = data.shortlist_stats;

            // Efficiency
            document.getElementById('metric-single-page').textContent = eff.pct_single_page.toFixed(1) + '%';
            document.getElementById('metric-single-page-detail').textContent = `${eff.single_page_scans} / ${eff.total_scans} scans`;

            document.getElementById('metric-multi-page').textContent = eff.pct_multi_page.toFixed(1) + '%';
            document.getElementById('metric-multi-page-detail').textContent = `${eff.multi_page_scans} / ${eff.total_scans} scans`;

            // Budget
            document.getElementById('metric-budget').textContent = budget.exhausted_count;
            document.getElementById('metric-budget-detail').textContent = `${budget.total_creators} creators (${budget.pct_budget_exhausted.toFixed(1)}%)`;

            // Tombstone
            document.getElementById('metric-tombstone').textContent = tombstone.skips_in_24h;
            document.getElementById('metric-tombstone-detail').textContent = `~${tombstone.estimated_credits_saved_24h.toLocaleString()} credits saved`;

            // Creators
            document.getElementById('metric-creators').textContent = budget.total_creators;

            // Avg Budget
            document.getElementById('metric-avg-budget').textContent = budget.avg_pct_budget_used.toFixed(1) + '%';
            document.getElementById('metric-avg-budget-detail').textContent = `${budget.avg_credits_spent} avg credits`;

            // Max Credits
            document.getElementById('metric-max-credits').textContent = budget.max_credits_spent;

            // Avg Credits
            document.getElementById('metric-avg-credits').textContent = budget.avg_credits_spent;

            // Funders
            document.getElementById('metric-total-funders').textContent = shortlist.total_funders.toLocaleString();
            document.getElementById('metric-shortlisted').textContent = shortlist.shortlisted_funders.toLocaleString();
            document.getElementById('metric-shortlisted-pct').textContent = `${shortlist.pct_shortlisted.toFixed(1)}% shortlist`;

            // CEX/INFRA
            document.getElementById('metric-cex').textContent = shortlist.cex_count;
            document.getElementById('metric-infra').textContent = shortlist.infra_count;

            // Tombstones
            document.getElementById('metric-empty-tombstones').textContent = tombstone.empty_tombstones.toLocaleString();
            document.getElementById('metric-shallow-tombstones').textContent = tombstone.shallow_tombstones.toLocaleString();
            document.getElementById('metric-skips-24h').textContent = tombstone.skips_in_24h.toLocaleString();
            document.getElementById('metric-savings-24h').textContent = tombstone.estimated_credits_saved_24h.toLocaleString();
        }

        function updateTimeline(timeline) {
            const dates = timeline.map(d => d.date);
            const singlePage = timeline.map(d => d.pct_single_page);
            const budgetExhausted = timeline.map(d => d.budget_exhausted);
            const tombstoneSkips = timeline.map(d => d.tombstone_skips);

            const ctx = document.getElementById('timeline-chart').getContext('2d');

            if (timelineChart) {
                timelineChart.destroy();
            }

            timelineChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: dates,
                    datasets: [
                        {
                            label: 'Single-Page Scans %',
                            data: singlePage,
                            borderColor: '#22c55e',
                            backgroundColor: 'rgba(34, 197, 94, 0.1)',
                            yAxisID: 'y',
                            tension: 0.4,
                        },
                        {
                            label: 'Budget Exhausted',
                            data: budgetExhausted,
                            borderColor: '#ef4444',
                            backgroundColor: 'rgba(239, 68, 68, 0.1)',
                            yAxisID: 'y1',
                            tension: 0.4,
                        },
                        {
                            label: 'Tombstone Skips',
                            data: tombstoneSkips,
                            borderColor: '#06b6d4',
                            backgroundColor: 'rgba(6, 182, 212, 0.1)',
                            yAxisID: 'y2',
                            tension: 0.4,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false,
                    },
                    scales: {
                        y: {
                            type: 'linear',
                            display: true,
                            position: 'left',
                            ticks: { color: '#888' },
                            grid: { color: '#333' },
                        },
                        y1: {
                            type: 'linear',
                            display: true,
                            position: 'center',
                            ticks: { color: '#888' },
                            grid: { color: '#333' },
                        },
                        y2: {
                            type: 'linear',
                            display: true,
                            position: 'right',
                            ticks: { color: '#888' },
                            grid: { color: '#333' },
                        },
                        x: {
                            ticks: { color: '#888' },
                            grid: { color: '#333' },
                        },
                    },
                    plugins: {
                        legend: {
                            labels: { color: '#e0e0e0' },
                        },
                    },
                },
            });
        }

        // Load on page load
        document.addEventListener('DOMContentLoaded', loadAllMetrics);

        // Refresh every 60 seconds
        setInterval(loadAllMetrics, 60000);
    </script>
</body>
</html>
```

---

## Step 5: Add Navigation Link

Add a link to the optimization dashboard in your main navbar:

```html
<a href="/optimization-dashboard" style="color: #a78bfa; text-decoration: none; margin: 0 15px;">
  🎯 Optimization Dashboard
</a>
```

---

## Testing

Test the API endpoints from the command line:

```bash
# Get optimization summary
curl http://localhost:5000/api/optimization/summary | jq

# Get efficiency metrics
curl http://localhost:5000/api/optimization/efficiency-24h | jq

# Get budget summary
curl http://localhost:5000/api/optimization/budget-summary | jq

# Get tombstone stats
curl http://localhost:5000/api/optimization/tombstone-stats | jq

# Get shortlist stats
curl http://localhost:5000/api/optimization/shortlist-stats | jq

# Get timeline
curl http://localhost:5000/api/optimization/timeline | jq
```

---

## Integration Checklist

- [ ] Copy `optimization_api.py` to project
- [ ] Add `register_optimization_routes()` call in main.py
- [ ] Test API endpoints
- [ ] Add optimization card to main dashboard
- [ ] (Optional) Create dedicated optimization dashboard page
- [ ] (Optional) Add navigation link to dashboard

---

## Expected Result

After integration, you'll have:

✅ Real-time optimization metrics displayed on the main dashboard
✅ Dedicated optimization dashboard page showing detailed metrics
✅ 6 API endpoints for programmatic access
✅ Auto-refreshing metrics (60-second interval)
✅ Visual indicators for efficiency, budget, and tombstone tracking

---

**Version:** 1.0
**Status:** Ready to Deploy
**Time to Integrate:** 15-30 minutes
