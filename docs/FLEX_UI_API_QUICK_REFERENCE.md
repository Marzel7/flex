# FLEX UI API — Quick Reference

**All endpoints at**: `http://localhost:5002/api/*`

---

## 8 Core Endpoints

### 1. Dashboard Overview
```
GET /api/dashboard
```
**Returns**: Critical alerts, high alerts, monitored orgs, top candidates, system status

### 2. Launch Leaderboard
```
GET /api/launch-leaderboard?limit=50
```
**Returns**: Orgs ranked by master_launch_score with all 8 signal components

### 3. Organizations List
```
GET /api/organizations?min_score=0.3&limit=100
```
**Returns**: All orgs with basic info (wallet, cluster size, score)

### 4. Organization Detail
```
GET /api/organization/<org_id>
```
**Returns**: Complete profile (members, signals, risk, tokens, creators, wallets)

### 5. Launch Waves
```
GET /api/launch-waves?limit=50
```
**Returns**: Detected coordinated launch waves with participation counts

### 6. Dev Clusters
```
GET /api/dev-clusters?limit=50
```
**Returns**: Farm clusters with strength, wallet count, rug probability

### 7. Wallet Intelligence
```
GET /api/wallet/<wallet_address>
```
**Returns**: Wallet org membership, reputation, token history

### 8. Signals Detail
```
GET /api/signals/<org_id>
```
**Returns**: All 8 signals + master score for single org

---

## 8 Predictive Signals (Normalized 0-1)

1. **launch_probability** — Overall launch likelihood
2. **launch_wave_score** — Coordinated wave participation
3. **seed_concentration** — Seed funding concentration
4. **funder_overlap_score** — Cross-organization overlap
5. **organization_momentum** — Activity acceleration
6. **creator_reuse_score** — Multi-creator coordination
7. **operator_activity_score** — Operator wallet activity
8. **reputation_adjustment** — Creator history factor

**Composite**: `master_launch_score` = weighted average of all 8

---

## Alert Levels

| Score | Level | Meaning |
|-------|-------|---------|
| ≥ 0.75 | CRITICAL | Launch today/tomorrow |
| 0.60-0.74 | HIGH | Launch within 3 days |
| 0.40-0.59 | WATCH | Launch within week |
| < 0.40 | LOW | No immediate signal |

---

## Service Layer (6 Classes)

**DashboardService**
- `get_dashboard_overview() → Dict`

**OrganizationService**
- `get_all_organizations(limit, min_score) → List[Dict]`
- `get_organization_detail(org_id) → Dict`

**LaunchService**
- `get_launch_leaderboard(limit) → List[Dict]`
- `get_launch_waves(limit) → List[Dict]`

**ClusterService**
- `get_dev_clusters(limit) → List[Dict]`

**WalletService**
- `get_wallet_intelligence(address) → Dict`

**SignalService**
- `get_organization_signals(org_id) → Dict`

---

## cURL Examples

```bash
# Dashboard
curl http://localhost:5002/api/dashboard

# Top 20 launch candidates
curl http://localhost:5002/api/launch-leaderboard?limit=20

# All orgs above score 0.5
curl http://localhost:5002/api/organizations?min_score=0.5

# Org #42 detail
curl http://localhost:5002/api/organization/42

# Wallet address info
curl http://localhost:5002/api/wallet/8GhGLVZ6n38hpFGBqb6r6CfSfXzKLHwmXgbQzBNREEch

# Org #42 signals
curl http://localhost:5002/api/signals/42

# Launch waves
curl http://localhost:5002/api/launch-waves

# Dev clusters
curl http://localhost:5002/api/dev-clusters
```

---

## JavaScript Examples

```javascript
// Fetch dashboard
const dashboard = await fetch('/api/dashboard').then(r => r.json());
console.log(`${dashboard.critical_alerts} critical, ${dashboard.high_alerts} high`);

// Fetch top launches
const launches = await fetch('/api/launch-leaderboard?limit=10').then(r => r.json());
launches.forEach(org => {
  console.log(`${org.operator_wallet}: ${org.master_launch_score}`);
});

// Fetch org detail
const org = await fetch(`/api/organization/42`).then(r => r.json());
console.log(org.signals.launch_probability);
console.log(org.risk.rug_probability);

// Filter by alert level
const critical = launches.filter(o => o.alert_level === 'CRITICAL');
```

---

## Response Size Estimates

| Endpoint | Size |
|----------|------|
| Dashboard | ~2 KB |
| Leaderboard (50) | ~10 KB |
| Organizations (100) | ~20 KB |
| Organization detail | ~10 KB |
| Launch waves (50) | ~5 KB |
| Dev clusters (50) | ~8 KB |
| Wallet intelligence | ~3 KB |
| Signals | ~1 KB |

---

## Response Time Estimates

| Endpoint | Time |
|----------|------|
| Dashboard | 50-100ms |
| Leaderboard (50) | 10-30ms |
| Organizations (100) | 20-50ms |
| Organization detail | 30-80ms |
| Launch waves (50) | 15-40ms |
| Dev clusters (50) | 20-50ms |
| Wallet intelligence | 20-60ms |
| Signals | 5-10ms |

---

## Error Responses

**404 Not Found**:
```json
{"error": "Organization not found"}
```

**500 Server Error**:
```json
{"error": "Database connection failed"}
```

All errors include HTTP status code + error message.

---

## Integration Checklist

✅ Files created and tested
✅ 6 service classes implemented
✅ 8 REST endpoints working
✅ Error handling in place
✅ Registered in main.py
✅ Ready for frontend dashboard

---

## Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `src/core/flex_ui_services.py` | 390 | Database queries |
| `src/core/flex_ui_api.py` | 380 | Flask endpoints |
| `docs/FLEX_UI_API_IMPLEMENTATION.md` | 550 | Full documentation |

---

## Quick Start

1. **Start server**: `python3 src/core/main.py`
2. **Try endpoint**: `curl http://localhost:5002/api/dashboard`
3. **Build UI**: Use endpoints to populate dashboard pages

All endpoints return JSON and follow REST conventions.
