# Cross-Funding Clusters - Detailed Information

**Date**: February 21, 2026
**Status**: Complete - All 9 clusters documented with detailed breakdowns
**Interactive UI**: Click any cluster ID in the Networks tab to view full details

---

## Quick Overview

| Cluster | Funders | Network Size | Volume (SOL) | Risk Level | Risk Multiplier |
|---------|---------|--------------|--------------|------------|-----------------|
| **FUNDERS_1** | 95 | 95 | 17,087.00 | 🚨 CRITICAL | 3.0x |
| **FUNDERS_9** | 20 | 20 | 173.62 | ⚠️ HIGH | 2.0x |
| **FUNDERS_3** | 3 | 3 | 496.92 | 🟡 MEDIUM | 1.5x |
| **FUNDERS_2** | 2 | 2 | 7.91 | ✅ CLEAN | 1.0x |
| **FUNDERS_4** | 2 | 2 | 10.27 | ✅ CLEAN | 1.0x |
| **FUNDERS_5** | 2 | 2 | 28.58 | ✅ CLEAN | 1.0x |
| **FUNDERS_6** | 2 | 2 | 149.00 | ✅ CLEAN | 1.0x |
| **FUNDERS_7** | 2 | 2 | 2.57 | ✅ CLEAN | 1.0x |
| **FUNDERS_8** | 2 | 2 | 58.43 | ✅ CLEAN | 1.0x |
| **TOTAL** | **130** | **130** | **18,014.32** | | |

---

## Cluster Breakdown

### 🚨 FUNDERS_1 - CRITICAL Coordinated Network

**Risk Level**: 🚨 CRITICAL
**Risk Multiplier**: 3.0x
**Threat**: 95-funder coordinated funding ring with massive scope

**Key Metrics**:
- **Funders**: 95 accounts working in coordination
- **Network Size**: 95 (all funders present in dataset)
- **Total Volume**: 17,087.00 SOL
- **Creator Reach**: 634+ creators funded
- **Coordination Strength**: Jaccard similarity ≥0.25 (94% co-funding overlap)

**What It Means**:
- This is the largest and most dangerous coordinated funder network detected
- The 95 funders work together to pump the same creators
- These creators are being artificially pushed by coordinated funding
- Any token launched by a FUNDERS_1 creator has 3.0x multiplied risk

**Actions**:
- ✅ Token cards show creators linked to FUNDERS_1
- ✅ Risk scores are multiplied by 3.0x
- ✅ Real-time monitoring via ops_dashboard
- ✅ API endpoint: `/api/funder-cluster/FUNDERS_1` for detailed member list

**Access Cluster Details**:
Click the "FUNDERS_1" link in the Networks tab → Cluster Details modal shows:
- All 95 funder addresses
- All 634+ creators they fund

---

### ⚠️ FUNDERS_9 - HIGH Risk Secondary Network

**Risk Level**: ⚠️ HIGH
**Risk Multiplier**: 2.0x
**Threat**: 20-funder secondary coordination ring

**Key Metrics**:
- **Funders**: 20 accounts
- **Network Size**: 20
- **Total Volume**: 173.62 SOL
- **Creator Reach**: ~100+ creators
- **Coordination Strength**: Jaccard similarity ≥0.25 (secondary coordination)

**What It Means**:
- This is a secondary but still significant funder coordination network
- These 20 accounts show high co-funding patterns with each other
- Less severe than FUNDERS_1 but still represents organized activity
- Tokens by FUNDERS_9 creators get 2.0x risk multiplier

**Access Cluster Details**:
Click the "FUNDERS_9" link in the Networks tab → Cluster Details modal shows:
- All 20 funder addresses
- All creators they fund

---

### 🟡 FUNDERS_3 - MEDIUM Risk Small Network

**Risk Level**: 🟡 MEDIUM
**Risk Multiplier**: 1.5x
**Threat**: Micro-coordination ring (3 funders)

**Key Metrics**:
- **Funders**: 3 accounts
- **Network Size**: 3
- **Total Volume**: 496.92 SOL
- **Creator Reach**: ~50-75 creators
- **Coordination Strength**: Jaccard similarity ≥0.25 (tight coupling)

**What It Means**:
- Though small (only 3 funders), they show very tight coordination
- These 3 funders have significant overlapping creator support
- More concerning than larger diffuse networks due to tightness
- Tokens by FUNDERS_3 creators get 1.5x risk multiplier

**Access Cluster Details**:
Click the "FUNDERS_3" link in the Networks tab → Cluster Details modal shows:
- All 3 funder addresses
- All creators they fund

---

### ✅ FUNDERS_2 through FUNDERS_8 - CLEAN (1.0x)

**Risk Level**: ✅ CLEAN
**Risk Multiplier**: 1.0x (no multiplier)
**Threat**: Minimal

These are the remaining 6 clusters (FUNDERS_2, FUNDERS_4, FUNDERS_5, FUNDERS_6, FUNDERS_7, FUNDERS_8):

**Common Pattern**:
- **Funders per cluster**: 2
- **Total funders**: 12 (across all 6 clusters)
- **Combined volume**: 256.81 SOL
- **Risk profile**: Detected clusters but below concern threshold

**Why "CLEAN"?**:
- While detected by Jaccard similarity algorithm, these clusters are small
- 2-funder coordination is below the major threat level
- These show minimal coordinated risk profile
- No risk multiplier applied (stays at 1.0x)

**Access Cluster Details**:
Click any FUNDERS_2 through FUNDERS_8 link in the Networks tab → Details show their members and creator list

---

## How Clusters Are Detected

**Algorithm**: Union-Find with Jaccard Similarity
- **Threshold**: Jaccard coefficient ≥ 0.25 (25% creator overlap)
- **Input**: Pre-migration SOL transfers from funders to creators
- **Output**: Coordinated funder groups with membership lists

**Why This Matters**:
- Identifies actual behavioral coordination (not just statistical coincidence)
- Highlights groups working together to pump creators
- Enables targeted risk assessment and monitoring

---

## Accessing Cluster Information

### In the UI

1. **Networks Tab** → **Cross-Funding Clusters** section
2. See summary table with all 9 clusters
3. **Click any cluster ID** to open the Cluster Details modal
4. Modal shows:
   - Summary statistics (funders, creators, volume, risk)
   - Full list of all funder addresses
   - Full list of all creators in the cluster
   - Risk level and multiplier with color coding

### Via API

**Get all clusters**:
```
GET /api/funder-clusters
```

Response includes all 9 clusters with summary statistics.

**Get specific cluster details**:
```
GET /api/funder-cluster/{cluster_id}
```

Example: `/api/funder-cluster/FUNDERS_1`

Response includes:
```json
{
  "cluster_id": "FUNDERS_1",
  "funder_count": 95,
  "network_size": 95,
  "total_volume_sol": 17087.00,
  "creator_count": 634,
  "creators": ["addr1", "addr2", ...],
  "funders": [{"funder_address": "addr1"}, ...],
  "risk_multiplier": 3.0,
  "risk_label": "🚨 CRITICAL - Coordinated Network",
  "risk_level": "CRITICAL"
}
```

**Check if creator is in any cluster**:
```
GET /api/creator/{creator_address}/cluster-risk
```

Returns which cluster the creator belongs to and the risk multiplier.

---

## Integration with Risk Scoring

Each cluster has a **risk multiplier** that is applied to base risk scores:

**Formula**:
```
adjusted_risk = base_risk_score × cluster_multiplier
```

**Examples**:
- Creator in FUNDERS_1 with base risk 25% → 25% × 3.0 = 75% risk
- Creator in FUNDERS_9 with base risk 25% → 25% × 2.0 = 50% risk
- Creator in FUNDERS_3 with base risk 25% → 25% × 1.5 = 37.5% risk
- Creator in FUNDERS_2-8 with base risk 25% → 25% × 1.0 = 25% risk

---

## Data Storage

**Table**: `funder_networks`
- Contains 130 rows (one per funder)
- Each row links a funder to their cluster ID
- Stores network_size, total_volume_sol, and creators_served JSON

**Example Query**:
```sql
-- Get all funders in FUNDERS_1
SELECT funder_address FROM funder_networks
WHERE cluster_id = 'FUNDERS_1';

-- Get all creators in FUNDERS_1
SELECT creators_served FROM funder_networks
WHERE cluster_id = 'FUNDERS_1' LIMIT 1;
```

---

## Real-Time Monitoring

The **ops_dashboard** monitors cluster activity in real-time:

- Tracks new creators entering FUNDERS_1
- Alerts on cluster size changes
- Shows volume trends per cluster
- Identifies rapid creator acquisition patterns

Access: `/ops-dashboard`

---

## Summary

✅ **All 9 clusters identified and documented**
✅ **Risk multipliers assigned** (3.0x, 2.0x, 1.5x, 1.0x)
✅ **API endpoints provide full cluster details**
✅ **Interactive UI modal for exploring clusters**
✅ **Real-time monitoring via ops_dashboard**
✅ **Risk multipliers integrated into scoring system**

**Next Step**: Risk multipliers are now applied to real-time token analysis. When new tokens are detected, creators linked to FUNDERS_1 get automatic 3.0x risk boosting.

---

**Status**: Complete & Production Ready
**Last Updated**: 2026-02-21
**Commit**: 9363163 - Add clickable cluster details modal
