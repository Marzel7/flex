# FLEX Intelligence Dashboard — Quick Start Guide

**Status**: ✅ Ready to Deploy
**Version**: 2.0
**Date**: March 12, 2026

---

## 30-Second Setup

```bash
# 1. Start the server
python3 src/core/main.py

# 2. Open browser
http://localhost:5002

# Done! Dashboard is live
```

---

## Pages Overview

| Page | URL | What You Get |
|------|-----|----------|
| **Dashboard** | `/` | System KPIs, top candidates, alert charts |
| **Launch Radar** | `/launch-radar` | Ranked leaderboard (100 orgs), all 8 signals |
| **Organizations** | `/org-explorer` | Browse all orgs, search, filter by score |
| **Organization Detail** | `/organization/123` | Complete profile: signals, charts, risk, members |
| **Launch Waves** | `/launch-waves` | Timeline of detected coordination waves |
| **Dev Clusters** | `/dev-clusters` | Cluster analysis and network view |
| **Signal Explorer** | `/signal-explorer` | Radar chart of predictive signals |
| **Wallet Search** | (sidebar tool) | Look up wallet intelligence |

---

## Key Features

### 🎯 Real-Time Filtering
- **Launch Radar**: Filter by alert level (CRITICAL/HIGH/WATCH/LOW)
- **Organizations**: Search box + min score filter
- **DataTables**: Built-in search and pagination

### 📊 Data Visualization
- **Bar Charts**: Top orgs by score
- **Line Charts**: 7-day activity trends
- **Doughnut Charts**: Alert distribution
- **Radar Charts**: All 8 signals polygon
- **Score Bars**: Visual progress bars with percentages

### 🔍 Deep Dive Intelligence
- **8 Predictive Signals**: launch_probability, wave_score, seed_concentration, funder_overlap, momentum, creator_reuse, operator_activity, reputation
- **Multi-Window Predictions**: 24h, 72h, 7d probability
- **Risk Assessment**: Rug probability, instability, confidence scores
- **Member Lists**: All wallets/creators in organization
- **Activity History**: 7-day snapshots of active creators and burst events

### 📱 Mobile Responsive
- Sidebar collapses on mobile
- Tables remain readable
- Charts adapt to screen size
- Touch-friendly buttons

---

## Using Each Page

### Dashboard
1. Open `/` (home)
2. See 4 KPI stat cards
3. View top launch candidates table
4. Click "View" on any row to see organization detail

### Launch Radar
1. Open `/launch-radar`
2. View ranked list of organizations
3. **Filter by Alert**: Select dropdown at top
4. **Search**: Type operator wallet or ID
5. **View Details**: Click row or "View" button

### Organizations
1. Open `/org-explorer`
2. Browse all organizations (500 max)
3. **Filter by Score**: Enter minimum score (0-1), click "Filter"
4. **Search**: Type in search box
5. **View Details**: Click "View" button

### Organization Profile
1. Click "View" from any table
2. Or navigate to `/organization/123`
3. See 4 stat cards at top
4. **Signals**: 8 animated bars showing each signal
5. **Activity Chart**: 7-day line chart
6. **Predictions**: 24h/72h/7d bars
7. **Risk**: Rug probability and confidence
8. **Members**: Scroll down to see all wallets
9. **Back**: Click "Back to Radar" to return

### Launch Waves
1. Open `/launch-waves`
2. See timeline of detected waves
3. Each card shows: wave ID, type, org count, creator count, avg score
4. Table below shows all waves sortable

### Dev Clusters
1. Open `/dev-clusters`
2. Left panel: Click cluster to see details
3. Right panel: Stats for selected cluster
4. Bottom table: All clusters with strength/wallets/creators

### Signal Explorer
1. Open `/signal-explorer`
2. (Optional) Enter org ID and click "Load Signals"
3. Default: Shows top CRITICAL organization
4. **Radar Chart**: All 8 signals as polygon
5. **Signal Grid**: Each signal with value and bar

### Wallet Search
1. Click "Wallet Search" in sidebar
2. Enter wallet address in search box
3. Click "Search"
4. See wallet profile: member type, tokens launched, rug rate
5. View all tokens created by wallet
6. Click "View Organization" to see related org

---

## Understanding the Alerts

Each organization gets classified by **Master Launch Score**:

| Score | Alert | Meaning |
|-------|-------|---------|
| ≥ 75% | 🔴 **CRITICAL** | Launch expected today/tomorrow |
| 60-74% | 🟠 **HIGH** | Launch expected within 3 days |
| 40-59% | 🟡 **WATCH** | Launch possible within week |
| < 40% | 🟢 **LOW** | No immediate signal |

All 8 signals combine to create this score. View the **Signal Explorer** to see how each signal contributes.

---

## Understanding the Signals

The dashboard displays 8 independent predictive signals:

1. **Launch Probability** — Overall likelihood of token launch
2. **Launch Wave Score** — Participation in coordinated waves
3. **Seed Concentration** — How concentrated seed funding is
4. **Funder Overlap** — Overlap with other organizations
5. **Organization Momentum** — Activity acceleration rate
6. **Creator Reuse** — How many times creators coordinate
7. **Operator Activity** — Activity of main operator wallet
8. **Reputation Adjustment** — Creator history and reputation

Use **Signal Explorer** (`/signal-explorer`) to visualize these on a radar chart.

---

## Interpreting the Data

### High Score Organization
- ✅ Multiple signals trending high
- ✅ High momentum (activity increasing)
- ✅ High seed concentration (many funders, few seeds)
- ✅ Launch likely imminent

### Low Score Organization
- ⚠️ Signals below 40%
- ⚠️ Low momentum (activity stable/declining)
- ⚠️ No unusual patterns
- ⚠️ Continue monitoring

### Medium Score Organization
- 📊 Mixed signals
- 📊 Keep in WATCH status
- 📊 Monitor for momentum changes
- 📊 Check back daily

---

## Common Tasks

### "Find all CRITICAL organizations"
1. Go to Launch Radar (`/launch-radar`)
2. Select "CRITICAL" from alert filter
3. All CRITICAL orgs will be shown

### "Compare two organizations"
1. Visit each org profile (`/organization/123`, `/organization/456`)
2. Compare their signals side-by-side
3. Or use Signal Explorer to visualize both

### "See what wallets are in an organization"
1. Go to organization detail
2. Scroll to "Members" table
3. See all wallets/creators with their types

### "Look up a specific wallet"
1. Click "Wallet Search" in sidebar
2. Paste wallet address
3. See reputation, tokens, and related organization

### "Track a wave"
1. Go to Launch Waves (`/launch-waves`)
2. Find wave by ID or date
3. See how many orgs/creators involved
4. View average wave score

---

## Keyboard Shortcuts

- `Ctrl+F` — Open browser find (search current table)
- `Ctrl+P` — Open browser print (download page)

---

## Troubleshooting

### "Dashboard shows 'Loading' forever"
1. Check server is running: `python3 src/core/main.py`
2. Check browser console (F12 → Console) for errors
3. Verify API working: `curl http://localhost:5002/api/dashboard`

### "Search/Filter not working"
1. Refresh page (Ctrl+R)
2. Check browser console (F12)
3. Verify API returns data: `curl http://localhost:5002/api/organizations`

### "Charts not showing"
1. Refresh page
2. Check that page fully loaded
3. Check browser console for JS errors

### "Organization detail is blank"
1. Verify org ID is numeric
2. Check that organization exists in database
3. Try `/organization/1` to test with org 1

---

## API Endpoints Behind the Scenes

The dashboard automatically calls these endpoints:

```
GET /api/dashboard                      → Dashboard KPIs
GET /api/launch-leaderboard?limit=100   → Launch Radar
GET /api/organizations?limit=500        → Org Explorer
GET /api/organization/<id>              → Org Profile base
GET /api/signals/<id>                   → 8 signals
GET /api/orgs/<id>/windows              → Multi-window predictions
GET /api/orgs/<id>/snapshots?days=7     → Activity history
GET /api/orgs/<id>/risk                 → Risk assessment
GET /api/orgs/<id>/alerts?limit=10      → Recent alerts
GET /api/launch-waves?limit=50          → Waves timeline
GET /api/dev-clusters?limit=50          → Cluster data
GET /api/wallet/<address>               → Wallet intelligence
```

No manual API calls needed — the dashboard handles all of this!

---

## Tips & Tricks

### 💡 Navigate Efficiently
- Use browser back button to return to previous page
- Bookmark frequently visited organizations
- Use sidebar to jump between pages

### 💡 Analyze Trends
- Check Dashboard daily for new CRITICAL alerts
- Monitor organization momentum over time
- Watch Launch Waves for coordination patterns
- Track creators in Member lists

### 💡 Get Insights
- High cluster strength + high rug prob = risky farm
- High momentum + high cadence confidence = launch imminent
- Multiple orgs with same members = possible coordination
- Creator reuse score high = experienced team launching together

---

## Support

**Need help?**
1. Check this guide first
2. Read [FLEX_DASHBOARD_COMPLETE.md](docs/FLEX_DASHBOARD_COMPLETE.md) for technical details
3. Check server logs: `tail -f logs/dev_intelligence.log`
4. Review code: `templates/flex_dashboard.html`

---

**Happy analyzing! 🚀**

For more details on the backend API and detection pipeline, see:
- [FLEX_V3_1_INTEGRATION_COMPLETE.md](docs/FLEX_V3_1_INTEGRATION_COMPLETE.md)
- [FLEX_SYSTEM_STATUS_MARCH12_2026.md](docs/FLEX_SYSTEM_STATUS_MARCH12_2026.md)
- [FLEX_UI_API_README.md](docs/FLEX_UI_API_README.md)
