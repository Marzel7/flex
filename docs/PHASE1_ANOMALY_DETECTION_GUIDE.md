# Phase 1 Anomaly Detection Guide

**Purpose**: Catch Phase 1 deployment issues early during the 7-day validation period (March 10-17, 2026).

**Key Insight**: Phase 1 is additive—if anything goes wrong, we'll see RPC spikes, cursor stalls, or extraction backlogs. Early detection prevents rollout problems from impacting production.

---

## Quick Start

### Run Anomaly Detection

```bash
# View anomalies only
python3 phase1_monitoring_enhanced.py --once --alerts-only

# Full dashboard with anomalies
python3 phase1_monitoring_enhanced.py --once

# Continuous monitoring (refresh every 60 seconds)
python3 phase1_monitoring_enhanced.py --interval 60

# Detailed anomaly analysis
python3 phase1_anomaly_detection.py
```

---

## Section 1: Anomaly Types

### ANOMALY 1: RPC_SPIKE
**What it means**: RPC calls increased >30% from baseline
**Root cause**:
- Cursors not loading (fallback to full scans)
- Cursor updates failing (retries triggering)
- Network issues causing retries
**Expected**: Should NOT see this if Phase 1 is working
**Action**: Check logs for "Loaded cursor" messages

```bash
grep "Loaded cursor" .logs/app.log | wc -l
```

---

### ANOMALY 2: CURSOR_GROWTH_STALL
**What it means**: No new cursors created for 24 hours
**Root cause**:
- Extraction process stopped
- Helius API became unreachable
- Database connection issues
**Expected**: Should see new cursors daily during Day 1-7
**Action**: Verify extraction is running, check API access

```bash
# Should show recent activity
sqlite3 flex_complete_database.db \
  "SELECT MAX(last_scan_at) FROM address_scan_state;"
```

---

### ANOMALY 3: EXTRACTION_BACKLOG
**What it means**: >100 creators due for extraction but not yet processed
**Root cause**:
- Extraction rate slower than expected
- Helius API rate limiting
- System resource constraints
**Expected**: Manageable backlog <50 on day 1-2, <20 by day 7
**Action**: Check extraction logs, verify Helius rate limits not hit

```bash
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM address_scan_state \
   WHERE next_scan_at <= CURRENT_TIMESTAMP AND status='active';"
```

---

### ANOMALY 4: CURSOR_ACTIVITY_DROP
**What it means**: <5 cursor updates per hour
**Root cause**:
- Extraction rate declining
- System performance degrading
- Extraction queue getting blocked
**Expected**: Should see 10-50+ updates/hour during active periods
**Action**: Check system resources, verify extraction isn't blocked

```bash
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM address_scan_state \
   WHERE last_scan_at >= datetime('now', '-1 hour');"
```

---

### ANOMALY 5: NO_RECENT_ACTIVITY
**What it means**: No extractions for >2 hours
**Root cause**:
- pumpfun_curve_listener is down
- Extraction process crashed
- No new tokens being detected
**Expected**: Should see continuous activity during trading hours
**Action**: Check if listener is running, verify token detection

```bash
ps aux | grep pumpfun_curve_listener
```

---

### ANOMALY 6: LOW_CURSOR_COVERAGE
**What it means**: <20% cursor coverage by day 2+
**Root cause**:
- Early indicator of stalled extraction
- CursorManager not initializing
- Database issues preventing cursor saves
**Expected**: Should see 5-15% by end of day 1
**Action**: Check CursorManager logs, verify database

```bash
grep "CursorManager initialized" .logs/app.log
```

---

### ANOMALY 7: STALE_CURSORS
**What it means**: 50%+ of cursors unchanged for 7+ days
**Root cause**:
- Certain creators have no recent activity
- Extraction queue is stuck on specific creators
- Activity-based scheduling isn't working
**Expected**: Some stale cursors OK, but not 50%+
**Action**: Check if certain creators have activity issues

---

## Section 2: Alert Thresholds

| Anomaly | Metric | Threshold | Severity |
|---------|--------|-----------|----------|
| RPC_SPIKE | RPC increase | >30% vs yesterday | CRITICAL |
| RPC_PER_HOUR | RPC calls/hour | >150 | WARNING |
| CURSOR_GROWTH_STALL | New cursors/day | 0 for 24h | CRITICAL |
| EXTRACTION_BACKLOG | Overdue creators | >100 | WARNING |
| CURSOR_UPDATES_PER_HOUR | Updates/hour | <5 | WARNING |
| NO_RECENT_ACTIVITY | Hours since last update | >2 hours | CRITICAL |
| LOW_CURSOR_COVERAGE | Coverage % | <20% on day 2+ | WARNING |
| STALE_CURSORS | % unchanged 7+ days | >50% | WARNING |

---

## Section 3: Monitoring Schedule

### Daily (March 10-17)

```bash
# Morning check
python3 phase1_monitoring_enhanced.py --once --alerts-only

# Midday check
python3 phase1_monitoring_enhanced.py --once

# Evening continuous monitoring (let it run for 30 min)
python3 phase1_monitoring_enhanced.py --interval 120
```

### Actions by Day

**Day 1 (March 10)**
- ✅ Phase 1 deployed
- 📊 Run baseline dashboard
- 🔍 Verify CursorManager initializing (grep logs)
- Monitor for errors

**Day 2-3 (March 11-12)**
- 📈 Check cursor coverage rising (target: 5-15%)
- 🔍 Verify "Loaded cursor" messages appearing
- 📊 Confirm RPC calls not spiking

**Day 4-5 (March 13-14)**
- 📈 Check cursor coverage building (target: 20-40%)
- 🔍 Monitor extraction backlog staying <50
- 📊 Verify RPC trending downward

**Day 6 (March 15)**
- 📈 Check cursor coverage good (target: 40-60%)
- 🔍 Confirm extraction is keeping up
- 📊 Verify 40-50% RPC reduction visible

**Day 7 (March 17)**
- 📈 Validate cursor coverage ≥60%
- 🔍 Confirm RPC reduction ≥60%
- ✅ Approve Phase 2 deployment

---

## Section 4: SQL Queries for Manual Checks

### RPC Spike Detection
```sql
WITH daily AS (
  SELECT
    DATE(timestamp) AS day,
    COUNT(*) AS rpc_calls
  FROM rpc_request_log
  WHERE source_file = 'realtime_creator_funding_extractor'
  AND timestamp >= datetime('now', '-7 days')
  GROUP BY DATE(timestamp)
  ORDER BY day DESC
)
SELECT day, rpc_calls FROM daily LIMIT 3;
```

### Cursor Coverage Trend
```sql
SELECT
  DATE(last_scan_at) AS day,
  COUNT(*) AS cursors_created
FROM address_scan_state
WHERE last_scan_at IS NOT NULL
AND last_scan_at >= datetime('now', '-7 days')
GROUP BY DATE(last_scan_at)
ORDER BY day DESC;
```

### Overdue Creators
```sql
SELECT
  status,
  COUNT(*) AS count,
  COUNT(CASE WHEN next_scan_at <= CURRENT_TIMESTAMP THEN 1 END) AS overdue
FROM address_scan_state
GROUP BY status;
```

### Extraction Rate (updates/hour)
```sql
SELECT
  strftime('%Y-%m-%d %H:00:00', last_scan_at) AS hour,
  COUNT(*) AS updates
FROM address_scan_state
WHERE last_scan_at >= datetime('now', '-24 hours')
GROUP BY hour
ORDER BY hour DESC;
```

### Combined Health Check
```sql
WITH rpc_last_hour AS (
  SELECT COUNT(*) rpc_calls
  FROM rpc_request_log
  WHERE timestamp >= datetime('now', '-1 hour')
  AND source_file = 'realtime_creator_funding_extractor'
),
cursor_recent AS (
  SELECT COUNT(*) updated_last_hour
  FROM address_scan_state
  WHERE last_scan_at >= datetime('now', '-1 hour')
),
overdue AS (
  SELECT COUNT(*) overdue
  FROM address_scan_state
  WHERE next_scan_at <= CURRENT_TIMESTAMP
)
SELECT
  rpc_calls,
  updated_last_hour,
  overdue,
  CASE
    WHEN rpc_calls > 150 THEN 'RPC_ALERT'
    WHEN updated_last_hour < 5 THEN 'CURSOR_ALERT'
    WHEN overdue > 100 THEN 'BACKLOG_ALERT'
    ELSE 'OK'
  END status
FROM rpc_last_hour, cursor_recent, overdue;
```

---

## Section 5: Troubleshooting by Symptom

### Symptom: "RPC_SPIKE" Alert

**Check 1**: Are cursors loading?
```bash
grep "Loaded cursor" .logs/app.log | tail -20
```

**Check 2**: Is cursor table populated?
```bash
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM address_scan_state WHERE last_signature IS NOT NULL;"
```

**Check 3**: Check for errors in cursor update
```bash
grep "Error updating cursor" .logs/app.log | tail -10
```

**Solutions**:
- Verify CursorManager initialized (check __init__ logs)
- Check database permissions
- Verify SQLite WAL mode enabled

---

### Symptom: "CURSOR_GROWTH_STALL" Alert

**Check 1**: Are extractions running?
```bash
grep "REALTIME_FUNDING" .logs/app.log | tail -20
```

**Check 2**: Time since last extraction
```bash
sqlite3 flex_complete_database.db \
  "SELECT MAX(last_scan_at) FROM address_scan_state;"
```

**Check 3**: Check for Helius API errors
```bash
grep "Helius" .logs/app.log | grep -i error | tail -10
```

**Solutions**:
- Verify pumpfun_curve_listener is running
- Check Helius API key and quotas
- Verify network connectivity to Helius

---

### Symptom: "EXTRACTION_BACKLOG" Alert

**Check 1**: How many creators are overdue?
```bash
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM address_scan_state \
   WHERE next_scan_at <= CURRENT_TIMESTAMP AND status='active';"
```

**Check 2**: Extraction rate (should be >10/hour)
```bash
sqlite3 flex_complete_database.db \
  "SELECT COUNT(*) FROM address_scan_state \
   WHERE last_scan_at >= datetime('now', '-1 hour');"
```

**Solutions**:
- Check Helius rate limits not exceeded
- Verify extraction process isn't blocked
- Check system resources (CPU, memory, disk)

---

### Symptom: "NO_RECENT_ACTIVITY" Alert

**Check 1**: Is listener running?
```bash
ps aux | grep pumpfun_curve_listener
```

**Check 2**: Last extraction timestamp
```bash
sqlite3 flex_complete_database.db \
  "SELECT MAX(last_scan_at) FROM address_scan_state;"
```

**Check 3**: Check listener logs
```bash
tail -100 .logs/app.log | grep -i "listener\|websocket"
```

**Solutions**:
- Restart pumpfun_curve_listener if down
- Verify Solana WebSocket connectivity
- Check listener error logs

---

## Section 6: Health Check Decision Tree

```
Run: python3 phase1_anomaly_detection.py

├─ No alerts?
│  └─ ✅ Phase 1 is healthy
│
├─ Warning alerts only?
│  ├─ CURSOR_ACTIVITY_DROP? → Check extraction rate
│  ├─ EXTRACTION_BACKLOG? → Check system resources
│  └─ LOW_CURSOR_COVERAGE? → Early warning, monitor closely
│
└─ Critical alerts?
   ├─ RPC_SPIKE? → Cursors not loading (CHECK IMMEDIATELY)
   ├─ CURSOR_GROWTH_STALL? → Extraction stalled (RESTART LISTENER)
   └─ NO_RECENT_ACTIVITY? → System down (RESTART SERVICES)
```

---

## Section 7: Do Not Deploy Phase 2 Unless

- [ ] ✅ No critical anomaly alerts for 24+ hours
- [ ] ✅ Cursor coverage ≥60%
- [ ] ✅ RPC calls trending down (60% reduction visible)
- [ ] ✅ Extraction results consistent
- [ ] ✅ No RPC spikes or stalls
- [ ] ✅ Zero backlog (overdue creators <20)

---

## Section 8: Alert Response Checklist

### When "RPC_SPIKE" Alert Fires
- [ ] Check cursor loading (grep "Loaded cursor")
- [ ] Verify cursor table has data
- [ ] Check database for errors
- [ ] Review cursor_manager.py initialization
- [ ] Run: `python3 test_phase1_with_env.py` to test extraction

### When "CURSOR_GROWTH_STALL" Alert Fires
- [ ] Check if listener is running
- [ ] Verify Helius API key works
- [ ] Check network connectivity
- [ ] Run: `grep "REALTIME_FUNDING" .logs/app.log | tail -50`
- [ ] Consider restarting listener

### When "EXTRACTION_BACKLOG" Alert Fires
- [ ] Check extraction rate (updates/hour)
- [ ] Verify Helius rate limits not exceeded
- [ ] Check system resources
- [ ] Review extraction process logs

### When "NO_RECENT_ACTIVITY" Alert Fires
- [ ] Immediately check if listener is down
- [ ] Restart listener if needed
- [ ] Verify Solana WebSocket connectivity
- [ ] Check listener error logs

---

## Section 9: Escalation Procedures

**If any CRITICAL alert fires**:
1. Run: `python3 phase1_anomaly_detection.py` for full details
2. Check relevant logs (grep commands above)
3. Identify root cause from troubleshooting section
4. Take immediate action (restart, fix, verify)
5. Re-run anomaly detection to confirm resolution

**If CRITICAL alert persists >30 minutes**:
1. Consider rolling back Phase 1 (one-line code change)
2. Investigate root cause in non-production
3. Fix issue and test with Phase 1 test scripts
4. Re-deploy when confident

---

## Summary

The anomaly detection system watches for 7 key problems:

1. **RPC spikes** → Cursors not working
2. **Cursor stalls** → Extraction stopped
3. **Extraction backlog** → Falling behind
4. **Activity drops** → Extraction slowing
5. **No activity** → Listener down
6. **Low coverage** → Extraction stalled early
7. **Stale cursors** → Certain creators not processing

**Daily monitoring** (5 minutes):
```bash
python3 phase1_monitoring_enhanced.py --once --alerts-only
```

**Full status** (5 minutes):
```bash
python3 phase1_monitoring_enhanced.py --once
```

**Continuous monitoring** (let run, watch for alerts):
```bash
python3 phase1_monitoring_enhanced.py --interval 120
```

---

**Last Updated**: March 10, 2026
**Status**: Ready for deployment
